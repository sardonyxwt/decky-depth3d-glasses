# CLAUDE.md — контекст проекта

Decky Loader плагин для SteamOS / Steam Game Mode на Linux (Ubuntu + gamescope-session). Цель: помечать игры, у которых есть готовый профиль Depth3D (SuperDepth3D / Overwatch.fxh), и ставить в них ReShade + SuperDepth3D, чтобы играть в псевдо-3D (SBS) на XR-очках.

## Железо автора
- XR-очки: VITURE Luma Pro (3D-режим = 3840×1080, full SBS: по 1920×1080 на глаз)
- GPU: AMD Radeon RX 9060 XT (RADV, без CUDA/TensorRT)
- Разработка на macOS (arm64), запуск на Linux-машине со Steam

## Структура
- `main.py` — бэкенд Decky (класс `Plugin`, методы вызываются из фронта через `callable`)
- `py_modules/depth3d/` — логика без зависимостей от Decky (только стандартная библиотека):
  - `hashing.py` — `__APPLICATION__` ReShade = `std::hash<std::string>(exe_stem) & 0xFFFFFFFF`; в MSVC это FNV-1a: 64-битная игра → FNV-1a 64 обрезанный до 32 бит, 32-битная → FNV-1a 32. Проверено на хэшах из настоящего Overwatch.fxh.
  - `pe.py` — разрядность exe и импорты (выбор dxgi / d3d9 / opengl32 / vulkan)
  - `overwatch.py` — парсер блоков `#elif (App == 0x...) //Name`, флаги NPW / NDW / NCW
  - `steamlib.py` — libraryfolders.vdf + appmanifest_*.acf + бинарный shortcuts.vdf (сторонние игры активного аккаунта, только `.exe`)
  - `display.py` — активный экран: EDID из `GAMESCOPE_PATCHED_EDID_FILE` (через /proc/<gamescope>/environ, иначе `~/.config/gamescope/edid.bin`) сверяется с `/sys/class/drm`; очки — по имени в EDID или по пометке пользователя
  - `gamescope.py` — SBS для очков, у которых в EDID нет 3840×1080 (VITURE Luma Pro: кнопка R1 переключает 3D, EDID не меняется). Lua-скрипт `~/.config/gamescope/scripts/depth3d-glasses.lua` описывает очки как known display с dynamic_refresh_rates (частоты 1920×1080 из EDID) и dynamic_modegen: частота-метка 59 (тоже в списке частот) → 3840×1080@60 с таймингом «два CEA 1080p60 рядом» (4400×1125, 297 МГц; CVT-RB давал полосу мусора сверху на Luma Pro), остальные → обычный 1080p. Обёртка на время 3D-игры просит у gamescope цикл обновления 59 через Wayland-протокол `gamescope_control.set_app_target_refresh_cycle` (флаги allow_refresh_switching | only_change_refresh_rate, клиент на stdlib в `gamescope.py`), повторяет каждую 1 с первые 30 с и потом каждые 3 с (Steam сбрасывает цикл при появлении окна игры и смене фокуса), после выхода отправляет 0. Свойство X11 `GAMESCOPE_DYNAMIC_REFRESH_EXTERNAL` в 3.16 не работает: gamescope перезаписывает его каждый кадр. Готовность проверяется у самого gamescope: событие `gamescope_control.active_display_info` (список частот активного экрана содержит 59 → скрипт загружен); если протокол недоступен — по времени файла скрипта. gamescope читает скрипты только при старте — нужен перезапуск сессии (кнопка в панели, `systemctl --user restart gamescope-session…`).
  - `launch.py` — обёртка `depth3d-run <appid> %command%` (пишется в `DECKY_PLUGIN_RUNTIME_DIR/bin` при старте) и решение 3D/2D. 2D = DLL ReShade паркуются в `*.depth3d-off` (`installer.set_active`, флаг `active` в маркере), окружение не трогается; запасной режим `2d-profile` = `RESHADE_BASE_PATH_OVERRIDE` на пустой профиль `reshade-2d/`, если папку нельзя менять. Обёртка вызывает системный `python3` без `LD_LIBRARY_PATH` Steam
  - `scanner.py` — сопоставление exe с профилями, маркер `depth3d-glasses.json` рядом с exe
  - `assets.py` — загрузка ReShade (reshade.me), d3dcompiler_47 (из zip Electron 33.4.11 range-запросами, SHA-256; запасной — Firefox 62 + 7z), шейдеров Depth3D (raw.githubusercontent)
  - `installer.py` — раскладка файлов в папку игры с бэкапом `*.depth3d-bak`, ReShade.ini, ReShadePreset.ini
