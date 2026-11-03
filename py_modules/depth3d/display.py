"""Which display the game will be shown on, and whether it is a pair of XR glasses.

Sources, in order:
  1. The EDID gamescope writes for the output it is driving. gamescope-session
     (SteamOS, Bazzite, gamescope-session-plus) points it to
     GAMESCOPE_PATCHED_EDID_FILE, by default ~/.config/gamescope/edid.bin.
     The identity bytes are matched against the connected DRM connectors so a
     stale file from an earlier session is not trusted.
  2. The connected connectors in /sys/class/drm. With several displays and no
     usable gamescope EDID the answer is ambiguous; a regular monitor then wins,
     so 3D is never forced onto a monitor.

Glasses are recognised by the EDID monitor name. The user can mark any display
as glasses (or not) in the plugin settings.
"""

import os
from typing import List, Optional

DRM_DIR = "/sys/class/drm"
EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"

# Lower-case substrings of EDID monitor names (or PNP vendor ids) of XR glasses.
KNOWN_GLASSES = ("viture", "xreal", "nreal", "rokid", "rayneo")

# Mode widths a side-by-side 3D signal uses (two 1920-wide eyes).
SBS_WIDTHS = (3840,)


# CTA-861 video codes of 1920x1080 progressive modes -> refresh rate
_VIC_1080 = {16: 60, 31: 50, 32: 24, 33: 25, 34: 30, 63: 120, 64: 100}


def _dtd(d: bytes):
    clock = int.from_bytes(d[0:2], "little") * 10_000
    if clock == 0:
        return None
    hact = d[2] | ((d[4] & 0xF0) << 4)
    hblank = d[3] | ((d[4] & 0x0F) << 8)
    vact = d[5] | ((d[7] & 0xF0) << 4)
    vblank = d[6] | ((d[7] & 0x0F) << 8)
    if d[17] & 0x80 or not (hact + hblank) or not (vact + vblank):  # interlaced or broken
        return None
    return hact, vact, round(clock / ((hact + hblank) * (vact + vblank)))


