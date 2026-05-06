"""
voicemeeter_genius.py — build a consistent (vbDateInst, vbCheckInst) pair instead
of chasing a single "golden" hash. Pieces involved:

  1. Byte-accurate XOR buffer construction (fun_0040fe70.build_fe70_xor_body),
     with fixes for quirks we saw in the original:
       - the KEY is 39 ASCII bytes only (sprintf overwrites the null terminator);
       - XOR uses signed arithmetic right shift (sar), not logical shift (shr).
  2. The real FUN_00402940 from Voicemeeter8Setup.exe itself, via
     voicemeeter_hash_native.NativeFe70HashSession, so the digest matches the
     installer bit for bit — no need to reimplement the hash in Python.

PREFIX (important):
  In native FUN_0040fe70 the buffer you hash is `this` (ECX). In the installer
  that is the full path to Voicemeeter8Setup.exe under Program Files, e.g.
  "C:\\Program Files (x86)\\VB\\Voicemeeter\\Voicemeeter8Setup.exe".
  The KEY, date string, and HD chunk are appended to that, then XOR + hash.
  So vbCheckInst depends on install path: same date and disk, different path
  → different hash on two machines.

  We read the prefix from the ARP Uninstall key as UninstallString. If that
  is missing, pass --prefix or fall back to the default path under Program
  Files (x86).

CLI:
  gen       generate a pair for (date, label, serial, prefix)
  now       same as gen but time = now and system label/serial
  extract   read whatever is already in the registry (if installed)
  validate  given (date, hash) [and optional label, serial, prefix], check we match
  selftest  quick sanity check for buffer layout and XOR seed

Native hash needs 32-bit Python (the setup EXE is 32-bit PE):
  py -3.12-32 voicemeeter_genius.py now
  py -3.12-32 voicemeeter_genius.py validate --from-registry
"""
from __future__ import annotations

import argparse
import re
import sys
import winreg
from datetime import datetime

from fun_0040fe70 import build_fe70_xor_body, parse_vb_check_inst_braced
from fun_00410760 import fun_00410760
from voicemeeter_hash_native import (
    DEFAULT_EXE,
    NativeFe70HashSession,
    _is_32bit_process,
)
from voicemeeter_query_local870 import query_voicemeeter_hd_inputs


# --- helpers ------------------------------------------------------------------


def parse_date_arg(s: str) -> datetime:
    """Accepts installer-style '08/01/2026-12:54:56.518' or ISO."""
    s = s.strip()
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})-(\d{2}):(\d{2}):(\d{2})\.(\d{1,3})", s)
    if m:
        day, month, year, hh, mm, ss, ms = map(int, m.groups())
        return datetime(year, month, day, hh, mm, ss, min(ms, 999) * 1000)
    return datetime.fromisoformat(s)


def fmt_date_arg(when: datetime) -> str:
    return (
        f"{when.day:02d}/{when.month:02d}/{when.year:04d}-"
        f"{when.hour:02d}:{when.minute:02d}:{when.second:02d}.{when.microsecond // 1000:03d}"
    )


def parse_int_arg(s: str) -> int:
    s = s.strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s, 0)


def _format_braced(digest16: bytes) -> str:
    parts = [f"{b:02X}" for b in digest16]
    return f"{{{''.join(parts[:8])}-{''.join(parts[8:])}}}"


# --- registry I/O -------------------------------------------------------------


# Voicemeeter8Setup.exe stashes vbDateInst / vbCheckInst in the ARP Uninstall
# key next to DisplayName and UninstallString. Unusual, but that is what the
# installer writes and what the app reads back at runtime.
_REG_LOCATIONS: list[tuple[int, str]] = [
    # Primary location (found empirically).
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
     r"\VB:Voicemeeter {17359A74-1236-5467}"),
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
     r"\VB:Voicemeeter {17359A74-1236-5467}"),
    # Fallback: classic VB-Audio paths.
    (winreg.HKEY_CURRENT_USER, r"Software\VB-Audio\Voicemeeter"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\VB-Audio\Voicemeeter"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\VB-Audio\Voicemeeter"),
]


