"""Puts the machine back the way it was when the plugin is uninstalled.

The backend's _unload/_uninstall hooks are not enough: Decky also "uninstalls"
before installing a new version, and it kills a plugin that does not stop
within 5 seconds, so they may never run. Instead every plugin start launches a
small detached guardian (start_guardian). It watches the plugin folder; when the
folder is gone and does not come back within a grace period (an update unzips
the new version within a second or two), the plugin was uninstalled: every game
gets its original files back, the gamescope script is removed and so are the
plugin's settings and data folders. A newer guardian replaces an older one.

If Steam still has the launch wrapper in some game's launch options (the
frontend normally removes it when the plugin unloads), the wrapper is replaced
by a pass-through instead of being deleted, so that game keeps starting.

Steam's per-game resolution and scaling are restored by the frontend after
every 3D session, and outside a 3D session ReShade is parked, so at rest a game
folder already looks as it did without the plugin.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from depth3d import gamescope, installer  # noqa: E402
from depth3d.launch import GAMES_FILE, WRAPPER_NAME  # noqa: E402
from depth3d.scanner import MARKER  # noqa: E402

SCAN_CACHE = "scan.json"
UPDATE_GRACE_S = 20   # an update unzips the new version within a second or two
POLL_S = 2
LOG_NAME = "depth3d-glasses-cleanup.log"
PID_NAME = "depth3d-glasses-guardian.pid"
PASS_THROUGH = "#!/bin/bash\n# Depth3D Glasses was uninstalled; this only starts the game.\nshift\nexec \"$@\"\n"


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def installed_exes(settings_dir: str) -> list:
    """Executables of every game that still has the plugin's marker next to it."""
    exes = {g.get("exe") for g in _read_json(os.path.join(settings_dir, GAMES_FILE), {}).values()}
    for entry in _read_json(os.path.join(settings_dir, SCAN_CACHE), {}).get("apps", {}).values():
        m = (entry or {}).get("match")
        if m:
            exes.add(m.get("exe"))
    return sorted(e for e in exes if e and os.path.isfile(os.path.join(os.path.dirname(e), MARKER)))


def park_all(settings_dir: str) -> int:
    """Plugin unloaded: every game starts without ReShade until the plugin is back."""
    n = 0
    for exe in installed_exes(settings_dir):
        try:
            if installer.set_active(exe, False):
                n += 1
        except Exception:  # noqa: BLE001 - one broken folder must not stop the rest
            pass
    return n


def _owned_dir(path: str, home: str) -> bool:
    """Only the plugin's own folders under ~/homebrew/{settings,data} are ever deleted."""
    real = os.path.realpath(path)
    parent = os.path.dirname(real)
    allowed = {os.path.realpath(os.path.join(home, "homebrew", d)) for d in ("settings", "data")}
    return parent in allowed and os.path.basename(real) not in ("", ".", "..")


def wrapper_still_referenced(home: str) -> bool:
    """Does any Steam account still have the launch wrapper in a game's launch options?"""
    from depth3d.steamlib import steam_roots
    needle = WRAPPER_NAME.encode()
    for root in steam_roots(home):
        try:
            accounts = os.listdir(os.path.join(root, "userdata"))
        except OSError:
            continue
        for acc in accounts:
            for name in ("localconfig.vdf", "shortcuts.vdf"):
                try:
                    with open(os.path.join(root, "userdata", acc, "config", name), "rb") as f:
                        if needle in f.read():
                            return True
                except OSError:
                    continue
    return False


def full_cleanup(settings_dir: str, runtime_dir: str, home: str) -> list:
    log = []
    for exe in installed_exes(settings_dir):
        try:
            installer.uninstall(exe)
            log.append(f"restored {os.path.dirname(exe)}")
        except Exception as e:  # noqa: BLE001
            log.append(f"could not restore {exe}: {e}")
    script = gamescope.script_path(home)
    if os.path.isfile(script):
        os.remove(script)
        log.append(f"removed {script}")
    keep_wrapper = wrapper_still_referenced(home)
    if os.path.isdir(settings_dir) and _owned_dir(settings_dir, home):
        shutil.rmtree(settings_dir, ignore_errors=True)
        log.append(f"removed {settings_dir}")
    if os.path.isdir(runtime_dir) and _owned_dir(runtime_dir, home):
        shutil.rmtree(runtime_dir, ignore_errors=True)
        log.append(f"removed {runtime_dir}")
        if keep_wrapper:
            # Steam still points a game at the wrapper: leave a stub that just starts it.
            path = os.path.join(runtime_dir, "bin", WRAPPER_NAME)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(PASS_THROUGH)
            os.chmod(path, 0o755)
            log.append(f"launch options still use the wrapper: left a pass-through at {path}")
    return log


def _pid_file() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(runtime if os.path.isdir(runtime) else tempfile.gettempdir(), PID_NAME)


def start_guardian(plugin_dir: str, settings_dir: str, runtime_dir: str, home: str, env: dict) -> None:
    """Runs guard() in a detached process, from a copy of this package (the plugin folder may vanish)."""
    tmp = tempfile.mkdtemp(prefix="depth3d-glasses-guardian-")
    shutil.copytree(os.path.dirname(os.path.abspath(__file__)), os.path.join(tmp, "depth3d"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from depth3d.cleanup import main; main(sys.argv[2:])")
    subprocess.Popen(["python3", "-I", "-c", code, tmp, plugin_dir, settings_dir, runtime_dir, home, tmp],
                     env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def guard(plugin_dir: str, settings_dir: str, runtime_dir: str, home: str, pid_file: Optional[str] = None,
          grace: float = UPDATE_GRACE_S, step: float = POLL_S) -> list:
    """Waits for the plugin folder to go away for good, then cleans up. Returns what it did."""
    pid_file = pid_file or _pid_file()
    me = str(os.getpid()) + ":" + str(time.time())
    with open(pid_file, "w") as f:
        f.write(me)

    def replaced() -> bool:
        try:
            with open(pid_file) as f:
                return f.read() != me
        except OSError:
            return True

    main_py = os.path.join(plugin_dir, "main.py")
    while True:
        if replaced():
            return ["a newer guardian took over"]
        if os.path.exists(main_py):
            time.sleep(step)
            continue
        deadline = time.time() + grace
        while time.time() < deadline and not os.path.exists(main_py):
            time.sleep(min(step, 0.5))
        if os.path.exists(main_py):
            continue  # an update: the new version is in place
        lines = full_cleanup(settings_dir, runtime_dir, home)
        if not replaced():
            try:
                os.remove(pid_file)
            except OSError:
                pass
        return lines


def main(argv) -> None:
    plugin_dir, settings_dir, runtime_dir, home, tmp = argv
    try:
        lines = guard(plugin_dir, settings_dir, runtime_dir, home)
    except Exception as e:  # noqa: BLE001
        lines = [f"cleanup failed: {e}"]
    if lines and lines != ["a newer guardian took over"]:
        try:
            with open(os.path.join(tempfile.gettempdir(), LOG_NAME), "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except OSError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
