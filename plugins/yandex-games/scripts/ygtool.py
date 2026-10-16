"""ygtool — утилиты подготовки материалов для Яндекс Игр.

Все пути относительны к корню Unity-проекта (аргумент --project, по умолчанию cwd).
Структура в проекте:
  yandex/config.json        настройки проекта (языки, платформы, ориентация, id игры)
  yandex/state.json         прогресс пайплайна (что сделано / заблокировано)
  yandex/listing.json       тексты по языкам + категории/теги
  yandex/raw/...            исходники: screenshots/{desktop,mobile}, keyart/, video/
  yandex/out/...            готовые файлы для консоли

Команды:
  doctor      проверить окружение (Python-пакеты, ffmpeg, Unity, Recorder, SDK) и подсказать установку
  init        создать структуру и config.json
  check-texts проверить listing.json по правилам Яндекса
  shots       raw/screenshots -> out/screenshots (16:9 / 9:16, 1920px, RGB PNG)
  keyart      raw/keyart -> out/media/<lang>/icon.png, cover.png (+hero)
  video       raw/video -> out/video/horizontal.mp4, vertical.mp4 (<=28s, <=100MB)
  status      сводка готовности
  size        сводка веса WebGL-билда по yandex/out/build-assets.json
  tech-scan   статический аудит: SDK-плагин, модули, локализация, остатки старого API
  loc-extract русские строки из сцен/префабов (текст и бинарь) и кода -> yandex/loc/strings.json
  loc-missing непереведённое из Editor.log (LocYG пишет в лог) -> strings.json
  loc-build   strings.json (с переводами) -> Assets/Resources/LocYG/<lang>.json для рантайма LocYG
  loc-audit   статический аудит вывода текста: код без LocYG.T, legacy Text/TextMesh, запечённые переводы
  state       отметить шаг: state <step> <done|blocked|todo> [note]
  profile     факты о проекте (SDK, ввод, 2D/3D, жанровые сигналы, риски) -> yandex/profile.json
  reqs-check  сверить требования Яндекса (knowledge/sources.json) со снимками; --accept принять изменения
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:  # user-site пакеты (imageio-ffmpeg, fonttools) доступны и при python -I
    import site
    if site.getusersitepackages() not in sys.path:
        sys.path.append(site.getusersitepackages())
except Exception:
    pass

LIMITS = {
    "title": (1, 50),
    "seo": (50, 160),
    "about": (100, 1000),
    "short": (0, 70),
    "howToPlay": (100, 1000),
    "keywords": (0, 100),
}
REQUIRED_TEXT = ("title", "about", "howToPlay")
SHOT_LONG = 1920
SHOT_SHORT = 1080
MAX_VIDEO_SEC = 28
MAX_VIDEO_MB = 100
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
VID_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

DEFAULT_CONFIG = {
    "gameId": None,
    "consoleUrl": None,
    "languages": ["ru", "en", "tr"],
    "platforms": ["desktop", "mobile"],
    "orientation": "landscape",
    "buildDir": "WEB",
    "titleFont": None,
    "brandColor": "#FFD23F",
    "screenshotsPerPlatform": 5,
    "notes": "",
    # область кадра без HUD/текста (доли ширины/высоты: x0, y0, x1, y1); null — весь кадр.
    # Текст на скриншотах/видео пришлось бы переводить под каждый язык — проще вырезать.
    "hudSafeRect": None,
    # где на hero-баннере держать кадр по вертикали (0 — верх, 1 — низ): лица/персонажи обычно выше центра
    "heroAnchorY": 0.15,
    # папки сторонних ассетов (кроме встроенного списка), которые не считать кодом/текстами игры
    "thirdPartyDirs": [],
    # папка с примерами промо-картинок пользователя (стиль для art-prompts.md); null — нет
    "artStyleRef": None,
    # путь к Unity.exe этой версии (ygtool doctor находит сам) — для CLI-сборки/съёмки при закрытом редакторе
    "unityPath": None,
}
STEPS = ["profile", "tech", "build", "texts", "screenshots", "keyart", "video", "console", "moderation"]

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
# Папки, которые почти всегда сторонние (SDK, пакеты стора, редакторские утилиты). Дополняется
# config.thirdPartyDirs — у каждого проекта свои паки ассетов.
VENDOR_DIRS = ("PluginYourGames", "YandexGame", "Imported Assets", "TextMesh Pro", "Modern UI Pack",
               "Polygon Arsenal", "Plugins", "Editor", "ThirdParty", "Third Party", "AssetStore",
               "Asset Store", "Demigiant", "DOTween", "Packages", "Samples", "Demo", "Demos", "Examples")


OTHER_SDK_MARKERS = ("yagames", "yandexsdk", "gamepush", "instantgames", "playgama", "gamedistribution",
                     "crazygames", "poki")
PLUGIN_YG_DOCS = "https://max-games.ru/plugin-yg/doc/"


def detect_sdk(root):
    """("PluginYG2" | "PluginYG1" | "<папка другого SDK>" | None, режим поддержки плагином)."""
    assets = Path(root) / "Assets"
    if (assets / "PluginYourGames").exists():
        return "PluginYG2", "full"
    if (assets / "YandexGame").exists():
        return "PluginYG1", "migrate"
    for d in (assets.iterdir() if assets.exists() else []):
        if d.is_dir() and any(k in d.name.lower() for k in OTHER_SDK_MARKERS):
            return d.name, "media-only"
    return None, "install"


def vendor_dirs(cfg):
    return tuple(VENDOR_DIRS) + tuple(cfg.get("thirdPartyDirs") or ())


def load_json(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def yroot(project):
    return Path(project) / "yandex"


def config(project):
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(yroot(project) / "config.json", {}) or {})
    return cfg


def ffmpeg_exe():
    from shutil import which
    exe = which("ffmpeg")
    if exe:
        return exe
    try:
        import site
        if site.getusersitepackages() not in sys.path:
            sys.path.append(site.getusersitepackages())
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg не найден: установите `python -m pip install --user imageio-ffmpeg` или ffmpeg в PATH")


# ---------------------------------------------------------------- init / state

def cmd_init(a):
    y = yroot(a.project)
    for d in ["raw/screenshots/desktop", "raw/screenshots/mobile", "raw/keyart", "raw/video",
              "out/screenshots", "out/media", "out/video"]:
        (y / d).mkdir(parents=True, exist_ok=True)
    cfg_path = y / "config.json"
    if not cfg_path.exists():
        cfg = dict(DEFAULT_CONFIG)
        if a.game_id:
            cfg["gameId"] = a.game_id
            cfg["consoleUrl"] = f"https://games.yandex.ru/console/application/{a.game_id}"
        save_json(cfg_path, cfg)
        print(f"создан {cfg_path}")
    if not (y / "state.json").exists():
        save_json(y / "state.json", {s: {"status": "todo"} for s in STEPS})
    gi = y / ".gitignore"
    if not gi.exists():
        gi.write_text("raw/video/\nout/video/\n", encoding="utf-8")
    print(f"структура готова: {y}")


def cmd_state(a):
    p = yroot(a.project) / "state.json"
    st = load_json(p, {s: {"status": "todo"} for s in STEPS})
    st[a.step] = {"status": a.status, "note": a.note or "",
                  "at": datetime.datetime.now().isoformat(timespec="seconds")}
    save_json(p, st)
    print(f"{a.step}: {a.status}")


# ---------------------------------------------------------------- texts

def check_lang(lang, t, problems):
    def bad(msg):
        problems.append(f"[{lang}] {msg}")

    for k in REQUIRED_TEXT:
        if not (t.get(k) or "").strip():
            bad(f"{k}: пусто (обязательное поле)")
    for k, (lo, hi) in LIMITS.items():
        v = (t.get(k) or "").strip()
        if not v:
            continue
        n = len(v)
        if n > hi or (n < lo):
            bad(f"{k}: {n} симв., нужно {lo}–{hi}")
        if re.search(r"(.)\1{3,}", v) or "  " in v:
            bad(f"{k}: повторяющиеся символы/двойные пробелы (п. 5.11)")
    title = (t.get("title") or "").strip()
    if title:
        if not title[0].isupper() and title[0].isalpha():
            bad("title: должно начинаться с заглавной")
        letters = [c for c in title if c.isalpha()]
        if len(letters) > 3 and all(c.isupper() for c in letters):
            bad("title: целиком КАПСОМ нельзя")
        if re.search(r"\b(игра|game)\b", title, re.I):
            print(f"[{lang}] предупреждение: «игра»/«game» в названии — п. 6.5 рекомендует без них")
    short = (t.get("short") or "").strip()
    if short:
        if short[0].isalpha() and not short[0].isupper():
            bad("short: должно начинаться с заглавной")
        if title and short.lower().strip(" .!") == title.lower():
            bad("short: не должно повторять название")
        if re.search(r"бесплатн|на русском языке", short, re.I):
            bad("short: запрещены «бесплатная», «на русском языке»")
        if short[0] in "\"«'“" and short[-1] in "\"»'”":
            bad("short: нельзя оборачивать в кавычки")
    kw = (t.get("keywords") or "").strip()
    if kw and kw != kw.lower():
        bad("keywords: только строчные")
    fields = [(k, (t.get(k) or "").strip().lower()) for k in ("seo", "about", "short", "howToPlay")]
    for i, (k1, v1) in enumerate(fields):
        for k2, v2 in fields[i + 1:]:
            if v1 and v2 and (v1 == v2 or (len(v1) > 40 and v1 in v2)):
                bad(f"{k1} дублирует {k2} (п. 5.11)")


def cmd_check_texts(a):
    cfg = config(a.project)
    path = yroot(a.project) / "listing.json"
    data = load_json(path)
    if data is None:
        sys.exit(f"нет {path}")
    problems = []
    langs = data.get("langs", {})
    for lang in cfg["languages"]:
        if lang not in langs:
            problems.append(f"[{lang}] язык из config.json отсутствует в listing.json")
    for lang, t in langs.items():
        check_lang(lang, t, problems)
    cats = data.get("categories", [])
    if not cats or len(cats) > 2:
        problems.append(f"categories: нужно 1–2, сейчас {len(cats)}")
    if len(data.get("tags", [])) > 20:
        problems.append("tags: больше 20")
    if not data.get("ageRating"):
        problems.append("ageRating: не задан")
    for lang, t in langs.items():
        print(f"{lang}: " + ", ".join(f"{k}={len((t.get(k) or '').strip())}" for k in LIMITS))
    if problems:
        print("\nПРОБЛЕМЫ:")
        print("\n".join(" - " + p for p in problems))
        sys.exit(1)
    print("\nOK: тексты проходят проверки")


# ---------------------------------------------------------------- images

def fit_crop(img, w, h, anchor_y=0.5):
    """Обрезка по центру до пропорции w:h и ресайз (без полей — поля запрещены п. 5.9)."""
    from PIL import Image
    sw, sh = img.size
    target = w / h
    if sw / sh > target:
        nw = round(sh * target)
        x = (sw - nw) // 2
        img = img.crop((x, 0, x + nw, sh))
    else:
        nh = round(sw / target)
        y = int((sh - nh) * anchor_y)
        img = img.crop((0, y, sw, y + nh))
    return img.resize((w, h), Image.LANCZOS), 1 - (img.size[0] * img.size[1]) / (sw * sh)


def to_rgb(img):
    from PIL import Image
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def cmd_shots(a):
    from PIL import Image
    cfg = config(a.project)
    y = yroot(a.project)
    ok = True
    for platform in cfg["platforms"]:
        if platform == "tv":
            continue
        src = y / "raw/screenshots" / platform
        dst = y / "out/screenshots" / platform
        dst.mkdir(parents=True, exist_ok=True)
        for old in dst.glob("*.png"):
            old.unlink()
        portrait = platform == "mobile" and cfg["orientation"] == "portrait"
        w, h = (SHOT_SHORT, SHOT_LONG) if portrait else (SHOT_LONG, SHOT_SHORT)
        files = sorted(p for p in src.glob("*") if p.suffix.lower() in IMG_EXT) if src.exists() else []
        if platform == "mobile" and not files:
            # мобильные альбомные можно взять из desktop
            files = sorted(p for p in (y / "raw/screenshots/desktop").glob("*") if p.suffix.lower() in IMG_EXT)
        for i, f in enumerate(files[: cfg["screenshotsPerPlatform"]], 1):
            # clean_* сняты без UI (Hide UI in Captures) — обрезать HUD не нужно
            img = to_rgb(Image.open(f)) if "clean" in f.name else safe_crop(to_rgb(Image.open(f)), cfg.get("hudSafeRect"))
            out, lost = fit_crop(img, w, h)
            o = dst / f"{i:02d}.png"
            out.save(o, optimize=True)
            warn = f"  ⚠ обрезано {lost:.0%}" if lost > 0.15 else ""
            print(f"{platform}: {f.name} -> {o.name} {w}x{h}{warn}")
        n = min(len(files), cfg["screenshotsPerPlatform"])
        if n < 2:
            print(f"{platform}: ⚠ нужно минимум 2 скриншота, есть {n}")
            ok = False
    sys.exit(0 if ok else 1)


def find_font(cfg, project):
    cands = [cfg.get("titleFont")] if cfg.get("titleFont") else []
    cands += [str(p) for p in Path(project, "Assets").rglob("*.ttf")
              if p.stem.lower() in ("bangers", "anton", "oswald-bold")][:3]
    cands += ["C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
              "/System/Library/Fonts/Supplemental/Impact.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    return [c for c in cands if c and Path(c).exists()]


def font_supports(font_path, text):
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        # без fontTools доверяем только системным шрифтам с кириллицей/латиницей-расширенной
        return Path(font_path).stem.lower() in ("impact", "arialbd", "segoeuib", "dejavusans-bold")
    cmap = TTFont(font_path).getBestCmap()
    return all(ord(c) in cmap for c in text if not c.isspace())


def draw_title(img, text, fonts, color, box, max_lines=2):
    """Вписывает название в box (x0,y0,x1,y1) с обводкой; гарантирует, что текст не обрезан."""
    from PIL import ImageDraw, ImageFont
    font_path = next((f for f in fonts if font_supports(f, text)), fonts[0])
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    words = text.split()
    for size in range(int(bh * 0.9), 10, -2):
        font = ImageFont.truetype(font_path, size)
        stroke = max(2, size // 12)
        for n in range(1, max_lines + 1):
            lines = split_lines(words, n)
            sizes = [d.textbbox((0, 0), l, font=font, stroke_width=stroke) for l in lines]
            tw = max(s[2] - s[0] for s in sizes)
            lh = max(s[3] - s[1] for s in sizes)
            th = lh * len(lines) + (len(lines) - 1) * size // 8
            if tw <= bw and th <= bh:
                y = y0 + (bh - th) // 2
                for l, s in zip(lines, sizes):
                    lw = s[2] - s[0]
                    d.text((x0 + (bw - lw) // 2 - s[0], y - s[1]), l, font=font, fill=color,
                           stroke_width=stroke, stroke_fill=(20, 20, 20))
                    y += lh + size // 8
                return font_path, size
    raise SystemExit(f"название «{text}» не помещается в {box}")


def split_lines(words, n):
    if n == 1 or len(words) < 2:
        return [" ".join(words)]
    best, best_w = None, 1e9
    for cut in range(1, len(words)):
        a, b = " ".join(words[:cut]), " ".join(words[cut:])
        w = max(len(a), len(b))
        if w < best_w:
            best, best_w = [a, b], w
    return best


def pick(src_dir, *names):
    for n in names:
        for ext in (".png", ".jpg", ".jpeg"):
            p = src_dir / f"{n}{ext}"
            if p.exists():
                return p
    return None


def cmd_keyart(a):
    from PIL import Image
    cfg = config(a.project)
    y = yroot(a.project)
    src = y / "raw/keyart"
    listing = load_json(y / "listing.json", {}) or {}
    fonts = find_font(cfg, a.project)
    if not fonts:
        sys.exit("не найден ни один шрифт для названия; задайте titleFont в config.json")
    icon_src = pick(src, "icon", "square", "keyart_square")
    cover_src = pick(src, "cover", "wide", "keyart_wide")
    hero_src = pick(src, "hero", "ultrawide", "keyart_ultrawide") or cover_src
    if not icon_src and not cover_src:
        sys.exit(f"нет исходников в {src}: нужен icon.png (квадрат) и/или cover.png (широкий). "
                 "Сделайте рендер через Unity: меню Yandex Games/Render Key Art")
    color = cfg["brandColor"]
    langs = listing.get("langs", {}) or {l: {} for l in cfg["languages"]}
    # один шрифт на все языки, чтобы обложки выглядели единообразно
    titles = " ".join((t.get("title") or "") for t in langs.values())
    fonts = [f for f in fonts if font_supports(f, titles)] or fonts
    for lang in langs:
        title = "" if a.no_title else (langs[lang].get("title") or "").strip()
        out = y / "out/media" / lang
        out.mkdir(parents=True, exist_ok=True)
        if icon_src:
            icon, _ = fit_crop(to_rgb(Image.open(icon_src)), 512, 512)
            if a.icon_title and title:
                draw_title(icon, title, fonts, color, (36, 330, 476, 486), max_lines=2)
            icon.save(out / "icon.png", optimize=True)
        if cover_src:
            cover, _ = fit_crop(to_rgb(Image.open(cover_src)), 800, 470)
            if title:
                fp, sz = draw_title(cover, title, fonts, color, (40, 300, 760, 450), max_lines=2)
                print(f"{lang}: обложка, шрифт {Path(fp).name} {sz}px")
            cover.save(out / "cover.png", optimize=True)
        if hero_src:
            hero, _ = fit_crop(to_rgb(Image.open(hero_src)), 1560, 520, anchor_y=cfg["heroAnchorY"])
            if title:
                draw_title(hero, title, fonts, color, (80, 330, 1480, 490), max_lines=1)
            hero.save(out / "hero.png", optimize=True)
        print(f"{lang}: -> {out}")


# ---------------------------------------------------------------- video

def probe(ff, path):
    r = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr)
    w, h = (int(m[1]), int(m[2])) if m else (0, 0)
    return dur, w, h, "Audio:" in r.stderr


def safe_crop(img, rect):
    if not rect:
        return img
    x0, y0, x1, y1 = rect
    W, H = img.size
    return img.crop((round(W * x0), round(H * y0), round(W * x1), round(H * y1)))


def encode(ff, src, dst, start, dur, w, h, has_audio, fade=True, rect=None):
    vf = ""
    if rect:
        x0, y0, x1, y1 = rect
        vf = f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},"
    vf += f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1"
    if fade:
        vf += f",fade=t=in:st=0:d=0.4,fade=t=out:st={max(dur - 0.5, 0):.2f}:d=0.5"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start}", "-t", f"{dur}",
           "-i", str(src), "-vf", vf, "-r", "30", "-c:v", "libx264", "-preset", "slow",
           "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd += ["-c:a", "aac", "-b:a", "128k", "-af", f"afade=t=out:st={max(dur - 0.5, 0):.2f}:d=0.5"] \
        if has_audio else ["-an"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True)
    mb = dst.stat().st_size / 1e6
    if mb > MAX_VIDEO_MB:
        sys.exit(f"{dst.name}: {mb:.1f} МБ > {MAX_VIDEO_MB} МБ")
    print(f"{dst.name}: {w}x{h}, {dur:.1f}s, {mb:.1f} МБ")


def cmd_video(a):
    ff = ffmpeg_exe()
    y = yroot(a.project)
    raw = y / "raw/video"
    out = y / "out/video"
    out.mkdir(parents=True, exist_ok=True)
    vids = sorted((p for p in raw.glob("*") if p.suffix.lower() in VID_EXT),
                  key=lambda p: p.stat().st_mtime) if raw.exists() else []
    if a.src:
        vids = [Path(a.src)]
    if not vids:
        sys.exit(f"нет видео в {raw}. Запишите геймплей: Unity меню Yandex Games/Record Gameplay")
    horiz_src = next((v for v in vids if "vert" not in v.stem.lower()), vids[-1])
    vert_src = next((v for v in vids if "vert" in v.stem.lower()), horiz_src)
    if a.segments:
        # монтаж лучших отрезков "2-6,194-209,14-22" в один промежуточный файл (имя сохраняет метку clean)
        segs = [tuple(float(x) for x in s.split("-")) for s in a.segments.split(",")]
        total = sum(e - s for s, e in segs)
        if total > MAX_VIDEO_SEC - 0.5:
            sys.exit(f"сумма отрезков {total:.1f}s > {MAX_VIDEO_SEC - 0.5}s")
        _, _, _, has_a = probe(ff, horiz_src)
        parts, labels = [], ""
        for i, (s, e) in enumerate(segs):
            parts.append(f"[0:v]trim={s}:{e},setpts=PTS-STARTPTS[v{i}]")
            labels += f"[v{i}]"
            if has_a:
                parts.append(f"[0:a]atrim={s}:{e},asetpts=PTS-STARTPTS[a{i}]")
                labels += f"[a{i}]"
        parts.append(f"{labels}concat=n={len(segs)}:v=1:a={1 if has_a else 0}[v]" + ("[a]" if has_a else ""))
        montage = raw / f"_montage_{horiz_src.stem}.mp4"
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(horiz_src),
               "-filter_complex", ";".join(parts), "-map", "[v]"] + (["-map", "[a]"] if has_a else []) + \
              ["-c:v", "libx264", "-crf", "14", "-preset", "fast", "-c:a", "aac", "-b:a", "192k", str(montage)]
        subprocess.run(cmd, check=True)
        print(f"монтаж: {', '.join(f'{s:g}-{e:g}' for s, e in segs)} = {total:.1f}s")
        horiz_src = vert_src = montage
        a.start, a.duration = 0, total
    dur_all, sw, sh, aud = probe(ff, horiz_src)
    dur = min(a.duration, MAX_VIDEO_SEC - 0.5, max(dur_all - a.start, 0))
    if dur < 5:
        sys.exit(f"{horiz_src.name}: слишком короткое ({dur_all:.1f}s) для старта {a.start}s")
    print(f"источник {horiz_src.name}: {sw}x{sh}, {dur_all:.1f}s")
    rect = None if "clean" in horiz_src.name else config(a.project).get("hudSafeRect")
    encode(ff, horiz_src, out / "horizontal.mp4", a.start, dur, 1920, 1080, aud, rect=rect)
    vd, vw, vh, vaud = probe(ff, vert_src)
    vstart = a.start if vert_src == horiz_src else 0
    vdur = min(dur, max(vd - vstart, 0))
    if vert_src == horiz_src and sw > sh:
        print("⚠ вертикальное видео вырезается из центра горизонтального — проверьте, что действие в центре кадра")
    encode(ff, vert_src, out / "vertical.mp4", vstart, vdur, 1080, 1920, vaud,
           rect=rect if vert_src == horiz_src else None)


# ---------------------------------------------------------------- status

def cmd_status(a):
    from PIL import Image
    cfg = config(a.project)
    y = yroot(a.project)
    st = load_json(y / "state.json", {}) or {}
    print(f"Проект: {Path(a.project).resolve().name}  id={cfg.get('gameId')}  языки={','.join(cfg['languages'])}")
    idx = load_json(PLUGIN_ROOT / "knowledge" / "snapshots" / "index.json", {}) or {}
    checked = [e["checked"] for e in idx.values() if e.get("checked")]
    pending = [k for k, e in idx.items() if e.get("pendingSha")]
    print(f"Требования Яндекса сверены: {min(checked) if checked else 'никогда'}"
          f"{'  ⚠ непринятые изменения: ' + ', '.join(pending) if pending else ''}")
    g = (load_json(y / "profile.json", {}) or {}).get("game", {})
    print(f"Профиль игры: {g.get('genre') or 'не заполнен'}{' / ' + g['camera'] if g.get('camera') else ''}")
    print("\nШаги:")
    for s in STEPS:
        e = st.get(s, {"status": "todo"})
        print(f"  {s:12} {e.get('status'):8} {e.get('note', '')}")
    print("\nФайлы:")
    listing = y / "listing.json"
    print(f"  listing.json            {'есть' if listing.exists() else 'НЕТ'}")
    for platform in cfg["platforms"]:
        if platform == "tv":
            continue
        shots = list((y / "out/screenshots" / platform).glob("*.png"))
        sizes = {Image.open(p).size for p in shots}
        print(f"  скриншоты {platform:8}    {len(shots)} шт {sorted(sizes)}")
    for lang in cfg["languages"]:
        m = y / "out/media" / lang
        have = [n for n in ("icon.png", "cover.png", "hero.png") if (m / n).exists()]
        print(f"  медиа {lang:3}               {', '.join(have) or 'НЕТ'}")
    for v in ("horizontal.mp4", "vertical.mp4"):
        p = y / "out/video" / v
        print(f"  {v:22}  {f'{p.stat().st_size / 1e6:.1f} МБ' if p.exists() else 'НЕТ'}")
    build = Path(a.project) / cfg["buildDir"]
    if build.exists():
        total = sum(f.stat().st_size for f in build.rglob("*") if f.is_file())
        bad = [str(f.relative_to(build)) for f in build.rglob("*")
               if re.search(r"[\sа-яА-ЯёЁ]", f.name)]
        print(f"  билд {cfg['buildDir']}: {total / 1e6:.1f} МБ распакован"
              f"{'  ⚠ >100 МБ' if total > 100e6 else ''}"
              f"{'  index.html OK' if (build / 'index.html').exists() else '  ⚠ нет index.html в корне'}")
        if bad:
            print(f"  ⚠ пробелы/кириллица в именах: {bad[:5]}")


# ---------------------------------------------------------------- tech-scan

OLD_YG_API = re.compile(r"\bYandexGame\.(\w+)")
CYR_LITERAL = re.compile(r'"[^"\n]*[А-Яа-яЁё][^"\n]*"')


def cmd_tech_scan(a):
    """Быстрый статический аудит Unity-проекта: SDK-плагин, модули, локализация, остатки старого API."""
    root = Path(a.project)
    assets = root / "Assets"
    issues = []
    yg2 = assets / "PluginYourGames"
    yg1 = assets / "YandexGame"
    cfg = config(a.project)
    other_langs = [l for l in cfg["languages"] if l != "ru"]
    locyg_ok = bool(list(assets.rglob("LocYG.cs"))) and all(
        (assets / "Resources" / "LocYG" / f"{l}.json").exists() for l in other_langs)
    translated = set()
    if locyg_ok:
        for e in (load_json(yroot(a.project) / "loc" / "strings.json", {}) or {}).get("strings", []):
            if e.get("skip") or all(e.get(l) for l in other_langs):
                translated.add(e["ru"])
        print(f"Локализация: LocYG, языки {other_langs}, переведено строк: {len(translated)}")
    if yg2.exists():
        ver = (yg2 / "Version.txt").read_text(encoding="utf-8", errors="replace").strip() \
            if (yg2 / "Version.txt").exists() else "?"
        mods = sorted(p.name for p in (yg2 / "Modules").iterdir() if p.is_dir()) \
            if (yg2 / "Modules").exists() else []
        print(f"SDK: PluginYG2 {ver}; модули: {', '.join(mods) or '—'}")
        if "Localization" not in mods and not locyg_ok:
            issues.append("PluginYG2: нет модуля Localization (и нет LocYG) — язык не переключается через SDK (2.14)")
        if locyg_ok and "EnvirData" not in mods:
            issues.append("LocYG берёт язык из YG2.envir — нужен модуль PluginYG2 EnvirData")
        if yg1.exists():
            issues.append("одновременно лежат Assets/YandexGame (PluginYG1) и PluginYourGames — удалить старый")
    elif yg1.exists():
        print("SDK: PluginYG (v1, Assets/YandexGame) — устаревший, рекомендуется миграция на PluginYG2")
    else:
        other, _ = detect_sdk(root)
        if other:
            print(f"SDK: {other} — не PluginYG2: проверки ниже частичные, тех. требования сверять вручную "
                  "(GameReady, пауза/звук на рекламе, язык из SDK, сохранения)")
        else:
            issues.append(f"Yandex SDK не подключён — без него модерация не пройдёт. Рекомендуется PluginYG2 "
                          f"(бесплатный): {PLUGIN_YG_DOCS}")

    skip = vendor_dirs(cfg)
    own = [p for p in assets.rglob("*.cs") if not any(part in p.parts for part in skip)]
    old_api, cyr = {}, []
    for p in own:
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if yg2.exists():
            for m in OLD_YG_API.finditer(src):
                old_api.setdefault(str(p.relative_to(root)), set()).add(m[1])
        for i, line in enumerate(src.splitlines(), 1):
            s = line.strip()
            if s.startswith("//") or "Debug.Log" in s:
                continue
            if CYR_LITERAL.search(s):
                if locyg_ok and all(t in translated for t in _code_strings(s)):
                    continue
                cyr.append(f"{p.relative_to(root)}:{i}: {s[:90]}")
    if old_api:
        issues.append("остатки API PluginYG1 (YandexGame.*): " +
                      "; ".join(f"{f} [{', '.join(sorted(v))}]" for f, v in list(old_api.items())[:8]))
    if cyr:
        issues.append(f"русские строки в коде без перевода: {len(cyr)}" +
                      (" (запустите loc-extract и переведите)" if locyg_ok else " — без локализации игра только на ru"))
        for c in cyr[:12]:
            print("   " + c)

    if other_langs and cyr and not locyg_ok and not (yg2 / "Modules" / "Localization").exists():
        issues.append(f"config.languages={cfg['languages']}, но локализации нет: модерация проверяет "
                      "КАЖДЫЙ заявленный язык — либо локализовать, либо оставить только ru")
    if issues:
        print("\nПРОБЛЕМЫ:")
        print("\n".join(" - " + i for i in issues))
        sys.exit(1)
    print("OK")


# ---------------------------------------------------------------- size

def cmd_size(a):
    """Сводка yandex/out/build-assets.json: типы, папки, топ ассетов, сравнение с .prev.json."""
    y = yroot(a.project)
    data = load_json(y / "out" / "build-assets.json")
    if not data:
        sys.exit("нет yandex/out/build-assets.json — соберите билд: Unity → Yandex Games → Build WebGL + ZIP")
    assets = data["assets"]
    mb = lambda b: f"{b / 1048576:7.2f} МБ"
    print(f"Распаковано: {mb(data['unpackedBytes'])}  (лимит 100 МБ); ассетов в отчёте: {len(assets)}")
    by_type, by_dir = {}, {}
    for e in assets:
        by_type[e["type"]] = by_type.get(e["type"], 0) + e["bytes"]
        d = "/".join(e["path"].split("/")[:3])
        by_dir[d] = by_dir.get(d, 0) + e["bytes"]
    print("\nПо типам:")
    for k, v in sorted(by_type.items(), key=lambda x: -x[1])[:10]:
        print(f"  {mb(v)}  {k}")
    print("\nПо папкам:")
    for k, v in sorted(by_dir.items(), key=lambda x: -x[1])[:15]:
        print(f"  {mb(v)}  {k}")
    print(f"\nТоп-{a.top} ассетов:")
    for e in assets[:a.top]:
        print(f"  {mb(e['bytes'])}  {e['type']:14} {e['path']}")
    prev = load_json(y / "out" / "build-assets.prev.json")
    if prev:
        d = data["unpackedBytes"] - prev["unpackedBytes"]
        print(f"\nПо сравнению с прошлым отчётом: {'+' if d >= 0 else ''}{d / 1048576:.2f} МБ")


# ---------------------------------------------------------------- localization

LOC_SKIP_DIRS = VENDOR_DIRS
CYR = re.compile("[А-Яа-яЁё]")


def _binary_strings(b):
    """UTF-8 строки с кириллицей из бинарно сериализованного Unity-ассета (int32 длина + байты)."""
    import struct
    lead = re.compile(rb"[\xd0\xd1][\x80-\xbf]")
    pos, out = 0, []
    while True:
        m = lead.search(b, pos)
        if not m:
            return out
        s, hit = m.start(), None
        for start in range(s, max(s - 4000, 4) - 1, -1):
            n = struct.unpack_from("<i", b, start - 4)[0]
            if 0 < n < 8000 and start <= s < start + n <= len(b):
                try:
                    t = b[start:start + n].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if CYR.search(t) and "\x00" not in t:
                    hit = (t, start + n)
                    break
        if hit:
            out.append(hit[0])
            pos = hit[1]
        else:
            pos = s + 2


def _text_strings(src):
    out = []
    for m in re.finditer(r'^\s*m_text: (.*)$', src, re.M):
        v = m[1].strip()
        if v.startswith('"'):
            v = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda x: chr(int(x[1], 16)), v.strip('"'))
            v = v.replace("\\n", "\n").replace('\\"', '"')
        if CYR.search(v):
            out.append(v)
    return out


def _code_strings(src):
    """Кириллические литералы C#; $"...{expr}..." → шаблон с {0},{1}."""
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("//") or "Debug.Log" in s:
            continue
        for m in re.finditer(r'(\$?)"((?:[^"\\]|\\.)*)"', s):
            t = m[2]
            if not CYR.search(t):
                continue
            if m[1]:
                i = iter(range(100))
                t = re.sub(r"\{[^{}]+\}", lambda _: "{%d}" % next(i), t)
            out.append(t.replace('\\"', '"').replace("\\n", "\n"))
    return out