def edid_rates(data: bytes, width: int = 1920, height: int = 1080) -> List[int]:
    """Refresh rates the EDID lists for one resolution (detailed timings and CTA video codes)."""
    rates = set()
    if len(data) < 128:
        return []
    timings = [data[o:o + 18] for o in (54, 72, 90, 108)]
    for b in range(1, min(data[126], len(data) // 128 - 1) + 1):
        ext = data[128 * b:128 * (b + 1)]
        if ext[0] != 0x02:
            continue
        dtd_start = ext[2]
        i = 4
        while i < dtd_start and i < 127:
            tag, ln = ext[i] >> 5, ext[i] & 31
            if tag == 2 and (width, height) == (1920, 1080):
                rates.update(_VIC_1080[v & 0x7F] for v in ext[i + 1:i + 1 + ln] if (v & 0x7F) in _VIC_1080)
            i += 1 + ln
        j = dtd_start
        while dtd_start and j + 18 <= 127:
            timings.append(ext[j:j + 18])
            j += 18
    for d in timings:
        t = _dtd(d)
        if t and t[0] == width and t[1] == height:
            rates.add(t[2])
    return sorted(rates)


def parse_edid(data: bytes) -> Optional[dict]:
    if len(data) < 128 or data[:8] != EDID_HEADER:
        return None
    v = int.from_bytes(data[8:10], "big")
    vendor = "".join(chr(((v >> s) & 0x1F) + 64) for s in (10, 5, 0))
    name = ""
    serial_text = ""
    for off in (54, 72, 90, 108):
        d = data[off:off + 18]
        if d[0:2] != b"\0\0" or d[2] != 0:
            continue
        text = d[5:18].split(b"\n")[0].decode("ascii", errors="replace").strip()
        if d[3] == 0xFC:
            name = text
        elif d[3] == 0xFF:
            serial_text = text
    return {
        "vendor": vendor,
        "product": int.from_bytes(data[10:12], "little"),
        "name": name,
        "serial": serial_text,
        "identity": data[8:18].hex(),  # vendor, product, serial, week, year
        "rates_1080": edid_rates(data),
    }


def _read(path: str, binary: bool = True, limit: int = 1 << 16):
    try:
        with open(path, "rb" if binary else "r") as f:
            return f.read(limit)
    except OSError:
        return None


def connected_displays(drm_dir: str = DRM_DIR) -> List[dict]:
    out = []
    try:
        entries = sorted(os.listdir(drm_dir))
    except OSError:
        return out
    for e in entries:
        if not e.startswith("card") or "-" not in e:
            continue
        base = os.path.join(drm_dir, e)
        status = _read(os.path.join(base, "status"), binary=False)
        if not status or status.strip() != "connected":
            continue
        info = parse_edid(_read(os.path.join(base, "edid")) or b"")
        if not info:
            continue
        modes = (_read(os.path.join(base, "modes"), binary=False) or "").split()
        info["connector"] = e.split("-", 1)[1]
        info["modes"] = list(dict.fromkeys(modes))
        out.append(info)
    return out


def gamescope_edid_paths(home: str, proc_dir: str = "/proc") -> List[str]:
    """EDID files of running gamescope instances, then the session default."""
    paths = []
    try:
        pids = [p for p in os.listdir(proc_dir) if p.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        comm = _read(os.path.join(proc_dir, pid, "comm"), binary=False, limit=64)
        if not comm or comm.strip() not in ("gamescope", "gamescope-wl"):
            continue
        env = _read(os.path.join(proc_dir, pid, "environ"), limit=1 << 20) or b""
        for item in env.split(b"\0"):
            if item.startswith(b"GAMESCOPE_PATCHED_EDID_FILE="):
                p = item.split(b"=", 1)[1].decode("utf-8", errors="replace")
                if p and p not in paths:
                    paths.append(p)
    default = os.path.join(home, ".config", "gamescope", "edid.bin")
    if default not in paths:
        paths.append(default)
    return paths


def is_glasses(info: dict, glasses_names=(), not_glasses_names=()) -> bool:
    name = (info.get("name") or "").strip()
    if name and name in not_glasses_names:
        return False
    if name and name in glasses_names:
        return True
    hay = f"{name} {info.get('vendor', '')}".lower()
    return any(k in hay for k in KNOWN_GLASSES)


def active_display(home: str, glasses_names=(), not_glasses_names=(),
                   drm_dir: str = DRM_DIR, proc_dir: str = "/proc") -> dict:
    connected = connected_displays(drm_dir)
    active = None
    source = "none"
    for path in gamescope_edid_paths(home, proc_dir):
        info = parse_edid(_read(path) or b"")
        if not info:
            continue
        for c in connected:
            if c["identity"] == info["identity"]:
                active, source = c, "gamescope"
                break
        if active:
            break
    if active is None and connected:
        if len(connected) == 1:
            active, source = connected[0], "drm"
        else:
            screens = [c for c in connected if not is_glasses(c, glasses_names, not_glasses_names)]
            active, source = (screens or connected)[0], "drm-ambiguous"

    if active is None:
        return {"connected": False, "source": source, "name": "", "vendor": "", "connector": "",
                "is_glasses": False, "sbs": False, "modes": [], "rates_1080": [], "others": []}
    glasses = is_glasses(active, glasses_names, not_glasses_names)
    widths = {m.split("x")[0] for m in active.get("modes", []) if "x" in m}
    return {
        "connected": True,
        "source": source,
        "name": active["name"] or active["vendor"],
        "vendor": active["vendor"],
        "connector": active["connector"],
        "is_glasses": glasses,
        "sbs": any(str(w) in widths for w in SBS_WIDTHS),  # the EDID itself lists a 3840-wide mode
        "modes": active.get("modes", [])[:6],
        "rates_1080": active.get("rates_1080", []),
        "others": [c["name"] or c["vendor"] for c in connected if c is not active],
    }
