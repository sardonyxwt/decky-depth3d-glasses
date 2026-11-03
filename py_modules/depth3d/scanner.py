"""Matches installed Steam games against Overwatch profiles by executable hash."""

import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .hashing import app_hash
from .overwatch import Profile, STATUS_OK, STATUS_NETWORK
from .pe import read_pe, guess_reshade_dll
from .steamlib import InstalledApp

MAX_DEPTH = 6
MIN_EXE_BYTES = 150 * 1024

# Folders that never hold the game's own executable. Matching is by hash, so
# this is only about scan speed on big installs.
SKIP_DIRS = {
    "_commonredist", "commonredist", "redist", "redistributables", "directx",
    "__installer", "installer", "installers", "easyanticheat", "battleye",
    "support", ".egstore", "vcredist", "dotnet", "prereq", "prerequisites",
    "thirdparty", "shadercache", "logs", "saves", "screenshots", "movies",
    "content", "paks", "data_win", "_redist", "steamworks shared",
}

# Our own marker, written next to the game's executable on install.
MARKER = "depth3d-glasses.json"


@dataclass
class Match:
    appid: int
    name: str
    exe: str            # absolute path of the matched executable
    exe_rel: str        # path relative to the game folder, for display
    is_64bit: bool
    app_hash: str       # "0x7B81CCAB"
    profile: str        # game name as written in Overwatch
    status: str         # ok | network | not_compatible | no_profile
    dll: str            # dxgi | d3d9 | opengl32 | vulkan
    installed: bool
    kind: str = "steam"  # steam | shortcut

    def to_dict(self) -> dict:
        return asdict(self)


def _iter_exes(root: str):
    root_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.count(os.sep) - root_depth
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for fn in filenames:
            if not fn.lower().endswith(".exe"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(full) < MIN_EXE_BYTES:
                    continue
            except OSError:
                continue
            yield full


def _rank(m: Match) -> tuple:
    status_rank = {STATUS_OK: 0, STATUS_NETWORK: 1}.get(m.status, 2)
    in_binaries = 0 if "binaries" in m.exe_rel.lower() else 1
    return (status_rank, in_binaries, -m.exe_rel.count(os.sep))


def scan_app(app: InstalledApp, profiles: Dict[int, Profile]) -> Optional[Match]:
    """Best Overwatch match for one installed game, or None."""
    found: List[Match] = []
    for exe in _iter_exes(app.install_dir):
        info = read_pe(exe)
        if info is None:
            continue
        stem = os.path.splitext(os.path.basename(exe))[0]
        h = app_hash(stem, info.is_64bit)
        prof = profiles.get(h)
        if prof is None:
            continue
        found.append(Match(
            appid=app.appid,
            name=app.name,
            exe=exe,
            exe_rel=os.path.relpath(exe, app.install_dir),
            is_64bit=info.is_64bit,
            app_hash="0x%08X" % h,
            profile=prof.name,
            status=prof.status,
            dll=guess_reshade_dll(info),
            installed=os.path.isfile(os.path.join(os.path.dirname(exe), MARKER)),
            kind=app.kind,
        ))
    if not found:
        return None
    found.sort(key=_rank)
    return found[0]


def refresh_installed_flag(m: dict) -> dict:
    m["installed"] = os.path.isfile(os.path.join(os.path.dirname(m["exe"]), MARKER))
    return m
