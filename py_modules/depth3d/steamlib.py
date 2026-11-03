"""Steam library discovery: library folders and installed app manifests."""

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Parse Valve KeyValues text (libraryfolders.vdf, appmanifest_*.acf)."""
    stack: List[dict] = [{}]
    key: Optional[str] = None
    for m in _TOKEN.finditer(text):
        s, brace = m.group(1), m.group(2)
        if brace == "{":
            new: dict = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = s.replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return stack[0]


def _ci_get(d: dict, k: str):
    for kk, v in d.items():
        if kk.lower() == k.lower():
            return v
    return None


def steam_roots(home: str) -> List[str]:
    candidates = [
        os.path.join(home, ".local/share/Steam"),
        os.path.join(home, ".steam/steam"),
        os.path.join(home, ".steam/root"),
        os.path.join(home, ".var/app/com.valvesoftware.Steam/.local/share/Steam"),
        os.path.join(home, "snap/steam/common/.local/share/Steam"),
    ]
    seen, roots = set(), []
    for c in candidates:
        if os.path.isdir(os.path.join(c, "steamapps")):
            real = os.path.realpath(c)
            if real not in seen:
                seen.add(real)
                roots.append(real)
    return roots


def library_folders(home: str) -> List[str]:
    libs: List[str] = []
    seen = set()

    def add(p: str):
        real = os.path.realpath(p)
        if real not in seen and os.path.isdir(os.path.join(real, "steamapps")):
            seen.add(real)
            libs.append(real)

    for root in steam_roots(home):
        add(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                data = parse_vdf(f.read())
        except OSError:
            continue
        lf = _ci_get(data, "libraryfolders") or {}
        for entry in lf.values():
            if isinstance(entry, dict):
                path = _ci_get(entry, "path")
                if path:
                    add(path)
            elif isinstance(entry, str):  # very old format: "1" "/path"
                add(entry)
    return libs


@dataclass
class InstalledApp:
    appid: int
    name: str
    install_dir: str
    library: str
    manifest_mtime: float = 0.0
    kind: str = "steam"        # steam | shortcut (non-Steam game added to the library)

    @property
    def stamp(self) -> str:
        """Changes whenever the game has to be scanned again."""
        return f"{self.manifest_mtime}|{self.install_dir}"


def installed_apps(home: str) -> Iterator[InstalledApp]:
    seen = set()
    for lib in library_folders(home):
        steamapps = os.path.join(lib, "steamapps")
        try:
            entries = os.listdir(steamapps)
        except OSError:
            continue
        for fn in entries:
            if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                continue
            manifest = os.path.join(steamapps, fn)
            try:
                with open(manifest, encoding="utf-8", errors="replace") as f:
                    data = parse_vdf(f.read())
                mtime = os.path.getmtime(manifest)
            except OSError:
                continue
            state = _ci_get(data, "AppState") or {}
            try:
                appid = int(_ci_get(state, "appid") or 0)
            except ValueError:
                continue
            installdir = _ci_get(state, "installdir")
            if not appid or not installdir or appid in seen:
                continue
            full = os.path.join(steamapps, "common", installdir)
            if not os.path.isdir(full):
                continue
            seen.add(appid)
            yield InstalledApp(
                appid=appid,
                name=_ci_get(state, "name") or installdir,
                install_dir=full,
                library=lib,
                manifest_mtime=mtime,
            )


def apps_by_id(home: str) -> Dict[int, InstalledApp]:
    return {a.appid: a for a in installed_apps(home)}


# ---------------- non-Steam games (shortcuts.vdf) ----------------

STEAMID64_BASE = 76561197960265728


def parse_binary_vdf(data: bytes) -> dict:
    """Parse Valve's binary KeyValues (shortcuts.vdf).

    Each entry is a type byte, a NUL-terminated key, then the value:
    0x00 nested map (ends with 0x08), 0x01 string, 0x02 int32, 0x07 uint64.
    """
    def read_str(i: int):
        j = data.index(b"\0", i)
        return data[i:j].decode("utf-8", errors="replace"), j + 1

    def read_map(i: int):
        out: dict = {}
        while i < len(data):
            t = data[i]
            i += 1
            if t == 0x08:
                return out, i
            key, i = read_str(i)
            if t == 0x00:
                out[key], i = read_map(i)
            elif t == 0x01:
                out[key], i = read_str(i)
            elif t == 0x02:
                out[key] = int.from_bytes(data[i:i + 4], "little")
                i += 4
            elif t == 0x07:
                out[key] = int.from_bytes(data[i:i + 8], "little")
                i += 8
            else:
                raise ValueError(f"unknown binary VDF type 0x{t:02x}")
        return out, i

    try:
        return read_map(0)[0]
    except (ValueError, IndexError):
        return {}


def _active_account_ids(root: str) -> List[str]:
    """userdata folder of the most recently logged in account, or all of them when unknown."""
    try:
        with open(os.path.join(root, "config", "loginusers.vdf"), encoding="utf-8", errors="replace") as f:
            users = _ci_get(parse_vdf(f.read()), "users") or {}
    except OSError:
        users = {}
    recent = []
    for sid, info in users.items():
        if isinstance(info, dict) and str(_ci_get(info, "MostRecent") or "") == "1":
            try:
                recent.append(str(int(sid) - STEAMID64_BASE))
            except ValueError:
                pass
    try:
        present = sorted(d for d in os.listdir(os.path.join(root, "userdata")) if d.isdigit() and d != "0")
    except OSError:
        present = []
    ordered = [a for a in recent if a in present]
    return ordered[:1] or present


def _unquote(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1]
    return s


def shortcut_apps(home: str) -> Iterator[InstalledApp]:
    """Windows games added to Steam as non-Steam shortcuts (run through Proton).

    Only the active account's shortcuts are read; when Steam has never written
    loginusers.vdf every account found under userdata is used.
    """
    seen = set()
    for root in steam_roots(home):
        for account in _active_account_ids(root):
            path = os.path.join(root, "userdata", account, "config", "shortcuts.vdf")
            try:
                with open(path, "rb") as f:
                    data = parse_binary_vdf(f.read())
            except OSError:
                continue
            entries = _ci_get(data, "shortcuts") or {}
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                appid = _ci_get(entry, "appid")
                exe = _unquote(_ci_get(entry, "exe") or "")
                name = _ci_get(entry, "AppName") or os.path.basename(exe)
                if not isinstance(appid, int) or not appid or appid in seen:
                    continue
                if not exe.lower().endswith(".exe") or not os.path.isfile(exe):
                    continue  # Linux programs, Flatpaks and launchers that are not a Windows game
                seen.add(appid)
                try:
                    mtime = os.path.getmtime(exe)
                except OSError:
                    mtime = 0.0
                yield InstalledApp(
                    appid=appid & 0xFFFFFFFF,
                    name=name,
                    install_dir=os.path.dirname(exe),
                    library="",
                    manifest_mtime=mtime,
                    kind="shortcut",
                )


def all_games(home: str) -> Iterator[InstalledApp]:
    yield from installed_apps(home)
    yield from shortcut_apps(home)
