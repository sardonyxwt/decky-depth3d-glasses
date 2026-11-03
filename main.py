import asyncio
import json
import os
import subprocess
import time
import traceback

import decky  # provided by Decky Loader

from depth3d import cleanup, gamescope
from depth3d.assets import Assets, AssetError, _clean_env
from depth3d.display import active_display
from depth3d.installer import InstallError, install, uninstall, read_marker, set_active
from depth3d.launch import GAMES_FILE, launch_token, write_2d_profile, write_wrapper
from depth3d.overwatch import parse_overwatch, overwatch_version
from depth3d.scanner import scan_app, refresh_installed_flag
from depth3d.steamlib import all_games

SCAN_CACHE = "scan.json"
SETTINGS = "settings.json"
DEFAULT_SETTINGS = {
    "reshade_addon": True,       # add-on build: needed for the Generic Depth add-on and newer DX12 fixes
    "show_network_games": True,  # show games Overwatch marks as online (anti-cheat risk)
    "auto_sbs_output": True,     # switch Steam's output to the glasses' 3840-wide mode while they are in 3D
}
# Lists of EDID monitor names the user marked as glasses / not glasses.
DISPLAY_LISTS = ("glasses_names", "not_glasses_names")
DISPLAY_POLL_SECONDS = 3
# Game render resolutions for 3D. SuperDepth3D draws half-SBS, so each eye gets half the width:
# 1920x1080 -> 960 px per eye, 2560x1440 -> 1280, 3200x1800 -> 1600, 3840x2160 -> the full 1920.
SBS_RESOLUTIONS = ("1920x1080", "2560x1440", "3200x1800", "3840x2160")
# gamescope session units of the common distributions, the running one gets restarted
SESSION_UNITS = ("gamescope-session-plus@steam.service", "gamescope-session.service",
                 "gamescope-session-plus.service")


def _err(e: Exception) -> dict:
    decky.logger.error("".join(traceback.format_exception(e)))
    return {"ok": False, "error": str(e)}