def _hive_name(hive: int) -> str:
    return {
        winreg.HKEY_CURRENT_USER: "HKCU",
        winreg.HKEY_LOCAL_MACHINE: "HKLM",
    }.get(hive, str(hive))


def read_registry_pair() -> list[tuple[str, str | None, str | None, str | None]]:
    """
    Return rows: (location, vbDateInst, vbCheckInst, install_exe_path).

    install_exe_path comes from UninstallString on the same key — that path is
    the hash prefix. Keys that do not exist are skipped.
    """
    out: list[tuple[str, str | None, str | None, str | None]] = []
    for hive, sub in _REG_LOCATIONS:
        try:
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                date_v = check_v = uninst_v = None
                try:
                    date_v, _ = winreg.QueryValueEx(k, "vbDateInst")
                except OSError:
                    pass
                try:
                    check_v, _ = winreg.QueryValueEx(k, "vbCheckInst")
                except OSError:
                    pass
                try:
                    uninst_v, _ = winreg.QueryValueEx(k, "UninstallString")
                except OSError:
                    pass
                if date_v or check_v:
                    out.append(
                        (f"{_hive_name(hive)}\\{sub}", date_v, check_v, _strip_uninstall_quotes(uninst_v))
                    )
        except OSError:
            continue
    return out


def _strip_uninstall_quotes(s: str | None) -> str | None:
    """
    Parse UninstallString into the bare .exe path.

    Typical shapes:
      "C:\\X\\Y.exe" /uninstall
      C:\\X\\Y.exe /uninstall   (path may contain spaces)
      C:\\X\\Y.exe

    Splitting on the first space is wrong for paths with spaces; we trim at .exe.
    """
    if not s:
        return s
    s = s.strip()
    if s.startswith('"'):
        end = s.find('"', 1)
        if end > 0:
            return s[1:end]
    low = s.lower()
    idx = low.find(".exe")
    if idx > 0:
        return s[: idx + 4]
    return s


_DEFAULT_INSTALL_PREFIX = (
    r"C:\Program Files (x86)\VB\Voicemeeter\Voicemeeter8Setup.exe"
)


def detect_install_prefix() -> str:
    """Read UninstallString from Voicemeeter's ARP key — that string is the hash prefix."""
    for hive, sub in _REG_LOCATIONS:
        if "Uninstall" not in sub:
            continue
        try:
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                uninst_v, _ = winreg.QueryValueEx(k, "UninstallString")
                stripped = _strip_uninstall_quotes(uninst_v)
                if stripped:
                    return stripped
        except OSError:
            continue
    return _DEFAULT_INSTALL_PREFIX


# --- core generator -----------------------------------------------------------


def compute_hash(
    when: datetime,
    label: str,
    serial: int,
    exe_path: str,
    prefix: str,
) -> tuple[bytes, bytes, int]:
    """
    Return (body, digest16, xor_seed).

    body is the post-XOR blob passed into FUN_00402940; digest16 is 16 bytes.

    prefix is the install path to Voicemeeter8Setup.exe — the same buffer the
    installer treats as `this` before KEY + date + HD are appended.
    """
    if not _is_32bit_process():
        raise RuntimeError(
            "Native hash needs a 32-bit Python process "
            "(Voicemeeter8Setup.exe is a 32-bit PE). Run with `py -3.12-32`."
        )
    prefix_bytes = prefix.encode("ascii", errors="replace")
    body = build_fe70_xor_body(prefix_bytes, when, hd_label=label, hd_id=serial)
    seed = fun_00410760(when.year, when.day, when.month)
    with NativeFe70HashSession(exe_path) as sess:
        digest = sess.digest(body)
    return body, digest, seed & 0xFFFFFFFF


# --- commands -----------------------------------------------------------------


def _resolve_prefix(args: argparse.Namespace) -> str:
    """Use --prefix if set, else registry, else built-in default."""
    if getattr(args, "prefix", None):
        return args.prefix
    return detect_install_prefix()