- `src/` — фронтенд (React, @decky/ui, @decky/api)
  - `sbs.ts` — вывод Steam на 3840×1080, пока очки в 3D (`SystemDisplayManagerStore.SetMode`), и настройки игры на время 3D-запуска: `SetAppResolutionOverride` 1920×1080 (хук `RegisterForGameActionStart`) и масштабирование Stretch=5 через `SystemPerfStore` (после `RegisterForAppLifetimeNotifications`). Прежние значения — в `games.json` → `steam`.
  - `libraryPatch.tsx` — бейдж очков на странице игры (routerHook.addPatch на `/library/app/:appid`, подход как у ProtonDB Badges; хрупко к обновлениям Steam)
  - `GameModal.tsx`, `index.tsx`, `steam.ts` (SetAppLaunchOptions, collectionStore), `launchOptions.ts` (чистые функции)

## Команды
```bash
pnpm install
pnpm run typecheck
pnpm run build                 # → dist/index.js
python3 tests/test_backend.py  # тесты бэкенда (без сети, фейковая библиотека Steam)
OVERWATCH=/path/Overwatch.fxh python3 tests/test_backend.py   # + проверка на настоящем Overwatch
./scripts/package.sh           # → out/depth3d-glasses.zip для «Установить из ZIP» в Decky
./scripts/deploy.sh user@host   # собрать и поставить на ПК по SSH без sudo (через Decky API и CEF-отладчик Steam)
```

## Правила
- **Depth3D проприетарен** (бесплатно для личного использования, без распространения). Нельзя коммитить SuperDepth3D.fx / Overwatch.fxh / Sentinel.fxh / AXAA.fxh в репозиторий и класть в сборку. Только скачивание на машине пользователя. Тесты используют синтетический Overwatch.
- То же для ReShade DLL и d3dcompiler_47.dll — не коммитить.
- Python-бэкенд работает в Python, который приносит Decky (PyInstaller): только стандартная библиотека, никаких pip-зависимостей. Для внешних утилит (`7z`) чистить `LD_LIBRARY_PATH` (см. `assets._clean_env`).
- Плагин должен быть универсальным (для публикации): никаких привязок к машине автора и её скриптам. Бэкенд работает от пользователя (без флага root).
- Состояние: `settings/games.json` (appid → exe, dll, enabled), `settings/settings.json` (+ `glasses_names` / `not_glasses_names`), `settings/scan.json` (кэш скана, ключ `stamp`). Лог решений обёртки — `data/launch.log`.
- Удаление из папки игры трогает только файлы из маркера и только в той же папке.
- В покое игра «ванильная»: после установки и после каждой 3D-сессии DLL паркуются (`*.depth3d-off`, флаг `active` в маркере); обёртка включает их только на 3D-запуск. Steam-настройки игры (разрешение, масштабирование) фронтенд возвращает при закрытии игры.
- Удаление плагина: `_unload`/`_uninstall` ненадёжны (Decky вызывает uninstall и при обновлении из магазина, а плагин, не остановившийся за 5 с, убивает SIGKILL — на ПК автора так и было). Поэтому `_main` запускает сторожа (`cleanup.start_guardian`, отдельная сессия, копия пакета в /tmp, pid-файл в `$XDG_RUNTIME_DIR`): папка плагина пропала и не вернулась за 20 с → `full_cleanup`. Обёртку из параметров запуска убирает фронтенд в `onDismount` и возвращает при загрузке.

## Известные ограничения / что не проверено на железе
- Бэкенд проверен на ПК автора без Decky (заглушка `decky`): скан Steam + сторонних игр, определение экрана, загрузка ReShade 6.8.0 и d3dcompiler_47, обёртка.
- Не проверено в живом Steam: бейдж, коллекция, параметры запуска сторонних игр, что парковка DLL и запасной `RESHADE_BASE_PATH_OVERRIDE` под Proton дают чистое 2D.
- Проверено на железе (2026-11-02): настоящее удаление плагина через Decky — сторож за ~20 с вернул файлы Stellar Blade, удалил скрипт gamescope и папки плагина, фронтенд убрал обёртку из параметров запуска. Отсюда добавлены удаление `ReShade.log` и возврат Steam-настроек в `onDismount`.
- Проверено на железе (2026-11-02): скрипт грузится (`Got known display: depth3d_glasses_1`, ValidRefreshRates 60, 90, 120). Сам SBS-вывод в игре ещё не проверен.
- gamescope выбирает режим так: `-W/-H/-r` → сохранённый режим из `GAMESCOPE_MODE_SAVE_FILE` (его пишет Steam) → предпочтительный из EDID. Поэтому сам по себе переход очков в 3D не даёт 3840×1080, если у очков остался 1920×1080 в списке.
- Переключение режима очков 2D/3D пока вручную. План: официальный VITURE SDK (`libglasses.so`, `xr_device_provider_switch_dimension`), нужен доступ к hidraw.
- Vulkan-игры под Proton: ReShade не работает (ограничение ReShade под Wine).
- DX12 требует ReShade 6.8+ с аддонами.

Подробнее о направлениях развития — `docs/ROADMAP.md`.
