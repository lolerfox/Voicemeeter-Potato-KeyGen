"""
Background helper that refreshes vbCheckInst at logon (and vbDateInst only when needed).

Scheduled by install_autostart.bat via Task Scheduler. One pass:

  1. Read vbDateInst from registry when present (rehash that timestamp); otherwise use now.
  2. Auto-detect hd_label / hd_serial for the %SystemRoot% drive (not always C:).
  3. Read the hash prefix from UninstallString on the Voicemeeter ARP key.
  4. Digest via native FUN_00402940.
  5. Write vbDateInst + vbCheckInst (skipped if disk query failed — see log).

Log: voicemeeter_autostart.log next to this script.

Manual flags:
  --fresh     always use current time for vbDateInst
  --dry-run   compute and log only, do not write the registry
"""
from __future__ import annotations

import argparse
import ctypes
import getpass
import os
import sys
import traceback
import winreg
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from voicemeeter_genius import (  # noqa: E402
    _REG_LOCATIONS,
    _format_braced,
    compute_hash,
    detect_install_prefix,
    fmt_date_arg,
    parse_date_arg,
)
from voicemeeter_query_local870 import (  # noqa: E402
    query_voicemeeter_hd_inputs,
    system_boot_drive_letter,
)


_LOG = _HERE / "voicemeeter_autostart.log"
_EXE = _HERE / "Voicemeeter8Setup.exe"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='milliseconds')}] {msg}"
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def find_target_key() -> tuple[int | None, str | None]:
    """Return (hive, subkey) for the Voicemeeter uninstall entry, or (None, None)."""
    for hive, sub in _REG_LOCATIONS:
        if "Uninstall" not in sub:
            continue
        try:
            handle = winreg.OpenKey(
                hive, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            )
            handle.Close()
            return hive, sub
        except OSError:
            continue
    return None, None


def write_pair(hive: int, sub: str, date_str: str, hash_str: str) -> None:
    with winreg.OpenKey(
        hive, sub, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
    ) as k:
        winreg.SetValueEx(k, "vbDateInst", 0, winreg.REG_SZ, date_str)
        winreg.SetValueEx(k, "vbCheckInst", 0, winreg.REG_SZ, hash_str)


def read_registry_date(hive: int, sub: str) -> datetime | None:
    try:
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            raw, _ = winreg.QueryValueEx(k, "vbDateInst")
            if raw:
                return parse_date_arg(str(raw))
    except OSError:
        pass
    return None


def run_once(*, fresh: bool = False, dry_run: bool = False) -> int:
    user = getpass.getuser()
    log(
        f"START user={user!r} SystemRoot={os.environ.get('SystemRoot', '?')!r} "
        f"fresh={fresh} dry_run={dry_run}"
    )

    if not _EXE.is_file():
        log(f"FAIL: Voicemeeter8Setup.exe missing next to script ({_EXE})")
        return 1

    hive, sub = find_target_key()
    if hive is None or sub is None:
        log("FAIL: Voicemeeter uninstall key not found in registry")
        return 1

    existing = read_registry_date(hive, sub)
    if fresh or existing is None:
        when = datetime.now()
        log("MODE fresh: vbDateInst from current clock")
    else:
        when = existing
        log(f"MODE rehash: keeping vbDateInst={fmt_date_arg(when)!r}")

    drive = system_boot_drive_letter()
    label = ""
    serial = 0
    try:
        label, serial = query_voicemeeter_hd_inputs(drive)
    except OSError as e:
        err = ctypes.get_last_error()
        log(
            f"FAIL: disk query on drive {drive}: {e!r} winerr={err}. "
            "Registry not updated. Run: py -3.12-32 voicemeeter_genius.py diagnose"
        )
        return 1
    except Exception as e:
        log(f"FAIL: disk query unexpected error: {e!r}")
        return 1

    if not label:
        log(
            f"WARN: SPDRP_FRIENDLYNAME empty for boot drive {drive}: — "
            "hash may still work if the installer also saw an empty name"
        )

    prefix = detect_install_prefix()
    if not os.path.isfile(prefix):
        log(f"WARN: prefix path does not exist on disk: {prefix!r}")

    body, digest, seed = compute_hash(when, label, serial, str(_EXE), prefix)
    date_str = fmt_date_arg(when)
    hash_str = _format_braced(digest)
    log(
        f"COMPUTED drive={drive} date={date_str} hash={hash_str} "
        f"label={label!r} serial=0x{serial:08X} prefix={prefix!r} seed=0x{seed:08X}"
    )

    if dry_run:
        log("DRY-RUN: registry left unchanged")
        return 0

    try:
        write_pair(hive, sub, date_str, hash_str)
    except OSError as e:
        log(
            f"FAIL: registry write to [{sub}] failed: {e!r}. "
            "Run install_autostart.bat elevated so the task uses /ru SYSTEM."
        )
        return 1

    log(f"OK wrote {date_str} -> {hash_str}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Voicemeeter registry pair autostart helper")
    ap.add_argument("--fresh", action="store_true", help="Set vbDateInst to now instead of reusing registry")
    ap.add_argument("--dry-run", action="store_true", help="Log only, do not write registry")
    args = ap.parse_args()
    try:
        return run_once(fresh=args.fresh, dry_run=args.dry_run)
    except SystemExit:
        raise
    except BaseException as e:
        log("EXC: " + repr(e) + "\n" + traceback.format_exc())
        return 0


if __name__ == "__main__":
    sys.exit(main())