def cmd_gen(args: argparse.Namespace) -> int:
    when = parse_date_arg(args.date)
    if args.auto:
        label, serial = query_voicemeeter_hd_inputs(args.drive)
        print(f"# auto on {args.drive}: label={label!r} serial=0x{serial:08X}")
    else:
        label = args.label
        serial = parse_int_arg(args.serial) if args.serial else 0
    prefix = _resolve_prefix(args)

    body, digest, seed = compute_hash(when, label, serial, args.exe, prefix)
    print(f"vbDateInst   : {fmt_date_arg(when)}")
    print(f"vbCheckInst  : {_format_braced(digest)}")
    print(f"# prefix      : {prefix!r}")
    print(f"# inputs      : label={label!r} serial=0x{serial:08X} seed=0x{seed:08X}")
    print(f"# body        : {len(body)} bytes")
    return 0


def cmd_now(args: argparse.Namespace) -> int:
    when = datetime.now()
    label, serial = query_voicemeeter_hd_inputs(args.drive)
    prefix = _resolve_prefix(args)
    body, digest, seed = compute_hash(when, label, serial, args.exe, prefix)
    print(f"vbDateInst   : {fmt_date_arg(when)}")
    print(f"vbCheckInst  : {_format_braced(digest)}")
    print(f"# prefix      : {prefix!r}")
    print(f"# inputs      : label={label!r} serial=0x{serial:08X} seed=0x{seed:08X}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Print vbDateInst, vbCheckInst, and UninstallString from the registry."""
    found = read_registry_pair()
    if not found:
        print("Voicemeeter not installed (no matching registry keys).", file=sys.stderr)
        return 1
    for loc, d, c, prefix in found:
        print(f"[{loc}]")
        print(f"  vbDateInst      = {d!r}")
        print(f"  vbCheckInst     = {c!r}")
        print(f"  UninstallString = {prefix!r}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """
    Main correctness check: reproduce (date, hash) with our pipeline.

    If label, serial, or prefix are omitted, we pull defaults from this machine.
    """
    reg_prefix: str | None = None
    if args.from_registry:
        found = read_registry_pair()
        if not found:
            print("Registry has no vbDateInst / vbCheckInst.", file=sys.stderr)
            return 2
        loc, d, c, reg_prefix = found[0]
        if not (d and c):
            print(f"At {loc} only part of the pair is present: date={d!r} check={c!r}", file=sys.stderr)
            return 2
        print(f"# ground-truth from registry [{loc}]")
        gt_date_str, gt_hash_str = d, c
    else:
        if not (args.date and args.hash):
            print("Pass --date and --hash, or use --from-registry.", file=sys.stderr)
            return 2
        gt_date_str, gt_hash_str = args.date, args.hash

    print(f"# ground-truth: date={gt_date_str!r} hash={gt_hash_str!r}")
    when = parse_date_arg(gt_date_str)
    target = parse_vb_check_inst_braced(gt_hash_str)

    if args.label is not None:
        label = args.label
    else:
        label, _auto_serial = query_voicemeeter_hd_inputs(args.drive)
    if args.serial is not None:
        serial = parse_int_arg(args.serial)
    else:
        _auto_label, serial = query_voicemeeter_hd_inputs(args.drive)

    if args.prefix:
        prefix = args.prefix
    elif reg_prefix:
        prefix = reg_prefix
    else:
        prefix = detect_install_prefix()

    print(f"# inputs (used in compute): label={label!r} serial=0x{serial:08X}")
    print(f"# prefix : {prefix!r}")

    body, digest, seed = compute_hash(when, label, serial, args.exe, prefix)
    got = _format_braced(digest)
    print(f"# computed: {got}")
    if digest == target:
        print("OK -- our generator reproduces ground-truth hash byte-for-byte")
        return 0
    print("MISMATCH — computed hash does not match ground truth.", file=sys.stderr)
    print(f"  expected = {gt_hash_str}")
    print(f"  got      = {got}")
    print(f"  diff bytes = {[i for i,(a,b) in enumerate(zip(target,digest)) if a!=b]}")
    return 1


def cmd_selftest(args: argparse.Namespace) -> int:
    when = datetime(2026, 1, 8, 12, 54, 56, 518_000)
    prefix = _resolve_prefix(args)
    body = build_fe70_xor_body(prefix.encode("ascii", "replace"), when, hd_label="", hd_id=0x12345678)
    expected_len = len(prefix) + 39 + 23 + len("HD:(0x12345678)")
    print(f"body length: {len(body)} (expected {expected_len})")
    if len(body) != expected_len:
        print("FAIL: buffer length mismatch.", file=sys.stderr)
        return 1
    if body[len(prefix) + 39] == 0:
        print("FAIL: null at KEY boundary (the KEY null must be overwritten by the date).", file=sys.stderr)
        return 1
    seed = fun_00410760(when.year, when.day, when.month)
    print(f"xor seed (FUN_00410760): {seed} (0x{seed & 0xFFFFFFFF:08X})")
    if args.exe and _is_32bit_process():
        with NativeFe70HashSession(args.exe) as s:
            d = s.digest(body)
        print(f"native digest: {_format_braced(d)}")
    return 0


# --- argparse -----------------------------------------------------------------


def _add_exe(p: argparse.ArgumentParser) -> None:
    p.add_argument("--exe", default=DEFAULT_EXE, help="Path to Voicemeeter8Setup.exe (for native hash).")


def _add_prefix(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--prefix",
        default=None,
        help=(
            "Install path to the setup EXE — this is the hash prefix (FUN_0040fe70 `this` buffer). "
            "Defaults to UninstallString under the ARP key; if missing, "
            "C:\\Program Files (x86)\\VB\\Voicemeeter\\Voicemeeter8Setup.exe."
        ),
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Voicemeeter vbCheckInst / vbDateInst pair generator")
    sp = p.add_subparsers(dest="cmd", required=True)

    g = sp.add_parser("gen", help="Generate a (vbDateInst, vbCheckInst) pair")
    g.add_argument("--date", required=True, help='Installer-style "DD/MM/YYYY-HH:MM:SS.mmm" or ISO')
    g.add_argument("--label", default="", help="local_870 (SPDRP_FRIENDLYNAME)")
    g.add_argument("--serial", default="0", help="local_3834 (volume serial, uint32)")
    g.add_argument("--auto", action="store_true", help="Auto-detect label and serial for this PC")
    g.add_argument("--drive", default="C")
    _add_prefix(g)
    _add_exe(g)
    g.set_defaults(func=cmd_gen)

    n = sp.add_parser("now", help="Generate a pair for the current time and this machine")
    n.add_argument("--drive", default="C")
    _add_prefix(n)
    _add_exe(n)
    n.set_defaults(func=cmd_now)

    e = sp.add_parser("extract", help="Read values from the registry (if Voicemeeter is installed)")
    e.set_defaults(func=cmd_extract)

    v = sp.add_parser("validate", help="Check that we reproduce a known-good (date, hash) pair")
    v.add_argument("--date", help="Ground-truth date (when not using --from-registry)")
    v.add_argument("--hash", help='Ground-truth hash "{....-....}"')
    v.add_argument("--from-registry", action="store_true",
                   help="Load date/hash from the registry (installed Voicemeeter)")
    v.add_argument("--label", default=None,
                   help="Override friendly name (default: detect on this machine)")
    v.add_argument("--serial", default=None,
                   help="Override volume serial (default: detect on this machine)")
    v.add_argument("--drive", default="C")
    _add_prefix(v)
    _add_exe(v)
    v.set_defaults(func=cmd_validate)

    s = sp.add_parser("selftest", help="Quick sanity check: buffer layout and XOR seed")
    _add_prefix(s)
    _add_exe(s)
    s.set_defaults(func=cmd_selftest)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
