# Roadmap и исследование

## Состояние v0.1.0
- [x] Сканирование библиотеки Steam и сопоставление с профилями Overwatch (995 хэшей в Overwatch v5.0.8)
- [x] Бейдж очков на странице игры, список в панели, коллекция «3D Glasses»
- [x] Установка/удаление ReShade + SuperDepth3D, параметры запуска
- [x] Тесты бэкенда (хэши, PE, VDF, парсер, установка/удаление)
- [ ] Первый прогон на реальной машине (Ubuntu + gamescope + RX 9060 XT + Luma Pro)

## Состояние v0.2.0
- [x] Сторонние (non-Steam) игры из `shortcuts.vdf`, автоскан при запуске Steam
- [x] Исправлено определение API для игр на Unreal Engine (импортируют d3d9 рядом с d3d11/12)
- [x] Определение активного экрана (очки / монитор), на мониторе всегда 2D
- [x] Переключатель 3D для игры через обёртку запуска, без перекладывания файлов
- [x] d3dcompiler_47 без 7-Zip

## Ближайшее
1. **Проверка на железе**: загрузка ReShade/d3dcompiler, бейдж, коллекция, реальная игра с профилем (DX11 и DX12).
2. **Generic Depth Mod (GDM)** — аддон Depth3D с лучшим выбором буфера глубины и отдельным буфером для рук с оружием. Сейчас не ставится. Источник — релизы GPU Selector (github.com/BlueSkyDefender/GPUSelector/releases). При установке GDM отключать встроенный Generic Depth в ReShade.ini.
3. **Переключение очков в 3D-режим** из плагина при запуске игры в 3D и обратно в 2D при выходе (фронтенд: уведомления о запуске/закрытии игры). VITURE XR Glasses SDK для Linux: `xr_device_provider_switch_dimension(handle, 1|0)`, режимы 0x31 1920×1080@60 / 0x32 3840×1080@60. SDK закрытый — скачивать на машине пользователя, нужен доступ к hidraw (udev-правило на 35ca или root). Проверить, как gamescope переживает смену режима 1920→3840.
3a. **Значок на постерах в сетке библиотеки** (сейчас только на странице игры) — патч компонентов сетки или CSS-инъекция по appid в URL обложки.
3b. **Разрешение игры 1920×1080 в 3D** выставлять автоматически (сейчас вручную), иначе half-SBS сплющен.
4. **Автоматический режим масштабирования gamescope** «Растянуть» на время игры с 3D.
5. **Пресет под Full SBS**: SuperDepth3D сейчас в half-SBS + растяжение gamescope. Проверить `DoubleBuffer_Mode` и вывод игры сразу в 3840×1080.

## Дальше
- **Vulkan-игры**: нативный порт ReShade под Linux Vulkan/Wayland (TheForgotten69/reshade, бета) или VKIntox (форк vkShade с рабочим depth). Оба — в стадии эксперимента.
- **Универсальное 2D→3D без буфера глубины** (AI-оценка глубины по кадру) как запасной вариант для игр без профиля:
  - VitureBD/braindance — готовый пайплайн под VITURE на Linux, но X11 + NVIDIA/TensorRT. Для RX 9060 XT нужно портировать на ROCm / MIGraphX / ONNX Runtime или Vulkan compute.
  - Desktop2Stereo — поддерживает AMD, Ubuntu, Depth-Anything-V2-Small.
  - Встраивание в gamescope или Vulkan-слой (по образцу lsfg-vk / decky-lsfg-vk) — основной объём работы.
- **Публикация в магазине Decky** — только после согласования с автором Depth3D (BlueSkyDefender), т.к. плагин автоматизирует загрузку его шейдеров.

## Ключевые выводы исследования
- Псевдо-3D на основе буфера глубины (SuperDepth3D, Rendepth) работает под Proton через ReShade для DX9–DX11 уверенно, DX12 — в большинстве случаев (ReShade 6.8+ с аддонами).
- gamescope `--reshade-effect` не даёт доступа к буферу глубины. vkBasalt — depth capture экспериментальный и ненадёжный.
- Настройка под игру нужна из-за: перевёрнутой/инвертированной глубины, нескольких depth-буферов, апскейлеров/frame gen, рук с оружием в FPS, постэффектов (DoF, motion blur, CA). Overwatch закрывает это для ~1000 игр автоматически.
- Онлайн-игры с античитом — риск; Overwatch помечает их флагом NDW.

## Источники
- Depth3D: https://github.com/BlueSkyDefender/Depth3D
- ReShade (хэш `__APPLICATION__`, source/runtime.cpp): https://github.com/crosire/reshade
- reshade-steam-proton (установка под Proton): https://github.com/kevinlekiller/reshade-steam-proton
- decky-XRGaming / Breezy: https://github.com/wheaney/decky-XRGaming
- VitureBD (braindance): https://github.com/zerobitfulladder/VitureBD
- Rendepth ReShade: https://github.com/outmode/rendepth-reshade
- VKIntox: https://github.com/buwryme/VKIntox
- Нативный ReShade для Linux: https://github.com/TheForgotten69/reshade
- Desktop2Stereo: https://github.com/lc700x/desktop2stereo
- decky-lsfg-vk (пример Vulkan-слоя через Decky): https://github.com/xXJSONDeruloXx/decky-lsfg-vk
