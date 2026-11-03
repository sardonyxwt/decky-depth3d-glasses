"""Minimal PE reader: bitness and imported DLL names. Standard library only."""

import struct
from dataclasses import dataclass, field
from typing import List, Optional

MACHINE_I386 = 0x014C
MACHINE_AMD64 = 0x8664


@dataclass
class PEInfo:
    machine: int
    is_64bit: bool
    imports: List[str] = field(default_factory=list)


def _rva_to_offset(rva: int, sections) -> Optional[int]:
    for va, vsize, raw_ptr, raw_size in sections:
        size = max(vsize, raw_size)
        if va <= rva < va + size:
            return rva - va + raw_ptr
    return None


def _read_cstr(f, offset: int, limit: int = 256) -> str:
    f.seek(offset)
    raw = f.read(limit)
    end = raw.find(b"\0")
    if end >= 0:
        raw = raw[:end]
    return raw.decode("ascii", errors="replace")


def read_pe(path: str) -> Optional[PEInfo]:
    """Return PEInfo, or None when the file is not a PE image we understand."""
    try:
        with open(path, "rb") as f:
            dos = f.read(64)
            if len(dos) < 64 or dos[:2] != b"MZ":
                return None
            e_lfanew = struct.unpack_from("<I", dos, 0x3C)[0]
            f.seek(e_lfanew)
            if f.read(4) != b"PE\0\0":
                return None
            coff = f.read(20)
            if len(coff) < 20:
                return None
            machine, n_sections, _, _, _, opt_size, _ = struct.unpack("<HHIIIHH", coff)
            opt = f.read(opt_size)
            if len(opt) < 2:
                return None
            magic = struct.unpack_from("<H", opt, 0)[0]
            if magic == 0x20B:  # PE32+
                is_64 = True
                dd_offset = 112
            elif magic == 0x10B:  # PE32
                is_64 = False
                dd_offset = 96
            else:
                return None
            info = PEInfo(machine=machine, is_64bit=is_64 or machine == MACHINE_AMD64)

            sections = []
            for _ in range(n_sections):
                sh = f.read(40)
                if len(sh) < 40:
                    break
                vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", sh, 8)
                sections.append((va, vsize, raw_ptr, raw_size))

            # Data directory 1 = import table
            if len(opt) < dd_offset + 16:
                return info
            imp_rva, imp_size = struct.unpack_from("<II", opt, dd_offset + 8)
            if not imp_rva:
                return info
            off = _rva_to_offset(imp_rva, sections)
            if off is None:
                return info
            names = []
            for i in range(512):  # hard cap
                f.seek(off + i * 20)
                desc = f.read(20)
                if len(desc) < 20:
                    break
                oft, _, _, name_rva, ft = struct.unpack("<IIIII", desc)
                if not (oft or name_rva or ft):
                    break
                name_off = _rva_to_offset(name_rva, sections)
                if name_off is not None:
                    names.append(_read_cstr(f, name_off).lower())
            info.imports = names
            return info
    except (OSError, struct.error):
        return None


def guess_reshade_dll(info: Optional[PEInfo]) -> str:
    """Name (without .dll) ReShade should be installed as for this executable.

    Returns one of: "dxgi" (D3D10/11/12), "d3d9", "opengl32", "vulkan".
    Games that load D3D dynamically import nothing obvious; dxgi is the
    best default for modern titles. Unreal Engine 4/5 executables import
    d3d9.dll (for D3DPERF_* markers) and opengl32.dll next to d3d11/d3d12,
    so D3D10+ wins over D3D9 and OpenGL.
    """
    if info is None:
        return "dxgi"
    imp = set(info.imports)
    if imp & {"d3d11.dll", "d3d12.dll", "d3d10.dll", "d3d10_1.dll"}:
        return "dxgi"
    if "d3d9.dll" in imp:
        return "d3d9"
    if "dxgi.dll" in imp:
        return "dxgi"
    if "opengl32.dll" in imp:
        return "opengl32"
    if "vulkan-1.dll" in imp:
        return "vulkan"
    return "dxgi"
