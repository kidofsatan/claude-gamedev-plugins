---
description: Подключить текущий Unity-проект к пайплайну Яндекс Игр (config, папки, editor-скрипт съёмки)
argument-hint: "[gameId]"
---

PLUGIN_ROOT = `${CLAUDE_PLUGIN_ROOT}`. Текущая папка — корень Unity-проекта (должны быть `Assets/` и `ProjectSettings/`; иначе остановись).

0. `python "${CLAUDE_PLUGIN_ROOT}/scripts/ygtool.py" --project . doctor` (нет `python` — `python3`). Код 1 — выполни
   команды из блока «УСТАНОВИТЬ» (pip — сам; пакеты Unity — попроси пользователя) и повтори.
1. `python "${CLAUDE_PLUGIN_ROOT}/scripts/ygtool.py" --project . init --game-id $ARGUMENTS`
   (без аргумента — без `--game-id`). После init ещё раз `doctor` — запишет `unityPath` в config.
1a. `... profile` — факты о проекте (SDK, ввод, 2D/3D, жанровые сигналы, чужие бренды) в `yandex/profile.json`.
   По ним заполняй config ниже. Папки сторонних паков, которые профиль принял за код игры (видно по жанровым
   сигналам от чужих демо), добавь в `config.thirdPartyDirs` и перезапусти profile.
2. Заполни `yandex/config.json` по проекту, спрашивая пользователя только то, что нельзя узнать из проекта:
   - `orientation` — из PlayerSettings (defaultScreenOrientation / шаблон WebGL) или по UI;
   - `platforms` — desktop и/или mobile (есть ли тач-управление);
   - `languages` — языки, на которые игра РЕАЛЬНО переведена через SDK (`ygtool tech-scan` покажет
     модуль Localization и захардкоженные строки);
     если переведена только на часть — предложи пользователю расширить список, но в config пиши фактические;
   - `buildDir` — папка WebGL-билда (с index.html);
   - `gameId` / `consoleUrl` — из аргумента или спроси;
   - `brandColor` — цвет названия на обложке под палитру игры (по скриншотам/арту), `artStyleRef` — папка
     с примерами промо пользователя, если он её назовёт.
3. Скопируй `${CLAUDE_PLUGIN_ROOT}/unity/Editor/*` в `Assets/Editor/YandexStoreTools/` (не перезаписывай,
   если файлы совпадают). Сообщи, что Unity перекомпилирует скрипты и появится меню **Yandex Games**.
4. Добавь `yandex/raw/video/` и `yandex/out/video/` в игнор (ygtool создаёт `yandex/.gitignore`).
5. Покажи `ygtool status` и кратко: как снять материалы (Auto Capture / F9 / F10 / Render Key Art) и что
   дальше запустить `/yandex-games:yandex-release`.