class Plugin:
    # ---------------- lifecycle ----------------
    async def _main(self):
        self.lock = asyncio.Lock()
        self.assets = Assets(decky.DECKY_PLUGIN_RUNTIME_DIR)
        self.settings_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, SETTINGS)
        self.cache_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, SCAN_CACHE)
        self.games_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, GAMES_FILE)
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        self._profiles = None
        self._profiles_version = None
        self._display = None
        self.wrapper = write_wrapper(
            decky.DECKY_PLUGIN_RUNTIME_DIR, decky.DECKY_PLUGIN_SETTINGS_DIR, decky.DECKY_USER_HOME,
            os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules"))
        write_2d_profile(decky.DECKY_PLUGIN_RUNTIME_DIR)
        self._display_task = asyncio.get_running_loop().create_task(self._watch_display())
        try:
            cleanup.start_guardian(decky.DECKY_PLUGIN_DIR, decky.DECKY_PLUGIN_SETTINGS_DIR,
                                   decky.DECKY_PLUGIN_RUNTIME_DIR, decky.DECKY_USER_HOME, _clean_env())
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"could not start the uninstall guardian: {e}")
        decky.logger.info("Depth3D Glasses backend started")

    async def _unload(self):
        # Also runs on updates and when Decky stops (if Decky lets it run at all).
        # ReShade is parked outside 3D sessions anyway; this is a second net.
        task = getattr(self, "_display_task", None)
        if task:
            task.cancel()
        try:
            n = cleanup.park_all(decky.DECKY_PLUGIN_SETTINGS_DIR)
            decky.logger.info(f"unload: ReShade parked in {n} game(s)")
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"unload: parking failed: {e}")

    async def _uninstall(self):
        # Nothing here on purpose: Decky also calls this before an update and may
        # kill the plugin before it returns. The guardian started in _main
        # (cleanup.py) restores everything once the plugin folder is gone for good.
        pass

    # ---------------- helpers ----------------
    def _read_json(self, path: str, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _write_json(self, path: str, data) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)

    def _settings(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        s.update({k: [] for k in DISPLAY_LISTS})
        s["glasses_2d_modes"] = {}
        s["sbs_glasses"] = []  # glasses the gamescope SBS script knows: vendor, model, rates
        s.update(self._read_json(self.settings_path, {}))
        return s

    def _games(self) -> dict:
        return self._read_json(self.games_path, {})

    def _load_profiles(self):
        path = self.assets.overwatch_path
        if not os.path.isfile(path):
            return None, None
        mtime = os.path.getmtime(path)
        if self._profiles is None or self._profiles_version != mtime:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            self._profiles = parse_overwatch(text)
            self._profiles_version = mtime
            self._overwatch_label = overwatch_version(text)
        return self._profiles, getattr(self, "_overwatch_label", None)

    def _with_state(self, m: dict, games: dict) -> dict:
        m = refresh_installed_flag(m)
        entry = games.get(str(m["appid"]), {})
        m["enabled"] = bool(entry.get("enabled", True))
        m["steam"] = entry.get("steam")  # Steam settings to restore after a 3D launch
        m["sbs_resolution"] = entry.get("sbs_resolution", SBS_RESOLUTIONS[0])
        if m["installed"]:
            m["launch_token"] = launch_token(self.wrapper, m["appid"])
        return m

    def _cached_games(self) -> list:
        cache = self._read_json(self.cache_path, {})
        games = self._games()
        out = [self._with_state(m, games) for m in
               (e.get("match") for e in cache.get("apps", {}).values()) if m]
        out.sort(key=lambda m: m["name"].lower())
        return out

    def _find_cached(self, appid: int):
        entry = self._read_json(self.cache_path, {}).get("apps", {}).get(str(appid))
        if entry and entry.get("match"):
            return self._with_state(entry["match"], self._games())
        return None

    def _sync_games_file(self, matches: list) -> None:
        """Keeps games.json in step with the markers in the game folders."""
        games = self._games()
        changed = False
        for m in matches:
            key = str(m["appid"])
            if m["installed"] and key not in games:
                marker = read_marker(m["exe"])
                games[key] = {"name": m["name"], "exe": m["exe"], "dll": marker.get("dll", m["dll"]),
                              "enabled": True}
                changed = True
            elif not m["installed"] and key in games and games[key].get("exe") == m["exe"]:
                del games[key]
                changed = True
        if changed:
            self._write_json(self.games_path, games)

    def _scan_blocking(self, force: bool, loop) -> dict:
        profiles, ow_label = self._load_profiles()
        if profiles is None:
            self.assets.update_shaders()
            profiles, ow_label = self._load_profiles()
        cache = self._read_json(self.cache_path, {})
        old = {} if force or cache.get("overwatch") != ow_label else cache.get("apps", {})
        apps = list(all_games(decky.DECKY_USER_HOME))
        new = {}
        for i, app in enumerate(apps):
            prev = old.get(str(app.appid))
            if prev and prev.get("stamp") == app.stamp:
                new[str(app.appid)] = prev
            else:
                m = scan_app(app, profiles)
                new[str(app.appid)] = {"stamp": app.stamp, "match": m.to_dict() if m else None}
            if i % 5 == 0 or i == len(apps) - 1:
                asyncio.run_coroutine_threadsafe(
                    decky.emit("depth3d_scan_progress", i + 1, len(apps), app.name), loop)
        data = {"overwatch": ow_label, "scanned_at": time.time(), "apps": new}
        self._write_json(self.cache_path, data)
        self._sync_games_file([refresh_installed_flag(e["match"]) for e in new.values() if e.get("match")])
        return data

    def _display_now(self) -> dict:
        s = self._settings()
        d = active_display(decky.DECKY_USER_HOME, s["glasses_names"], s["not_glasses_names"])
        d["gamescope_sbs"] = self._gamescope_sbs(d, s)
        return d

    def _gamescope_sbs(self, d: dict, s: dict) -> dict:
        """Glasses without a 3840-wide EDID mode get it from the gamescope script (gamescope.py)."""
        home = decky.DECKY_USER_HOME
        needed = bool(d["is_glasses"] and not d["sbs"] and d["rates_1080"])
        known = s["sbs_glasses"]
        if needed:
            entry = {"vendor": d["vendor"], "model": d["name"], "rates": d["rates_1080"]}
            if entry not in known:
                known = [g for g in known if g["model"].lower() != d["name"].lower()] + [entry]
                saved = self._read_json(self.settings_path, {})
                saved["sbs_glasses"] = known
                self._write_json(self.settings_path, saved)
        if known:
            gamescope.install_script(home, known)
        ready = gamescope.sbs_mode_ready(d["name"]) if needed else False
        if ready is None:  # no answer from gamescope: judge by the script's age
            has_it = any(g["model"].lower() == d["name"].lower() for g in known)
            ready = bool(gamescope.script_loaded(home) and has_it)
        return {"needed": needed, "ready": bool(needed and ready)}

    async def _watch_display(self):
        while True:
            try:
                d = await asyncio.to_thread(self._display_now)
                if d != self._display:
                    self._display = d
                    await decky.emit("depth3d_display", d)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                decky.logger.warning(f"display check failed: {e}")
            await asyncio.sleep(DISPLAY_POLL_SECONDS)

    # ---------------- API for the frontend ----------------
    async def get_state(self) -> dict:
        try:
            _, ow_label = self._load_profiles()
            cache = self._read_json(self.cache_path, {})
            if self._display is None:
                self._display = await asyncio.to_thread(self._display_now)
            return {
                "ok": True,
                "assets": self.assets.summary(),
                "overwatch": ow_label,
                "profiles": len(self._profiles or {}),
                "scanned_at": cache.get("scanned_at"),
                "games": self._cached_games(),
                "settings": self._settings(),
                "display": self._display,
            }
        except Exception as e:  # noqa: BLE001
            return _err(e)

    async def scan(self, force: bool = False) -> dict:
        async with self.lock:
            try:
                loop = asyncio.get_running_loop()
                await asyncio.to_thread(self._scan_blocking, bool(force), loop)
                return {"ok": True, "games": self._cached_games()}
            except Exception as e:  # noqa: BLE001
                return _err(e)

    async def get_game(self, appid: int) -> dict:
        m = self._find_cached(int(appid))
        if not m:
            return {"ok": True, "game": None}
        return {"ok": True, "game": m, "launch_token": launch_token(self.wrapper, m["appid"])}

    async def update_shaders(self) -> dict:
        async with self.lock:
            try:
                await asyncio.to_thread(self.assets.update_shaders)
                self._profiles = None
                return {"ok": True, "assets": self.assets.summary()}
            except Exception as e:  # noqa: BLE001
                return _err(e)

    async def update_reshade(self) -> dict:
        async with self.lock:
            try:
                addon = self._settings()["reshade_addon"]
                path = await asyncio.to_thread(self.assets.ensure_reshade, addon, True)
                return {"ok": True, "reshade": os.path.basename(path)}
            except Exception as e:  # noqa: BLE001
                return _err(e)

    async def install_game(self, appid: int, dll_override: str = "") -> dict:
        async with self.lock:
            try:
                m = self._find_cached(int(appid))
                if not m:
                    raise InstallError("Game is not in the scan results. Run a scan first.")
                addon = self._settings()["reshade_addon"]
                marker = await asyncio.to_thread(install, m, self.assets, dll_override or "", addon)
                # At rest ReShade is parked; the launch wrapper puts it in place for a 3D start.
                await asyncio.to_thread(set_active, m["exe"], False)
                games = self._games()
                games[str(m["appid"])] = {"name": m["name"], "exe": m["exe"], "dll": marker["dll"],
                                          "enabled": True}
                self._write_json(self.games_path, games)
                return {"ok": True, "launch_token": launch_token(self.wrapper, m["appid"]),
                        "dll": marker["dll"]}
            except (InstallError, AssetError) as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001
                return _err(e)

    async def uninstall_game(self, appid: int) -> dict:
        async with self.lock:
            try:
                m = self._find_cached(int(appid))
                if not m:
                    raise InstallError("Game is not in the scan results.")
                await asyncio.to_thread(uninstall, m["exe"])
                games = self._games()
                games.pop(str(m["appid"]), None)
                self._write_json(self.games_path, games)
                return {"ok": True, "launch_token": launch_token(self.wrapper, m["appid"])}
            except InstallError as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001
                return _err(e)

    async def set_game_3d(self, appid: int, enabled: bool) -> dict:
        games = self._games()
        entry = games.get(str(int(appid)))
        if not entry:
            return {"ok": False, "error": "3D is not installed for this game"}
        entry["enabled"] = bool(enabled)
        self._write_json(self.games_path, games)
        return {"ok": True, "enabled": entry["enabled"]}

    async def set_sbs_resolution(self, appid: int, resolution: str) -> dict:
        if resolution not in SBS_RESOLUTIONS:
            return {"ok": False, "error": f"unsupported resolution {resolution}"}
        games = self._games()
        entry = games.get(str(int(appid)))
        if not entry:
            return {"ok": False, "error": "3D is not installed for this game"}
        entry["sbs_resolution"] = resolution
        self._write_json(self.games_path, games)
        return {"ok": True, "sbs_resolution": resolution}

    async def set_game_steam(self, appid: int, data) -> dict:
        """Remembers (or forgets, data=None) the game's Steam settings the plugin changed for 3D."""
        games = self._games()
        entry = games.get(str(int(appid)))
        if not entry:
            return {"ok": False, "error": "3D is not installed for this game"}
        if data is None:
            entry.pop("steam", None)
        else:
            entry["steam"] = data
        self._write_json(self.games_path, games)
        return {"ok": True}

    async def remember_2d_mode(self, name: str, mode) -> dict:
        """Output mode Steam used for these glasses in 2D, to go back to after 3D."""
        if not name or not isinstance(mode, dict):
            return {"ok": False, "error": "bad arguments"}
        s = self._read_json(self.settings_path, {})
        modes = s.get("glasses_2d_modes", {})
        modes[name] = {k: int(mode[k]) for k in ("width", "height", "refresh_hz") if k in mode}
        s["glasses_2d_modes"] = modes
        self._write_json(self.settings_path, s)
        return {"ok": True}

    async def sbs_output(self, on: bool) -> dict:
        """Safety net from the frontend: drop the SBS request when a 3D game has ended."""
        try:
            await asyncio.to_thread(gamescope.set_sbs_output, bool(on))
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    async def restart_session(self) -> dict:
        """Restarts the gamescope session so gamescope loads the SBS script."""
        uid = os.getuid()
        env = _clean_env()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")

        def run(*args):
            return subprocess.run(["systemctl", "--user", *args], env=env, capture_output=True,
                                  text=True, timeout=20)
        for unit in SESSION_UNITS:
            if run("is-active", "--quiet", unit).returncode == 0:
                res = run("--no-block", "restart", unit)
                if res.returncode == 0:
                    return {"ok": True, "unit": unit}
                return {"ok": False, "error": res.stderr.strip() or f"cannot restart {unit}"}
        return {"ok": False, "error": "Не нашёл сессию gamescope. Перезапусти игровой режим вручную "
                                      "(выйди на рабочий стол и вернись или перезагрузи)."}

    async def get_display(self) -> dict:
        try:
            self._display = await asyncio.to_thread(self._display_now)
            return {"ok": True, "display": self._display}
        except Exception as e:  # noqa: BLE001
            return _err(e)

    async def mark_display(self, name: str, kind: str) -> dict:
        """kind: glasses | screen | auto (drop the user's choice for this display)."""
        if not name or kind not in ("glasses", "screen", "auto"):
            return {"ok": False, "error": "bad arguments"}
        s = self._read_json(self.settings_path, {})
        lists = {k: [n for n in s.get(k, []) if n != name] for k in DISPLAY_LISTS}
        if kind == "glasses":
            lists["glasses_names"].append(name)
        elif kind == "screen":
            lists["not_glasses_names"].append(name)
        s.update(lists)
        self._write_json(self.settings_path, s)
        return await self.get_display()

    async def set_setting(self, key: str, value) -> dict:
        if key not in DEFAULT_SETTINGS:
            return {"ok": False, "error": f"unknown setting {key}"}
        s = self._read_json(self.settings_path, {})
        s[key] = value
        self._write_json(self.settings_path, s)
        return {"ok": True, "settings": self._settings()}
