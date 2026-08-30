"""Launch the MF17 bridge on the logged-in Windows desktop.

Codex command runners execute on an isolated desktop in the same user session.
That desktop can access the HID device but cannot send keystrokes to Codex,
Antigravity, or any other visible application.  This launcher explicitly
creates the bridge process on ``WinSta0\\Default`` instead.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys


CREATE_NO_WINDOW = 0x08000000


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("This launcher is only needed on Windows.")

    repo_root = Path(__file__).resolve().parents[1]
    pythonw = repo_root / ".venv" / "Scripts" / "pythonw.exe"
    bridge = repo_root / "host" / "mf17_bridge.py"
    if not pythonw.exists() or not bridge.exists():
        raise SystemExit("MF17 bridge files are missing; run this from the project checkout.")

    command_line = subprocess.list2cmdline([str(pythonw), str(bridge)])
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    startup.lpDesktop = "WinSta0\\Default"
    process = PROCESS_INFORMATION()

    create_process = ctypes.windll.kernel32.CreateProcessW
    create_process.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    create_process.restype = wintypes.BOOL

    command_buffer = ctypes.create_unicode_buffer(command_line)
    if not create_process(
        str(pythonw),
        command_buffer,
        None,
        None,
        False,
        CREATE_NO_WINDOW,
        None,
        str(repo_root),
        ctypes.byref(startup),
        ctypes.byref(process),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    ctypes.windll.kernel32.CloseHandle(process.hThread)
    ctypes.windll.kernel32.CloseHandle(process.hProcess)
    print(f"Started MF17 bridge on WinSta0\\Default (PID {process.dwProcessId}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
