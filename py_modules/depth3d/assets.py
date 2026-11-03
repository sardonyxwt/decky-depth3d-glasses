"""Downloads the third-party pieces on the user's own machine.

Nothing here is bundled with the plugin:
  * ReShade (with add-on support) from reshade.me
  * d3dcompiler_47.dll (Microsoft's redistributable) out of the official
    Electron Windows zip on GitHub. Only the one file is read, with HTTP range
    requests, so no 7-Zip is needed. Mozilla's Firefox 62 installer (the
    winetricks source) is the fallback when 7-Zip is installed.
  * SuperDepth3D + Overwatch from the official Depth3D GitHub repository.
    Depth3D is free for personal use and must not be redistributed, which is
    why it is fetched here instead of shipped.
"""

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import struct
import tempfile
import time
import urllib.request
import zipfile
import zlib
from typing import Callable, Optional

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) decky-depth3d-glasses"

RESHADE_SITES = ["https://reshade.me", "https://static.reshade.me"]
RESHADE_FALLBACK_VERSION = "6.8.0"

DEPTH3D_RAW = "https://raw.githubusercontent.com/BlueSkyDefender/Depth3D/master/Shaders/"
DEPTH3D_FILES = ["SuperDepth3D.fx", "Overwatch.fxh", "AXAA.fxh", "Sentinel.fxh"]

ELECTRON_URL = ("https://github.com/electron/electron/releases/download/v33.4.11/"
                "electron-v33.4.11-win32-{arch}.zip")
ELECTRON_ARCH = {32: "ia32", 64: "x64"}
D3DCOMPILER_SHA256 = {  # d3dcompiler_47.dll inside the Electron 33.4.11 zips
    32: "2438b520ac961e38c5852779103734be373ee2b6d1e5a7a5d49248b52acc7c4d",
    64: "af7b99be1b8770c0e4d18e43b04e81d11bdeb667fa6b07ade7a88f4c5676bf9a",
}

FIREFOX_URL = ("https://download-installer.cdn.mozilla.net/pub/firefox/releases/62.0.3/"
               "win{bits}/ach/Firefox%20Setup%2062.0.3.exe")
FIREFOX_SHA256 = {
    32: "d6edb4ff0a713f417ebd19baedfe07527c6e45e84a6c73ed8c66a33377cc0aca",
    64: "721977f36c008af2b637aedd3f1b529f3cfed6feb10f68ebe17469acb1934986",
}


class AssetError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # Decky's bundled Python may not find the system CA store on its own.
    for cafile in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if os.path.isfile(cafile):
            try:
                ctx.load_verify_locations(cafile)
            except ssl.SSLError:
                pass
            break
    return ctx


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001 - surfaced to the UI as text
        raise AssetError(f"Download failed: {url} ({e})") from e


