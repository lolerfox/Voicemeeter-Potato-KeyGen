"""
Call FUN_00402940 inside Voicemeeter8Setup.exe (RVA 0x2940, VA 0x402940).

Heads-up:
  • The file is 32-bit PE (machine 0x14C). You must load it from a 32-bit Python
    process. 64-bit Python gets ERROR_BAD_EXE_FORMAT (193) from LoadLibrary.
  • Globals like DAT_004338f8, DAT_00432d4c, … are initialized by FUN_0041be00
    before hashing runs. LoadLibrary does not execute CRT startup, so we poke the
    same constants FUN_0041be00 writes (see lines 39–43 in those notes).

Run with 32-bit Python (full path to python.exe if you avoid the launcher):
  "C:\\Users\\...\\Python312-32\\python.exe" voicemeeter_hash_native.py

If the Python Launcher is installed:
  py --list
  py -3.12-32 voicemeeter_hash_native.py
(Avoid a bare `-3` flag on older wrappers — some do not support it.)

Dependencies: ctypes; bind_pe_imports needs pefile (`pip install pefile`).
"""

from __future__ import annotations

import ctypes
import re
import struct
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from fun_0040fe70 import GeneratorConfig, build_payload


def _parse_when_argv() -> datetime | None:
    """
    Optional argv[2]: timestamp used when demo-printing vb_check_inst.

    Accepted: ISO 2026-01-08T12:54:56.518
    installer style 08/01/2026-12:54:56.518
    or with a space 08/01/2026 12:54:56.518
    """
    if len(sys.argv) < 3:
        return None
    s = sys.argv[2].strip()
    if not s:
        return None
    m = re.fullmatch(
        r"(\d{2})/(\d{2})/(\d{4})-(\d{2}):(\d{2}):(\d{2})\.(\d{1,3})",
        s,
    )
    if m:
        day, month, year, hh, mm, ss, ms = map(int, m.groups())
        return datetime(year, month, day, hh, mm, ss, min(ms, 999) * 1000)
    if s.count(" ") == 1 and "/" in s.split()[0]:
        d, t = s.split()
        day, month, year = map(int, d.split("/"))
        hms, _, ms = t.partition(".")
        hh, mm, ss = map(int, hms.split(":"))
        msec = int(ms[:3].ljust(3, "0")[:3]) if ms else 0
        return datetime(year, month, day, hh, mm, ss, msec * 1000)
    return datetime.fromisoformat(s)

# Default: Voicemeeter8Setup.exe next to this script.
_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_EXE = str(_SCRIPT_DIR / "Voicemeeter8Setup.exe")

# From FUN_0041be00 after FUN_004148a0
RVA_DAT_00432D4C = 0x32D4C
RVA__DAT_00432D48 = 0x32D48
RVA__DAT_00432D50 = 0x32D50
RVA_DAT_004338F8 = 0x338F8

VAL_DAT_00432D4C = 0x100
VAL__DAT_00432D48 = 0x003020100
VAL__DAT_00432D50 = 0x007060504
VAL_DAT_004338F8 = 0x4115E433

# FUN_00402940
RVA_FUN_00402940 = 0x2940

# First bytes of the function (sanity check that this is the build we expect)
EXPECTED_FUN_HEAD = bytes.fromhex("558becb878810000e8")

# Voicemeeter8Setup.exe builds this tool's RVA offsets and EXPECTED_FUN_HEAD were
# reverse-engineered against. A different FileVersion doesn't guarantee a broken
# offset, but it's the first thing to check if the prologue check below ever
# starts failing after a VB-Audio update.
KNOWN_GOOD_EXE_VERSIONS = {"3.1.2.2"}


def get_exe_version(exe_path: str) -> str | None:
    """Read FileVersion (fallback ProductVersion) from the PE's VERSIONINFO resource."""
    try:
        import pefile
    except ImportError:
        return None
    try:
        pe = pefile.PE(exe_path, fast_load=True)
    except Exception:
        return None
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        for fileinfo in getattr(pe, "FileInfo", []):
            for entry in fileinfo:
                for st in getattr(entry, "StringTable", []):
                    raw = st.entries.get(b"FileVersion") or st.entries.get(b"ProductVersion")
                    if raw:
                        text = raw.decode(errors="replace").strip()
                        # Some VB6/Delphi-style builds store this as "3, 1, 2, 2" instead of "3.1.2.2".
                        parts = re.split(r"[,\s]+", text)
                        if len(parts) > 1 and all(p.isdigit() for p in parts):
                            return ".".join(parts)
                        return text
    except Exception:
        return None
    finally:
        pe.close()
    return None


