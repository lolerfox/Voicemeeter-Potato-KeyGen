"""
Compute local_870 / local_3834 the way Voicemeeter does, in plain Python, without
calling native FUN_00410460.

Rough flow:
  1. GetSystemDirectoryA — only to learn which drive letter we care about.
  2. CreateFileA('\\\\.\\X:') + IOCTL_STORAGE_GET_DEVICE_NUMBER → device / partition ids.
  3. SetupDiGetClassDevsA(GUID_DEVINTERFACE_DISK / _CDROM, …, DIGCF_DEVICEINTERFACE|DIGCF_PRESENT).
  4. SetupDiEnumDeviceInterfaces + SetupDiGetDeviceInterfaceDetailA → device path string.
  5. Open that path, same IOCTL — when DeviceNumber matches, we found our disk interface.
  6. GetVolumeInformationA('X:\\') → volume serial → local_3834.

Step 2 used to want elevation for some paths; opening the drive root with
dwDesiredAccess=0 (query only) works without admin on typical setups.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
DIGCF_DEVICEINTERFACE = 0x10
DIGCF_PRESENT = 0x02

GUID_DEVINTERFACE_DISK = b"\x07\x63\xf5\x53\xbf\xb6\xd0\x11\x94\xf2\x00\xa0\xc9\x1e\xfb\x8b"
GUID_DEVINTERFACE_CDROM = b"\x08\x63\xf5\x53\xbf\xb6\xd0\x11\x94\xf2\x00\xa0\xc9\x1e\xfb\x8b"

# Setup API bindings
class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", ctypes.c_byte * 16),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


SetupDiGetClassDevsA = setupapi.SetupDiGetClassDevsA
SetupDiGetClassDevsA.argtypes = [
    ctypes.c_char_p,  # ClassGuid (raw 16 bytes)
    ctypes.c_char_p,  # Enumerator
    wintypes.HWND,
    wintypes.DWORD,
]
SetupDiGetClassDevsA.restype = wintypes.HANDLE

SetupDiEnumDeviceInterfaces = setupapi.SetupDiEnumDeviceInterfaces
SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,  # SP_DEVINFO_DATA*
    ctypes.c_char_p,  # InterfaceClassGuid (16 bytes)
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

SetupDiGetDeviceInterfaceDetailA = setupapi.SetupDiGetDeviceInterfaceDetailA
SetupDiGetDeviceInterfaceDetailA.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,  # PSP_DEVICE_INTERFACE_DETAIL_DATA_A
    wintypes.DWORD,   # DeviceInterfaceDetailDataSize
    ctypes.POINTER(wintypes.DWORD),  # RequiredSize
    ctypes.c_void_p,  # PSP_DEVINFO_DATA
]
SetupDiGetDeviceInterfaceDetailA.restype = wintypes.BOOL

SetupDiDestroyDeviceInfoList = setupapi.SetupDiDestroyDeviceInfoList
SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL


def system_boot_drive_letter() -> str:
    """Drive letter where %SystemRoot% lives (usually C:, not always)."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    letter = os.path.splitdrive(root)[0]
    if letter and len(letter) >= 2 and letter[1] == ":":
        return letter[0].upper()
    return "C"


def _get_device_number_for_drive(drive_letter: str) -> tuple[int, int, int] | None:
    path = rf"\\.\{drive_letter}:"
    h = k32.CreateFileA(
        path.encode("ascii"),
        0,           # FILE_QUERY_ATTRIBUTES — no heavy access required
        3,           # share read + write
        None,
        3,           # OPEN_EXISTING
        0,
        None,
    )
    if h == INVALID_HANDLE_VALUE:
        return None
    try:
        out = (ctypes.c_uint8 * 12)()
        ret = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(
            h,
            IOCTL_STORAGE_GET_DEVICE_NUMBER,
            None, 0,
            out, 12,
            ctypes.byref(ret),
            None,
        )
        if not ok:
            return None
        return struct.unpack("<III", bytes(out))
    finally:
        k32.CloseHandle(h)


SetupDiGetDeviceRegistryPropertyA = setupapi.SetupDiGetDeviceRegistryPropertyA
SetupDiGetDeviceRegistryPropertyA.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,  # SP_DEVINFO_DATA*
    wintypes.DWORD,   # Property
    ctypes.POINTER(wintypes.DWORD),  # PropertyRegDataType
    ctypes.c_char_p,  # PropertyBuffer
    wintypes.DWORD,   # PropertyBufferSize
    ctypes.POINTER(wintypes.DWORD),  # RequiredSize
]
SetupDiGetDeviceRegistryPropertyA.restype = wintypes.BOOL


# SP_DEVINFO_DATA: cbSize, ClassGuid (16), DevInst (DWORD), Reserved (UINT_PTR → 4 on Win32)
class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", ctypes.c_byte * 16),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


SPDRP_FRIENDLYNAME = 0x0C  # same source FUN_004100D0 uses for local_870


