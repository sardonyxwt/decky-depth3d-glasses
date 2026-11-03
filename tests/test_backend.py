"""Offline tests for the backend: run with  python3 -m pytest tests/  (or python3 tests/test_backend.py)

Uses a fake Steam library and hand-built PE files. The Overwatch table used
here is a small synthetic file, not the real (proprietary) one; pass
OVERWATCH=/path/to/Overwatch.fxh to also check the real parser output.
"""

import json
import os
import struct
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))

from depth3d.hashing import app_hash  # noqa: E402
from depth3d.pe import read_pe, guess_reshade_dll  # noqa: E402
from depth3d.overwatch import parse_overwatch  # noqa: E402
from depth3d.steamlib import installed_apps, parse_vdf, parse_binary_vdf, shortcut_apps  # noqa: E402
from depth3d.scanner import scan_app, MARKER  # noqa: E402
from depth3d.assets import Assets  # noqa: E402
from depth3d.assets import zip_member  # noqa: E402
from depth3d import cleanup, installer, launch, display, gamescope  # noqa: E402


def build_pe(is64: bool, imports: list, pad_to: int = 200 * 1024) -> bytes:
    """Smallest PE image with an import table the reader can follow."""
    file_align = 0x200
    sect_rva = 0x1000
    # import section content
    n = len(imports)
    desc_size = 20 * (n + 1)
    names = b""
    name_offsets = []
    for dll in imports:
        name_offsets.append(desc_size + len(names))
        names += dll.encode() + b"\0"
    descs = b""
    for off in name_offsets:
        descs += struct.pack("<IIIII", 0, 0, 0, sect_rva + off, 0)
    descs += b"\0" * 20
    section = descs + names
    raw_size = (len(section) + file_align - 1) // file_align * file_align

    opt_size = 240 if is64 else 224
    e_lfanew = 0x80
    headers_size = e_lfanew + 4 + 20 + opt_size + 40
    raw_ptr = (headers_size + file_align - 1) // file_align * file_align

    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, e_lfanew)
    out = bytearray(dos) + b"\0" * (e_lfanew - 64)
    out += b"PE\0\0"
    machine = 0x8664 if is64 else 0x14C
    out += struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, opt_size, 0x0022)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x20B if is64 else 0x10B)
    dd = 112 if is64 else 96
    struct.pack_into("<I", opt, dd - 4, 16)  # NumberOfRvaAndSizes
    struct.pack_into("<II", opt, dd + 8, sect_rva, len(section))
    out += opt
    sh = bytearray(40)
    sh[0:8] = b".idata\0\0"
    struct.pack_into("<IIII", sh, 8, len(section), sect_rva, raw_size, raw_ptr)
    out += sh
    out += b"\0" * (raw_ptr - len(out))
    out += section + b"\0" * (raw_size - len(section))
    if len(out) < pad_to:
        out += b"\0" * (pad_to - len(out))
    return bytes(out)


def make_overwatch(entries) -> str:
    lines = ['#define OVERWATCH "Overwatch vTEST\\n"', "#define App __APPLICATION__"]
    first = True
    for hashes, name, flags in entries:
        cond = " || ".join(f"App == 0x{h:08X}" for h in hashes)
        lines.append(f"{'#if' if first else '#elif'} ({cond} ) //{name}")
        first = False
        for fl in flags:
            lines.append(f"\t#define {fl} 1")
        lines.append("\t#define DA_Y 10.0")
    lines.append("#else")
    lines.append("\t#define NPW 1")
    lines.append("#endif")
    return "\n".join(lines)