def cmd_loc_extract(a):
    """Собирает все русские строки проекта в yandex/loc/strings.json (сохраняя готовые переводы)."""
    root = Path(a.project)
    assets = root / "Assets"
    found = {}

    def add(t, where):
        t = t.strip()
        if t and CYR.search(t):
            found.setdefault(t, set()).add(where)

    for f in assets.rglob("*"):
        if f.suffix not in (".unity", ".prefab", ".asset", ".cs") or any(p in f.parts for p in LOC_SKIP_DIRS):
            continue
        if f.suffix == ".asset" and (f.stat().st_size > 2_000_000 or
                                     re.search(r"LightingData|NavMesh|Terrain|AddressableAssetsData|Fonts?/|SDF",
                                               f.as_posix())):
            continue
        rel = f.relative_to(root).as_posix()
        if f.suffix == ".cs":
            for t in _code_strings(f.read_text(encoding="utf-8", errors="replace")):
                add(t, rel)
            continue
        b = f.read_bytes()
        strings = _text_strings(b.decode("utf-8", "replace")) if b.startswith(b"%YAML") else _binary_strings(b)
        for t in strings:
            add(t, rel)

    path = yroot(a.project) / "loc" / "strings.json"
    old = {e["ru"]: e for e in (load_json(path, {}) or {}).get("strings", [])}
    langs = [l for l in config(a.project)["languages"] if l != "ru"]
    entries = []
    for t in sorted(found):
        e = old.get(t, {"ru": t})
        e.pop("notFound", None)
        e["files"] = sorted(found[t])[:5]
        e["pattern"] = bool(re.search(r"\{\d+\}", t))
        for l in langs:
            e.setdefault(l, "")
        entries.append(e)
    # Старые записи, которых больше нет в ассетах, НЕ выбрасываем: это строки из loc-missing (runtime),
    # а также тексты, которые кто-то уже заменил в сцене переводом (тогда нужен обратный перевод en→ru).
    gone = [e for t, e in old.items() if t not in found]
    for e in gone:
        if "(runtime)" not in e.get("files", []):
            e["notFound"] = True
        entries.append(e)
    save_json(path, {"languages": ["ru"] + langs, "strings": entries})
    todo = sum(1 for e in entries for l in langs if not e.get(l) and not e.get("skip"))
    print(f"{len(entries)} строк -> {path}; без перевода: {todo}")
    stale = [e for e in gone if e.get("notFound") and not e.get("skip")]
    if stale:
        print(f"\nне найдено в ассетах, но оставлено ({len(stale)}): текст удалён, собирается в коде или "
              "уже заменён переводом прямо в сцене (см. loc-audit):")
        for e in stale[:20]:
            print(f"   {e['ru'][:60]}  <- {', '.join(e.get('files', []))[:80]}")


