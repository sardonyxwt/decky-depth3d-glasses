# Depth3D Glasses — a Decky Loader plugin

Play ordinary PC games in pseudo-3D (side-by-side) on XR glasses such as the VITURE Luma Pro, from Steam's Game Mode.
The plugin marks games in the Steam library that have a ready [Depth3D / SuperDepth3D](https://github.com/BlueSkyDefender/Depth3D)
profile and installs ReShade + SuperDepth3D into them with one button, for SBS 3D on any glasses with a 3840×1080 mode.
The UI follows Steam's language (English or Russian).

## What it does

- **Scans installed games — Steam and non-Steam.** Reads `libraryfolders.vdf`, `appmanifest_*.acf` and the active account's
  `shortcuts.vdf` (Windows games added to Steam as non-Steam games and run through Proton), finds every `.exe`, computes for
  each the same hash ReShade puts into `__APPLICATION__`, and looks it up in the profile table of `Overwatch.fxh`.
- **The list updates itself** when Steam starts (only new and changed games, instant). The "Update game list" button in the
  panel is for a game you have just added.
- **Glasses icon on the game page.** Its colour shows the status: green — a profile exists; yellow — a profile exists but the
  game is online; red — Depth3D marked the game incompatible; grey — the game is known but has no profile; blue — 3D is
  installed. Pressing it opens the install dialog.
- **List in the side panel**, and a "Build the 3D Glasses collection" button — a library tab with every game that has a profile.
- **Installing into a game.** Puts `dxgi.dll` / `d3d9.dll` / `opengl32.dll` (ReShade of the right bitness), `d3dcompiler_47.dll`,
  `ReShade.ini` and `ReShadePreset.ini` with SuperDepth3D enabled in Side-by-Side mode next to the exe, and adds the plugin's
  wrapper to the launch options: `"~/homebrew/data/depth3d-glasses/bin/depth3d-run" <appid> %command%`. Files of the same
  name that were already there are kept as `*.depth3d-bak` and restored on removal.
- **3D or 2D on every launch.** The wrapper checks whether 3D is on for the game ("3D in this game" in the game dialog) and
  which display gamescope is driving. Glasses → 3D. A monitor, or 3D off → plain 2D **without ReShade**: before the launch the
  ReShade DLLs are renamed to `*.depth3d-off`, the game's own files (if the plugin kept any) are put back, nothing is added
  to the environment — the game runs as if the plugin were not there, with no performance cost. These are renames only and
  take milliseconds. If the game folder cannot be changed, the fallback loads ReShade with an empty profile through
  `RESHADE_BASE_PATH_OVERRIDE`. Your own launch options (`gamemoderun`, your own `WINEDLLOVERRIDES`, …) are kept.
- **Resolution in 3D** is chosen in the game dialog: 1920×1080 (960 px per eye), 2560×1440 (1280), 3200×1800 (1600) or
  3840×2160 (the full 1920 per eye). SuperDepth3D draws half-SBS, so sharpness in the glasses depends on the render width;
  the output to the glasses stays 3840×1080. It is set only for a 3D launch; in 2D the game's usual resolution is back.
- **SBS signal for glasses without a 3840 mode in their EDID (VITURE).** Such glasses switch to 3D with a button (Luma: hold
  R1) but still report only 1920×1080 to the computer, while they expect 3840×1080. The plugin installs a gamescope script
  (`~/.config/gamescope/scripts/depth3d-glasses.lua`) that can generate a 3840×1080@60 mode and asks gamescope to use it while
  a 3D game runs. After the game the output returns to plain 1920×1080. gamescope loads the script after a Game Mode restart
  (a button in the plugin panel, once).
- **The glasses' 3D mode and Steam.** For glasses that report a 3840-wide mode in 3D themselves, the plugin switches Steam's
  output to 3840×1080 (like Settings → Display → Resolution) and back when the glasses return to 2D. A game launched in 3D
  gets a 1920×1080 resolution and "Stretch" scaling (SuperDepth3D draws half-SBS, each half is stretched to a 1920×1080 eye);
  a 2D launch restores its previous settings.
- **Display detection.** The EDID of gamescope's active output (`GAMESCOPE_PATCHED_EDID_FILE`, by default
  `~/.config/gamescope/edid.bin`) is matched against the connectors in `/sys/class/drm`. Glasses are recognised by the EDID
  name: VITURE, XREAL/Nreal, Rokid, RayNeo. Any other display can be marked in the panel with "These are my XR glasses" (and
  the other way round). With several displays and no hint from gamescope, a monitor is assumed — 3D never goes to a monitor.
- **Removal** returns the game folder to its original state and takes the wrapper out of the launch options.
- **Outside a 3D session everything is original.** ReShade's DLLs stay in the game folder renamed (`*.depth3d-off`) and come
  back only for a 3D launch; the game's resolution and scaling in Steam are restored as soon as the game closes.
- **Uninstalling the plugin cleans up by itself.** While the plugin is loaded, its wrapper is in the launch options of 3D
  games; it is removed when the plugin unloads and added back when it loads. At start the plugin launches a small guardian
  process: if the plugin folder disappears and does not come back within 20 seconds (an update brings it back at once), it
  removes ReShade from all games, restoring their original files, plus the gamescope script and the plugin's settings and
  data folders. If Steam still holds a link to the wrapper, a stub stays in its place that just starts the game.
  Log: `/tmp/depth3d-glasses-cleanup.log`.

### How the profile is found

ReShade sets `__APPLICATION__ = std::hash<std::string>()(exe_name_without_extension) & 0xFFFFFFFF`. With MSVC that is FNV-1a:
64-bit FNV-1a truncated to 32 bits for 64-bit games, 32-bit FNV-1a for 32-bit ones. The bitness comes from the PE header.
Checked against the values in the real `Overwatch.fxh` (Borderlands 2, Oblivion, Cemu, PCSX2 and others).