def _enumerate_disk_paths(guid: bytes) -> list[tuple[str, tuple[int, int, int] | None, str]]:
    """Return (DevicePath, storage_number_tuple_or_None, friendly_name) for each interface."""
    h = SetupDiGetClassDevsA(guid, None, None, DIGCF_DEVICEINTERFACE | DIGCF_PRESENT)
    if h == INVALID_HANDLE_VALUE:
        return []
    results: list[tuple[str, tuple[int, int, int] | None, str]] = []
    try:
        i = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = SetupDiEnumDeviceInterfaces(h, None, guid, i, ctypes.byref(ifd))
            if not ok:
                err = ctypes.get_last_error()
                if err in (259,):  # ERROR_NO_MORE_ITEMS
                    break
                if err in (87, 13):
                    break
                break
            i += 1

            req = wintypes.DWORD(0)
            SetupDiGetDeviceInterfaceDetailA(h, ctypes.byref(ifd), None, 0, ctypes.byref(req), None)

            buf = (ctypes.c_uint8 * req.value)()
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 5

            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)

            ok2 = SetupDiGetDeviceInterfaceDetailA(
                h, ctypes.byref(ifd), buf, req.value, None, ctypes.byref(devinfo)
            )
            if not ok2:
                continue
            raw = bytes(buf)[4:]
            zero = raw.find(b"\x00")
            if zero >= 0:
                raw = raw[:zero]
            try:
                device_path = raw.decode("cp1252")
            except UnicodeDecodeError:
                device_path = raw.hex()

            friendly_buf = ctypes.create_string_buffer(0x80)
            req2 = wintypes.DWORD(0)
            ok_friendly = SetupDiGetDeviceRegistryPropertyA(
                h, ctypes.byref(devinfo), SPDRP_FRIENDLYNAME,
                None, friendly_buf, 0x80, ctypes.byref(req2),
            )
            if ok_friendly:
                fbytes = friendly_buf.value
                try:
                    friendly = fbytes.decode("cp1252")
                except UnicodeDecodeError:
                    friendly = fbytes.hex()
            else:
                friendly = ""

            dh = k32.CreateFileA(device_path.encode("cp1252"), 0, 3, None, 3, 0, None)
            number_info = None
            if dh != INVALID_HANDLE_VALUE:
                try:
                    out = (ctypes.c_uint8 * 12)()
                    ret = wintypes.DWORD(0)
                    if k32.DeviceIoControl(
                        dh, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
                        out, 12, ctypes.byref(ret), None,
                    ):
                        number_info = struct.unpack("<III", bytes(out))
                finally:
                    k32.CloseHandle(dh)
            results.append((device_path, number_info, friendly))
    finally:
        SetupDiDestroyDeviceInfoList(h)
    return results


def query_voicemeeter_hd_inputs(drive_letter: str = "C") -> tuple[str, int]:
    """
    Return (local_870, local_3834) — friendly disk name and volume serial Voicemeeter expects.
    """
    info = _get_device_number_for_drive(drive_letter)
    if info is None:
        raise OSError(f"cannot get device number for {drive_letter}: (need admin rights?)")
    drive_dt, drive_num, drive_part = info

    # Map drive type to GUID (DRIVE_FIXED=3, DRIVE_REMOVABLE=2 → disk; DRIVE_CDROM=5 → CDROM)
    GetDriveTypeA = k32.GetDriveTypeA
    GetDriveTypeA.argtypes = [ctypes.c_char_p]
    GetDriveTypeA.restype = wintypes.UINT
    dt = GetDriveTypeA(f"{drive_letter}:\\".encode("ascii"))
    guid = GUID_DEVINTERFACE_CDROM if dt == 5 else GUID_DEVINTERFACE_DISK

    paths = _enumerate_disk_paths(guid)
    local_870 = ""
    matched = 0
    for dp, ni, fn in paths:
        if ni is not None and ni[1] == drive_num:
            matched += 1
            if fn:
                local_870 = fn  # SPDRP_FRIENDLYNAME, same as FUN_004100D0
            elif not local_870:
                local_870 = ""

    if matched == 0:
        raise OSError(
            f"no disk interface matched device number {drive_num} for drive {drive_letter}:"
            f" (enumerated {len(paths)} interfaces)"
        )

    # Volume serial from GetVolumeInformationA on "X:\"
    label_buf = ctypes.create_string_buffer(0x80)
    serial = wintypes.DWORD(0)
    GetVolumeInformationA = k32.GetVolumeInformationA
    GetVolumeInformationA.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_char_p, wintypes.DWORD,
    ]
    GetVolumeInformationA.restype = wintypes.BOOL
    vol_root = f"{drive_letter}:\\".encode("ascii")
    ok = GetVolumeInformationA(
        vol_root,
        label_buf, 0x80,
        ctypes.byref(serial),
        None, None, None, 0,
    )
    if not ok:
        raise OSError(
            f"GetVolumeInformationA({vol_root!r}) failed: winerr={ctypes.get_last_error()}"
        )

    return local_870, int(serial.value)


def main() -> int:
    drive = sys.argv[1] if len(sys.argv) > 1 else "C"
    drive = drive.rstrip(":\\")
    print(f"querying for drive {drive}:")
    info = _get_device_number_for_drive(drive)
    if info is None:
        print(f"ERROR: CreateFile/IOCTL on \\\\.\\{drive}: failed: {ctypes.get_last_error()}")
        return 1
    print(f"  STORAGE_DEVICE_NUMBER: type={info[0]} number={info[1]} partition={info[2]}")

    paths = _enumerate_disk_paths(GUID_DEVINTERFACE_DISK)
    print(f"\n  enumerated {len(paths)} disk interfaces:")
    for dp, ni, fn in paths:
        marker = "  <-- MATCHES C:" if (ni and ni[1] == info[1]) else ""
        print(f"    friendly={fn!r}")
        print(f"    path    ={dp!r} -> {ni}{marker}")

    local_870, serial = query_voicemeeter_hd_inputs(drive)
    print(f"\n=== RESULT ===")
    print(f"local_870  : {local_870!r}")
    print(f"local_3834 : 0x{serial:08X}")
    print(f"=> HD: \"HD:{local_870}(0x{serial:08X})\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
