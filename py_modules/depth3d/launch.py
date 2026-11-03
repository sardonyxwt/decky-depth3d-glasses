"""Per-launch choice between 3D and 2D.

Steam launch options of an installed game call a small wrapper:

    "<runtime>/bin/depth3d-run" <appid> %command%

The wrapper asks this module (with the system python3, outside Steam's
runtime libraries) what to do, sets the environment and runs the game:

  3D  the game has 3D switched on and the active display is a pair of glasses:
      ReShade's DLLs are put in place and load the SuperDepth3D preset. When
      the glasses' EDID has no 3840-wide mode (VITURE), the wrapper also asks
      gamescope for its script-generated SBS mode while the game runs
      (see gamescope.py); without the script loaded the start falls back to 2D.
  2D  3D is switched off for the game or a regular monitor is active:
      ReShade's DLLs are parked (*.depth3d-off), the game's own files are back,
      and nothing is added to the environment: the game runs exactly as
      without the plugin, with no ReShade and no performance cost.
  2D with an empty profile (fallback, only when the DLLs cannot be moved):
      ReShade loads, but RESHADE_BASE_PATH_OVERRIDE points it to an empty
      profile, so the picture is still the plain game.

The choice is made on every start, so it works even when Decky is not running.
"""

import json
import os
import shlex
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from depth3d import gamescope  # noqa: E402
from depth3d.display import active_display  # noqa: E402

GAMES_FILE = "games.json"
SETTINGS_FILE = "settings.json"
WRAPPER_NAME = "depth3d-run"
PROFILE_2D = "reshade-2d"
LOG_FILE = "launch.log"
LOG_LIMIT = 64 * 1024

WRAPPER_TEMPLATE = r"""#!/bin/bash
# Depth3D Glasses launch wrapper, written by the plugin. Do not edit.
# Steam launch options: "<this file>" <appid> %command%
# Chooses 3D or 2D for this start, then runs the game with Steam's environment.
appid=$1
shift
plugin() {
	env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONHOME -u PYTHONPATH \
		python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from depth3d.launch import main; main(sys.argv[2:])' \
		@PYDIR@ "$@" 2>>@LOG@
}
decision=$(plugin decide "$appid" @SETTINGS@ @RUNTIME@ @HOME@)
mode= dll= base2d= sbs=
while IFS='=' read -r k v; do
	case $k in
		MODE) mode=$v ;;
		DLL) dll=$v ;;
		BASE2D) base2d=$v ;;
		SBS) sbs=$v ;;
	esac
done <<< "$decision"
# 2d and off: nothing is added, the game starts exactly as without the plugin.
if [[ $mode == 2d-profile && -n $dll ]]; then
	export WINEDLLOVERRIDES="d3dcompiler_47=n;$dll=n,b${WINEDLLOVERRIDES:+;$WINEDLLOVERRIDES}"
	export RESHADE_BASE_PATH_OVERRIDE=$base2d
fi
[[ $mode == 3d && -n $dll ]] || exec "$@"

export WINEDLLOVERRIDES="d3dcompiler_47=n;$dll=n,b${WINEDLLOVERRIDES:+;$WINEDLLOVERRIDES}"
unset RESHADE_BASE_PATH_OVERRIDE
again=
if [[ $sbs == gamescope ]]; then
	# The glasses need a 3840x1080 signal while the game runs; gamescope makes it on request.
	plugin sbs on @RUNTIME@
	# Steam sends its own refresh cycle when the game window appears (and when its frame
	# limit changes): keep asking, every second at first, then every few seconds.
	( for i in $(seq 1 30); do sleep 1; plugin sbs on @RUNTIME@ quiet; done
	  while sleep 3; do plugin sbs on @RUNTIME@ quiet; done ) &
	again=$!
fi
# After the game: back to 2D output and ReShade parked, so at rest the game folder
# is as it was without the plugin.
trap '[[ -n $again ]] && { kill $again 2>/dev/null; plugin sbs off @RUNTIME@; }; plugin park "$appid" @SETTINGS@ @RUNTIME@' EXIT
"$@"
"""


def wine_path(linux_path: str) -> str:
    """Proton maps the Linux root to drive Z:."""
    return "Z:" + os.path.abspath(linux_path).replace("/", "\\")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _log(runtime_dir: str, line: str) -> None:
    path = os.path.join(runtime_dir, LOG_FILE)
    try:
        if os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + ".1")
    except OSError:
        pass
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def decide(appid: str, settings_dir: str, runtime_dir: str, home: str, **display_kw) -> dict:
    games = _read_json(os.path.join(settings_dir, GAMES_FILE), {})
    game = games.get(str(appid))
    if not game:
        return {"MODE": "off", "REASON": "game is not installed by the plugin"}
    settings = _read_json(os.path.join(settings_dir, SETTINGS_FILE), {})
    disp = active_display(home, settings.get("glasses_names", []), settings.get("not_glasses_names", []),
                          **display_kw)
    if not game.get("enabled", True):
        mode, reason = "2d", "3D is switched off for this game"
    elif not disp["is_glasses"]:
        mode, reason = "2d", f"active display is not glasses ({disp['name'] or 'none'})"
    else:
        mode, reason = "3d", f"glasses: {disp['name']} on {disp['connector']}"
    sbs = "edid" if disp["sbs"] else "gamescope"
    if mode == "3d" and sbs == "gamescope" and not gamescope_ready(home, disp, **display_kw):
        mode, reason = "2d", (f"glasses {disp['name']} need the gamescope SBS script; "
                              "restart the gamescope session once")
    mode, reason = _apply_files(game.get("exe", ""), mode, reason)
    return {
        "MODE": mode,
        "DLL": game.get("dll", "dxgi"),
        "BASE2D": wine_path(os.path.join(runtime_dir, PROFILE_2D)),
        "SBS": sbs,
        "REASON": reason,
    }


