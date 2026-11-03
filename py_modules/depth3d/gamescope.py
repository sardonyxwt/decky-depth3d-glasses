"""Side-by-side 3D output on glasses whose EDID does not list a 3840-wide mode.

VITURE glasses (and others) switch to 3D with a button on the glasses but keep
reporting the same 1920-wide EDID: the computer has to send 3840x1080 on its
own. gamescope only offers EDID modes to Steam, so this goes through its
display scripts instead:

  * A Lua script in ~/.config/gamescope/scripts describes the connected glasses
    as a "known display" with dynamic refresh rates (the 1920x1080 rates from
    their EDID) and a mode generator. The generator returns the normal 1920x1080
    timing for those rates and a reduced-blanking 3840x1080@60 timing for the
    special rate SBS_HZ.
  * SBS_HZ is one of the script's refresh rates. While a game runs in 3D the
    launch wrapper asks gamescope for an app refresh cycle of SBS_HZ through the
    gamescope_control Wayland protocol (the call Steam uses for its frame limit,
    with "only change the refresh rate"). gamescope picks the highest rate that
    is a multiple of the requested one, which for SBS_HZ is only SBS_HZ itself,
    and switches the output to the generated mode. Steam's own limits (30, 40,
    45, 60 ...) still land on the normal rates. Requesting 0 drops it, and with
    no game open gamescope always goes back to the highest normal rate.

gamescope reads its scripts only when it starts, so a new or changed script
needs a restart of the gamescope session.
"""

import os
import socket
import struct
from typing import Iterable, List, Optional

SBS_HZ = 59  # not a real 1080p rate of any glasses, so it can only mean "SBS"
SCRIPT_NAME = "depth3d-glasses.lua"

# Two CEA 1080p60 lines side by side (the timing of VIC 16 with every horizontal
# value doubled): 297 MHz, h 3840 4016 4104 4400, v 1080 1084 1089 1125.
# Glasses build their 3D mode from two 1080p60 panels and expect the 1080p
# vertical timing; CVT reduced blanking (1111 lines) showed a band of smeared
# lines at the top on the VITURE Luma Pro. 297 MHz is also the clock of the
# glasses' own 1920x1080@120 mode, so the link carries it.
SBS_TIMING = {"width": 3840, "height": 1080, "hfp": 176, "hsync": 88, "hbp": 296,
              "vfp": 4, "vsync": 5, "vbp": 36, "refresh": 60}

LUA_TEMPLATE = """-- Written by the Depth3D Glasses Decky plugin; rewritten when it starts, do not edit.
-- Lets gamescope send a side-by-side 3D signal ({w}x{h}) to XR glasses whose EDID
-- does not list it. The plugin asks for it with an app refresh cycle of {sbs}
-- (gamescope_control) while a game runs in 3D; every other rate gives the glasses'
-- normal 1920x1080 timing.
local SBS_HZ = {sbs}
local glasses = {{
{entries}
}}

local function find(display)
    local model = string.lower(display.model or "")
    local vendor = string.lower(display.vendor or "")
    for _, g in ipairs(glasses) do
        if model == g.model and (g.vendor == "" or vendor == g.vendor) then
            return g
        end
    end
    return nil
end

local function modegen(base_mode, refresh)
    local mode = base_mode
    if refresh == SBS_HZ then
        gamescope.modegen.set_resolution(mode, {w}, {h})
        gamescope.modegen.set_h_timings(mode, {hfp}, {hsync}, {hbp})
        gamescope.modegen.set_v_timings(mode, {vfp}, {vsync}, {vbp})
        mode.clock = gamescope.modegen.calc_max_clock(mode, {refresh})
    else
        mode.clock = gamescope.modegen.calc_max_clock(mode, refresh)
    end
    mode.vrefresh = gamescope.modegen.calc_vrefresh(mode)
    return mode
end

for i, g in ipairs(glasses) do
    gamescope.config.known_displays["depth3d_glasses_" .. i] = {{
        pretty_name = g.pretty_name .. " (Depth3D Glasses SBS)",
        dynamic_refresh_rates = g.rates,
        dynamic_modegen = modegen,
        matches = function(display)
            if find(display) == g then
                debug("[depth3d_glasses] SBS mode available for " .. g.pretty_name)
                return 4000
            end
            return -1
        end
    }}
end
"""


def script_path(home: str) -> str:
    return os.path.join(home, ".config", "gamescope", "scripts", SCRIPT_NAME)


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_script(glasses: Iterable[dict]) -> str:
    """glasses: dicts with vendor (PNP id), model (EDID name) and rates (1920x1080 Hz)."""
    entries = []
    for g in sorted(glasses, key=lambda g: (g["model"].lower(), g.get("vendor", ""))):
        rates = ", ".join(str(int(r)) for r in sorted(set(g["rates"]) | {SBS_HZ}))
        entries.append(f'    {{ vendor = {_lua_str(g.get("vendor", "").lower())}, '
                       f'model = {_lua_str(g["model"].lower())}, pretty_name = {_lua_str(g["model"])}, '
                       f'rates = {{ {rates} }} }},')
    t = SBS_TIMING
    return LUA_TEMPLATE.format(entries="\n".join(entries), sbs=SBS_HZ, w=t["width"], h=t["height"],
                               hfp=t["hfp"], hsync=t["hsync"], hbp=t["hbp"], vfp=t["vfp"],
                               vsync=t["vsync"], vbp=t["vbp"], refresh=t["refresh"])