def editor_log_path():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Unity" / "Editor" / "Editor.log"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Unity" / "Editor.log"
    return Path.home() / ".config" / "unity3d" / "Editor.log"


def cmd_loc_missing(a):
    """Собирает «[LocYG] нет перевода: ...» из Editor.log в yandex/loc/strings.json (пустые переводы)."""
    log = Path(a.log) if a.log else editor_log_path()
    if not log.exists():
        sys.exit(f"нет лога {log}")
    found = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"\[LocYG\] нет перевода: (.*)$", line)
        if m:
            t = m[1].replace("\n", "\n").replace("\\\\", "\\").strip()
            if t not in found:
                found.append(t)
    path = yroot(a.project) / "loc" / "strings.json"
    data = load_json(path, {"languages": config(a.project)["languages"], "strings": []})
    have = {e["ru"] for e in data["strings"]}
    langs = [l for l in data["languages"] if l != "ru"]
    added = 0
    for t in found:
        if t in have:
            continue
        e = {"ru": t, "files": ["(runtime)"], "pattern": False}
        for l in langs:
            e[l] = ""
        data["strings"].append(e)
        added += 1
        print(" + " + t.replace("\n", "\n")[:110])
    save_json(path, data)
    print(f"в логе: {len(found)}, новых: {added} -> {path}")
    if found and not added:
        print("все уже есть в словаре — значит текст в игре отличается (пробелы/переносы) или собирается из частей;"
              " сравните строку из лога с записью в strings.json или замените в коде на LocYG.T(...)")