def gamescope_ready(home: str, disp: dict, proc_dir: str = "/proc", **_) -> bool:
    """The running gamescope has loaded a script that knows these glasses."""
    if proc_dir == "/proc":  # the live system: gamescope itself says which rates the glasses have
        ready = gamescope.sbs_mode_ready(disp["name"])
        if ready is not None:
            return ready
    if gamescope.script_loaded(home, proc_dir) is not True:
        return False
    try:
        with open(gamescope.script_path(home), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    return f'model = "{disp["name"].lower()}"' in text


def _apply_files(exe: str, mode: str, reason: str):
    """Moves ReShade's DLLs to match the mode. Falls back so the game always starts."""
    from depth3d.installer import read_marker, set_active  # installer imports this module

    try:
        set_active(exe, mode == "3d")
        return mode, reason
    except Exception as e:  # noqa: BLE001
        active = bool(read_marker(exe).get("active", True)) if exe else False
        if mode == "2d" and active:
            return "2d-profile", f"{reason}; DLLs not parked ({e}), empty ReShade profile"
        if mode == "3d" and not active:
            return "2d", f"{reason}; DLLs not restored ({e}), plain 2D"
        return mode, f"{reason}; {e}"


def write_wrapper(runtime_dir: str, settings_dir: str, home: str, py_dir: str) -> str:
    bin_dir = os.path.join(runtime_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, WRAPPER_NAME)
    text = (WRAPPER_TEMPLATE
            .replace("@PYDIR@", shlex.quote(py_dir))
            .replace("@SETTINGS@", shlex.quote(settings_dir))
            .replace("@RUNTIME@", shlex.quote(runtime_dir))
            .replace("@HOME@", shlex.quote(home))
            .replace("@LOG@", shlex.quote(os.path.join(runtime_dir, LOG_FILE))))
    old = _read_text(path)
    if old != text:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    return path


def write_2d_profile(runtime_dir: str) -> str:
    """ReShade base folder with no effects: the game looks exactly as without ReShade."""
    d = os.path.join(runtime_dir, PROFILE_2D)
    os.makedirs(d, exist_ok=True)
    files = {
        "ReShade.ini": "\r\n".join([
            "[GENERAL]",
            "EffectSearchPaths=",
            "TextureSearchPaths=",
            "PresetPath=.\\ReShadePreset.ini",
            "SkipLoadingDisabledEffects=1",
            "",
            "[INPUT]",
            "KeyOverlay=36,0,0,0",
            "",
            "[OVERLAY]",
            "TutorialProgress=4",
            "",
        ]),
        "ReShadePreset.ini": "Techniques=\r\nTechniqueSorting=\r\n",
    }
    for name, text in files.items():
        p = os.path.join(d, name)
        if _read_text(p) is None:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)
    return d


def _read_text(path: str):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except OSError:
        return None


def launch_token(wrapper: str, appid: int) -> str:
    """What goes in front of %command% in the game's launch options."""
    return f'"{wrapper}" {int(appid)}'


def main(argv) -> None:
    if len(argv) in (3, 4) and argv[0] == "sbs":
        state, runtime_dir, quiet = argv[1], argv[2], argv[3:] == ["quiet"]
        try:
            gamescope.set_sbs_output(state == "on")
            if not quiet:
                _log(runtime_dir, f"sbs output {state}")
        except Exception as e:  # noqa: BLE001
            _log(runtime_dir, f"sbs output {state} failed: {e}")
        return
    if len(argv) == 4 and argv[0] == "park":
        _, appid, settings_dir, runtime_dir = argv
        exe = _read_json(os.path.join(settings_dir, GAMES_FILE), {}).get(str(appid), {}).get("exe")
        try:
            if exe:
                from depth3d.installer import set_active
                set_active(exe, False)
                _log(runtime_dir, f"app {appid}: ReShade parked after the game")
        except Exception as e:  # noqa: BLE001
            _log(runtime_dir, f"app {appid}: parking failed: {e}")
        return
    if len(argv) == 5 and argv[0] == "decide":
        _, appid, settings_dir, runtime_dir, home = argv
        try:
            d = decide(appid, settings_dir, runtime_dir, home)
        except Exception as e:  # noqa: BLE001 - the game must start whatever happens
            d = {"MODE": "off", "REASON": f"error: {e}"}
        _log(runtime_dir, f"app {appid}: {d['MODE']} ({d['REASON']})")
        for k, v in d.items():
            print(f"{k}={v}")
        return
    print("usage: launch.py decide <appid> <settings_dir> <runtime_dir> <home>", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