def http_range(url: str, start: int, end: int, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            if r.status != 206:
                raise AssetError(f"Server ignored the range request: {url}")
            return r.read()
    except AssetError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AssetError(f"Download failed: {url} ({e})") from e


def http_size(url: str, timeout: int = 60) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            return int(r.headers["Content-Length"])
    except Exception as e:  # noqa: BLE001
        raise AssetError(f"Download failed: {url} ({e})") from e


def zip_member(read: Callable[[int, int], bytes], size: int, name: str) -> bytes:
    """One file out of a zip, reading only the byte ranges it needs.

    read(start, end) returns bytes start..end inclusive. Plain zip only
    (no ZIP64), which is what Electron publishes.
    """
    tail_start = max(0, size - 65536)
    tail = read(tail_start, size - 1)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise AssetError("Not a zip archive (end of central directory not found)")
    cd_size, cd_off = struct.unpack_from("<II", tail, i + 12)
    cd = read(cd_off, cd_off + cd_size - 1)
    p = 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b"PK\x01\x02":
        method, = struct.unpack_from("<H", cd, p + 10)
        crc, csize, _usize, nlen, elen, clen = struct.unpack_from("<IIIHHH", cd, p + 16)
        local_off, = struct.unpack_from("<I", cd, p + 42)
        fname = cd[p + 46:p + 46 + nlen].decode("utf-8", errors="replace")
        p += 46 + nlen + elen + clen
        if fname != name:
            continue
        lh = read(local_off, local_off + 29)
        lnlen, lelen = struct.unpack_from("<HH", lh, 26)
        start = local_off + 30 + lnlen + lelen
        raw = read(start, start + csize - 1) if csize else b""
        if method == 8:
            data = zlib.decompress(raw, -15)
        elif method == 0:
            data = raw
        else:
            raise AssetError(f"{name}: unsupported zip compression {method}")
        if zlib.crc32(data) & 0xFFFFFFFF != crc:
            raise AssetError(f"{name}: CRC mismatch")
        return data
    raise AssetError(f"{name} not found in the archive")


def _clean_env() -> dict:
    # Decky runs from a PyInstaller bundle; its LD_LIBRARY_PATH breaks system tools.
    env = dict(os.environ)
    for k in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"):
        env.pop(k, None)
    env["PATH"] = env.get("PATH", "") + ":/usr/bin:/bin:/usr/local/bin"
    return env


def find_7z() -> Optional[str]:
    env = _clean_env()
    for name in ("7z", "7zz", "7za"):
        p = shutil.which(name, path=env["PATH"])
        if p:
            return p
    return None


def _extract_with_7z(archive: str, out_dir: str, *patterns: str) -> None:
    exe = find_7z()
    if not exe:
        raise AssetError("7-Zip not found. Install it: sudo apt install 7zip  (or p7zip-full)")
    cmd = [exe, "e", "-y", f"-o{out_dir}", archive, *patterns, "-r"]
    res = subprocess.run(cmd, env=_clean_env(), capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise AssetError(f"7-Zip failed: {res.stderr.strip() or res.stdout.strip()}")


class Assets:
    def __init__(self, runtime_dir: str):
        self.root = runtime_dir
        self.reshade_dir = os.path.join(runtime_dir, "reshade")
        self.d3dc_dir = os.path.join(runtime_dir, "d3dcompiler")
        self.shader_dir = os.path.join(runtime_dir, "shaders")
        self.texture_dir = os.path.join(runtime_dir, "textures")
        self.state_file = os.path.join(runtime_dir, "assets.json")
        for d in (self.reshade_dir, self.d3dc_dir, self.shader_dir, self.texture_dir):
            os.makedirs(d, exist_ok=True)

    # ---------- state ----------
    def state(self) -> dict:
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, **kw) -> None:
        s = self.state()
        s.update(kw)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, self.state_file)

    # ---------- shaders ----------
    @property
    def overwatch_path(self) -> str:
        return os.path.join(self.shader_dir, "Overwatch.fxh")

    def have_shaders(self) -> bool:
        return all(os.path.isfile(os.path.join(self.shader_dir, f)) for f in DEPTH3D_FILES)

    def update_shaders(self) -> None:
        fetched = {f: http_get(DEPTH3D_RAW + f) for f in DEPTH3D_FILES}
        for f, data in fetched.items():
            if len(data) < 200:
                raise AssetError(f"{f} looks empty, Depth3D repository layout may have changed")
            tmp = os.path.join(self.shader_dir, f + ".tmp")
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, os.path.join(self.shader_dir, f))
        self._save_state(shaders_updated=time.time())

    # ---------- ReShade ----------
    def latest_reshade_version(self, addon: bool = True) -> str:
        pat = re.compile(r"/downloads/ReShade_Setup_([0-9][0-9.]*[0-9])(_Addon)?\.exe")
        for site in RESHADE_SITES:
            try:
                html = http_get(site, timeout=15).decode("utf-8", errors="replace")
            except AssetError:
                continue
            for m in pat.finditer(html):
                if bool(m.group(2)) == addon:
                    return m.group(1)
        return RESHADE_FALLBACK_VERSION

    def reshade_path(self, version: str, addon: bool) -> str:
        return os.path.join(self.reshade_dir, version + ("_Addon" if addon else ""))

    def installed_reshade(self) -> Optional[str]:
        return self.state().get("reshade")

    def ensure_reshade(self, addon: bool = True, force_check: bool = False) -> str:
        current = self.installed_reshade()
        if (current and not force_check and current.endswith("_Addon") == addon
                and os.path.isfile(os.path.join(self.reshade_dir, current, "ReShade64.dll"))):
            return os.path.join(self.reshade_dir, current)
        version = self.latest_reshade_version(addon)
        tag = version + ("_Addon" if addon else "")
        dest = os.path.join(self.reshade_dir, tag)
        if not (os.path.isfile(os.path.join(dest, "ReShade64.dll"))
                and os.path.isfile(os.path.join(dest, "ReShade32.dll"))):
            data = None
            last_err = None
            for site in RESHADE_SITES:
                try:
                    data = http_get(f"{site}/downloads/ReShade_Setup_{tag}.exe", timeout=120)
                    break
                except AssetError as e:
                    last_err = e
            if data is None:
                raise last_err or AssetError("ReShade download failed")
            if data[:2] != b"MZ":
                raise AssetError(f"ReShade {tag}: the download is not a Windows executable")
            with tempfile.TemporaryDirectory(dir=self.root) as tmp:
                exe = os.path.join(tmp, "setup.exe")
                with open(exe, "wb") as f:
                    f.write(data)
                out = os.path.join(tmp, "out")
                os.makedirs(out)
                try:
                    with zipfile.ZipFile(exe) as z:  # the setup carries a zip payload
                        for n in ("ReShade64.dll", "ReShade32.dll"):
                            with z.open(n) as src, open(os.path.join(out, n), "wb") as dst:
                                shutil.copyfileobj(src, dst)
                except (zipfile.BadZipFile, KeyError):
                    _extract_with_7z(exe, out, "ReShade64.dll", "ReShade32.dll")
                for n in ("ReShade64.dll", "ReShade32.dll"):
                    if not os.path.isfile(os.path.join(out, n)):
                        raise AssetError(f"{n} not found inside the ReShade installer")
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                shutil.move(out, dest)
        self._save_state(reshade=tag, reshade_checked=time.time())
        return dest

    # ---------- d3dcompiler_47 ----------
    def d3dcompiler_path(self, bits: int) -> str:
        return os.path.join(self.d3dc_dir, f"d3dcompiler_47.dll.{bits}")

    def ensure_d3dcompiler(self, bits: int) -> str:
        target = self.d3dcompiler_path(bits)
        if os.path.isfile(target):
            return target
        try:
            data = self._d3dcompiler_from_electron(bits)
        except AssetError:
            if not find_7z():
                raise
            self._d3dcompiler_from_firefox(bits, target)
            return target
        tmp = target + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        return target

    def _d3dcompiler_from_electron(self, bits: int) -> bytes:
        url = ELECTRON_URL.format(arch=ELECTRON_ARCH[bits])
        data = zip_member(lambda a, b: http_range(url, a, b), http_size(url), "d3dcompiler_47.dll")
        digest = hashlib.sha256(data).hexdigest()
        if digest != D3DCOMPILER_SHA256[bits]:
            raise AssetError(f"d3dcompiler_47 {bits}-bit checksum mismatch ({digest})")
        return data

    def _d3dcompiler_from_firefox(self, bits: int, target: str) -> None:
        data = http_get(FIREFOX_URL.format(bits=bits), timeout=300)
        digest = hashlib.sha256(data).hexdigest()
        if digest != FIREFOX_SHA256[bits]:
            raise AssetError(f"Firefox {bits}-bit installer checksum mismatch ({digest})")
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            exe = os.path.join(tmp, "firefox.exe")
            with open(exe, "wb") as f:
                f.write(data)
            _extract_with_7z(exe, tmp, "d3dcompiler_47.dll")
            src = os.path.join(tmp, "d3dcompiler_47.dll")
            if not os.path.isfile(src):
                raise AssetError("d3dcompiler_47.dll not found in the Firefox installer")
            shutil.copyfile(src, target)

    def summary(self) -> dict:
        s = self.state()
        return {
            "reshade": s.get("reshade"),
            "shaders": self.have_shaders(),
            "shaders_updated": s.get("shaders_updated"),
            "d3dcompiler64": os.path.isfile(self.d3dcompiler_path(64)),
            "d3dcompiler32": os.path.isfile(self.d3dcompiler_path(32)),
            "has_7z": find_7z() is not None,
        }