## Install

Needs Decky Loader in Game Mode (gamescope-session: SteamOS, Bazzite, gamescope-session-plus). Nothing else to install.

Download `depth3d-glasses-<version>.zip` from [Releases](../../releases) and use Decky → Settings → Developer →
**Install Plugin from ZIP**.

Building yourself (Node 18+, pnpm):

```bash
pnpm install
pnpm run build
./scripts/package.sh            # out/depth3d-glasses.zip
./scripts/deploy.sh user@host   # build and install on a Steam machine over SSH (no sudo needed)
```

Or copy the folder to `~/homebrew/plugins/depth3d-glasses/` (needs `dist/`, `main.py`, `plugin.json`, `package.json`,
`py_modules/`, `LICENSE`) and restart Decky: `sudo systemctl restart plugin_loader`.

## Usage

1. Side panel → Depth3D Glasses → "Update game list". The first time the plugin downloads the Depth3D shaders.
2. Open a game with the glasses icon → "Install 3D". The first install downloads ReShade and d3dcompiler_47.
3. Switch the glasses to 3D with their button. Steam switches the output to 3840×1080. On a monitor, or with the glasses in
   2D, the game starts in plain 2D.
4. Start the game: the plugin sets it to 1920×1080 with "Stretch" scaling.
5. The ReShade menu is the Home key. The Overwatch profile loads automatically; usually nothing needs tuning.

## Limitations

- **Games through Proton only**, on DX9/10/11/12 and OpenGL. The Vulkan build of ReShade does not work under Wine: if a game
  has DX11/DX12, switch to it and pick the API in the install dialog.
- **DX12** needs ReShade 6.8+ with add-on support (on by default). Works in most cases, check per game.
- **Online games:** ReShade with add-ons may trip anti-cheat. Such games are marked yellow and can be hidden in the settings.
- **An empty depth buffer** happens with upscalers and frame generation (FSR 4 on an RX 9060 XT too): turn them off in the
  game if 3D does not appear.
- **The icon on the game page** is injected the same way as ProtonDB Badges does and depends on Steam's UI internals. After a
  big client update it may disappear; the plugin panel keeps working.
- **The collection** uses Steam's internal `collectionStore`. If that API changes, the button shows an error; nothing else
  depends on it.
- **Switching the glasses to 3D** is manual for now, with the button on the glasses. The plugin shows which mode the glasses
  are in (by the 3840×1080 mode in the EDID).
- **A forced gamescope resolution** (`-W/-H/-r` in the session's launch options) gets in the way of switching the output:
  gamescope prefers it over the mode chosen in Steam. Remove them for 3D.
- **Display output, game resolution and scaling** are changed through Steam UI stores (`SystemDisplayManagerStore`,
  `SystemPerfStore`); a Steam update may break this.
- **Generic Depth Mod** (better depth buffer selection) is not installed automatically; ReShade's built-in Generic Depth is
  used. GDM can be added by hand from the GPU Selector releases.

## Licenses

The plugin's code is BSD-3-Clause.

The plugin contains **no** Depth3D, ReShade or d3dcompiler files. They are downloaded on your computer from their official
sources:

- SuperDepth3D, Overwatch, AXAA, Sentinel — `raw.githubusercontent.com/BlueSkyDefender/Depth3D`. Depth3D is free for personal
  use but proprietary: it may not be redistributed, including as part of tools, without its author's permission. That is why
  the plugin downloads the files instead of shipping them. Before publishing the plugin, e.g. in the Decky store, contact the
  Depth3D author first.
- ReShade — `reshade.me`.
- `d3dcompiler_47.dll` (a Microsoft redistributable) — from the official Electron 33.4.11 zip on GitHub, SHA-256 checked. Only
  that file is read (HTTP range requests, ~2 MB instead of 110 MB), no 7-Zip needed. Fallback: Mozilla's Firefox 62
  installer, if 7-Zip is installed.

Not affiliated with VITURE, ReShade or BlueSkyDefender.

## Tests

```bash
python3 tests/test_backend.py                                   # hashes, PE, VDF, parser, install/remove on a fake library
OVERWATCH=/path/to/Overwatch.fxh python3 tests/test_backend.py  # plus the real Overwatch (in shaders/ of the plugin's data folder)
node --test tests/launchOptions.test.mjs
```

## Layout

```
main.py                    — API for the frontend (scan, install_game, uninstall_game, …)
py_modules/depth3d/
  hashing.py               — the __APPLICATION__ hash (FNV-1a 32/64)
  pe.py                    — exe bitness and imports → which DLL to install
  overwatch.py             — parser of the Overwatch.fxh profile table
  steamlib.py              — Steam libraries, manifests, shortcuts.vdf (non-Steam games)
  display.py               — gamescope's active display: glasses or monitor
  gamescope.py             — the gamescope script and refresh-cycle client for 3840×1080 on VITURE-type glasses
  launch.py                — the launch wrapper: 3D or 2D on every start
  scanner.py               — matching games with profiles
  assets.py                — downloading ReShade, d3dcompiler_47, the shaders
  installer.py             — install/remove in the game folder, ReShade.ini and the preset
  cleanup.py               — the uninstall guardian
src/
  index.tsx                — side panel
  libraryPatch.tsx         — glasses icon on the game page
  GameModal.tsx            — install dialog
  sbs.ts                   — Steam output and per-game settings for 3D launches
  steam.ts                 — launch options and the collection through SteamClient
  launchOptions.ts         — merging launch options (tested)
  i18n.ts                  — English/Russian UI text
```
