# gamedev-tools — плагины Claude Code

## yandex-games

Пайплайн релиза Unity WebGL-игры в [Яндекс Игры](https://yandex.ru/games/): тех. проверка, локализация,
тексты страницы на всех языках, скриншоты, иконка, обложка, видео и заполнение черновика в консоли.
Подстраивается под игру: жанр, камера, управление и сложности проекта определяются по коду, сценам,
скриншотам и видео (`yandex/profile.json`).

| Команда | Что делает |
|---|---|
| `/yandex-games:yandex-init [gameId]` | Подключает проект: `yandex/config.json`, профиль игры, папки, меню **Yandex Games** в Unity |
| `/yandex-games:yandex-release [tech\|listing\|console\|all]` | Полный пайплайн через субагентов |
| `/yandex-games:yandex-status` | Готовность материалов |

Субагенты:

| Агент | Зона |
|---|---|
| `yandex-tech` | SDK (PluginYG2 / YG1 / другие), реклама и пауза, сохранения, локализация, архив |
| `yandex-listing` | профиль игры, тексты по языкам, скриншоты, иконка/обложка, видео-монтаж |
| `yandex-console` | заполнение черновика в браузере (Claude in Chrome), без отправки на модерацию |
| `webgl-size` | уменьшение веса WebGL-билда по отчёту сборки |

### Установка
```
claude plugin marketplace add kidofsatan/claude-gamedev-plugins
claude plugin install yandex-games@gamedev-tools
```
Локальная копия для разработки плагина: `claude plugin marketplace add <путь к папке репозитория>`,
после правок — `/reload-plugins`.

Зависимости: Python 3.8+ и пакеты `pillow imageio-ffmpeg fonttools` (`python -m pip install --user ...`;
на macOS/Linux — `python3`). Проверить всё разом: `ygtool doctor` (агенты запускают его сами).
Работает на Windows, macOS и Linux. Для записи видео в Unity — пакет Unity Recorder (`com.unity.recorder`).

### Yandex SDK
Плагин рассчитан на [PluginYG2](https://max-games.ru/plugin-yg/doc/): он сам закрывает большую часть
технических требований Яндекса, а плагин проверяет и дочиняет остальное.

| SDK в проекте | Материалы страницы, сборка, вес | Тех. требования |
|---|---|---|
| PluginYG2 | автоматически | аудит и исправления автоматически |
| PluginYG1 | автоматически | предложит миграцию на PluginYG2 и выполнит её |
| нет SDK | автоматически | поможет поставить PluginYG2 и подключить рекламу, паузу, сохранения, язык |
| другой (GamePush и т.п.) | автоматически | только проверка по смыслу и отчёт, исправления вручную |

Без SDK игра модерацию не пройдёт. Что стоит в проекте, покажет `ygtool doctor`.

### Данные
- по проекту, в `<проект>/yandex/`: `config.json` (настройки), `profile.json` (жанр, стиль, продающие моменты),
  `state.json` (прогресс), `listing.json` (тексты), `raw/` (исходники), `out/` (готовое для консоли);
- общие, в `plugins/yandex-games/knowledge/`: требования и чек-листы, жанровые плейбуки (`genres.md`),
  уроки прошлых релизов (`lessons.md`, агенты дописывают сами).

### Утилита `ygtool.py`
```
python plugins/yandex-games/scripts/ygtool.py --project <путь к Unity-проекту> <команда>
```
| Команда | Что делает |
|---|---|
| `doctor` | проверка окружения: Python-пакеты, ffmpeg, Unity (находит сам), Recorder, SDK; печатает команды установки |
| `profile` | факты о проекте: SDK, ввод, 2D/3D, жанровые сигналы, используемые фичи, чужие бренды |
| `reqs-check` | сверяет страницы требований Яндекса со снимками в `knowledge/snapshots/` (`--if-older 30`, `--accept`) |
| `tech-scan` | быстрый тех. аудит SDK и локализации |
| `loc-extract` / `loc-build` / `loc-missing` / `loc-audit` | локализация через рантайм-словарь `LocYG` |
| `check-texts` | лимиты и правила текстов страницы |
| `shots` / `keyart` / `video` | скриншоты 16:9 и 9:16, иконка/обложка/баннер с названием, видео-монтаж `--segments` |
| `size` | что сколько весит в WebGL-билде |
| `status` / `state` | прогресс пайплайна |

### Съёмка материалов в Unity
- **Yandex Games → Auto Capture While Playing** — скриншот каждые 3 с в Play Mode; агент сам отберёт лучшие.
- **Yandex Games → Record Video While Playing** — запись Game View в mp4; **F9** — скриншот, **F10** — старт/стоп видео.
- **Yandex Games → Hide UI in Captures** — съёмка без интерфейса (по умолчанию включено).
- **Yandex Games → Build WebGL + ZIP** — релизная сборка, проверки размера/имён, `yandex/out/build.zip`
  и отчёт о весе ассетов (`ygtool size`).
- **Yandex Games → Render Key Art** — рендер камеры `KeyArtCamera` для иконки/обложки
  (скриншоты для них запрещены правилами). Можно вместо этого положить свои иллюстрации в `yandex/raw/keyart/`.
- Без участия человека (редактор закрыт):
  `Unity.exe -projectPath . -executeMethod YandexGames.StoreTools.CaptureSessionCli -ygScene "Assets/Scenes/X.unity" -ygSeconds 90`

### Без интерактива
```
claude -p "/yandex-games:yandex-release listing"
```

Отправку игры на модерацию плагин никогда не нажимает — это делает автор.
