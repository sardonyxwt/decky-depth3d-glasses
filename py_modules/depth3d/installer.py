"""Puts ReShade + SuperDepth3D next to a game's executable, and takes it out again."""

import json
import os
import shutil
import time

from .assets import Assets
from .launch import wine_path
from .scanner import MARKER

BACKUP_SUFFIX = ".depth3d-bak"
# Written by ReShade itself while the game runs, so it is not among the placed files.
RESHADE_LOG = "ReShade.log"
# Our DLLs while the game runs in plain 2D: parked under this suffix, so Wine
# never sees ReShade and the game's own files (if any) are back in place.
OFF_SUFFIX = ".depth3d-off"
SUPPORTED_DLLS = ("dxgi", "d3d9", "opengl32")


class InstallError(Exception):
    pass


def _reshade_ini(assets: Assets) -> str:
    return "\n".join([
        "[GENERAL]",
        f"EffectSearchPaths={wine_path(assets.shader_dir)}",
        f"TextureSearchPaths={wine_path(assets.texture_dir)}",
        "PresetPath=.\\ReShadePreset.ini",
        "SkipLoadingDisabledEffects=1",
        "NoReloadOnInit=0",
        "PerformanceMode=0",
        "",
        "[INPUT]",
        "KeyOverlay=36,0,0,0",   # Home
        "GamepadNavigation=1",
        "",
        "[OVERLAY]",
        "TutorialProgress=4",    # skip the first-run tutorial
        "",
    ])


def _preset_ini() -> str:
    # Stereoscopic_Mode 0 = Side by Side (half width per eye). The glasses run
    # in 3D mode at 3840x1080 and gamescope stretches the 1920x1080 frame, so
    # each eye ends up with a full 1920x1080 picture.
    return "\n".join([
        "PreprocessorDefinitions=",
        "Techniques=SuperDepth3D@SuperDepth3D.fx",
        "TechniqueSorting=SuperDepth3D@SuperDepth3D.fx",
        "",
        "[SuperDepth3D.fx]",
        "Stereoscopic_Mode=0",
        "",
    ])