def cmd_loc_build(a):
    """yandex/loc/strings.json → Assets/Resources/LocYG/<lang>.json для рантайма LocYG."""
    path = yroot(a.project) / "loc" / "strings.json"
    data = load_json(path)
    if not data:
        sys.exit(f"нет {path} — сначала loc-extract")
    out_dir = Path(a.project) / "Assets" / "Resources" / "LocYG"
    missing = []
    live = [e for e in data["strings"] if not e.get("skip")]

    def reverse_pairs(lang, patterns=False):
        """Обратные пары «английский текст → <lang>» для ВСЕХ строк: если текст в сцене/открытой в редакторе
        версии оказался английским (вручную, случайно сохранён, не перезагружен после git), игрок на своём
        языке всё равно увидит свой язык. Английский словарь этим не затрагивается."""
        out, have = [], set()
        for e in live:
            if bool(e.get("pattern")) != patterns:
                continue
            en = (e.get("en") or "").strip()
            res = (e.get("ruOverride") or e["ru"]) if lang == "ru" else (e.get(lang) or "").strip()
            if not en or not res or en == res or en in have or CYR.search(en):
                continue
            have.add(en)
            out.append({"ru": en, "tr": res})
        return out

    for lang in data["languages"]:
        if lang == "ru":
            continue
        pairs, patterns = [], []
        for e in live:
            tr = (e.get(lang) or "").strip()
            if not tr:
                missing.append(f"{lang}: {e['ru'][:60]}")
                continue
            if CYR.search(tr):
                missing.append(f"{lang}: перевод содержит кириллицу: {tr[:60]}")
            (patterns if e.get("pattern") else pairs).append({"ru": e["ru"], "tr": tr})
        # название игры из listing.json — всегда в словаре (п. 5.1.3: в игре = в черновике на каждом языке)
        langs_listing = (load_json(yroot(a.project) / "listing.json", {}) or {}).get("langs", {})
        ru_title = (langs_listing.get("ru", {}).get("title") or "").strip()
        tr_title = (langs_listing.get(lang, {}).get("title") or "").strip()
        if ru_title and tr_title and not any(p["ru"] == ru_title for p in pairs):
            pairs.append({"ru": ru_title, "tr": tr_title})
        rev = reverse_pairs(lang) if lang != "en" else []
        rev_p = reverse_pairs(lang, patterns=True) if lang != "en" else []
        save_json(out_dir / f"{lang}.json", {"exact": pairs + rev, "patterns": patterns + rev_p})
        print(f"{lang}: {len(pairs)} строк (+{len(rev)} en→{lang}), {len(patterns)} шаблонов -> "
              f"{out_dir / (lang + '.json')}")
    # ru.json: "ruOverride" (замена русского текста без открытия сцены, например новое название игры)
    # + обратные пары en→ru для текстов, уже заменённых в сценах на английский.
    overrides = [{"ru": e["ru"], "tr": e["ruOverride"]} for e in live if e.get("ruOverride")]
    rev = reverse_pairs("ru")
    rev_p = reverse_pairs("ru", patterns=True)
    ru_path = out_dir / "ru.json"
    if overrides or rev:
        save_json(ru_path, {"exact": overrides + rev, "patterns": rev_p})
        print(f"ru: {len(overrides)} замен русских текстов, {len(rev)} обратных en→ru, "
              f"{len(rev_p)} обратных шаблонов -> {ru_path}")
    elif ru_path.exists():
        ru_path.unlink()
    if missing:
        print("\nНЕ ПЕРЕВЕДЕНО:")
        print("\n".join(" - " + m for m in missing[:40]))
        sys.exit(1)


