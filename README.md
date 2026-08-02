# voicemeeter_genius - `vbDateInst` / `vbCheckInst` generator

Reproduces **byte-for-byte** the same value pair that `Voicemeeter8Setup.exe`
writes to the registry after installation, without launching the installer itself.

Ground truth was confirmed on a real Voicemeeter installation:

```
ground-truth (registry): {93580B7AE432D5FE-D21DF9FCE478897D}
computed   (generator):  {93580B7AE432D5FE-D21DF9FCE478897D}
OK -- byte-for-byte
```

---

## Requirements

- **Windows 10** (maybe Win11... i dont test it on win11)
- **32-bit Python 3.12+**. `Voicemeeter8Setup.exe` is a 32-bit PE,
  and we call the native hash `FUN_00402940` directly via `ctypes`,
  so a 64-bit interpreter will NOT work (`OSError: [WinError 193]`).
  Any 3.12/3.13/3.14+ 32-bit build works — just make sure the `-32` tag
  matches whatever version you actually installed.

  Install/run like this (swap `3.12` for your version):

  ```
  py -3.12-32 voicemeeter_genius.py ...
  ```

  Not sure which tags you have installed? Run:

  ```
  py --list
  ```

  and pick whichever line ends in `-32`.

---
### Installation
1. Install a **32-bit** Python 3.12+ build, e.g. [Python 3.12.3 32-bit](https://www.python.org/ftp/python/3.12.3/python-3.12.3.exe)
2. Install pefile (for the *same* interpreter you'll run the script with)
```
   py -3.12-32 -m pip install pefile
```
3. Download and Place `Voicemeeter8Setup.exe` in KeyGen folder.

   I think there is no need to explain why it is not included in the repository.

   However, you need `Voicemeeter8Setup.exe Version 3.1.2.2 (DECEMBER 2025)` 
     
     Hash`Voicemeeter8Setup.exe Version 3.1.2.2 (DECEMBER 2025)`:
   
     SHA256: `11D1487736AAB346AD82FCC88568C06F01CF82B20E1831D18088FB89B469424B`
   
     MD5: `10803E8A8AC3B803D9A269C7133187DB`
   
     SHA1: `31610B59BA26A2693E276B2A8AD45B9F9213B92E`
     
    Why is this necessary? 
	The reason is simple... the script uses system calls from the installer.

    Using a different EXE build is not a hard blocker, but the script pins its
    RVA offsets to this exact version. If you drop in another build, `diagnose`
    and `validate` will tell you the detected version and whether the byte
    signature at `FUN_00402940` still matches (see [Diagnostics](#diagnostics) below).
4. Gen Keys once
```
py -3.12-32 voicemeeter_genius.py now
```
5. Edit `vbCheckInst` and `vbDateInst` in `regedit` on path 
`HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {ID}`

---
## Autostart (background re-generator)

If you want `vbDateInst`/`vbCheckInst` to refresh automatically on every
Windows logon, there is a ready wrapper around `voicemeeter_autostart.py`:

### What `voicemeeter_autostart.py` does

1. Gets current time.
2. Auto-detects `hd_label`/`hd_serial` of the boot drive (from `%SystemRoot%`, not always `C:`).
3. Reads hash prefix from the ARP key `UninstallString`.
4. Computes `vbCheckInst` via native `FUN_00402940`.
5. Rewrites a fresh pair `(vbDateInst, vbCheckInst)` into the registry.
6. Never crashes outward: everything is caught and logged to
   `voicemeeter_autostart.log` next to the script, including a clear
   line if the Python interpreter running the task isn't 32-bit or is
   missing `pefile`.

### Run **as Administrator**

```
install_autostart.bat
```

The script will:
- find 32-bit `pythonw.exe`,
- register task `VoicemeeterAutogen` in Task Scheduler,
- trigger: **OnLogon** (any user),
- account: **NT AUTHORITY\\SYSTEM** (full `HKLM` access, no UAC, no console),
- offer to run the task immediately for verification. After a few seconds,
  check `voicemeeter_autostart.log`.

### Uninstall

```
uninstall_autostart.bat
```
(also run as admin.)

### What should appear in the log on success

```
[2026-05-06T13:45:00.000] COMPUTED date=06/05/2026-13:45:00.000 hash={....-....} ...
[2026-05-06T13:45:00.005] OK wrote 06/05/2026-13:45:00.000 -> {....-....}
```

If you see `FAIL: could not write to [...]: PermissionError`,
it means the task did not start under SYSTEM for some reason. Remove it and
install again using `install_autostart.bat` as admin.

If you see `FAIL: environment not usable for native hashing: ...`, the task
is running under the wrong interpreter (wrong bitness, or `pefile` missing
for it) — the message tells you the exact `py -X.Y-32` tag to use.

---
## Folder contents

| File                            | Purpose                                                                              |
| ------------------------------- | ------------------------------------------------------------------------------------ |
| `voicemeeter_genius.py`         | Main CLI - the only script you run manually                                          |
| `fun_0040fe70.py`               | Builds XOR buffer (KEY + date + HD string), emulates `FUN_0040fe70`                  |
| `fun_00410760.py`               | Computes XOR seed from (year, day, month), emulates `FUN_00410760`                   |
| `fun_00402940.py`               | Pure Python hash port (used as fallback / for comparison)                            |
| `voicemeeter_hash_native.py`    | Loads `Voicemeeter8Setup.exe` and calls `FUN_00402940` natively                      |
| `voicemeeter_query_local870.py` | Retrieves `hd_label` (SPDRP_FRIENDLYNAME) and `hd_serial` for system disk            |
| `Voicemeeter8Setup.exe`         | Original installer - required for the native hash function<br>(NOT INCLUDED IN REPO) |
| `voicemeeter_autostart.py`      | Background daemon script: generate pair -> write registry -> log                     |
| `install_autostart.bat`         | Registers the script in Task Scheduler (OnLogon, as SYSTEM)                          |
| `uninstall_autostart.bat`       | Removes the task from Task Scheduler (DISABLED)                                      |

Do not delete or move any of these files - all six core `.py` modules import
each other, and `voicemeeter_hash_native.py` expects `Voicemeeter8Setup.exe`
to be next to it.

---
## Commands

### `extract` - what is currently in the registry

```
py -3.12-32 voicemeeter_genius.py extract
```

Reads the ARP key:
`HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}`
and prints `vbDateInst`, `vbCheckInst`, and `UninstallString` (= install path,
which is also the hash prefix). Doesn't touch the native EXE at all, so it
works even on the wrong Python bitness.

### `validate --from-registry` - main test

```
py -3.12-32 voicemeeter_genius.py validate --from-registry
```

Takes the real pair from the registry, feeds the date back into the generator,
and checks whether the hash matches. If it prints
`OK -- our generator reproduces ground-truth hash byte-for-byte`,
the algorithm is correct.

### `now` - pair for the current moment

```
py -3.12-32 voicemeeter_genius.py now
```

Time comes from `datetime.now()`, label/serial are auto-detected (from the
boot drive), prefix is read from the registry (if Voicemeeter is installed),
otherwise the default is used.

### `gen` - manual generation

```
py -3.12-32 voicemeeter_genius.py gen ^
    --date "06/05/2026-00:10:54.864" ^
    --label "KINGSTON SA400S37480G" ^
    --serial 0xC6598005
```

Useful when you want to reproduce a specific pair for a specific disk/path.
The install-path prefix can be set explicitly via `--prefix`.

### `selftest` - sanity check of buffer assembly

```
py -3.12-32 voicemeeter_genius.py selftest
```

Checks final buffer length, that byte 39 (KEY boundary) is non-zero, and
(if the environment is usable) prints the native digest too. Unlike `gen`
/ `now` / `validate`, this one degrades gracefully on the wrong Python
bitness or missing `pefile` — it just skips the native part and warns.

### `diagnose` - why it's not working on this PC

```
py -3.12-32 voicemeeter_genius.py diagnose
```

The one to run first when something is off (autostart log has a `FAIL`,
or `validate` doesn't match). Dumps, in one shot:

- Python version/bitness of the current process.
- Boot drive, detected `hd_label` / `hd_serial`.
- Resolved hash prefix and whether that path exists on disk.
- `Voicemeeter8Setup.exe` version detected via its PE resources, and
  whether that matches a version this tool has been verified against.
- Every `vbDateInst`/`vbCheckInst` pair found in the registry, recomputed
  and compared byte-for-byte.
- What a fresh "now" pair would look like.
- The last lines of `voicemeeter_autostart.log`, if present.

If the environment itself is broken (wrong bitness, missing `pefile`), it
prints a `WARN:` up front with the exact fix, then still runs everything
else it can (registry read, disk fingerprint) so you get as much signal
as possible in one go.

---
## How It Works

Inside `FUN_0040fe70`, `Voicemeeter8Setup.exe` builds a buffer in `this`
(ECX object), where `this` is the **full path to `Voicemeeter8Setup.exe`**
in the installation directory
(`C:\Program Files (x86)\VB\Voicemeeter\Voicemeeter8Setup.exe`).
This path is followed by:

```
[install_path][KEY 39 bytes][date DD/MM/YYYY-HH:MM:SS.mmm 23 bytes][HD:LABEL(0xSERIAL)]
```

where `KEY = "Voicemeeter, The Virtual Mixing Console"`. Then everything is
XORed with a seed computed from (year, day, month), and hashed by
`FUN_00402940`. The result is 16 bytes -> formatted as
`{XXXXXXXXXXXXXXXX-XXXXXXXXXXXXXXXX}`.

So the **hash depends on the install path**: on a machine where Voicemeeter is
installed outside the default folder, the same date + same disk will produce a
different `vbCheckInst`. The prefix is automatically extracted from
`UninstallString` in the same ARP key.

`FUN_00402940` itself is not reimplemented in Python — it's called directly
inside the loaded `Voicemeeter8Setup.exe` via `ctypes` (see
`voicemeeter_hash_native.py`), so the digest always matches the real
installer's build bit for bit, as long as the EXE's `FUN_00402940` prologue
bytes match what this tool expects (checked automatically before every hash).

---

## Diagnostics

Two safety nets catch the most common "it used to work, now it doesn't" cases
before they turn into a wrong `vbCheckInst` silently written to the registry:

- **Environment check** — every command that needs the native hash checks,
  up front, that the current Python process is 32-bit and has `pefile`
  installed. If not, you get a message with the *exact* command for your
  installed version (e.g. `py -3.14-32 ...`), not a generic hardcoded one.
  `gen`/`now`/`validate` refuse to run with a clear error; `selftest`/
  `diagnose` just warn and keep going with whatever they can still check.
- **EXE version check** — before hashing, the tool verifies the first bytes
  of `FUN_00402940` against the build it was reverse-engineered against
  (currently version `3.1.2.2`). If a different `Voicemeeter8Setup.exe` is
  dropped in and the signature no longer matches, you get an error naming
  the detected version instead of a silent wrong hash or a raw access
  violation. If the signature *does* match but the version string doesn't,
  you get a `WARN` instead — run `validate --from-registry` in that case to
  be sure.

---

## If Something Does Not Work

- `OSError: [WinError 193]` - you launched under 64-bit Python. Install the
  32-bit build and run as `py -X.Y-32 ...` (run `py --list` to see your tags).
- `ValueError: badly formed help string` / `unsupported format character` -
  an old `argparse` compatibility bug in a literal `%` inside a help string;
  fixed as of this version. If you still see it, you're on a stale copy of
  `voicemeeter_genius.py`.
- `pefile is required for native hashing` - install it for the interpreter
  you're actually running with, not just whichever one you installed it for
  earlier: `py -X.Y-32 -m pip install pefile`.
- `Voicemeeter is not installed (registry keys not found)` - `extract` /
  `validate --from-registry` have no ground truth because the registry key
  does not exist. This is normal on a fresh system; use `gen` or `now`
  instead (they do not require installed Voicemeeter, only
  `Voicemeeter8Setup.exe` next to the script).
- `FAIL: buffer length mismatch` in `selftest` - someone modified
  `fun_0040fe70.py` and broke `_KEY_ASCII` or the date format. Revert changes.
- `FUN_00402940 prologue mismatch` - you're using a different
  `Voicemeeter8Setup.exe` build than this tool was reverse-engineered
  against. Check the reported version against `3.1.2.2` and grab the exact
  build listed under [Installation](#installation) if they differ.
- Still stuck? Run `py -X.Y-32 voicemeeter_genius.py diagnose` first - it's
  built specifically to surface the cause of these.
