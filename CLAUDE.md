# CLAUDE.md — project context

A Decky Loader plugin for SteamOS / Steam Game Mode on Linux (Ubuntu + gamescope-session). Goal: mark games that have a
ready Depth3D profile (SuperDepth3D / Overwatch.fxh) and install ReShade + SuperDepth3D into them, to play in pseudo-3D (SBS)
on XR glasses.

## Hardware it was built on
- XR glasses: VITURE Luma Pro (3D mode = 3840×1080, full SBS: 1920×1080 per eye)
- GPU: AMD Radeon RX 9060 XT (RADV, no CUDA/TensorRT)
- Developed on macOS (arm64), run on a Linux machine with Steam

## Layout
- `main.py` — Decky backend (`Plugin` class; the frontend calls its methods through `callable`)
- `py_modules/depth3d/` — logic without Decky dependencies (standard library only):
  - `hashing.py` — ReShade's `__APPLICATION__` = `std::hash<std::string>(exe_stem) & 0xFFFFFFFF`; with MSVC that is FNV-1a:
    64-bit game → FNV-1a 64 truncated to 32 bits, 32-bit game → FNV-1a 32. Checked against hashes in the real Overwatch.fxh.
  - `pe.py` — exe bitness and imports (choosing dxgi / d3d9 / opengl32 / vulkan)
  - `overwatch.py` — parser of `#elif (App == 0x...) //Name` blocks, NPW / NDW / NCW flags
  - `steamlib.py` — libraryfolders.vdf + appmanifest_*.acf + binary shortcuts.vdf (the active account's non-Steam games, `.exe` only)
  - `display.py` — active display: the EDID from `GAMESCOPE_PATCHED_EDID_FILE` (through /proc/<gamescope>/environ, else
    `~/.config/gamescope/edid.bin`) is matched against `/sys/class/drm`; glasses by EDID name or by the user's mark
  - `gamescope.py` — SBS for glasses whose EDID has no 3840×1080 (VITURE Luma Pro: R1 toggles 3D, the EDID does not change).
    The Lua script `~/.config/gamescope/scripts/depth3d-glasses.lua` describes the glasses as a known display with
    dynamic_refresh_rates (the 1920×1080 rates from the EDID) and dynamic_modegen: the marker rate 59 (also in the list) →
    3840×1080@60 with "two CEA 1080p60 side by side" timing (4400×1125, 297 MHz; CVT-RB gave a band of garbage at the top
    on the Luma Pro), anything else → plain 1080p. While a 3D game runs, the wrapper asks gamescope for refresh cycle 59
    through the Wayland protocol `gamescope_control.set_app_target_refresh_cycle` (flags allow_refresh_switching |
    only_change_refresh_rate, a stdlib client in `gamescope.py`), repeating every 1 s for the first 30 s and then every 3 s
    (Steam resets the cycle when the game window appears and on focus changes); after exit it sends 0. The X11 property
    `GAMESCOPE_DYNAMIC_REFRESH_EXTERNAL` does not work in 3.16: gamescope overwrites it every frame. Readiness is checked with
    gamescope itself: the `gamescope_control.active_display_info` event (the active display's rate list contains 59 → the
    script is loaded); without the protocol, by the script file's time. gamescope reads scripts only at start — a session
    restart is needed (a panel button, `systemctl --user restart gamescope-session…`).
  - `launch.py` — the wrapper `depth3d-run <appid> %command%` (written to `DECKY_PLUGIN_RUNTIME_DIR/bin` at start) and the
    3D/2D decision. 2D = ReShade DLLs parked as `*.depth3d-off` (`installer.set_active`, the `active` flag in the marker), the
    environment untouched; the fallback `2d-profile` = `RESHADE_BASE_PATH_OVERRIDE` to the empty profile `reshade-2d/` when
    the folder cannot be changed. The wrapper calls the system `python3` without Steam's `LD_LIBRARY_PATH`
  - `scanner.py` — matching exes with profiles, the `depth3d-glasses.json` marker next to the exe
  - `assets.py` — downloading ReShade (reshade.me), d3dcompiler_47 (from the Electron 33.4.11 zip by range requests, SHA-256;
    fallback Firefox 62 + 7z), the Depth3D shaders (raw.githubusercontent)
  - `installer.py` — laying out files in the game folder with `*.depth3d-bak` backups, ReShade.ini, ReShadePreset.ini
- `src/` — frontend (React, @decky/ui, @decky/api)
  - `sbs.ts` — Steam output at 3840×1080 while the glasses are in 3D (`SystemDisplayManagerStore.SetMode`), and the game's
    settings for a 3D launch: `SetAppResolutionOverride` 1920×1080 (hook `RegisterForGameActionStart`) and Stretch=5 scaling
    through `SystemPerfStore` (after `RegisterForAppLifetimeNotifications`). Previous values are kept in `games.json` → `steam`.
  - `libraryPatch.tsx` — the glasses badge on the game page (routerHook.addPatch on `/library/app/:appid`, the ProtonDB Badges
    approach; fragile across Steam updates)
  - `GameModal.tsx`, `index.tsx`, `steam.ts` (SetAppLaunchOptions, collectionStore), `launchOptions.ts` (pure functions)
  - `i18n.ts` — `t(english, russian)` / `tr([english, russian])`: Russian when the Steam client runs in Russian, else English

## Commands
```bash
pnpm install
pnpm run typecheck
pnpm run build                 # → dist/index.js
python3 tests/test_backend.py  # backend tests (no network, fake Steam library)
OVERWATCH=/path/Overwatch.fxh python3 tests/test_backend.py   # + a check against the real Overwatch
npx tsc src/launchOptions.ts src/ids.ts --outDir /tmp/lo --module es2020 --target es2020 --skipLibCheck --typeRoots ./none && node tests/launchOptions.test.mjs /tmp/lo
./scripts/package.sh           # → out/depth3d-glasses.zip for Decky's "Install from ZIP"
./scripts/deploy.sh user@host  # build and install over SSH without sudo (through the Decky API and Steam's CEF debugger)
```

## Rules
- **Depth3D is proprietary** (free for personal use, no redistribution). Never commit SuperDepth3D.fx / Overwatch.fxh /
  Sentinel.fxh / AXAA.fxh or put them into a build. Download on the user's machine only. Tests use a synthetic Overwatch.
- The same for ReShade DLLs and d3dcompiler_47.dll — never commit them.
- The Python backend runs in the Python Decky ships (PyInstaller): standard library only, no pip dependencies. Clear
  `LD_LIBRARY_PATH` for external tools (`7z`) (see `assets._clean_env`).
- The plugin must stay generic (it is published): nothing tied to one machine or its scripts; no machine-specific data in the
  repository. The backend runs as the user (no root flag).
- State: `settings/games.json` (appid → exe, dll, enabled), `settings/settings.json` (+ `glasses_names` / `not_glasses_names`),
  `settings/scan.json` (scan cache, key `stamp`). The wrapper's decision log — `data/launch.log`.
- Removing from a game folder touches only the files listed in the marker and only in that folder.
- At rest a game is "vanilla": after install and after every 3D session the DLLs are parked (`*.depth3d-off`, the `active`
  flag in the marker); the wrapper enables them only for a 3D launch. The frontend restores the game's Steam settings
  (resolution, scaling) when the game closes.
- Uninstalling the plugin: `_unload`/`_uninstall` are unreliable (Decky also calls uninstall on a store update, and a plugin
  that does not stop within 5 s is SIGKILLed — seen on real hardware). So `_main` starts a guardian
  (`cleanup.start_guardian`, its own session, a copy of the package in /tmp, a pid file in `$XDG_RUNTIME_DIR`): the plugin
  folder gone and not back within 20 s → `full_cleanup`. The frontend removes the wrapper from launch options in
  `onDismount` and adds it back on load.

## Known limits / not yet verified on hardware
- The backend was checked on real hardware without Decky (a `decky` stub): scanning Steam + non-Steam games, display
  detection, downloading ReShade 6.8.0 and d3dcompiler_47, the wrapper.
- Not verified in a live Steam: the badge, the collection, launch options of non-Steam games, that DLL parking and the
  `RESHADE_BASE_PATH_OVERRIDE` fallback give clean 2D under Proton.
- Verified on hardware: a real plugin uninstall through Decky — the guardian restored a game's files within ~20 s, removed
  the gamescope script and the plugin folders, the frontend took the wrapper out of the launch options. That added removing
  `ReShade.log` and restoring Steam settings in `onDismount`.
- Verified on hardware: the script loads (`Got known display: depth3d_glasses_1`, ValidRefreshRates 60, 90, 120). The SBS
  output inside a game is not verified yet.
- gamescope picks its mode like this: `-W/-H/-r` → the saved mode from `GAMESCOPE_MODE_SAVE_FILE` (Steam writes it) → the
  EDID's preferred mode. So the glasses switching to 3D does not by itself give 3840×1080 if 1920×1080 is still in their list.
- Switching the glasses between 2D and 3D is manual for now. Plan: the official VITURE SDK (`libglasses.so`,
  `xr_device_provider_switch_dimension`), needs hidraw access.
- Vulkan games under Proton: ReShade does not work (a ReShade-under-Wine limitation).
- DX12 needs ReShade 6.8+ with add-ons.

More on where this is heading — `docs/ROADMAP.md`.
