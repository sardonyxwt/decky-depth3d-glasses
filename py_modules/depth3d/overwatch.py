"""Reads the game profile table out of Depth3D's Overwatch.fxh.

Each profile block starts at column 0 with a line such as

    #elif (App == 0x7B81CCAB || App == 0xFB9A99AB ) //Borderlands 2 & The Pre-Sequel

and its body may set a few status flags:

    NPW  No Profile Warning     -> hash is known, but there is no real profile yet
    NDW  Network Warning        -> online game, ReShade/anti-cheat risk
    NCW  Not Compatible Warning -> depth does not work in this game

Overwatch.fxh is proprietary (Depth3D, personal use only). This module only
reads a copy the user downloaded; nothing from it is shipped with the plugin.
"""

import re
from dataclasses import dataclass, asdict
from typing import Dict, Optional

_BLOCK_START = re.compile(r"^#(?:el)?if\s*\(\s*App\s*==")
_HASH = re.compile(r"App\s*==\s*(0x[0-9A-Fa-f]+)")
_FLAG = re.compile(r"^\s*#define\s+(NPW|NDW|NCW)\s+1\b")
_VERSION = re.compile(r'#define\s+OVERWATCH\s+"([^"\\]+)')

STATUS_OK = "ok"
STATUS_NETWORK = "network"
STATUS_NOT_COMPATIBLE = "not_compatible"
STATUS_NO_PROFILE = "no_profile"


@dataclass
class Profile:
    name: str
    status: str

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_name(comment: str) -> str:
    name = comment.strip()
    name = re.sub(r"\s#[0-9A-Fa-f]{6,8}\b", "", name)  # stray hash tags in comments
    name = name.rstrip("* ").strip()
    return name or "Unknown game"


def _status(flags: set) -> str:
    if "NCW" in flags:
        return STATUS_NOT_COMPATIBLE
    if "NPW" in flags:
        return STATUS_NO_PROFILE
    if "NDW" in flags:
        return STATUS_NETWORK
    return STATUS_OK


def parse_overwatch(text: str) -> Dict[int, Profile]:
    profiles: Dict[int, Profile] = {}
    hashes = []
    name = ""
    flags: set = set()

    def flush():
        if hashes:
            prof = Profile(name=name, status=_status(flags))
            for h in hashes:
                profiles.setdefault(h, prof)

    for line in text.splitlines():
        if _BLOCK_START.match(line):
            flush()
            code, _, comment = line.partition("//")
            hashes = [int(h, 16) & 0xFFFFFFFF for h in _HASH.findall(code)]
            name = _clean_name(comment)
            flags = set()
            continue
        if not hashes:
            continue
        if line.startswith("#endif") or line.startswith("#else"):
            flush()
            hashes = []
            continue
        m = _FLAG.match(line)
        if m:
            flags.add(m.group(1))
    flush()
    return profiles


def overwatch_version(text: str) -> Optional[str]:
    m = _VERSION.search(text)
    return m.group(1).strip() if m else None
