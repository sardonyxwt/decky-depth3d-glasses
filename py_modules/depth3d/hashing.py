"""ReShade's __APPLICATION__ value.

ReShade defines it as std::hash<std::string>()(exe_stem) & 0xFFFFFFFF
(source/runtime.cpp). MSVC's std::hash is FNV-1a, so the value depends on
the bitness of the ReShade build that the game loads:

* 64-bit game -> ReShade64.dll -> 64-bit FNV-1a, truncated to the low 32 bits
* 32-bit game -> ReShade32.dll -> 32-bit FNV-1a

The stem is the executable file name without ".exe", case-sensitive,
exactly as it is on disk.
"""

_FNV64_OFFSET = 14695981039346656037
_FNV64_PRIME = 1099511628211
_FNV32_OFFSET = 2166136261
_FNV32_PRIME = 16777619
_M64 = 0xFFFFFFFFFFFFFFFF
_M32 = 0xFFFFFFFF


def fnv1a64_low32(text: str) -> int:
    h = _FNV64_OFFSET
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * _FNV64_PRIME) & _M64
    return h & _M32


def fnv1a32(text: str) -> int:
    h = _FNV32_OFFSET
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * _FNV32_PRIME) & _M32
    return h


def app_hash(exe_stem: str, is_64bit: bool) -> int:
    return fnv1a64_low32(exe_stem) if is_64bit else fnv1a32(exe_stem)
