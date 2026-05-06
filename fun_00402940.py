"""
FUN_00402940 (see FUN_00402940.txt) — a big custom digest with hundreds of DAT_* reads.

Startup init from FUN_0041be00 (before EnumWindows in that build):
  DAT_00432d4c  = 0x100          → low byte 0x00, high byte of the word 0x01 (offsets into puVar40)
  _DAT_00432d48 = 0x003020100    → bytes 0..3 at 00432d48..4b
  _DAT_00432d50 = 0x007060504    → bytes 4..7 at 00432d50..53
  DAT_004338f8  = 0x4115E433     → uVar17 = param_2 * 0xB + DAT_004338f8; zero means early-out

That handful of constants is not enough for a faithful Python port: FUN_00402940
touches many more DWORDs in .data (0x00432d51 onward). Those tables are whatever
the linker laid down, not just those four writes.

If you need a perfect match against the EXE, pick one approach:
  1) Ghidra memory export / dump of .data (and maybe .rdata) covering 0x00432d40
     through the highest DAT_00432d* you care about, then resolve addresses as
     base + RVA in Python.
  2) Call the native FUN_00402940 (ctypes + LoadLibrary on the same PE, RVA 0x402940).
  3) Full-image emulation (Unicorn, etc.).

Below keeps the same (buffer, length) signature but returns MD5 as a placeholder.
"""

from __future__ import annotations

import hashlib

# From FUN_0041be00 post FUN_004148a0 — needed for a real port; unused by the MD5 stub.
DAT_004338f8: int = 0x4115E433
DAT_00432d4C: int = 0x100
# Packed dwords the way Ghidra names overlapping symbols.
DAT_PACK_00432D48: int = 0x003020100
DAT_PACK_00432D50: int = 0x007060504


def fun_00402940(param_1: bytes, param_2: int) -> bytes:
    """
    param_1 — input bytes, param_2 — active length (C-style third arg omitted here).

    The real routine gates on DAT_004338f8; when it is zero the binary skips
    writing the output buffer.
    """
    if DAT_004338f8 == 0:
        return bytes(16)
    chunk = param_1[:param_2] if param_2 >= 0 else param_1
    return hashlib.md5(chunk).digest()