# ---------------------------------------------------------------- loc-audit

# GUID скриптов текстовых компонентов (одинаковы в com.unity.ugui 2.x и com.unity.textmeshpro 3.x).
TEXT_COMPONENTS = {
    "UI.Text (legacy)": "5f7201a12d95ffc409449d95f23cf332",
    "UI.InputField (legacy)": "d199490a83bb2b844b9695cbf13b01ef",
    "UI.Dropdown (legacy)": "0d0b652f32a2cc243917e4028fa0f046",
    "TextMeshProUGUI": "f4688fdb7df04437aeb418b961361dc5",
    "TextMeshPro (3D)": "9541d86e2fd84c1d9990edf0852d74ab",
    "TMP_InputField": "2da0c512f12947e489f739169773d7ca",
    "TMP_Dropdown": "7b743370ac3e4ec2a1668f5455a8ef8a",
}
LEGACY_COMPONENTS = ("UI.Text (legacy)", "UI.InputField (legacy)", "UI.Dropdown (legacy)", "TextMesh (legacy)")


def _guid_bin(h):
    """GUID из .meta так, как он лежит в бинарно сериализованном ассете (полубайты переставлены)."""
    return bytes(int(h[i + 1] + h[i], 16) for i in range(0, 32, 2))


def _has_guid(b, guid):
    return guid.encode() in b if b.startswith(b"%YAML") else _guid_bin(guid) in b


def _serialized_class_ids(b):
    """classID типов из заголовка бинарного SerializedFile (Unity 2019+). None — не удалось разобрать."""
    import struct
    try:
        version = struct.unpack_from(">I", b, 8)[0]
        if not 17 <= version <= 40:
            return None
        little = b[16] == 0
        pos = 48 if version >= 22 else 20
        e = "<" if little else ">"
        end = b.index(b"\x00", pos)
        pos = end + 1 + 4                      # unityVersion, targetPlatform
        tree = b[pos] != 0
        pos += 1
        count = struct.unpack_from(e + "i", b, pos)[0]
        pos += 4
        ids = set()
        for _ in range(count):
            cid = struct.unpack_from(e + "i", b, pos)[0]
            pos += 4 + 1 + 2                   # classID, isStripped, scriptTypeIndex
            if cid == 114:
                pos += 16                      # scriptID
            pos += 16                          # oldTypeHash
            if tree:
                n, sz = struct.unpack_from(e + "ii", b, pos)
                pos += 8 + n * (32 if version >= 19 else 24) + sz
                if version >= 21:
                    deps = struct.unpack_from(e + "i", b, pos)[0]
                    pos += 4 + 4 * deps
            ids.add(cid)
        return ids
    except (struct.error, ValueError, IndexError):
        return None


def _text_component_usage(b):
    used = [name for name, g in TEXT_COMPONENTS.items() if _has_guid(b, g)]
    if b.startswith(b"%YAML"):
        if b"--- !u!102 " in b:
            used.append("TextMesh (legacy)")
    else:
        ids = _serialized_class_ids(b)
        if ids and 102 in ids:
            used.append("TextMesh (legacy)")
    return used


def _build_scenes(root):
    p = root / "ProjectSettings" / "EditorBuildSettings.asset"
    if not p.exists():
        return []
    b = p.read_bytes()
    return [m.decode("utf-8", "replace") for m in re.findall(rb"Assets/[^\x00\n\r]+?\.unity", b)]


