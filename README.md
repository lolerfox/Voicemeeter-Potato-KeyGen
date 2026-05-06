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

- **Windows10** (maybe Win11... i dont test it on win11)
- **32-bit Python 3.12+**. `Voicemeeter8Setup.exe` is a 32-bit PE,
  and we call the native hash `FUN_00402940` directly via `ctypes`,
  so a 64-bit interpreter will NOT work (`OSError: [WinError 193]`).

  Install/run like this:

  ```
  py -3.12-32 voicemeeter_genius.py ...
  ```

---
### Installation
1. Install [Python 3.12.3 32-bit](https://www.python.org/ftp/python/3.12.3/python-3.12.3.exe)
2. Install pefile
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
5. Gen Keys once
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
2. Auto-detects `hd_label`/`hd_serial` of system disk `C:`.
3. Reads hash prefix from the ARP key `UninstallString`.
4. Computes `vbCheckInst` via native `FUN_00402940`.
5. Rewrites a fresh pair `(vbDateInst, vbCheckInst)` into the registry.
6. Never crashes outward: everything is caught and logged to
   `voicemeeter_autostart.log` next to the script.

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
which is also the hash prefix).

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

Time comes from `datetime.now()`, label/serial are auto-detected (from system
disk `C:`), prefix is read from the registry (if Voicemeeter is installed),
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

Checks final buffer length and that byte 39 (KEY boundary) is non-zero.

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

---

## If Something Does Not Work

- `OSError: [WinError 193]` - you launched under 64-bit Python. Install the
  32-bit build and run as `py -3.12-32 ...`.
- `Voicemeeter is not installed (registry keys not found)` - `extract` /
  `validate --from-registry` have no ground truth because the registry key
  does not exist. This is normal on a fresh system; use `gen` or `now`
  instead (they do not require installed Voicemeeter, only
  `Voicemeeter8Setup.exe` next to the script).
- `FAIL: buffer length mismatch` in `selftest` - someone modified
  `fun_0040fe70.py` and broke `_KEY_ASCII` or the date format. Revert changes.
