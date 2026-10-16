# Оптимизация веса Unity WebGL-билда (Unity 6)

Цель для Яндекс Игр: ≤100 МБ распакованно — жёсткий лимит; для конверсии желательно архив ≤20–30 МБ,
первая загрузка как можно меньше. Измерять: `yandex/out/build-assets.json` (после
`Yandex Games → Build WebGL + ZIP`) и `ygtool size`.

## Сначала проверить (дают основную массу на типичных проектах)
- **Resources/ из ассетов стора**: Invector и подобные кладут в `Resources/` трейлеры (.mp4 17 МБ!), логотипы и
  баннеры для своих editor-окон — всё это едет в билд. В отчёте видео без пути видно как `(built-in) VideoClip`.
- **Размеры не кратны 4**: DXT/ASTC-сжатие не применяется, текстура лежит несжатой RGBA (4 байта/пиксель).
  Спрайт 3800×1878 = 6 МБ. Лечение: пересохранить исходник в размер, кратный 4 (и ≤2048), — сжатие ×4–8.
  На одном проекте так было 42 текстуры / 31.5 МБ.
- Unity Splash Screen (2.7 МБ «Splash Screen Unity Logo») — отключить в Player Settings, если лицензия позволяет.

## Порядок (от самого выгодного)
1. **Текстуры** — обычно 50–80% веса.
   - Max Size по реальному экранному размеру: UI-иконки 256–512, скины персонажей 512–1024, 2048+ только для
     крупных атласов. Проверять по `build-assets.json` типы Texture2D сверху списка.
   - Формат для WebGL: в Build Profile → Texture Compression выбрать ASTC или DXT+ASTC (Unity 6 умеет оба
     в одном билде, но это ×2 веса) — для Яндекса (десктоп+мобильные) обычно **DXT (desktop) + ASTC (mobile)**
     только если мобильная аудитория важна; иначе один формат.
   - Crunch compression (DXT): Use Crunch Compression + Quality 50–70 — сильно режет архив.
   - Отключить Read/Write и mip-maps у UI-спрайтов (Generate Mip Maps = off для UI).
   - Normal/mask-карты, которые не используются шейдером, — удалить из материалов.
2. **Аудио**
   - Музыка: Load Type = Streaming / Compressed In Memory, Vorbis Quality 40–60, Force To Mono, 22050–32000 Hz.
   - SFX: Vorbis/ADPCM, Mono, 22050 Hz; длинные «хвосты» тишины обрезать.
3. **Меши и анимации**
   - Model Import: Mesh Compression Medium/High, Read/Write off, Optimize Mesh, отключить импорт неиспользуемых
     камер/света/blendshapes.
   - Animation Compression = Optimal, отключить лишние кривые (scale).
4. **Шрифты** — TMP SDF-атласы: Atlas 1024–2048 вместо 4096, только нужные символы (латиница + кириллица),
   удалить неиспользуемые TMP Examples & Extras (шрифты Anton/Bangers/… из примеров попадают в билд,
   только если на них есть ссылки — проверять по отчёту).
5. **Мусор, который тянется в билд**
   - Всё в `Resources/` попадает в билд целиком — выносить неиспользуемое из Resources.
   - Неиспользуемые сцены в Build Settings, демо-ассеты пакетов (Polygon Arsenal Demo, Invector demo).
   - Ассеты, на которые ссылаются только выключенные объекты сцены, всё равно входят в билд.
6. **Код и движок (Player Settings → Web)**
   - Managed Stripping Level = High (проверить, что ничего не ломается: рефлексия, JSON → link.xml).
   - IL2CPP Code Generation = Optimize Size; C++ Compiler Configuration = Master.
   - Strip Engine Code = on; удалить неиспользуемые пакеты/модули (Package Manager → Built-in: Physics2D,
     Vehicles, Cloth, XR, AI и т.п. если не используются).
   - Exception Support = Explicitly Thrown Exceptions Only (или None для релиза).
   - Compression Format = Brotli (лучшее сжатие) + Decompression Fallback, если хостинг не отдаёт
     Content-Encoding (Яндекс отдаёт, PluginYG2 настраивает сам — проверить шаблон).
   - Data Caching = on.
7. **Освещение** — Lightmaps: уменьшить Lightmap Resolution/Max Lightmap Size (1024), формат с низким качеством,
   либо запекать меньше; Light Probes вместо карт для мелких объектов.

## Правила работы агента
- Меняй импорт-настройки через `.meta` только для текстовых meta (YAML). Если проект в Force Binary — meta всё
  равно текстовые, их можно править; ProjectSettings — бинарный, его настройки описывай пользователю.
- Каждое изменение — с ожидаемой экономией по `build-assets.json`. Не трогай ассеты с чужими незакоммиченными
  правками (git status). Визуально заметные потери качества (персонажи крупным планом, UI-логотипы) — только
  с согласия пользователя.
- После правок: пользователь пересобирает (`Build WebGL + ZIP`), агент сравнивает новый отчёт со старым
  (сохрани копию `build-assets.prev.json`).