def install_script(home: str, glasses: Iterable[dict]) -> bool:
    """Writes the script; True when its content changed (gamescope must restart to see it)."""
    path = script_path(home)
    text = render_script(glasses)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def gamescope_start_times(proc_dir: str = "/proc") -> List[float]:
    """Start times (epoch seconds) of running gamescope compositors."""
    out = []
    try:
        pids = [p for p in os.listdir(proc_dir) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            with open(os.path.join(proc_dir, pid, "comm")) as f:
                if f.read().strip() not in ("gamescope", "gamescope-wl"):
                    continue
            out.append(os.stat(os.path.join(proc_dir, pid)).st_mtime)
        except OSError:
            continue
    return out


def script_loaded(home: str, proc_dir: str = "/proc") -> Optional[bool]:
    """Whether the running gamescope started after the script was written (None: no gamescope)."""
    starts = gamescope_start_times(proc_dir)
    if not starts:
        return None
    try:
        written = os.path.getmtime(script_path(home))
    except OSError:
        return False
    return min(starts) >= written


# ---------------- gamescope_control over the Wayland wire protocol ----------------

# gamescope_control requests (protocol/gamescope-control.xml): 0 destroy, 1 set_app_target_refresh_cycle
REFRESH_FLAG_ALLOW_SWITCHING = 0x2   # change the output refresh rate (and so the mode)
REFRESH_FLAG_ONLY_REFRESH = 0x4      # ... without capping the game's frame rate


def _wl_string(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)


def _wl_msg(obj: int, opcode: int, payload: bytes) -> bytes:
    return struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload


def _wl_socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    name = os.environ.get("GAMESCOPE_WAYLAND_DISPLAY") or "gamescope-0"
    return name if name.startswith("/") else os.path.join(runtime, name)


def _read_messages(sock, until_obj: int, sink) -> None:
    """Reads Wayland events until wl_callback(until_obj).done; sink(obj, opcode, body) gets the rest."""
    buf = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            raise OSError("gamescope closed the connection")
        buf += chunk
        while len(buf) >= 8:
            obj, word = struct.unpack_from("<II", buf)
            size, opcode = word >> 16, word & 0xFFFF
            if len(buf) < size:
                break
            body, buf = buf[8:size], buf[size:]
            if obj == 1 and opcode == 0:  # wl_display.error
                raise OSError("gamescope reported a protocol error")
            if obj == until_obj and opcode == 0:
                return
            sink(obj, opcode, body)


def _control(requests, path: Optional[str] = None, timeout: float = 3.0) -> list:
    """Binds gamescope_control as object 4, sends requests [(opcode, payload)], returns its events."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(path or _wl_socket_path())
    try:
        # wl_display(1).get_registry(new_id 2), then sync(new_id 3) to collect the globals
        sock.sendall(_wl_msg(1, 1, struct.pack("<I", 2)) + _wl_msg(1, 0, struct.pack("<I", 3)))
        control = []

        def on_global(obj, opcode, body):
            if obj == 2 and opcode == 0:  # wl_registry.global(name, interface, version)
                name, ln = struct.unpack_from("<II", body)
                if body[8:8 + ln - 1].decode() == "gamescope_control":
                    control.append((name, struct.unpack_from("<I", body, 8 + ln + (-ln % 4))[0]))
        _read_messages(sock, 3, on_global)
        if not control or control[0][1] < 2:
            raise OSError("gamescope does not offer gamescope_control v2+")
        name, version = control[0]
        msg = _wl_msg(2, 0, struct.pack("<I", name) + _wl_string("gamescope_control")
                      + struct.pack("<II", min(version, 5), 4))          # registry.bind -> id 4
        for opcode, payload in requests:
            msg += _wl_msg(4, opcode, payload)
        sock.sendall(msg + _wl_msg(1, 0, struct.pack("<I", 5)))         # sync -> id 5
        events = []
        _read_messages(sock, 5, lambda obj, op, body: events.append((op, body)) if obj == 4 else None)
        return events
    finally:
        sock.close()


def set_refresh_cycle(fps: int, flags: int = REFRESH_FLAG_ALLOW_SWITCHING | REFRESH_FLAG_ONLY_REFRESH,
                      path: Optional[str] = None) -> None:
    """gamescope_control.set_app_target_refresh_cycle(fps, flags) for the external display.

    gamescope keeps the value after we disconnect; fps 0 drops it.
    """
    _control([(1, struct.pack("<II", int(fps), flags))], path)


def active_display_info(path: Optional[str] = None) -> Optional[dict]:
    """gamescope_control.active_display_info, sent by gamescope when the interface is bound."""
    for opcode, body in _control([], path):
        if opcode != 1:
            continue
        fields, off = [], 0
        for _ in range(3):  # connector, make, model
            ln = struct.unpack_from("<I", body, off)[0]
            fields.append(body[off + 4:off + 4 + ln - 1].decode("utf-8", errors="replace"))
            off += 4 + ln + (-ln % 4)
        flags, alen = struct.unpack_from("<II", body, off)
        rates = list(struct.unpack_from(f"<{alen // 4}I", body, off + 8))
        return {"connector": fields[0], "make": fields[1], "model": fields[2], "flags": flags, "rates": rates}
    return None


def sbs_mode_ready(model: str, path: Optional[str] = None) -> Optional[bool]:
    """Is our generated SBS rate offered for the active display? None: gamescope cannot tell."""
    try:
        info = active_display_info(path)
    except OSError:
        return None
    if not info:
        return None
    return info["model"].lower() == model.lower() and SBS_HZ in info["rates"]


def set_sbs_output(on: bool) -> None:
    """Asks gamescope for the script-generated SBS mode (refresh SBS_HZ), or drops the request."""
    set_refresh_cycle(SBS_HZ if on else 0)
