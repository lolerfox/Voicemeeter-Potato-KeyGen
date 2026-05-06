"""
Background helper that refreshes vbDateInst / vbCheckInst at logon.

Scheduled by install_autostart.bat via Task Scheduler. One pass:

  1. Read the current wall clock.
  2. Auto-detect hd_label / hd_serial for the system drive.
  3. Read the hash prefix from the Voicemeeter ARP key (UninstallString).
  4. Digest via native FUN_00402940.
  5. Rewrite vbDateInst and vbCheckInst under HKLM\\…\\Uninstall\\VB:Voicemeeter …

Log file lives next to this script: voicemeeter_autostart.log.

We swallow unexpected exceptions, log them, and still return exit 0 from main so
Task Scheduler does not mark the task as failed on benign issues.
"""
from __future__ import annotations

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
)
from voicemeeter_query_local870 import query_voicemeeter_hd_inputs  # noqa: E402


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


def run_once() -> int:
    if not _EXE.is_file():
        log(f"FAIL: Voicemeeter8Setup.exe missing next to script ({_EXE})")
        return 1

    hive, sub = find_target_key()
    if hive is None or sub is None:
        log("FAIL: Voicemeeter uninstall key not found in registry")
        return 1

    when = datetime.now()
    try:
        label, serial = query_voicemeeter_hd_inputs("C")
    except Exception as e:
        log(f"WARN: query_voicemeeter_hd_inputs failed: {e!r} — using empty label/serial")
        label, serial = "", 0

    prefix = detect_install_prefix()

    body, digest, seed = compute_hash(when, label, serial, str(_EXE), prefix)
    date_str = fmt_date_arg(when)
    hash_str = _format_braced(digest)
    log(
        f"COMPUTED date={date_str} hash={hash_str} "
        f"label={label!r} serial=0x{serial:08X} prefix={prefix!r}"
    )

    try:
        write_pair(hive, sub, date_str, hash_str)
    except OSError as e:
        log(
            f"FAIL: registry write to [{sub}] failed: {e!r}. "
            "If you launched by hand, try an elevated shell. "
            "Scheduled task should use /ru SYSTEM (see install_autostart.bat)."
        )
        return 1

    log(f"OK wrote {date_str} -> {hash_str}")
    return 0


def main() -> int:
    try:
        return run_once()
    except SystemExit:
        raise
    except BaseException as e:
        log("EXC: " + repr(e) + "\n" + traceback.format_exc())
        return 0


if __name__ == "__main__":
    sys.exit(main())
