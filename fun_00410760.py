"""
FUN_00410760 — day-of-year style counter the binary feeds into the XOR step
(see FUN_00410760.txt in the notes).

Calling convention in Ghidra shows (wDay, wMonth) in the formal args; the year
comes in EAX in the real binary. Python API: fun_00410760(year, day, month).
"""

from __future__ import annotations

from datetime import date


def _c_leap_year(year: int) -> bool:
    """Same leap rule as the decompiler: divisible by 400, or by 4 but not by 100."""
    if year % 400 == 0:
        return True
    y4 = year & 3
    if y4 != 0:
        return False
    return year != (year // 100) * 100


def _days_jan1_to_year_start(year: int) -> int:
    """iVar6 after the year loop (base 0xB2575 plus 0x16d / 0x16e per year)."""
    i_var6 = 0
    u_var3 = 0
    if year > 1999:
        i_var6 = 0xB2575
        u_var3 = 2000
    y = u_var3
    while y < year:
        i_var6 += 0x16E if _c_leap_year(y) else 0x16D
        y += 1
    return i_var6


def fun_00410760(year: int, day: int, month: int) -> int:
    """
    Matches FUN_00410760 with the stock month table in .data (cross-checked
    against the native routine on sample dates).

    year  — what lived in EAX (wYear) in the binary.
    day   — wDay.
    month — wMonth, 1–12.
    """
    if month < 1 or month > 12 or day < 1:
        raise ValueError("invalid date components")
    d = date(year, month, day)
    # Day-of-year: Jan 1 → 1, same as iVar6 + wDay for January in C.
    doy = (d - date(year, 1, 1)).days + 1
    return _days_jan1_to_year_start(year) + doy