def make_library(root: str, apps: dict) -> str:
    """apps: appid -> (name, installdir, {relative_exe_path: pe_bytes})"""
    home = os.path.join(root, "home")
    steam = os.path.join(home, ".local/share/Steam")
    sa = os.path.join(steam, "steamapps")
    os.makedirs(sa)
    with open(os.path.join(sa, "libraryfolders.vdf"), "w") as f:
        f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % steam)
    for appid, (name, instdir, files) in apps.items():
        with open(os.path.join(sa, f"appmanifest_{appid}.acf"), "w") as f:
            f.write('"AppState"\n{\n\t"appid"\t\t"%d"\n\t"name"\t\t"%s"\n\t"installdir"\t\t"%s"\n}\n'
                    % (appid, name, instdir))
        for rel, data in files.items():
            p = os.path.join(sa, "common", instdir, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
    return home


def test_hash_known_values():
    # Values that appear in the real Overwatch.fxh
    assert app_hash("Borderlands2", False) == 0x7B81CCAB        # 32-bit game
    assert app_hash("Oblivion", False) == 0xC753DADB
    assert app_hash("Cemu", True) == 0xFBEE8027                 # 64-bit
    assert app_hash("NvRemixBridge", True) == 0xF8D4C41F
    assert app_hash("pcsx2-qt", True) == 0x5DD31943


def test_pe_reader():
    with tempfile.TemporaryDirectory() as d:
        p64 = os.path.join(d, "a.exe")
        with open(p64, "wb") as f:
            f.write(build_pe(True, ["KERNEL32.dll", "d3d11.dll", "dxgi.dll"]))
        info = read_pe(p64)
        assert info and info.is_64bit and "d3d11.dll" in info.imports
        assert guess_reshade_dll(info) == "dxgi"
        p32 = os.path.join(d, "b.exe")
        with open(p32, "wb") as f:
            f.write(build_pe(False, ["d3d9.dll"]))
        info = read_pe(p32)
        assert info and not info.is_64bit and guess_reshade_dll(info) == "d3d9"
        # Unreal Engine shipping exe: imports d3d9 and opengl32 next to D3D11/12
        pue = os.path.join(d, "ue.exe")
        with open(pue, "wb") as f:
            f.write(build_pe(True, ["dxgi.dll", "d3d9.dll", "d3d11.dll", "opengl32.dll"]))
        assert guess_reshade_dll(read_pe(pue)) == "dxgi"
        txt = os.path.join(d, "c.exe")
        with open(txt, "wb") as f:
            f.write(b"not a pe")
        assert read_pe(txt) is None


def test_vdf():
    d = parse_vdf('"a"\n{\n "b" "c\\"d"\n "e" { "f" "g" }\n}')
    assert d == {"a": {"b": 'c"d', "e": {"f": "g"}}}


def test_overwatch_parser_synthetic():
    ow = make_overwatch([
        ([0x11111111, 0x22222222], "Game A | Game A2", []),
        ([0x33333333], "Online Game", ["NDW"]),
        ([0x44444444], "Broken Game", ["NCW"]),
    ])
    p = parse_overwatch(ow)
    assert p[0x11111111].name == "Game A | Game A2" and p[0x22222222].status == "ok"
    assert p[0x33333333].status == "network"
    assert p[0x44444444].status == "not_compatible"
    assert len(p) == 4


def test_real_overwatch_if_given():
    path = os.environ.get("OVERWATCH")
    if not path:
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        p = parse_overwatch(f.read())
    assert len(p) > 500
    assert p[0xC753DADB].name == "The Elder Scrolls IV: Oblivion"


def _scenario(root):
    h_game = app_hash("GameX-Win64-Shipping", True)
    h_old = app_hash("OldGame", False)
    ow = make_overwatch([([h_game], "Game X", []), ([h_old], "Old Game", ["NDW"])])
    home = make_library(root, {
        100: ("Game X", "GameX", {
            "GameX.exe": build_pe(True, ["kernel32.dll"]),                     # UE launcher stub
            "GameX/Binaries/Win64/GameX-Win64-Shipping.exe": build_pe(True, ["d3d12.dll", "dxgi.dll"]),
            "_CommonRedist/vcredist/vc_redist.x64.exe": build_pe(True, []),
        }),
        200: ("Old Game", "Old Game", {"OldGame.exe": build_pe(False, ["d3d9.dll"])}),
        300: ("Unknown", "Unknown", {"unknown.exe": build_pe(True, ["d3d11.dll"])}),
    })
    return home, parse_overwatch(ow), ow


def test_scan_and_install_roundtrip():
    with tempfile.TemporaryDirectory() as root:
        home, profiles, ow = _scenario(root)
        apps = {a.appid: a for a in installed_apps(home)}
        assert set(apps) == {100, 200, 300}

        m100 = scan_app(apps[100], profiles)
        assert m100 and m100.profile == "Game X" and m100.dll == "dxgi" and m100.is_64bit
        assert m100.exe_rel.endswith("GameX-Win64-Shipping.exe")
        m200 = scan_app(apps[200], profiles)
        assert m200 and m200.status == "network" and m200.dll == "d3d9" and not m200.is_64bit
        assert scan_app(apps[300], profiles) is None

        # Fake, pre-downloaded assets so no network is touched
        assets = Assets(os.path.join(root, "runtime"))
        for f in ("SuperDepth3D.fx", "Overwatch.fxh", "AXAA.fxh", "Sentinel.fxh"):
            with open(os.path.join(assets.shader_dir, f), "w") as fh:
                fh.write(ow if f == "Overwatch.fxh" else "// test")
        rs = os.path.join(assets.reshade_dir, "6.8.0_Addon")
        os.makedirs(rs)
        for n in ("ReShade64.dll", "ReShade32.dll"):
            with open(os.path.join(rs, n), "wb") as fh:
                fh.write(b"MZ" + n.encode())
        assets._save_state(reshade="6.8.0_Addon")
        for bits in (32, 64):
            with open(assets.d3dcompiler_path(bits), "wb") as fh:
                fh.write(b"MZd3dc%d" % bits)

        # A pre-existing ReShade.ini of the user's must be backed up and restored
        exe_dir = os.path.dirname(m100.exe)
        with open(os.path.join(exe_dir, "ReShade.ini"), "w") as fh:
            fh.write("users own")

        marker = installer.install(m100.to_dict(), assets)
        assert marker["dll"] == "dxgi"
        with open(os.path.join(exe_dir, "dxgi.dll"), "rb") as fh:
            assert fh.read() == b"MZReShade64.dll"
        with open(os.path.join(exe_dir, "ReShade.ini"), encoding="utf-8") as fh:
            ini = fh.read()
        assert "EffectSearchPaths=Z:\\" in ini and "\\runtime\\shaders" in ini
        assert os.path.isfile(os.path.join(exe_dir, MARKER))
        assert scan_app(apps[100], profiles).installed

        try:
            installer.install(m100.to_dict(), assets)
            raise AssertionError("second install must fail")
        except installer.InstallError:
            pass

        installer.uninstall(m100.exe)
        assert not os.path.exists(os.path.join(exe_dir, "dxgi.dll"))
        assert not os.path.exists(os.path.join(exe_dir, MARKER))
        with open(os.path.join(exe_dir, "ReShade.ini")) as fh:
            assert fh.read() == "users own"

        # 32-bit D3D9 game gets the 32-bit DLL as d3d9.dll
        marker = installer.install(m200.to_dict(), assets)
        with open(os.path.join(os.path.dirname(m200.exe), "d3d9.dll"), "rb") as fh:
            assert fh.read() == b"MZReShade32.dll"
        assert json.load(open(os.path.join(os.path.dirname(m200.exe), MARKER)))["bits"] == 32

        # Vulkan is refused
        v = m100.to_dict()
        v["dll"] = "vulkan"
        try:
            installer.install(v, assets)
            raise AssertionError("vulkan must be refused")
        except installer.InstallError:
            pass


def _bin_vdf(d: dict) -> bytes:
    out = b""
    for k, v in d.items():
        kb = k.encode() + b"\0"
        if isinstance(v, dict):
            out += b"\x00" + kb + _bin_vdf(v)
        elif isinstance(v, int):
            out += b"\x02" + kb + v.to_bytes(4, "little")
        else:
            out += b"\x01" + kb + v.encode() + b"\0"
    return out + b"\x08"


def test_shortcuts():
    with tempfile.TemporaryDirectory() as root:
        home, profiles, _ = _scenario(root)
        steam = os.path.join(home, ".local/share/Steam")
        games = os.path.join(root, "Games", "Shortcut Game")
        exe = os.path.join(games, "GameX", "Binaries", "Win64", "GameX-Win64-Shipping.exe")
        os.makedirs(os.path.dirname(exe))
        with open(exe, "wb") as f:
            f.write(build_pe(True, ["d3d12.dll", "d3d9.dll"]))
        launcher = os.path.join(games, "GameX.exe")
        with open(launcher, "wb") as f:
            f.write(build_pe(True, ["kernel32.dll"]))
        os.makedirs(os.path.join(steam, "config"))
        with open(os.path.join(steam, "config", "loginusers.vdf"), "w") as f:
            f.write('"users"\n{\n "76561198083722517"\n {\n "MostRecent" "1"\n }\n}\n')
        for account in ("123456789", "999"):
            cfg = os.path.join(steam, "userdata", account, "config")
            os.makedirs(cfg)
            vdf = {"shortcuts": {
                "0": {"appid": 3087367981, "AppName": "Shortcut Game", "Exe": f'"{launcher}"',
                      "StartDir": f'"{games}"', "LaunchOptions": ""},
                "1": {"appid": 3833989869, "AppName": "Chrome", "Exe": '"/usr/bin/flatpak"'},
            }} if account == "123456789" else {"shortcuts": {
                "0": {"appid": 1234, "AppName": "Other account", "Exe": f'"{launcher}"'}}}
            with open(os.path.join(cfg, "shortcuts.vdf"), "wb") as f:
                f.write(_bin_vdf(vdf))
        with open(os.path.join(steam, "userdata", "123456789", "config", "shortcuts.vdf"), "rb") as f:
            parsed = parse_binary_vdf(f.read())
        assert parsed["shortcuts"]["0"]["appid"] == 3087367981
        apps = list(shortcut_apps(home))
        assert [(a.appid, a.kind) for a in apps] == [(3087367981, "shortcut")]
        m = scan_app(apps[0], profiles)
        assert m and m.kind == "shortcut" and m.dll == "dxgi" and m.profile == "Game X"


def _edid(name: str, vendor: str = "VIT", serial: int = 1) -> bytes:
    e = bytearray(128)
    e[0:8] = display.EDID_HEADER
    v = sum((ord(c) - 64) << s for c, s in zip(vendor, (10, 5, 0)))
    e[8:10] = v.to_bytes(2, "big")
    e[12:16] = serial.to_bytes(4, "little")
    e[54:56] = b"\x01\x1d"  # a timing descriptor first
    d = bytearray(18)
    d[3] = 0xFC
    d[5:18] = (name.encode() + b"\n").ljust(13, b" ")
    e[72:90] = d
    return bytes(e)


def _fake_drm(root: str, outputs: dict) -> str:
    drm = os.path.join(root, "drm")
    for conn, (edid, modes) in outputs.items():
        c = os.path.join(drm, f"card1-{conn}")
        os.makedirs(c)
        with open(os.path.join(c, "status"), "w") as f:
            f.write("connected\n" if edid else "disconnected\n")
        with open(os.path.join(c, "edid"), "wb") as f:
            f.write(edid or b"")
        with open(os.path.join(c, "modes"), "w") as f:
            f.write("\n".join(modes) + "\n")
    return drm


def test_display_detection():
    viture, g5 = _edid("VITURE", "CVT"), _edid("Odyssey G5", "SAM", 2)
    info = display.parse_edid(viture)
    assert info["name"] == "VITURE" and info["vendor"] == "CVT"
    with tempfile.TemporaryDirectory() as root:
        home = os.path.join(root, "home")
        os.makedirs(os.path.join(home, ".config", "gamescope"))
        proc = os.path.join(root, "proc")
        os.makedirs(proc)
        drm = _fake_drm(root, {"DP-4": (viture, ["3840x1080", "1920x1080"]),
                               "HDMI-A-3": (g5, ["2560x1440", "1920x1080"]),
                               "DP-1": (None, [])})
        kw = dict(drm_dir=drm, proc_dir=proc)
        # Two displays, no gamescope EDID: a monitor wins, 3D is not offered
        d = display.active_display(home, **kw)
        assert d["source"] == "drm-ambiguous" and not d["is_glasses"] and d["name"] == "Odyssey G5"
        # gamescope says it drives the glasses
        with open(os.path.join(home, ".config", "gamescope", "edid.bin"), "wb") as f:
            f.write(viture)
        d = display.active_display(home, **kw)
        assert d["source"] == "gamescope" and d["is_glasses"] and d["connector"] == "DP-4" and d["sbs"]
        # The user says this display is not glasses
        d = display.active_display(home, not_glasses_names=["VITURE"], **kw)
        assert not d["is_glasses"]
        # A gamescope instance with its own EDID path takes priority
        own = os.path.join(root, "own-edid.bin")
        with open(own, "wb") as f:
            f.write(g5)
        os.makedirs(os.path.join(proc, "4242"))
        with open(os.path.join(proc, "4242", "comm"), "w") as f:
            f.write("gamescope-wl\n")
        with open(os.path.join(proc, "4242", "environ"), "wb") as f:
            f.write(b"A=1\0GAMESCOPE_PATCHED_EDID_FILE=" + own.encode() + b"\0")
        d = display.active_display(home, **kw)
        assert d["source"] == "gamescope" and d["name"] == "Odyssey G5" and not d["is_glasses"]
        # Unknown glasses, marked by the user
        drm2 = _fake_drm(os.path.join(root, "x"), {"DP-2": (_edid("Some XR", "ABC"), ["1920x1080"])})
        d = display.active_display(home, glasses_names=["Some XR"], drm_dir=drm2, proc_dir=proc)
        assert d["is_glasses"] and d["source"] == "drm" and not d["sbs"]


def _fake_assets(root: str, ow: str) -> Assets:
    """Pre-downloaded assets so no network is touched."""
    assets = Assets(os.path.join(root, "runtime-assets"))
    for f in ("SuperDepth3D.fx", "Overwatch.fxh", "AXAA.fxh", "Sentinel.fxh"):
        with open(os.path.join(assets.shader_dir, f), "w") as fh:
            fh.write(ow if f == "Overwatch.fxh" else "// test")
    rs = os.path.join(assets.reshade_dir, "6.8.0_Addon")
    os.makedirs(rs)
    for n in ("ReShade64.dll", "ReShade32.dll"):
        with open(os.path.join(rs, n), "wb") as fh:
            fh.write(b"MZ" + n.encode())
    assets._save_state(reshade="6.8.0_Addon")
    for bits in (32, 64):
        with open(assets.d3dcompiler_path(bits), "wb") as fh:
            fh.write(b"MZd3dc%d" % bits)
    return assets


def _read(p):
    with open(p, "rb") as f:
        return f.read()


def test_parking_2d_3d():
    with tempfile.TemporaryDirectory() as root:
        home, profiles, ow = _scenario(root)
        apps = {a.appid: a for a in installed_apps(home)}
        m = scan_app(apps[100], profiles)
        exe_dir = os.path.dirname(m.exe)
        dxgi = os.path.join(exe_dir, "dxgi.dll")
        d3dc = os.path.join(exe_dir, "d3dcompiler_47.dll")
        with open(dxgi, "wb") as f:  # the game ships its own dxgi.dll (e.g. an upscaler mod)
            f.write(b"games own dxgi")
        installer.install(m.to_dict(), _fake_assets(root, ow))
        assert _read(dxgi) == b"MZReShade64.dll"

        assert installer.set_active(m.exe, False)          # plain 2D
        assert _read(dxgi) == b"games own dxgi"
        assert _read(dxgi + installer.OFF_SUFFIX) == b"MZReShade64.dll"
        assert not os.path.exists(d3dc) and os.path.exists(d3dc + installer.OFF_SUFFIX)
        assert not os.path.exists(dxgi + installer.BACKUP_SUFFIX)
        assert os.path.exists(os.path.join(exe_dir, "ReShade.ini"))  # harmless without the DLL
        assert not installer.set_active(m.exe, False)      # already 2D: nothing moves

        assert installer.set_active(m.exe, True)           # 3D again
        assert _read(dxgi) == b"MZReShade64.dll" and _read(d3dc) == b"MZd3dc64"
        assert _read(dxgi + installer.BACKUP_SUFFIX) == b"games own dxgi"
        assert not os.path.exists(dxgi + installer.OFF_SUFFIX)

        installer.set_active(m.exe, False)
        installer.uninstall(m.exe)                         # works from the parked state too
        assert _read(dxgi) == b"games own dxgi"
        left = [f for f in os.listdir(exe_dir) if f.endswith((installer.OFF_SUFFIX, installer.BACKUP_SUFFIX))]
        assert left == [] and not os.path.exists(d3dc)
        assert not os.path.exists(os.path.join(exe_dir, MARKER))


def test_launch_decision_and_wrapper():
    import subprocess
    with tempfile.TemporaryDirectory() as root:
        home_lib, profiles, ow = _scenario(root)
        apps = {a.appid: a for a in installed_apps(home_lib)}
        m = scan_app(apps[100], profiles)
        exe_dir = os.path.dirname(m.exe)
        dxgi = os.path.join(exe_dir, "dxgi.dll")
        installer.install(m.to_dict(), _fake_assets(root, ow))

        settings, runtime, home = (os.path.join(root, n) for n in ("settings", "runtime", "user-home"))
        for p in (settings, runtime, home):
            os.makedirs(p)
        proc = os.path.join(root, "proc")
        os.makedirs(proc)
        glasses = dict(drm_dir=_fake_drm(root, {"DP-4": (_edid("VITURE"), ["3840x1080"])}), proc_dir=proc)
        games_file = os.path.join(settings, launch.GAMES_FILE)

        def games(enabled):
            with open(games_file, "w") as f:
                json.dump({"100": {"exe": m.exe, "dll": "dxgi", "enabled": enabled}}, f)

        games(True)
        d = launch.decide("100", settings, runtime, home, **glasses)
        assert d["MODE"] == "3d" and d["DLL"] == "dxgi" and _read(dxgi) == b"MZReShade64.dll"
        games(False)
        assert launch.decide("100", settings, runtime, home, **glasses)["MODE"] == "2d"
        assert not os.path.exists(dxgi) and os.path.exists(dxgi + installer.OFF_SUFFIX)
        assert launch.decide("300", settings, runtime, home, **glasses)["MODE"] == "off"
        games(True)
        flat = dict(drm_dir=_fake_drm(os.path.join(root, "flat"), {"DP-4": (_edid("VITURE"), ["1920x1080"])}),
                    proc_dir=proc)
        # VITURE: no 3840-wide mode in the EDID, gamescope has to generate it
        d = launch.decide("100", settings, runtime, home, **flat)   # script not loaded yet
        assert d["MODE"] == "2d" and "restart" in d["REASON"] and d["SBS"] == "gamescope"
        gamescope.install_script(home, [{"vendor": "VIT", "model": "VITURE", "rates": [60, 120]}])
        gs = os.path.join(flat["proc_dir"], "777")
        os.makedirs(gs)
        with open(os.path.join(gs, "comm"), "w") as f:
            f.write("gamescope-wl\n")
        old = os.path.getmtime(gamescope.script_path(home)) - 100
        os.utime(gs, (old, old))                                    # started before the script
        assert launch.decide("100", settings, runtime, home, **flat)["MODE"] == "2d"
        os.utime(gs, None)                                          # restarted: script loaded
        d = launch.decide("100", settings, runtime, home, **flat)
        assert d["MODE"] == "3d" and d["SBS"] == "gamescope"
        import shutil
        shutil.rmtree(gs)
        assert launch.decide("100", settings, runtime, home, **glasses)["SBS"] == "edid"
        with open(os.path.join(settings, launch.SETTINGS_FILE), "w") as f:
            json.dump({"not_glasses_names": ["VITURE"]}, f)
        assert launch.decide("100", settings, runtime, home, **glasses)["MODE"] == "2d"
        os.remove(os.path.join(settings, launch.SETTINGS_FILE))
        assert launch.decide("100", settings, runtime, home, **glasses)["MODE"] == "3d"

        # The real wrapper. No glasses on this test machine, so plain 2D: the
        # DLLs are parked and the game gets Steam's environment untouched.
        py_dir = os.path.abspath(os.path.join(HERE, "..", "py_modules"))
        wrapper = launch.write_wrapper(runtime, settings, home, py_dir)
        launch.write_2d_profile(runtime)
        assert os.access(wrapper, os.X_OK)
        env = dict(os.environ, WINEDLLOVERRIDES="winmm=n,b", LD_LIBRARY_PATH="/steam/runtime")
        env.pop("RESHADE_BASE_PATH_OVERRIDE", None)
        show = ["sh", "-c", 'printf "%s|%s|%s|%s" "$WINEDLLOVERRIDES" "${RESHADE_BASE_PATH_OVERRIDE:-none}" '
                            '"$LD_LIBRARY_PATH" "$0"', "arg0"]

        def run(appid):
            out = subprocess.run([wrapper, appid, *show], env=env, capture_output=True, text=True, timeout=30)
            assert out.returncode == 0, out.stderr
            return out.stdout.strip().split("|")

        assert run("100") == ["winmm=n,b", "none", "/steam/runtime", "arg0"]
        assert not os.path.exists(dxgi) and os.path.exists(dxgi + installer.OFF_SUFFIX)
        assert run("300") == ["winmm=n,b", "none", "/steam/runtime", "arg0"]   # unknown game: untouched

        # Folder that cannot be changed while ReShade is in place: still 2D, via the empty profile
        installer.set_active(m.exe, True)
        os.chmod(exe_dir, 0o555)
        try:
            ov, base, _, _ = run("100")
        finally:
            os.chmod(exe_dir, 0o755)
        assert ov == "d3dcompiler_47=n;dxgi=n,b;winmm=n,b"
        assert base.startswith("Z:") and base.endswith("\\reshade-2d")

        with open(os.path.join(runtime, launch.LOG_FILE)) as f:
            log = f.read()
        assert "app 100: 2d (" in log and "app 300: off" in log and "app 100: 2d-profile" in log
        assert launch.launch_token(wrapper, 100) == f'"{wrapper}" 100'


def _dtd_bytes(w, h, hblank, vblank, hz, interlaced=False):
    clock = round((w + hblank) * (h + vblank) * hz / 10_000)
    d = bytearray(18)
    d[0:2] = clock.to_bytes(2, "little")
    d[2], d[3], d[4] = w & 0xFF, hblank & 0xFF, ((w >> 8) << 4) | (hblank >> 8)
    d[5], d[6], d[7] = h & 0xFF, vblank & 0xFF, ((h >> 8) << 4) | (vblank >> 8)
    d[17] = 0x80 if interlaced else 0x18
    return bytes(d)


def test_edid_rates_and_gamescope_script():
    # Shaped like the VITURE Luma Pro EDID: 1080p and 1200p timings, 1080p@120 in the CTA block
    e = bytearray(_edid("VITURE", "CVT"))
    e[54:72] = _dtd_bytes(1920, 1080, 280, 45, 60)
    e[126] = 1
    ext = bytearray(128)
    ext[0], ext[1], ext[2] = 0x02, 3, 4 + 3
    ext[4:7] = bytes([(2 << 5) | 2, 16, 4])                        # VICs: 1080p60, 720p60
    ext[7:25] = _dtd_bytes(1920, 1080, 280, 45, 120)
    ext[25:43] = _dtd_bytes(1920, 1200, 160, 35, 90)
    ext[43:61] = _dtd_bytes(1920, 1080, 280, 45, 90, interlaced=True)
    e = bytes(e) + bytes(ext)
    assert display.edid_rates(e) == [60, 120]
    assert display.edid_rates(e, 1920, 1200) == [90]
    assert display.parse_edid(e)["rates_1080"] == [60, 120]

    text = gamescope.render_script([{"vendor": "CVT", "model": "VITURE", "rates": [120, 60, 90]}])
    assert 'model = "viture"' in text and "rates = { 59, 60, 90, 120 }" in text
    assert "local SBS_HZ = 59" in text and "set_resolution(mode, 3840, 1080)" in text
    assert "set_h_timings(mode, 176, 88, 296)" in text and "calc_max_clock(mode, 60)" in text
    t = gamescope.SBS_TIMING
    htotal = t["width"] + t["hfp"] + t["hsync"] + t["hbp"]
    vtotal = t["height"] + t["vfp"] + t["vsync"] + t["vbp"]
    assert (htotal, vtotal) == (4400, 1125) and htotal * vtotal * 60 / 1000 == 297_000
    with tempfile.TemporaryDirectory() as home:
        g = [{"vendor": "CVT", "model": "VITURE", "rates": [60, 120]}]
        assert gamescope.install_script(home, g) and not gamescope.install_script(home, g)
        assert gamescope.script_loaded(home, proc_dir=os.path.join(home, "noproc")) is None


def test_uninstall_cleanup():
    import subprocess
    import threading
    with tempfile.TemporaryDirectory() as root:
        home_lib, profiles, ow = _scenario(root)
        apps = {a.appid: a for a in installed_apps(home_lib)}
        m = scan_app(apps[100], profiles)
        exe_dir = os.path.dirname(m.exe)
        dxgi = os.path.join(exe_dir, "dxgi.dll")
        with open(dxgi, "wb") as f:
            f.write(b"games own dxgi")
        installer.install(m.to_dict(), _fake_assets(root, ow))

        home = home_lib  # the fake Steam library lives in this home too
        settings = os.path.join(home, "homebrew", "settings", "depth3d-glasses")
        runtime = os.path.join(home, "homebrew", "data", "depth3d-glasses")
        plugin = os.path.join(home, "homebrew", "plugins", "depth3d-glasses")
        for d in (settings, runtime, plugin):
            os.makedirs(d)
        with open(os.path.join(settings, launch.GAMES_FILE), "w") as f:
            json.dump({"100": {"exe": m.exe, "dll": "dxgi", "enabled": True}}, f)
        gamescope.install_script(home, [{"vendor": "CVT", "model": "VITURE", "rates": [60]}])
        main_py = os.path.join(plugin, "main.py")
        open(main_py, "w").close()

        # Unload: the game starts without ReShade while the plugin is away
        assert cleanup.installed_exes(settings) == [m.exe]
        assert cleanup.park_all(settings) == 1
        assert _read(dxgi) == b"games own dxgi"

        # The wrapper parks ReShade again after a 3D session
        installer.set_active(m.exe, True)
        launch.main(["park", "100", settings, runtime])
        assert _read(dxgi) == b"games own dxgi"

        pid_file = os.path.join(root, "guardian.pid")
        results = []

        def run_guard():
            results.append(cleanup.guard(plugin, settings, runtime, home, pid_file=pid_file, grace=0.6, step=0.05))

        # Update: the folder disappears and comes back -> the guardian keeps watching, nothing is touched
        t = threading.Thread(target=run_guard)
        t.start()
        time.sleep(0.2)
        os.remove(main_py)
        time.sleep(0.2)
        open(main_py, "w").close()
        time.sleep(0.8)
        assert t.is_alive() and os.path.isdir(settings) and os.path.exists(os.path.join(exe_dir, MARKER))

        # A newer plugin start takes over the watch
        with open(pid_file, "w") as f:
            f.write("someone else")
        t.join(2)
        assert results == [["a newer guardian took over"]]

        with open(os.path.join(exe_dir, "ReShade.log"), "w") as f:   # ReShade writes it while the game runs
            f.write("log")
        # Real uninstall: the folder stays gone -> everything back to the original state.
        # One game still has the wrapper in Steam's launch options: a pass-through stays.
        cfg = os.path.join(home_lib, ".local/share/Steam/userdata/123456789/config")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "localconfig.vdf"), "w") as f:
            f.write('"LaunchOptions" "\\"/x/depth3d-run\\" 100 %command%"')
        t = threading.Thread(target=run_guard)
        t.start()
        time.sleep(0.2)
        os.remove(main_py)
        t.join(5)
        out = results[-1]
        assert any("restored" in line for line in out)
        assert _read(dxgi) == b"games own dxgi"
        left = [f for f in os.listdir(exe_dir)
                if f.endswith((installer.OFF_SUFFIX, installer.BACKUP_SUFFIX)) or f in (MARKER, "ReShade.ini")]
        assert left == [] and "ReShade.log" not in os.listdir(exe_dir)
        assert not os.path.exists(gamescope.script_path(home))
        assert not os.path.exists(settings)
        assert sorted(os.listdir(runtime)) == ["bin"]                     # only the pass-through
        stub = os.path.join(runtime, "bin", launch.WRAPPER_NAME)
        out = subprocess.run([stub, "100", "echo", "game ran"], capture_output=True, text=True)
        assert out.stdout.strip() == "game ran"
        assert not cleanup._owned_dir(os.path.join(home, "homebrew"), home)   # never a parent folder


def test_zip_member_ranges():
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("electron.exe", os.urandom(5000))
        z.writestr("d3dcompiler_47.dll", b"MZ" + b"x" * 100000)
    data = buf.getvalue()
    calls = []

    def read(a, b):
        calls.append(b - a + 1)
        return data[a:b + 1]
    assert zip_member(read, len(data), "d3dcompiler_47.dll") == b"MZ" + b"x" * 100000
    try:
        zip_member(read, len(data), "missing.dll")
        raise AssertionError("missing member must fail")
    except Exception as e:  # noqa: BLE001
        assert "not found" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