def _place(src_bytes_or_path, dest: str, created: list, backups: dict, text: bool = False) -> None:
    if os.path.lexists(dest):
        bak = dest + BACKUP_SUFFIX
        if os.path.lexists(bak):
            raise InstallError(f"Backup already exists: {bak}. Remove it or uninstall first.")
        os.rename(dest, bak)
        backups[dest] = bak
    if text:
        with open(dest, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(src_bytes_or_path)
    else:
        shutil.copyfile(src_bytes_or_path, dest)
    created.append(dest)


def install(match: dict, assets: Assets, dll_override: str = "", addon: bool = True) -> dict:
    exe_dir = os.path.dirname(match["exe"])
    marker_path = os.path.join(exe_dir, MARKER)
    if os.path.isfile(marker_path):
        raise InstallError("Already installed for this game. Uninstall first to reinstall.")

    dll = dll_override or match["dll"]
    if dll == "vulkan":
        raise InstallError("Vulkan game: ReShade does not run under Wine/Proton for Vulkan titles. "
                           "Try forcing DX11/DX12 in the game's settings or launch options, "
                           "then pick the API manually.")
    if dll not in SUPPORTED_DLLS:
        raise InstallError(f"Unsupported API: {dll}")

    bits = 64 if match["is_64bit"] else 32
    if not assets.have_shaders():
        assets.update_shaders()
    reshade_dir = assets.ensure_reshade(addon=addon)
    d3dc = assets.ensure_d3dcompiler(bits)

    created: list = []
    backups: dict = {}
    log_existed = os.path.lexists(os.path.join(exe_dir, RESHADE_LOG))
    try:
        _place(os.path.join(reshade_dir, f"ReShade{bits}.dll"),
               os.path.join(exe_dir, f"{dll}.dll"), created, backups)
        _place(d3dc, os.path.join(exe_dir, "d3dcompiler_47.dll"), created, backups)
        _place(_reshade_ini(assets), os.path.join(exe_dir, "ReShade.ini"), created, backups, text=True)
        _place(_preset_ini(), os.path.join(exe_dir, "ReShadePreset.ini"), created, backups, text=True)
        marker = {
            "version": 1,
            "appid": match["appid"],
            "exe": match["exe"],
            "dll": dll,
            "bits": bits,
            "reshade": os.path.basename(reshade_dir),
            "files": created,
            "backups": backups,
            "reshade_log_existed": log_existed,
            "active": True,
            "installed_at": time.time(),
        }
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump(marker, f, indent=2)
    except Exception:
        _rollback(created, backups)
        raise
    return marker


def _rollback(created: list, backups: dict) -> None:
    for p in reversed(created):
        try:
            os.remove(p)
        except OSError:
            pass
    for orig, bak in backups.items():
        try:
            if not os.path.lexists(orig):
                os.rename(bak, orig)
        except OSError:
            pass


def read_marker(exe: str) -> dict:
    p = os.path.join(os.path.dirname(exe), MARKER)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _own_files(marker: dict, exe_dir: str):
    """Files and backups from the marker that really are in the game folder."""
    real_dir = os.path.realpath(exe_dir)
    files = [p for p in marker.get("files", [])
             if os.path.dirname(os.path.realpath(p)) == real_dir]  # never touch anything outside
    backups = {o: b for o, b in marker.get("backups", {}).items()
               if os.path.dirname(os.path.realpath(o)) == real_dir}
    return files, backups


def _write_marker(exe_dir: str, marker: dict) -> None:
    path = os.path.join(exe_dir, MARKER)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(marker, f, indent=2)
    os.replace(tmp, path)


def set_active(exe: str, active: bool) -> bool:
    """Puts ReShade's DLLs in place (3D) or parks them (plain 2D, no ReShade at all).

    Parking renames our DLLs to *.depth3d-off and brings back the game's own
    files we had backed up. Only renames, so it takes milliseconds and runs on
    every launch. Returns True when anything was moved.
    """
    exe_dir = os.path.dirname(exe)
    marker = read_marker(exe)
    if not marker:
        raise InstallError("Not installed by this plugin (marker file missing).")
    if bool(marker.get("active", True)) == bool(active):
        return False
    files, backups = _own_files(marker, exe_dir)
    dlls = [p for p in files if p.lower().endswith(".dll")]
    for p in dlls:
        off = p + OFF_SUFFIX
        bak = backups.get(p)
        if active:
            if not os.path.lexists(off):
                continue
            if os.path.lexists(p):  # the game's own file, back to its backup name
                if bak is None or os.path.lexists(bak):
                    raise InstallError(f"Cannot switch to 3D: {p} is in the way")
                os.rename(p, bak)
            os.rename(off, p)
        else:
            if os.path.lexists(off):
                raise InstallError(f"Cannot switch to 2D: {off} already exists")
            if os.path.lexists(p):
                os.rename(p, off)
            if bak and os.path.lexists(bak) and not os.path.lexists(p):
                os.rename(bak, p)
    marker["active"] = bool(active)
    _write_marker(exe_dir, marker)
    return True


def uninstall(exe: str) -> dict:
    exe_dir = os.path.dirname(exe)
    marker_path = os.path.join(exe_dir, MARKER)
    marker = read_marker(exe)
    if not marker:
        raise InstallError("Not installed by this plugin (marker file missing).")
    if not marker.get("active", True):
        set_active(exe, True)
        marker = read_marker(exe)
    files, backups = _own_files(marker, exe_dir)
    _rollback(files, backups)
    log = os.path.join(exe_dir, RESHADE_LOG)
    if not marker.get("reshade_log_existed") and os.path.isfile(log):
        os.remove(log)
    os.remove(marker_path)
    return marker
