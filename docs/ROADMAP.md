# Roadmap and research

## State of v0.1.0
- [x] Scanning the Steam library and matching it with Overwatch profiles (995 hashes in Overwatch v5.0.8)
- [x] Glasses badge on the game page, list in the panel, the "3D Glasses" collection
- [x] Installing/removing ReShade + SuperDepth3D, launch options
- [x] Backend tests (hashes, PE, VDF, parser, install/remove)
- [ ] First run on a real machine (Ubuntu + gamescope + RX 9060 XT + Luma Pro)

## State of v0.2.0
- [x] Non-Steam games from `shortcuts.vdf`, automatic scan when Steam starts
- [x] Fixed API detection for Unreal Engine games (they import d3d9 next to d3d11/12)
- [x] Active display detection (glasses / monitor), always 2D on a monitor
- [x] Per-game 3D switch through the launch wrapper, without moving files around
- [x] d3dcompiler_47 without 7-Zip
- [x] English/Russian UI following Steam's language

## Next
1. **Hardware checks**: ReShade/d3dcompiler download, the badge, the collection, a real game with a profile (DX11 and DX12).
2. **Generic Depth Mod (GDM)** — a Depth3D add-on with better depth buffer selection and a separate buffer for weapon hands.
   Not installed yet. Source: the GPU Selector releases (github.com/BlueSkyDefender/GPUSelector/releases). When GDM is
   installed, turn off ReShade's built-in Generic Depth in ReShade.ini.
3. **Switching the glasses to 3D** from the plugin when a game starts in 3D, and back to 2D on exit (frontend: game
   start/exit notifications). VITURE XR Glasses SDK for Linux: `xr_device_provider_switch_dimension(handle, 1|0)`, modes
   0x31 1920×1080@60 / 0x32 3840×1080@60. The SDK is closed — download it on the user's machine; needs hidraw access (a udev
   rule for 35ca, or root). Check how gamescope survives the 1920→3840 mode change.
   3a. **Badge on the library grid posters** (now only on the game page) — patch the grid components, or CSS injection by
   the appid in the cover URL.
   3b. **Game resolution 1920×1080 in 3D** set automatically (manual for now), otherwise half-SBS is squashed.
4. **gamescope's "Stretch" scaling** set automatically for a 3D game.
5. **A Full SBS preset**: SuperDepth3D now runs half-SBS + gamescope stretching. Check `DoubleBuffer_Mode` and having the
   game output 3840×1080 directly.

## Later
- **Vulkan games**: the native Linux Vulkan/Wayland port of ReShade (TheForgotten69/reshade, beta) or VKIntox (a vkShade fork
  with working depth). Both are experimental.
- **Universal 2D→3D without a depth buffer** (AI depth estimation per frame) as a fallback for games without a profile:
  - VitureBD/braindance — a ready pipeline for VITURE on Linux, but X11 + NVIDIA/TensorRT. An RX 9060 XT needs a port to
    ROCm / MIGraphX / ONNX Runtime or Vulkan compute.
  - Desktop2Stereo — supports AMD, Ubuntu, Depth-Anything-V2-Small.
  - Embedding it into gamescope or a Vulkan layer (like lsfg-vk / decky-lsfg-vk) — the bulk of the work.
- **Publishing in the Decky store** — only after agreeing with the Depth3D author (BlueSkyDefender), since the plugin
  automates downloading his shaders.

## Key research findings
- Depth-buffer pseudo-3D (SuperDepth3D, Rendepth) works under Proton through ReShade reliably for DX9–DX11, and in most cases
  for DX12 (ReShade 6.8+ with add-ons).
- gamescope's `--reshade-effect` gives no access to the depth buffer. vkBasalt's depth capture is experimental and unreliable.
- Per-game tuning is needed because of: flipped/inverted depth, several depth buffers, upscalers/frame generation, weapon
  hands in FPS games, post effects (DoF, motion blur, CA). Overwatch covers this automatically for ~1000 games.
- Online games with anti-cheat are a risk; Overwatch marks them with the NDW flag.

## Sources
- Depth3D: https://github.com/BlueSkyDefender/Depth3D
- ReShade (the `__APPLICATION__` hash, source/runtime.cpp): https://github.com/crosire/reshade
- reshade-steam-proton (installing under Proton): https://github.com/kevinlekiller/reshade-steam-proton
- decky-XRGaming / Breezy: https://github.com/wheaney/decky-XRGaming
- VitureBD (braindance): https://github.com/zerobitfulladder/VitureBD
- Rendepth ReShade: https://github.com/outmode/rendepth-reshade
- VKIntox: https://github.com/buwryme/VKIntox
- Native ReShade for Linux: https://github.com/TheForgotten69/reshade
- Desktop2Stereo: https://github.com/lc700x/desktop2stereo
- decky-lsfg-vk (an example of a Vulkan layer through Decky): https://github.com/xXJSONDeruloXx/decky-lsfg-vk