def _is_32bit_process() -> bool:
    return struct.calcsize("P") == 4


def _py32_launcher_tag() -> str:
    """`py` launcher tag for the 32-bit build of *this* Python version, e.g. '-3.14-32'."""
    v = sys.version_info
    return f"-{v.major}.{v.minor}-32"


def _pip_install_hint(package: str) -> str:
    """Install hint for the *current* interpreter (used once we know we're 32-bit)."""
    return f'"{sys.executable}" -m pip install {package}'


def _write_u32(base: int, rva: int, value: int) -> None:
    addr = base + rva
    ctypes.c_uint32.from_address(addr).value = value & 0xFFFFFFFF


_PAGE = 4096


def _covering_pages(addr: int, nbytes: int = 4) -> tuple[int, int]:
    lo = (addr // _PAGE) * _PAGE
    hi = ((addr + nbytes - 1) // _PAGE) * _PAGE
    return lo, hi - lo + _PAGE


def bind_pe_imports(base: int, pe_path: str) -> None:
    """
    Fill IAT slots after LoadLibrary(EXE).

    Some installers ship thunk placeholders like 0x2D5A2 in the image. When the
    module loads, those never resolve to real APIs unless we patch them — calls
    would AV otherwise.
    """
    try:
        import pefile
    except ImportError as e:
        raise OSError(
            f"pefile is required for native hashing. Install it for this interpreter: "
            f"{_pip_install_hint('pefile')}"
        ) from e

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LoadLibraryA = k32.LoadLibraryA
    LoadLibraryA.argtypes = (ctypes.c_char_p,)
    LoadLibraryA.restype = wintypes.HMODULE
    GetProcAddress = k32.GetProcAddress
    GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
    GetProcAddress.restype = wintypes.LPVOID
    VirtualProtect = k32.VirtualProtect
    VirtualProtect.argtypes = [
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    VirtualProtect.restype = wintypes.BOOL

    pe = pefile.PE(pe_path)
    try:
        image_base = pe.OPTIONAL_HEADER.ImageBase
        if getattr(pe, "DIRECTORY_ENTRY_IMPORT", None) is None:
            return
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            h_dll = LoadLibraryA(entry.dll)
            if not h_dll:
                raise OSError(
                    f"LoadLibraryA({entry.dll!r}): {ctypes.get_last_error()}"
                )
            for imp in entry.imports:
                if imp.name:
                    proc = GetProcAddress(h_dll, ctypes.c_char_p(imp.name))
                elif imp.ordinal is not None:
                    proc = GetProcAddress(h_dll, ctypes.c_void_p(imp.ordinal))
                else:
                    continue
                slot_va = imp.address
                if not proc or not slot_va:
                    continue
                # pefile reports the IAT slot VA at PE ImageBase (e.g. 0x4291cc), not always plain RVA.
                rva_thunk = (
                    slot_va - image_base if slot_va >= image_base else slot_va
                )
                addr = base + rva_thunk
                lo, ln = _covering_pages(addr, 4)
                old_prot = wintypes.DWORD()
                if not VirtualProtect(lo, ln, 0x40, ctypes.byref(old_prot)):
                    raise OSError(
                        "VirtualProtect IAT", ctypes.get_last_error()
                    )
                try:
                    ctypes.c_uint32.from_address(addr).value = (
                        ctypes.cast(proc, ctypes.c_void_p).value or 0
                    ) & 0xFFFFFFFF
                finally:
                    VirtualProtect(lo, ln, old_prot.value, ctypes.byref(old_prot))
    finally:
        pe.close()


def _verify_fun_prologue(base: int, exe_path: str) -> None:
    addr = base + RVA_FUN_00402940
    buf = (ctypes.c_uint8 * len(EXPECTED_FUN_HEAD)).from_address(addr)
    got = bytes(buf)
    if got != EXPECTED_FUN_HEAD:
        version = get_exe_version(exe_path) or "unknown"
        raise RuntimeError(
            f"FUN_00402940 prologue mismatch (expected {EXPECTED_FUN_HEAD.hex()}, got {got.hex()}).\n"
            f"  Detected Voicemeeter8Setup.exe version: {version}\n"
            f"  Verified against version(s): {', '.join(sorted(KNOWN_GOOD_EXE_VERSIONS))}\n"
            f"  Different installer build — the RVA offsets this tool relies on may have moved."
        )
    version = get_exe_version(exe_path)
    if version and version not in KNOWN_GOOD_EXE_VERSIONS:
        print(
            f"WARN: Voicemeeter8Setup.exe version {version} is not in the verified set "
            f"({', '.join(sorted(KNOWN_GOOD_EXE_VERSIONS))}). The FUN_00402940 prologue still "
            f"matched, but run 'validate --from-registry' to double-check the digest.",
            file=sys.stderr,
        )


class NativeFe70HashSession:
    """One LoadLibrary + one function pointer — reuse for many digests in a row."""

    def __init__(self, exe_path: str = DEFAULT_EXE) -> None:
        if not _is_32bit_process():
            raise OSError(
                f"Need 32-bit Python: Voicemeeter8Setup.exe is 32-bit PE; a "
                f"{struct.calcsize('P') * 8}-bit interpreter cannot LoadLibrary it (error 193). "
                f"Install the 32-bit build of Python {sys.version_info.major}.{sys.version_info.minor} "
                f"and run with: py {_py32_launcher_tag()} ..."
            )
        self.exe_path = exe_path
        self._h: int | None = None
        self._fn = None

    def __enter__(self) -> NativeFe70HashSession:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LoadLibraryW = kernel32.LoadLibraryW
        LoadLibraryW.argtypes = (wintypes.LPCWSTR,)
        LoadLibraryW.restype = wintypes.HMODULE
        h = LoadLibraryW(self.exe_path)
        if not h:
            raise OSError(f"LoadLibraryW failed: error {ctypes.get_last_error()}")
        self._h = h
        base = h
        bind_pe_imports(base, self.exe_path)
        _write_u32(base, RVA_DAT_00432D4C, VAL_DAT_00432D4C)
        _write_u32(base, RVA__DAT_00432D48, VAL__DAT_00432D48)
        _write_u32(base, RVA__DAT_00432D50, VAL__DAT_00432D50)
        _write_u32(base, RVA_DAT_004338F8, VAL_DAT_004338F8)
        _verify_fun_prologue(base, self.exe_path)
        Fun = ctypes.CFUNCTYPE(
            None,
            wintypes.LPVOID,
            wintypes.INT,
            wintypes.LPVOID,
        )
        self._fn = Fun(base + RVA_FUN_00402940)
        return self

    def digest(self, data: bytes) -> bytes:
        if self._fn is None:
            raise RuntimeError("use NativeFe70HashSession as context manager")
        out = (ctypes.c_uint8 * 16)()
        buf = (ctypes.c_uint8 * len(data))(*data)
        self._fn(ctypes.byref(buf), len(data), ctypes.byref(out))
        return bytes(out)

    def __exit__(self, *args: object) -> None:
        if self._h:
            ctypes.WinDLL("kernel32").FreeLibrary(self._h)
        self._h = None
        self._fn = None


def fun_00402940_native(data: bytes, exe_path: str = DEFAULT_EXE) -> bytes:
    """
    Calls native FUN_00402940(buffer, len, out).
    Returns the 16-byte output (same shape as C param_3).
    """
    with NativeFe70HashSession(exe_path) as s:
        return s.digest(data)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXE
    if not _is_32bit_process():
        print("Current interpreter:", struct.calcsize("P") * 8, "bit", file=sys.stderr)
        print(
            "You need 32-bit (x86) Python. Grab the \"Windows installer (32-bit)\" from python.org,\n"
            "then run this script with that interpreter explicitly, e.g.:\n"
            r'  "C:\Users\YOU\AppData\Local\Programs\Python\PythonXXX-32\python.exe" '
            f'"{__file__}" "{path}"\n'
            "\n"
            "With the stock py.exe launcher, list tags and pick the *-32 line:\n"
            "  py --list\n"
            "Example (32-bit build of the version you're running now):\n"
            f'  py {_py32_launcher_tag()} "{__file__}" "{path}"\n'
            "\n"
            "\"Unknown option: -3\" usually means the `py` command in PATH is not the "
            "Python launcher, or you passed `-3` without an architecture tag.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:

        def _digest_native(data: bytes, ln: int) -> bytes:
            return fun_00402940_native(data[:ln], path)

        when = _parse_when_argv()
        if when is None:
            when = datetime.now()

        # HD: line matches the installer's Windows guess path (fun_0040fe70.hd_use_windows_guess)
        r = build_payload(
            b"",
            when=when,
            hd_use_windows_guess=True,
            cfg=GeneratorConfig(digest_fn=_digest_native),
        )
    except OSError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    print("date_used_in_generation:", r.date_used_in_generation)
    print("hd_label_used:", r.hd_label_used)
    print("hd_serial_used:", f"0x{r.hd_serial_used:08X}")
    print("vb_check_inst:", r.vb_check_inst)


if __name__ == "__main__":
    main()
