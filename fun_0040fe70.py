"""
Python side of FUN_0040fe70 (see disassembly notes in 0040fe70.txt).

The same `when` drives both pieces:
  • the date string appended into the buffer (sprintf → local_a0),
  • the XOR stream and hash that become vb_check_inst.

Date layout matches the installer: DD/MM/YYYY-HH:MM:SS.mmm (day / month / year,
same ordering as SYSTEMTIME in the original code).

Why a naive port might disagree with Voicemeeter:
  • The HD: chunk is sprintf(..., "HD:%s(0x%08X)", local_870, local_3834). The %s
    is local_870 after FUN_004015b0, not a random guess from GetSystemDirectory.
    We used to paste the system-directory path there and wondered why the buffer
    differed from the real installer.
  • hd_use_windows_guess=True is a pragmatic fallback: short ANSI path for the
    system directory plus the boot volume serial — good enough for quick tests.
    For a lock-step match, capture local_870 / local_3834 from the same box.
  • The caller's prefix buffer may not be empty; the 40-byte literal region in
    the PE can differ from zero padding. In production the prefix is the full
    path to Voicemeeter8Setup.exe under the install directory.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from fun_00402940 import fun_00402940
from fun_00410760 import fun_00410760


# In FUN_0040fe70 the installer copies 40 bytes from a static literal (39 ASCII + NUL).
# Each later sprintf-style append finds the current NUL and writes over it, so the
# byte at position 39 never survives as zero — the first character of the date lands there.
# We therefore feed 39 raw ASCII bytes for the KEY, no trailing zero.
_KEY_ASCII = b"Voicemeeter, The Virtual Mixing Console"
assert len(_KEY_ASCII) == 39
# Legacy name kept for older call sites; build_payload / build_fe70_xor_body use 39 bytes.
KEY_CHUNK_40: bytes = _KEY_ASCII + b"\x00"

_OLE_DAY0 = datetime(1899, 12, 30)


def fe70_local_a0_string(when: datetime) -> str:
    """Same text the original sprintf writes into local_a0 before XOR."""
    return (
        f"{when.day:02d}/{when.month:02d}/{when.year:04d}-"
        f"{when.hour:02d}:{when.minute:02d}:{when.second:02d}.{when.microsecond // 1000:03d}"
    )


def ole_automation_date(when: datetime) -> float:
    """OLE / VB6 automation date if you need a float instead of the string form."""
    return (when - _OLE_DAY0).total_seconds() / 86400.0


def windows_hd_parts_for_fe70() -> tuple[str, int]:
    """
    Cheap stand-in for (local_870, local_3834): GetSystemDirectoryA short path
    plus the volume serial for %SystemRoot%. Windows only; not a guarantee that
    it matches FUN_004015b0 / FUN_00410460 on every machine.
    """
    if sys.platform != "win32":
        raise OSError("windows_hd_parts_for_fe70: Windows only")

    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    sysdir = ctypes.create_string_buffer(2048)
    n = k32.GetSystemDirectoryA(sysdir, ctypes.sizeof(sysdir))
    if not n:
        raise OSError("GetSystemDirectoryA", ctypes.get_last_error())

    long_path = ctypes.string_at(ctypes.addressof(sysdir), n).decode("cp1252", errors="replace")

    short = ctypes.create_string_buffer(2048)
    m = k32.GetShortPathNameA(sysdir, short, ctypes.sizeof(short))
    if m:
        path = ctypes.string_at(ctypes.addressof(short), m).decode("cp1252", errors="replace")
    else:
        path = long_path

    root = os.environ.get("SystemRoot", "C:\\Windows")
    drive = os.path.splitdrive(root)[0] + "\\"
    serial = wintypes.DWORD(0)
    ok = k32.GetVolumeInformationW(
        drive,
        None,
        0,
        ctypes.byref(serial),
        None,
        None,
        None,
        0,
    )
    if not ok:
        raise OSError("GetVolumeInformationW", ctypes.get_last_error())

    return path, int(serial.value)


def _xor_chain(buf: bytearray, seed: int) -> None:
    """
    Mirrors the FE70 XOR loop in assembly:

        loop:
          add  eax, ecx          ; eax = i2 + counter
          imul eax, eax          ; low 32 bits of (i2+counter)^2
          sar  eax, 8            ; signed shift by 8
          xor  [edx], al
          dec  ecx; inc edx
          jg   loop              ; while ecx > 0

    `sar` is signed: if bit 31 of the product was set, the high byte of the new
    seed is 0xFF, not 0x00 (unlike a logical `>>`).
    """
    n = len(buf)
    i2 = seed & 0xFFFFFFFF
    for i in range(n):
        counter = n - i  # starts at n, ends at 1 on the last byte
        s = (i2 + counter) & 0xFFFFFFFF
        prod = (s * s) & 0xFFFFFFFF
        # XOR byte from AL after SAR; sign-extension does not change AL.
        buf[i] ^= (prod >> 8) & 0xFF
        # New seed = signed SAR(prod, 8) on a 32-bit value.
        if prod & 0x80000000:
            i2 = ((prod >> 8) | 0xFF000000) & 0xFFFFFFFF
        else:
            i2 = (prod >> 8) & 0xFFFFFFFF


def parse_vb_check_inst_braced(braced: str) -> bytes:
    """Turn '{08A5...-C7F1...}' into 16 raw digest bytes."""
    s = braced.strip().strip("{}").replace("-", "").replace(" ", "")
    return bytes.fromhex(s)


def _format_braced_upper(digest16: bytes) -> str:
    if len(digest16) != 16:
        raise ValueError("digest must be 16 bytes")
    parts = [f"{b:02X}" for b in digest16]
    left = "".join(parts[:8])
    right = "".join(parts[8:])
    return f"{{{left}-{right}}}"


@dataclass
class GeneratorConfig:
    fun_00410760: Callable[[int, int, int], int] = fun_00410760
    digest_fn: Callable[[bytes, int], bytes] = fun_00402940


@dataclass(frozen=True)
class Fe70Result:
    """date_used_in_generation is the exact string baked into the buffer; hd_* is the HD: suffix."""

    body_xor: bytes
    vb_check_inst: str
    date_used_in_generation: str
    hd_label_used: str
    hd_serial_used: int


def build_fe70_xor_body(
    prefix: bytes,
    when: datetime,
    *,
    hd_label: str,
    hd_id: int,
    key_chunk: bytes = KEY_CHUNK_40,
    seed_fn: Callable[[int, int, int], int] | None = None,
    xor_seed: int | None = None,
) -> bytes:
    """
    Buffer after XOR — what FUN_00402940 digests — without running the hash.

    Pass xor_seed to override fun_00410760 when you are chasing a mismatch with
    the shipped EXE.

    In C each strcat/sprintf pass writes through the trailing NUL, so the final
    blob has no interior zeros between KEY, date, and HD. We mimic that directly:
    39 bytes KEY + 23-byte date + HD text, then XOR over everything (no extra
    terminator inside the XOR range — any NUL would live past this buffer).
    """
    seed_fn = seed_fn or fun_00410760
    date_str = fe70_local_a0_string(when)
    hd_str_no_null = f"HD:{hd_label}(0x{hd_id:08X})".encode("ascii", errors="replace")

    if not prefix:
        prefix_clean = b""
    else:
        z = prefix.find(b"\x00")
        prefix_clean = prefix if z == -1 else prefix[:z]

    body = bytearray(prefix_clean + _KEY_ASCII + date_str.encode("ascii") + hd_str_no_null)

    seed = int(xor_seed) if xor_seed is not None else seed_fn(when.year, when.day, when.month)
    _xor_chain(body, seed & 0xFFFFFFFF)
    return bytes(body)


def build_payload(
    prefix: bytes,
    *,
    when: datetime | None = None,
    key_chunk: bytes = KEY_CHUNK_40,
    hd_label: str = "",
    hd_id: int = 0,
    hd_use_windows_guess: bool = False,
    cfg: GeneratorConfig | None = None,
) -> Fe70Result:
    cfg = cfg or GeneratorConfig()
    dt = when or datetime.now()
    date_str = fe70_local_a0_string(dt)

    if hd_use_windows_guess:
        hd_label, hd_id = windows_hd_parts_for_fe70()

    body_bytes = build_fe70_xor_body(
        prefix,
        dt,
        hd_label=hd_label,
        hd_id=hd_id,
        seed_fn=cfg.fun_00410760,
    )

    digest = cfg.digest_fn(body_bytes, len(body_bytes))
    return Fe70Result(
        body_xor=body_bytes,
        vb_check_inst=_format_braced_upper(digest),
        date_used_in_generation=date_str,
        hd_label_used=hd_label,
        hd_serial_used=int(hd_id) & 0xFFFFFFFF,
    )


if __name__ == "__main__":
    r = build_payload(
        b"",
        when=datetime(2026, 1, 8, 12, 54, 56, 518_000),
        hd_use_windows_guess=(sys.platform == "win32"),
    )
    print("date_used_in_generation:", r.date_used_in_generation)
    print("hd_label_used:", r.hd_label_used)
    print("hd_serial_used:", f"0x{r.hd_serial_used:08X}")
    print("vb_check_inst:", r.vb_check_inst)