def cmd_loc_audit(a):
    """Статический аудит путей вывода текста: код (присваивания .text/SetText), компоненты в сценах/префабах,
    тексты, уже заменённые в сценах переводом. Печатает «файл:строка — что сделать»."""
    root = Path(a.project)
    assets = root / "Assets"
    out = {"code": [], "fields": [], "legacy-code": [], "assets": [], "baked": [], "images": []}

    # ---- код
    own = [p for p in assets.rglob("*.cs")
           if not any(part in p.parts for part in LOC_SKIP_DIRS) and p.name != "LocYG.cs"]
    assign = re.compile(r"(\.text\s*\+?=(?!=)|\.SetText\s*\()(.*)$")
    ser_field = re.compile(r"\[(?:field:\s*)?SerializeField[^\]]*\]\s*(?:\[[^\]]*\]\s*)*"
                           r"(?:public|private|protected|internal)?\s*string\s+(\w+)\s*(=\s*(\"[^\"]*\"))?")
    legacy_decl = re.compile(r"\b(?:UnityEngine\.UI\.)?(Text|TextMesh|InputField|Dropdown)\b\s+\w+\s*[;={]")
    for p in own:
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        fields = {}
        for m in ser_field.finditer(src):
            fields[m[1]] = m[3] or ""
        uses_ugui = "using UnityEngine.UI" in src or "UnityEngine.UI.Text" in src
        for i, line in enumerate(src.splitlines(), 1):
            s = line.strip()
            if s.startswith("//"):
                continue
            if uses_ugui or "TextMesh" in s:
                lm = legacy_decl.search(s)
                if lm and "TMP_" not in s and "TextMeshPro" not in s:
                    out["legacy-code"].append(f"{rel}:{i} — поле legacy {lm[1]}: LocYG переводит его опросом "
                                              f"раз в 15 кадров; при присвоении используйте LocYG.T(...) или замените на TMP")
            m = assign.search(s)
            if not m:
                continue
            expr = m[2]
            if "LocYG.T(" in expr:
                continue
            has_cyr = bool(CYR.search(expr))
            # поле с дефолтом без кириллицы ("x{0}", "{0}$") переводить не нужно
            used_fields = [f for f in fields if re.search(r"\b%s\b" % re.escape(f), expr)
                           and (not fields[f] or CYR.search(fields[f]))]
            glued = bool(re.search(r'\$"|\+\s*"|"\s*\+|string\.Format\(|\.Concat\(', expr))
            if has_cyr and glued:
                out["code"].append(f"{rel}:{i} — русский текст склеивается с данными: string.Format(LocYG.T(\"… {{0}}\"), x) "
                                   f"→ `{s[:90]}`")
            elif has_cyr:
                out["code"].append(f"{rel}:{i} — русский литерал: обернуть в LocYG.T(...) → `{s[:90]}`")
            elif used_fields and glued:
                out["fields"].append(f"{rel}:{i} — шаблон из инспектора ({', '.join(used_fields)}) в Format/склейке: "
                                     f"string.Format(LocYG.T({used_fields[0]}), …) → `{s[:90]}`")
            elif used_fields:
                out["fields"].append(f"{rel}:{i} — строка из инспектора ({', '.join(used_fields)}): LocYG.T(...) "
                                     f"(TMP подхватит и сам, legacy Text — только опросом) → `{s[:90]}`")

    # ---- сцены из Build Settings + префабы
    scenes = set(_build_scenes(root))
    for f in sorted(list(assets.rglob("*.unity")) + list(assets.rglob("*.prefab"))):
        rel = f.relative_to(root).as_posix()
        if f.suffix == ".unity" and rel not in scenes:
            continue
        if any(part in f.parts for part in ("PluginYourGames", "YandexGame", "TextMesh Pro")):
            continue
        b = f.read_bytes()
        used = _text_component_usage(b)
        legacy = [u for u in used if u in LEGACY_COMPONENTS]
        if legacy:
            out["assets"].append(f"{rel} — {', '.join(legacy)}: LocYG переводит опросом; если текст ставит код — "
                                 "LocYG.T(...) в коде")
        elif a.verbose and used:
            out["assets"].append(f"{rel} — {', '.join(used)}")

    # ---- тексты, уже заменённые в сценах переводом (английский «запечён» в сцену)
    data = load_json(yroot(a.project) / "loc" / "strings.json", {}) or {}
    stale = [e for e in data.get("strings", []) if e.get("notFound") and (e.get("en") or "").strip()]
    if stale:
        import struct
        blobs = {}
        for e in stale:
            for fr in e.get("files", []):
                fp = root / fr
                if fr.startswith("Assets/") and fp.exists() and fr not in blobs:
                    blobs[fr] = fp.read_bytes()
            en = e["en"].strip().encode()
            for fr, b in blobs.items():
                if fr in e.get("files", []) and (struct.pack("<i", len(en)) + en in b or b"m_text: " + en in b):
                    out["baked"].append(f"{fr} — «{e['ru'][:40]}» уже заменён в ассете на «{e['en'][:40]}»: "
                                        "ru-версия вернёт русский через обратную пару ru.json (loc-build); "
                                        "лучше вернуть русский текст в сцене")
                    break

    # ---- кандидаты «текст на картинке»
    kw = re.compile(r"(button|btn|title|logo|label|banner|text|txt|кноп|надпис|play|buy|shop)", re.I)
    for meta in assets.rglob("*.png.meta"):
        if any(part in meta.parts for part in LOC_SKIP_DIRS):
            continue
        name = meta.name[:-9]
        if kw.search(name):
            t = meta.read_text(encoding="utf-8", errors="replace")
            if re.search(r"spriteMode: [12]", t):
                out["images"].append(f"{meta.relative_to(root).as_posix()[:-5]} — спрайт с «текстовым» именем: "
                                     "проверьте глазами, нет ли надписи (её не перевести словарём)")

    titles = {
        "code": "Код: русский текст в .text/SetText без LocYG.T",
        "fields": "Код: строки/шаблоны из инспектора ([SerializeField] string) без LocYG.T",
        "legacy-code": "Код: legacy-компоненты текста",
        "assets": "Сцены (Build Settings) и префабы: legacy-компоненты текста",
        "baked": "Сцены: текст уже заменён переводом (пропал из loc-extract)",
        "images": "Картинки: возможный текст на спрайтах",
    }
    total = 0
    for k, title in titles.items():
        if not out[k]:
            continue
        print(f"\n{title} ({len(out[k])}):")
        for line in out[k][:a.top]:
            print("  " + line)
        if len(out[k]) > a.top:
            print(f"  … ещё {len(out[k]) - a.top}")
        total += len(out[k])
    if not total:
        print("loc-audit: замечаний нет")
    else:
        print(f"\nВсего замечаний: {total}. Не забудьте: loc-extract → перевести → loc-build; "
              "в редакторе на языке en пройти игру и выполнить loc-missing.")


# ---------------------------------------------------------------- profile

# Сигналы жанра: слово в именах классов/методов/сцен/папок игры -> жанр. Это подсказка агенту, не вердикт.
GENRE_SIGNALS = {
    "shooter": r"shoot|weapon|bullet|ammo|\bgun|rifle|pistol|reload|recoil|sniper|grenade",
    "racing": r"\bcar\b|wheel|drift|\blap\b|nitro|\brace\b|speedometer|vehicle",
    "puzzle": r"puzzle|match3|match-3|\bgrid\b|\btile\b|sokoban|jigsaw|sudoku|solve",
    "merge": r"\bmerge",
    "idle/clicker": r"\bidle\b|clicker|offline.?income|tapper",
    "runner": r"runner|\blane\b|endless|obstacle",
    "platformer": r"platformer|wall.?jump|double.?jump|checkpoint",
    "tower defense": r"\btower|\bwave(s)?\b|turret",
    "rpg": r"\bquest|inventory|\bxp\b|experience|skill.?tree|\bloot",
    "simulator": r"simulator|\bfarm|tycoon|business",
    "horror": r"horror|scream|jumpscare|\bscary|flashlight",
    "sport": r"\bball\b|\bgoal\b|football|soccer|basketball|penalty",
    "fighting": r"\bcombo|punch|\bkick\b|fighter|knockout",
    "strategy": r"\bunits?\b|\barmy|base.?build|\bresources?\b|\bcastle",
    "casual/hyper": r"hypercasual|\bstack\b|tap.?to|\bswipe",
    "zombie/survival": r"zombie|surviv|hunger|\bcraft",
    "multiplayer/io": r"photon|mirror|netcode|fishnet|lobby|matchmak",
    "bots/ai": r"\bbots?\b|navmesh|enemy.?ai",
    "levels": r"\blevels?\b|\bstages?\b",
    "shop/progression": r"\bshop\b|\bupgrade|\bcoins?\b|\bgems?\b|unlock|\bskins?\b",
}
CAMERA_SIGNALS = {
    "first-person": r"first.?person|\bfps.?controller|mouselook|\bfpp\b",
    "third-person": r"third.?person|\btps\b|invector|orbit.?camera",
    "top-down": r"top.?down|isometric",
    "side-view": r"side.?scroll|platformer|2d.?camera",
}
# Чужие бренды в именах: риск отказа модерации по товарным знакам. Решает пользователь.
BRAND_RISK = (r"fnaf|five nights|minecraft|roblox|among.?us|squid|pokemon|poppy|huggy|skibidi|\bsonic\b|"
              r"\bmario\b|fortnite|pubg|counter.?strike|\bcs.?go\b|\bgta\b|brawl.?stars|subway.?surf|"
              r"garten|banban|siren.?head")


def _count(rx, text):
    return len(re.findall(rx, text, re.I))


def cmd_profile(a):
    """Факты о проекте для подстройки пайплайна. Суждения (жанр, продающие моменты) агент пишет в "game"."""
    root = Path(a.project)
    assets = root / "Assets"
    cfg = config(a.project)
    skip = vendor_dirs(cfg)
    facts = {}
    pv = root / "ProjectSettings" / "ProjectVersion.txt"
    m = re.search(r"m_EditorVersion:\s*(\S+)", pv.read_text(encoding="utf-8")) if pv.exists() else None
    facts["unity"] = m.group(1) if m else None
    pk = (load_json(root / "Packages" / "manifest.json", {}) or {}).get("dependencies", {})
    facts["renderPipeline"] = "URP" if "com.unity.render-pipelines.universal" in pk else \
        "HDRP" if "com.unity.render-pipelines.high-definition" in pk else "Built-in"
    facts["inputSystem"] = "new" if "com.unity.inputsystem" in pk else "legacy"
    es = root / "ProjectSettings" / "EditorSettings.asset"
    facts["serialization"] = "text" if es.exists() and b"m_SerializationMode: 2" in es.read_bytes() else "binary/mixed"

    sdk = []
    if (assets / "PluginYourGames").exists():
        sdk.append("PluginYG2")
    if (assets / "YandexGame").exists():
        sdk.append("PluginYG1")
    for d in (assets.iterdir() if assets.exists() else []):
        if d.is_dir() and any(k in d.name.lower() for k in OTHER_SDK_MARKERS):
            sdk.append(d.name)
    facts["sdk"] = sdk or ["none"]
    facts["sdkSupport"] = detect_sdk(root)[1]   # full | migrate | media-only | install

    own = [p for p in assets.rglob("*.cs") if not any(part in p.parts for part in skip)]
    code = ""
    for p in own:
        try:
            code += p.read_text(encoding="utf-8", errors="replace") + "\n"
        except OSError:
            pass
    scenes = _build_scenes(root)
    names = " ".join([p.stem for p in own] + [Path(s).stem for s in scenes] +
                     [d.name for d in assets.rglob("*") if d.is_dir() and not any(x in d.parts for x in skip)])
    names = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", names)   # CamelCase -> слова, чтобы работали \b
    facts["ownScripts"] = len(own)
    facts["ownCodeLines"] = code.count("\n")
    facts["buildScenes"] = scenes

    d2 = _count(r"Rigidbody2D|Collider2D|SpriteRenderer|Physics2D|Tilemap", code)
    d3 = _count(r"\bRigidbody\b|CharacterController|NavMeshAgent|MeshRenderer|Physics\.Raycast", code)
    facts["dimension"] = "2D" if d2 > d3 * 1.5 else "3D" if d3 > d2 * 1.5 else "mixed"

    inp = {
        "keyboard": _count(r"GetKey(Down|Up)?\s*\(|Keyboard\.current|GetAxis(Raw)?\(\"(Horizontal|Vertical)", code),
        "mouse": _count(r"GetMouseButton|\"Mouse [XY]\"|Mouse\.current|mousePosition", code),
        "touch": _count(r"Input\.touch|Touchscreen|TouchPhase|IPointer(Down|Up|Click)Handler", code),
        "joystick": _count(r"joystick", code + names),
        "gamepad": _count(r"Gamepad\.current|joystick button", code),
    }
    facts["input"] = {k: v for k, v in inp.items() if v}
    facts["pointerLock"] = bool(re.search(r"CursorLockMode\.Locked", code))

    blob = code + "\n" + names
    g = {k: _count(rx, blob) for k, rx in GENRE_SIGNALS.items()}
    facts["genreSignals"] = dict(sorted(((k, v) for k, v in g.items() if v), key=lambda kv: -kv[1]))
    c = {k: _count(rx, blob) for k, rx in CAMERA_SIGNALS.items()}
    facts["cameraSignals"] = {k: v for k, v in sorted(c.items(), key=lambda kv: -kv[1]) if v}

    feats = {
        "interstitial": r"InterstitialAdvShow|FullscreenShow|showFullscreenAdv",
        "rewarded": r"RewardedAdvShow|RewVideoShow|showRewardedVideo",
        "saves": r"YG2\.saves|savesData|PlayerPrefs",
        "cloudSaves": r"SaveProgress\(|setData\(",
        "leaderboards": r"Leaderboard|NewLB",
        "purchases": r"BuyPayments|purchase|InApp",
        "gameReady": r"GameReadyAPI|GameReady\(|LoadingAPI",
        "gameplayApi": r"GameplayStart|GameplayStop",
        "pause": r"timeScale\s*=\s*0|AudioListener\.pause",
    }
    facts["features"] = {k: n for k, n in ((k, _count(rx, code)) for k, rx in feats.items()) if n}
    facts["textComponents"] = {
        "tmp": _count(r"TextMeshProUGUI|TMP_Text|\bTextMeshPro\b", code),
        "legacyText": _count(r"GetComponent<Text>|\bUI\.Text\b|public Text\b|\[SerializeField\][^;\n]*\bText\b", code),
    }
    facts["cyrillicLiterals"] = len(CYR_LITERAL.findall(code))
    facts["brandRisk"] = sorted({m.lower() for m in re.findall(BRAND_RISK, names + " " + code, re.I)})

    heavy = []
    for ext in (".mp4", ".mov", ".wav", ".fbx", ".psd", ".tga"):
        n = sum(1 for _ in assets.rglob("*" + ext))
        if n:
            heavy.append(f"{ext} {n}")
    facts["assetCounts"] = heavy

    y = yroot(a.project)
    prof = load_json(y / "profile.json", {}) or {}
    prof["scanned"] = datetime.date.today().isoformat()
    prof["facts"] = facts
    prof.setdefault("game", {
        "genre": None, "subgenre": None, "camera": None, "coreLoop": None, "session": None,
        "audience": None, "tone": None, "artStyle": None, "palette": [],
        "sellingMoments": [], "avoidInMedia": [], "catalogCategories": [], "tagsCandidates": [],
        "controlsPC": None, "controlsTouch": None, "complexity": [], "notes": "",
    })
    save_json(y / "profile.json", prof)

    print(f"Unity {facts['unity']}, {facts['renderPipeline']}, Input {facts['inputSystem']}, "
          f"сериализация {facts['serialization']}, {facts['dimension']}")
    print(f"SDK: {', '.join(facts['sdk'])}")
    print(f"Код игры: {facts['ownScripts']} файлов, {facts['ownCodeLines']} строк; сцен в билде: {len(scenes)}")
    print(f"Ввод: {facts['input'] or '—'}{'  (pointer lock)' if facts['pointerLock'] else ''}")
    print("Жанровые сигналы: " + (", ".join(f"{k} {v}" for k, v in list(facts['genreSignals'].items())[:8]) or "—"))
    print("Камера: " + (", ".join(f"{k} {v}" for k, v in facts["cameraSignals"].items()) or "—"))
    print("SDK-фичи в коде: " + (", ".join(f"{k} {v}" for k, v in facts["features"].items()) or "—"))
    print(f"Текст: TMP {facts['textComponents']['tmp']}, legacy {facts['textComponents']['legacyText']}; "
          f"русских литералов {facts['cyrillicLiterals']}")
    if facts["brandRisk"]:
        print(f"⚠ чужие бренды в именах: {', '.join(facts['brandRisk'])} — риск отказа по товарным знакам")
    if heavy:
        print("Ассеты: " + "; ".join(heavy))
    empty = [k for k, v in prof["game"].items() if v in (None, [], "")]
    if empty:
        print(f"\nАгенту заполнить в profile.json → game: {', '.join(empty)}")
    print(f"→ {y / 'profile.json'}")


# ---------------------------------------------------------------- reqs-check

def _page_text(html_src):
    import html as _html
    m = re.search(r"(?s)<main[^>]*>(.*)</main>", html_src)
    body = m.group(1) if m else html_src
    body = re.sub(r"(?s)<(script|style|svg|nav|button)\b[^>]*>.*?</\1>", "", body)
    body = re.sub(r'(?i)<li[^>]*class="[^"]*deprecated[^"]*"[^>]*>', "\n[упразднён] ", body)
    body = re.sub(r"(?i)</?(p|li|h[1-6]|tr|td|th|div|br|ul|ol|table|section|dt|dd)\b[^>]*>", "\n", body)
    body = _html.unescape(re.sub(r"<[^>]+>", "", body))
    out = []
    for line in body.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        # «Пункт 1.6.1.5.» отдельной строкой — склеить с текстом пункта, чтобы diff показывал номер
        if out and re.fullmatch(r"(\[упразднён\] )?Пункт [\d.]+\.?", out[-1]):
            out[-1] += " " + line
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def cmd_reqs_check(a):
    import difflib
    import hashlib
    import urllib.request
    kn = PLUGIN_ROOT / "knowledge"
    pages = (load_json(kn / "sources.json", {}) or {}).get("pages", [])
    if not pages:
        sys.exit(f"нет страниц в {kn / 'sources.json'}")
    snap = kn / "snapshots"
    idx_path = snap / "index.json"
    idx = load_json(idx_path, {}) or {}
    today = datetime.date.today()
    if a.if_older is not None and not a.accept:
        ages = [(today - datetime.date.fromisoformat(idx[p["id"]]["checked"])).days
                for p in pages if p["id"] in idx and idx[p["id"]].get("checked") and not idx[p["id"]].get("pendingSha")]
        if len(ages) == len(pages) and max(ages) < a.if_older:
            print(f"требования сверены {max(ages)} дн. назад (порог {a.if_older}) — пропуск")
            return
    changed, failed = [], []
    for page in pages:
        pid, url = page["id"], page["url"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                                                       "Accept-Language": "ru"})
            raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        except Exception as e:
            failed.append(pid)
            print(f"✗ {pid}: не скачалось ({e}) — сверить вручную: {url}")
            continue
        if "<main" not in raw:
            failed.append(pid)
            print(f"✗ {pid}: вместо страницы капча/заглушка — сверить вручную в браузере: {url}")
            continue
        text = _page_text(raw)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        old_p = snap / f"{pid}.txt"
        entry = idx.setdefault(pid, {})
        entry["url"] = url
        if not old_p.exists() or a.accept:
            first = not old_p.exists()
            snap.mkdir(parents=True, exist_ok=True)
            old_p.write_text(text, encoding="utf-8")
            (snap / f"{pid}.diff").unlink(missing_ok=True)
            if first or entry.get("sha") != sha:
                entry["changed"] = today.isoformat()
            entry.update(checked=today.isoformat(), sha=sha)
            entry.pop("pendingSha", None)
            print(f"{'+' if first else '✓'} {pid}: {'первый снимок' if first else 'снимок принят'} "
                  f"({len(text.splitlines())} строк)")
            continue
        old = old_p.read_text(encoding="utf-8")
        if old == text:
            entry["checked"] = today.isoformat()
            entry.pop("pendingSha", None)
            print(f"= {pid}: без изменений (последнее изменение {entry.get('changed', '?')})")
            continue
        diff = list(difflib.unified_diff(old.splitlines(), text.splitlines(), f"{pid} (снимок)",
                                         f"{pid} ({today})", n=1, lineterm=""))
        (snap / f"{pid}.diff").write_text("\n".join(diff) + "\n", encoding="utf-8")
        entry["pendingSha"] = sha
        plus = [l for l in diff if l.startswith("+") and not l.startswith("+++")]
        minus = [l for l in diff if l.startswith("-") and not l.startswith("---")]
        changed.append(pid)
        print(f"≠ {pid}: ИЗМЕНИЛОСЬ +{len(plus)} / -{len(minus)} строк → {snap / (pid + '.diff')}")
        print(f"   влияет на: {', '.join(page.get('affects', [])) or '—'}")
        for l in minus[:10] + plus[:10]:
            print("   " + l[:160])
    save_json(idx_path, idx)
    if changed:
        print("\nДальше: прочитать .diff, обновить затронутые файлы knowledge/ (и дату «сверено» в "
              "requirements.md), затем `reqs-check --accept`.")
        sys.exit(3)
    if failed:
        sys.exit(4)

# ---------------------------------------------------------------- doctor

def unity_editor_path(version):
    """Unity нужной версии из папок Unity Hub (стандартных и заданной в Hub). Нет точной — та же ветка (6000.3.*).
    Возвращает (путь, версия) или (None, None)."""
    if not version:
        return None, None
    home = Path.home()
    exe_rel = ["Editor/Unity.exe", "Unity.app/Contents/MacOS/Unity", "Editor/Unity"]
    roots = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Unity/Hub/Editor",
             Path("/Applications/Unity/Hub/Editor"), home / "Applications/Unity/Hub/Editor", home / "Unity/Hub/Editor"]
    for hub_cfg in (Path(os.environ.get("APPDATA", "")) / "UnityHub", home / "Library/Application Support/UnityHub",
                    home / ".config/UnityHub"):
        try:
            extra = json.loads((hub_cfg / "secondaryInstallPath.json").read_text(encoding="utf-8"))
            if extra:
                roots.insert(0, Path(extra))
        except (OSError, ValueError):
            pass
    found = {}
    for r in roots:
        if not r.is_dir():
            continue
        for d in r.iterdir():
            exe = next((d / e for e in exe_rel if (d / e).exists()), None)
            if exe:
                found.setdefault(d.name, str(exe))
    if version in found:
        return found[version], version
    branch = ".".join(version.split(".")[:2]) + "."
    same = sorted((v for v in found if v.startswith(branch)), reverse=True)
    return (found[same[0]], same[0]) if same else (None, None)


def cmd_doctor(a):
    """Проверка окружения: Python-пакеты, ffmpeg, Unity, пакеты проекта. Печатает команды установки."""
    import importlib.util
    py = Path(sys.executable).name
    bad = []

    def line(ok, name, detail="", fix=None):
        print(f"  {'✓' if ok else '✗'} {name}{'  ' + detail if detail else ''}")
        if not ok and fix:
            bad.append(fix)

    print("Python")
    line(sys.version_info >= (3, 8), f"Python {sys.version.split()[0]}", sys.executable,
         "Python 3.8+ — https://www.python.org/downloads/")
    pkgs = {"PIL": "pillow", "imageio_ffmpeg": "imageio-ffmpeg", "fontTools": "fonttools"}
    missing = [pip for mod, pip in pkgs.items() if importlib.util.find_spec(mod) is None]
    for mod, pip in pkgs.items():
        line(pip not in missing, pip)
    if missing:
        bad.append(f'"{sys.executable}" -m pip install --user {" ".join(missing)}')
    from shutil import which
    ff = which("ffmpeg") or (importlib.util.find_spec("imageio_ffmpeg") and "imageio-ffmpeg")
    line(bool(ff), "ffmpeg", ff or "нужен для видео (ставится вместе с imageio-ffmpeg)")

    root = Path(a.project)
    if (root / "Assets").exists():
        print(f"\nUnity-проект {root.resolve().name}")
        pv = root / "ProjectSettings" / "ProjectVersion.txt"
        m = re.search(r"m_EditorVersion:\s*(\S+)", pv.read_text(encoding="utf-8")) if pv.exists() else None
        ver = m.group(1) if m else None
        exe, got = unity_editor_path(ver)
        note = exe if got == ver else f"{exe}  (точной версии нет, ветка {got})" if exe else             "не найден в папках Unity Hub (нужен только для CLI-сборки/съёмки при закрытом редакторе)"
        line(bool(exe), f"Unity {ver or '?'}", note)
        pk = (load_json(root / "Packages" / "manifest.json", {}) or {}).get("dependencies", {})
        line("com.unity.recorder" in pk, "Unity Recorder", "" if "com.unity.recorder" in pk else
             "нужен для записи видео: Package Manager → Unity Recorder",
             "Unity: Window → Package Manager → Unity Registry → Recorder → Install")
        tmp = "com.unity.textmeshpro" in pk or "com.unity.ugui" in pk
        line(tmp, "TextMesh Pro / uGUI", "" if tmp else "нужен для LocYG")
        sdk, mode = detect_sdk(root)
        sdk_note = {
            "full": "полная поддержка: тех. аудит и исправления автоматически",
            "migrate": "устаревший — агент yandex-tech предложит миграцию на PluginYG2",
            "media-only": "не PluginYG2: материалы страницы и сборка — автоматически, тех. требования — вручную",
            "install": f"не подключён — модерация без SDK не пройдёт; агент yandex-tech поможет поставить "
                       f"PluginYG2 ({PLUGIN_YG_DOCS})",
        }[mode]
        print(f"  {'✓' if mode == 'full' else '⚠'} Yandex SDK  {sdk or '—'}: {sdk_note}")
        tools = list((root / "Assets").rglob("YandexStoreTools.cs"))
        line(bool(tools), "меню Yandex Games в Unity", "" if tools else "запустите /yandex-games:yandex-init")
        if (root / "yandex" / "config.json").exists():
            cfg = config(a.project)
            print(f"  · интерпретатор для команд плагина: {py}")
            if exe and cfg.get("unityPath") != exe:
                c = load_json(yroot(a.project) / "config.json", {}) or {}
                c["unityPath"] = exe
                save_json(yroot(a.project) / "config.json", c)
                print("  · путь к Unity записан в yandex/config.json → unityPath")
    print("\nБраузер: заполнение консоли требует расширения Claude in Chrome; без него агент выдаст список "
          "ручных загрузок.")
    if bad:
        print("\nУСТАНОВИТЬ:")
        print("\n".join("  " + b for b in dict.fromkeys(bad)))
        sys.exit(1)
    print("\nOK")


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--game-id", type=int); p.set_defaults(f=cmd_init)
    sub.add_parser("check-texts").set_defaults(f=cmd_check_texts)
    sub.add_parser("shots").set_defaults(f=cmd_shots)
    p = sub.add_parser("keyart"); p.add_argument("--icon-title", action="store_true",
                                                 help="писать название и на иконке")
    p.add_argument("--no-title", action="store_true",
                   help="без названия на обложке/hero (готовая иллюстрация, одна на все языки)")
    p.set_defaults(f=cmd_keyart)
    p = sub.add_parser("video"); p.add_argument("--src"); p.add_argument("--start", type=float, default=0)
    p.add_argument("--duration", type=float, default=27)
    p.add_argument("--segments", help='монтаж отрезков, сек: "2-6,194-209,14-22" (сумма ≤27.5)')
    p.set_defaults(f=cmd_video)
    sub.add_parser("status").set_defaults(f=cmd_status)
    sub.add_parser("tech-scan").set_defaults(f=cmd_tech_scan)
    p = sub.add_parser("size"); p.add_argument("--top", type=int, default=30); p.set_defaults(f=cmd_size)
    sub.add_parser("loc-extract").set_defaults(f=cmd_loc_extract)
    sub.add_parser("loc-build").set_defaults(f=cmd_loc_build)
    p = sub.add_parser("loc-missing"); p.add_argument("--log"); p.set_defaults(f=cmd_loc_missing)
    p = sub.add_parser("loc-audit"); p.add_argument("--top", type=int, default=60)
    p.add_argument("--verbose", action="store_true", help="перечислить и TMP-компоненты"); p.set_defaults(f=cmd_loc_audit)
    sub.add_parser("doctor").set_defaults(f=cmd_doctor)
    sub.add_parser("profile").set_defaults(f=cmd_profile)
    p = sub.add_parser("reqs-check"); p.add_argument("--accept", action="store_true",
                                                     help="принять текущие версии страниц как новые снимки")
    p.add_argument("--if-older", type=int, metavar="DAYS", help="ничего не делать, если сверялись меньше DAYS дней назад")
    p.set_defaults(f=cmd_reqs_check)
    p = sub.add_parser("state"); p.add_argument("step", choices=STEPS)
    p.add_argument("status", choices=["todo", "done", "blocked", "skipped"]); p.add_argument("note", nargs="?")
    p.set_defaults(f=cmd_state)
    a = ap.parse_args()
    global LOC_SKIP_DIRS
    LOC_SKIP_DIRS = vendor_dirs(config(a.project))
    a.f(a)


if __name__ == "__main__":
    main()
