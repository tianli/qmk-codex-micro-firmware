"""Cross-platform OS automation, window focusing, and input synthesis."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

try:
    from host.status_common import (
        PROVIDER_ANTIGRAVITY,
        PROVIDER_CODEX,
        conversation_uri,
        normalize_provider,
    )
    from host.session_slots import SessionSlots
except ModuleNotFoundError:
    from status_common import (  # type: ignore[no-redef]
        PROVIDER_ANTIGRAVITY,
        PROVIDER_CODEX,
        conversation_uri,
        normalize_provider,
    )
    from session_slots import SessionSlots  # type: ignore[no-redef]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class OSController:
    """Cross-platform controller for macOS, Windows, and Linux."""

    # Detect both standalone and integrated terminal hosts.  We inspect process,
    # title, and window class because an IDE may host its terminal in its own
    # process while a standalone terminal exposes a dedicated process name.
    TERMINAL_MARKERS = (
        "windowsterminal",
        "windowsterminal.exe",
        "openconsole",
        "openconsole.exe",
        "conhost",
        "conhost.exe",
        "cmd.exe",
        "powershell",
        "pwsh",
        "bash",
        "zsh",
        "fish",
        "wsl",
        "mintty",
        "alacritty",
        "wezterm",
        "xterm",
        "cascadia_hosting_window_class",
        "command prompt",
        "integrated terminal",
    )
    CODEX_MARKERS = ("codex", "openai codex", "chatgpt")

    MAC_KEY_CODES = {
        "ENTER": 36,
        "RETURN": 36,
        "ESC": 53,
        "ESCAPE": 53,
        "TAB": 48,
        "SPACE": 49,
        "LEFT": 123,
        "RIGHT": 124,
        "DOWN": 125,
        "UP": 126,
        "PAGEUP": 116,
        "PGUP": 116,
        "PAGEDOWN": 121,
        "PGDN": 121,
    }

    WIN_VK = {
        "CTRL": 0x11,
        "SHIFT": 0x10,
        "ALT": 0x12,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "RETURN": 0x0D,
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "DOWN": 0x28,
        "UP": 0x26,
        "LEFT": 0x25,
        "RIGHT": 0x27,
        "PAGEUP": 0x21,
        "PGUP": 0x21,
        "PAGEDOWN": 0x22,
        "PGDN": 0x22,
        "B": 0x42,
        "D": 0x44,
        "K": 0x4B,
        "N": 0x4E,
        "P": 0x50,
        "[": 0xDB,
        "]": 0xDD,
    }
    # Navigation keys use the E0 scan-code prefix.  Without the extended-key
    # flag Windows desktop clients can receive these as incomplete legacy
    # input, which is why Page Up/Down appeared to work in some terminals but
    # not reliably in Codex or Antigravity's desktop views.
    WIN_EXTENDED_VK = frozenset({
        0x21,  # VK_PRIOR / Page Up
        0x22,  # VK_NEXT / Page Down
        0x23,  # VK_END
        0x24,  # VK_HOME
        0x25,  # VK_LEFT
        0x26,  # VK_UP
        0x27,  # VK_RIGHT
        0x28,  # VK_DOWN
        0x2D,  # VK_INSERT
        0x2E,  # VK_DELETE
    })
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    MOUSEEVENTF_WHEEL = 0x0800
    WHEEL_DELTA = 120
    DESKTOP_SCROLL_NOTCHES = 7

    def __init__(self, sessions: SessionSlots) -> None:
        self.sessions = sessions
        self.is_mac = sys.platform == "darwin"
        self.is_win = sys.platform == "win32"
        self.is_linux = sys.platform.startswith("linux")
        self.previous_window_info: dict[str, Any] | None = None
        self.last_agent_window: dict[str, Any] | None = None

        if self.is_win:
            self._init_windows()
        else:
            self.user32 = None
            self.shell32 = None
            self.kernel32 = None

    def _init_windows(self) -> None:
        ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        self.kernel32 = ctypes.windll.kernel32
        self.user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self.user32.MapVirtualKeyW.restype = wintypes.UINT
        self.user32.keybd_event.argtypes = [
            wintypes.BYTE,
            wintypes.BYTE,
            wintypes.DWORD,
            ULONG_PTR,
        ]
        self.user32.keybd_event.restype = None
        self.user32.GetForegroundWindow.argtypes = []
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetClassNameW.restype = ctypes.c_int
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.user32.SetCursorPos.restype = wintypes.BOOL
        self.user32.mouse_event.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ULONG_PTR,
        ]
        self.user32.mouse_event.restype = None
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.shell32.ShellExecuteW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        self.shell32.ShellExecuteW.restype = wintypes.HINSTANCE

    def _foreground_context(self) -> str:
        """Return lower-case foreground title/class/process text for routing."""
        if self.is_win and self.user32:
            try:
                from ctypes import wintypes

                hwnd = self.user32.GetForegroundWindow()
                if not hwnd:
                    return ""
                title_buffer = ctypes.create_unicode_buffer(512)
                class_buffer = ctypes.create_unicode_buffer(256)
                self.user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
                self.user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
                parts = [title_buffer.value, class_buffer.value]

                pid = wintypes.DWORD()
                self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                # PROCESS_QUERY_LIMITED_INFORMATION works for normal desktop
                # apps without requiring administrator rights.
                process = self.kernel32.OpenProcess(0x1000, False, pid.value) if self.kernel32 else None
                if process:
                    try:
                        image_buffer = ctypes.create_unicode_buffer(520)
                        image_size = wintypes.DWORD(len(image_buffer))
                        if self.kernel32.QueryFullProcessImageNameW(
                            process, 0, image_buffer, ctypes.byref(image_size)
                        ):
                            parts.append(image_buffer.value)
                    finally:
                        self.kernel32.CloseHandle(process)
                return " ".join(part for part in parts if part).lower()
            except (AttributeError, OSError, TypeError):
                return ""

        if self.is_linux:
            try:
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True,
                    text=True,
                    timeout=0.25,
                )
                return result.stdout.strip().lower()
            except (OSError, subprocess.SubprocessError):
                return ""

        if self.is_mac:
            try:
                result = subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to get name of first process whose frontmost is true',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                )
                return result.stdout.strip().lower()
            except (OSError, subprocess.SubprocessError):
                return ""

        return ""

    def is_terminal_context(self) -> bool:
        """Return true when paging should use terminal viewport scrolling."""
        forced = os.environ.get("MF17_SCROLL_CONTEXT", "auto").strip().lower()
        if forced in {"terminal", "cli", "integrated"}:
            return True
        if forced in {"desktop", "app", "window"}:
            return False
        context = self._foreground_context().lower()
        return any(marker in context for marker in self.TERMINAL_MARKERS)

    def is_codex_context(self) -> bool:
        context = self._foreground_context().lower()
        return any(marker in context for marker in self.CODEX_MARKERS)

    def context_scroll(self, direction: str) -> None:
        """Scroll the desktop conversation pane or a focused terminal viewport."""
        page_key = "PAGEUP" if direction.lower() in {"up", "pageup", "pgup"} else "PAGEDOWN"
        if self.is_terminal_context():
            self.shortcut(["SHIFT", page_key], focus=False)
            return
        if self.is_win and self._scroll_windows_conversation(direction):
            return
        self.shortcut([page_key], focus=False)

    def _scroll_windows_conversation(self, direction: str) -> bool:
        """Send a page-sized wheel event over the foreground app's content pane."""
        if not self.user32:
            return False
        try:
            hwnd = self.user32.GetForegroundWindow()
            bounds = RECT()
            original = POINT()
            if not hwnd or not self.user32.GetWindowRect(hwnd, ctypes.byref(bounds)):
                return False
            if not self.user32.GetCursorPos(ctypes.byref(original)):
                return False
            width = max(1, bounds.right - bounds.left)
            height = max(1, bounds.bottom - bounds.top)
            target_x = bounds.left + int(width * 0.70)
            target_y = bounds.top + int(height * 0.45)
            delta = self.WHEEL_DELTA * self.DESKTOP_SCROLL_NOTCHES
            if direction.lower() not in {"up", "pageup", "pgup"}:
                delta = -delta
            self.user32.SetCursorPos(target_x, target_y)
            self.user32.mouse_event(self.MOUSEEVENTF_WHEEL, 0, 0, delta & 0xFFFFFFFF, 0)
            self.user32.SetCursorPos(original.x, original.y)
            return True
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def new_chat(self) -> None:
        """Use Codex's stable command-palette command, with legacy fallback."""
        if self.is_codex_context():
            self.palette("New chat")
        else:
            self.shortcut(["CTRL_OR_CMD", "SHIFT", "L"], focus=False)

    def microphone(self, keys: list[str] | None = None, terminal_keys: list[str] | None = None) -> None:
        """Invoke the Voice shortcut configured in the desktop app's Settings."""
        configured = (
            os.environ.get("MF17_VOICE_KEYS", "").strip()
            or os.environ.get("MF17_MICROPHONE_KEYS", "").strip()
        )
        desktop_keys = [item.strip() for item in configured.split(",") if item.strip()] if configured else None
        self.shortcut(desktop_keys or keys or ["CTRL_OR_CMD", "SHIFT", "D"], focus=False)

    def record_current_window(self) -> None:
        """Capture the currently focused window/application before switching to an agent."""
        if self.is_mac:
            script = """
            tell application "System Events"
                set p to first application process whose frontmost is true
                set pName to name of p
                set pPid to unix id of p
                set pBundle to ""
                try
                    set pBundle to bundle identifier of p
                end try
                set wName to ""
                try
                    set wName to name of window 1 of p
                end try
                set itermSid to ""
                if pName is "iTerm2" then
                    try
                        tell application "iTerm"
                            set itermSid to unique ID of current session of current window
                        end tell
                    end try
                end if
                return pName & "|||" & (pPid as string) & "|||" & pBundle & "|||" & wName & "|||" & itermSid
            end tell
            """
            try:
                res = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                if res.returncode == 0 and res.stdout.strip():
                    parts = res.stdout.strip().split("|||")
                    p_name = parts[0] if len(parts) > 0 else ""
                    p_bundle = parts[2] if len(parts) > 2 else ""
                    window_data = {
                        "platform": "darwin",
                        "process_name": p_name,
                        "pid": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                        "bundle_id": p_bundle,
                        "window_name": parts[3] if len(parts) > 3 else "",
                        "iterm_sid": parts[4] if len(parts) > 4 else "",
                    }
                    if (
                        p_name.lower() in {"electron", "jetski", "com.google.jetski", "codex"}
                        or p_bundle in {"com.google.jetski", "com.openai.codex"}
                    ):
                        self.last_agent_window = window_data
                        return

                    self.previous_window_info = window_data
            except Exception:
                pass
        elif self.is_win and self.user32:
            try:
                hwnd = self.user32.GetForegroundWindow()
                if hwnd:
                    self.previous_window_info = {"platform": "win32", "hwnd": hwnd}
            except Exception:
                pass
        elif self.is_linux:
            try:
                res = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    self.previous_window_info = {"platform": "linux", "win_id": res.stdout.strip()}
            except Exception:
                pass

    def restore_previous_window(self) -> bool:
        """Switch focus back to the previously active working window."""
        if not self.previous_window_info:
            if self.is_mac:
                try:
                    subprocess.run(
                        ["osascript", "-e", 'tell application "System Events" to key code 48 using command down'],
                        capture_output=True,
                        timeout=0.8,
                    )
                    return True
                except Exception:
                    return False
            return False

        if self.is_mac:
            info = self.previous_window_info
            pid = info.get("pid")
            p_name = info.get("process_name")
            bundle_id = info.get("bundle_id")
            w_name = info.get("window_name", "")
            iterm_sid = info.get("iterm_sid", "")

            # If the target was an iTerm2 session
            if p_name == "iTerm2" and iterm_sid:
                script = f"""
                tell application "iTerm"
                    activate
                    repeat with w in windows
                        repeat with t in tabs of w
                            repeat with s in sessions of t
                                if unique ID of s is "{iterm_sid}" or unique ID of s contains "{iterm_sid}" then
                                    tell s to select
                                    tell t to select
                                    tell application "System Events"
                                        tell process "iTerm2"
                                            set frontmost to true
                                            try
                                                perform action "AXRaise" of (first window whose name is (name of w))
                                            end try
                                        end tell
                                    end tell
                                    return
                                end if
                            end repeat
                        end repeat
                    end repeat
                end tell
                """
                try:
                    res = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2.0)
                    if res.returncode == 0:
                        return True
                except Exception:
                    pass

            escaped_w = w_name.replace('\\', '\\\\').replace('"', '\\"') if w_name else ""
            escaped_p = p_name.replace('\\', '\\\\').replace('"', '\\"') if p_name else ""

            script_lines = ['tell application "System Events"']
            script_lines.append('  set targetProc to missing value')
            if pid:
                script_lines.append(f'  repeat with p in (every application process whose unix id is {pid})')
                script_lines.append('    set targetProc to p')
                script_lines.append('    exit repeat')
                script_lines.append('  end repeat')
            if bundle_id:
                script_lines.append('  if targetProc is missing value then')
                script_lines.append(f'    repeat with p in (every application process whose bundle identifier is "{bundle_id}")')
                script_lines.append('      set targetProc to p')
                script_lines.append('      exit repeat')
                script_lines.append('    end repeat')
                script_lines.append('  end if')
            if p_name:
                script_lines.append('  if targetProc is missing value then')
                script_lines.append(f'    repeat with p in (every application process whose name is "{escaped_p}")')
                script_lines.append('      set targetProc to p')
                script_lines.append('      exit repeat')
                script_lines.append('    end repeat')
                script_lines.append('  end if')

            script_lines.append('  if targetProc is not missing value then')
            script_lines.append('    set frontmost of targetProc to true')
            if escaped_w:
                script_lines.append('    try')
                script_lines.append(f'      tell targetProc to perform action "AXRaise" of (first window whose name is "{escaped_w}")')
                script_lines.append('    on error')
                script_lines.append('      try')
                script_lines.append('        tell targetProc to perform action "AXRaise" of window 1')
                script_lines.append('      end try')
                script_lines.append('    end try')
            else:
                script_lines.append('    try')
                script_lines.append('      tell targetProc to perform action "AXRaise" of window 1')
                script_lines.append('    end try')
            script_lines.append('  end if')
            script_lines.append('end tell')

            script = "\n".join(script_lines)
            try:
                res = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2.0)
                if res.returncode == 0:
                    return True
            except Exception:
                pass

            # Fallback activate
            if bundle_id:
                try:
                    subprocess.run(["osascript", "-e", f'tell application id "{bundle_id}" to activate'], capture_output=True, timeout=1.0)
                    return True
                except Exception:
                    pass
            elif p_name:
                try:
                    subprocess.run(["osascript", "-e", f'tell application "{escaped_p}" to activate'], capture_output=True, timeout=1.0)
                    return True
                except Exception:
                    pass
            return False

        elif self.is_win and self.user32:
            hwnd = self.previous_window_info.get("hwnd")
            if hwnd:
                try:
                    self.user32.SetForegroundWindow(hwnd)
                    return True
                except Exception:
                    return False

        elif self.is_linux:
            win_id = self.previous_window_info.get("win_id")
            if win_id:
                try:
                    subprocess.run(["xdotool", "windowactivate", win_id], capture_output=True, timeout=1.0)
                    return True
                except Exception:
                    return False

        return False

    def open_thread(self, session_id: str | None, slot: int = 0, provider: str = PROVIDER_ANTIGRAVITY) -> None:
        """Activate Codex or Antigravity and focus the selected conversation."""
        if not session_id:
            return
        self.record_current_window()
        provider = normalize_provider(provider)
        uri = conversation_uri(session_id, provider) if session_id else None
        workspace_name = ""
        terminal_app = None
        iterm_sid = ""
        tty = ""
        pid = None
        if 0 <= slot < len(self.sessions.slots) and self.sessions.slots[slot]:
            slot_item = self.sessions.slots[slot]
            workspace_name = str(slot_item.get("workspace") or "")
            terminal_app = slot_item.get("terminal_app")
            iterm_sid = str(slot_item.get("iterm_session_id") or "")
            tty = str(slot_item.get("tty") or "")
            pid = slot_item.get("pid")

        if self.is_mac:
            # If terminal_app is not set, infer whether this session is running in a terminal emulator
            if not terminal_app:
                if pid and pid > 0:
                    try:
                        res = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "tty="],
                            capture_output=True,
                            text=True,
                            timeout=0.5,
                        )
                        t = res.stdout.strip()
                        if t and t not in {"??", "-"}:
                            tty = t
                            terminal_app = "iTerm"
                    except Exception:
                        pass
                if not terminal_app and provider == PROVIDER_ANTIGRAVITY:
                    # Check if any CLI jetski process is currently attached to a local tty
                    try:
                        res = subprocess.run(
                            ["ps", "-eo", "pid,tty,comm"],
                            capture_output=True,
                            text=True,
                            timeout=0.5,
                        )
                        for line in res.stdout.splitlines():
                            parts = line.strip().split(None, 2)
                            if len(parts) == 3:
                                p_pid, p_tty, p_comm = parts
                                comm_lower = p_comm.lower()
                                if "jetski" in comm_lower and "helper" not in comm_lower and "applications" not in comm_lower:
                                    if p_tty not in {"??", "-"}:
                                        tty = p_tty
                                        terminal_app = "iTerm"
                                        break
                    except Exception:
                        pass

            # If this session is running in a terminal emulator (e.g. iTerm2 / Terminal)
            if terminal_app:
                app_name = "iTerm" if "iterm" in terminal_app.lower() else terminal_app
                if 0 <= slot < len(self.sessions.slots) and self.sessions.slots[slot]:
                    slot_item = self.sessions.slots[slot]
                    if not iterm_sid:
                        iterm_sid = str(slot_item.get("iterm_session_id") or "")
                    if not tty:
                        tty = str(slot_item.get("tty") or "")

                if app_name == "iTerm":
                    tty_clean = tty.strip()
                    tty_path = f"/dev/{tty_clean}" if tty_clean and not tty_clean.startswith("/dev/") else tty_clean
                    script = f"""
                    tell application "iTerm"
                        activate
                        repeat with w in windows
                            repeat with t in tabs of w
                                repeat with s in sessions of t
                                    set matched to false
                                    if "{iterm_sid}" is not "" and (unique ID of s is "{iterm_sid}" or unique ID of s contains "{iterm_sid}") then
                                        set matched to true
                                    else if "{tty_clean}" is not "" and (tty of s is "{tty_path}" or tty of s is "{tty_clean}") then
                                        set matched to true
                                    end if
                                    if matched then
                                        tell s to select
                                        tell t to select
                                        tell application "System Events"
                                            tell process "iTerm2"
                                                set frontmost to true
                                                try
                                                    perform action "AXRaise" of (first window whose name is (name of w))
                                                end try
                                            end tell
                                        end tell
                                        return
                                    end if
                                end repeat
                            end repeat
                        end repeat
                        repeat with w in windows
                            repeat with t in tabs of w
                                repeat with s in sessions of t
                                    if name of s contains "jetski" or name of w contains "jetski" then
                                        tell s to select
                                        tell t to select
                                        tell application "System Events"
                                            tell process "iTerm2"
                                                set frontmost to true
                                                try
                                                    perform action "AXRaise" of (first window whose name is (name of w))
                                                end try
                                            end tell
                                        end tell
                                        return
                                    end if
                                end repeat
                            end repeat
                        end repeat
                        tell application "System Events"
                            tell process "iTerm2"
                                set frontmost to true
                                try
                                    perform action "AXRaise" of window 1
                                end try
                            end tell
                        end tell
                    end tell
                    """
                else:
                    script = f"""
                    try
                        tell application "{app_name}" to activate
                    end try
                    tell application "System Events"
                        repeat with p in (every process whose name contains "{app_name}")
                            set frontmost of p to true
                            try
                                tell p to perform action "AXRaise" of window 1
                            end try
                        end repeat
                    end tell
                    """
                try:
                    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2.0)
                except Exception:
                    pass
                return

            if provider == PROVIDER_ANTIGRAVITY:
                script = f"""
                try
                    tell application id "com.google.jetski" to activate
                end try
                tell application "System Events"
                    repeat with p in (every process whose name is "Electron" or name is "Jetski" or bundle identifier is "com.google.jetski")
                        set frontmost of p to true
                        set targetWin to missing value
                        if "{workspace_name}" is not "" then
                            repeat with w in (every window of p)
                                if name of w contains "{workspace_name}" then
                                    set targetWin to w
                                    exit repeat
                                end if
                            end repeat
                        end if
                        if targetWin is missing value and (count of windows of p) > 0 then
                            set targetWin to window 1 of p
                        end if
                        if targetWin is not missing value then
                            try
                                tell p to perform action "AXRaise" of targetWin
                            end try
                        end if
                    end repeat
                end tell
                """
                try:
                    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2.0)
                except Exception:
                    pass
            elif provider == PROVIDER_CODEX:
                try:
                    subprocess.run(["osascript", "-e", 'tell application "Codex" to activate'], capture_output=True, timeout=1.5)
                except Exception:
                    pass
            if uri:
                try:
                    subprocess.run(["open", "-u", uri], capture_output=True, timeout=1.5)
                except Exception:
                    pass
        elif self.is_win:
            if self.shell32 and uri:
                self.shell32.ShellExecuteW(None, "open", uri, None, None, 1)
        elif self.is_linux:
            if uri:
                try:
                    subprocess.run(["xdg-open", uri], capture_output=True, timeout=1.5)
                except Exception:
                    pass

    def shortcut(self, keys: list[str], focus: bool = False) -> None:
        normalized: list[str] = []
        for k in keys:
            k_upper = k.upper()
            if k_upper in {"CTRL_OR_CMD", "PRIMARY"}:
                normalized.append("CMD" if self.is_mac else "CTRL")
            else:
                normalized.append(k_upper)

        if self.is_mac:
            modifiers: list[str] = []
            target_key: str | None = None
            key_code: int | None = None

            for k in normalized:
                if k in {"CMD", "COMMAND"}:
                    modifiers.append("command down")
                elif k in {"CTRL", "CONTROL"}:
                    modifiers.append("control down")
                elif k in {"SHIFT"}:
                    modifiers.append("shift down")
                elif k in {"ALT", "OPTION"}:
                    modifiers.append("option down")
                elif k in self.MAC_KEY_CODES:
                    key_code = self.MAC_KEY_CODES[k]
                else:
                    target_key = k.lower()

            using_clause = ""
            if modifiers:
                if len(modifiers) == 1:
                    using_clause = f" using {modifiers[0]}"
                else:
                    using_clause = f" using {{{', '.join(modifiers)}}}"

            if key_code is not None:
                script = f'tell application "System Events" to key code {key_code}{using_clause}'
            elif target_key is not None:
                escaped_key = target_key.replace('\\', '\\\\').replace('"', '\\"')
                script = f'tell application "System Events" to keystroke "{escaped_key}"{using_clause}'
            else:
                return

            try:
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=1.5)
            except Exception as exc:
                print(f"macOS shortcut error: {exc}", file=sys.stderr)

        elif self.is_win:
            codes = []
            for k in normalized:
                if k in self.WIN_VK:
                    codes.append(self.WIN_VK[k])
                elif len(k) == 1:
                    codes.append(ord(k))
            for code in codes:
                if self.user32:
                    scan = self.user32.MapVirtualKeyW(code, 0)
                    flags = self.KEYEVENTF_EXTENDEDKEY if code in self.WIN_EXTENDED_VK else 0
                    self.user32.keybd_event(code, scan, flags, 0)
            for code in reversed(codes):
                if self.user32:
                    scan = self.user32.MapVirtualKeyW(code, 0)
                    flags = self.KEYEVENTF_KEYUP
                    if code in self.WIN_EXTENDED_VK:
                        flags |= self.KEYEVENTF_EXTENDEDKEY
                    self.user32.keybd_event(code, scan, flags, 0)

        elif self.is_linux:
            xdo_keys = []
            for k in normalized:
                if k in {"CTRL", "CMD"}:
                    xdo_keys.append("ctrl")
                elif k == "SHIFT":
                    xdo_keys.append("shift")
                elif k in {"ALT", "OPTION"}:
                    xdo_keys.append("alt")
                elif k in {"ENTER", "RETURN"}:
                    xdo_keys.append("Return")
                elif k in {"ESC", "ESCAPE"}:
                    xdo_keys.append("Escape")
                elif k == "UP":
                    xdo_keys.append("Up")
                elif k == "DOWN":
                    xdo_keys.append("Down")
                elif k in {"PAGEUP", "PGUP"}:
                    xdo_keys.append("Prior")
                elif k in {"PAGEDOWN", "PGDN"}:
                    xdo_keys.append("Next")
                else:
                    xdo_keys.append(k.lower())
            try:
                subprocess.run(["xdotool", "key", "+".join(xdo_keys)], capture_output=True, timeout=1.5)
            except Exception:
                pass

    def type_text(self, text: str) -> None:
        if self.is_mac:
            escaped = text.replace('\\', '\\\\').replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{escaped}"'
            try:
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=1.5)
            except Exception:
                pass
        elif self.is_win and self.user32 is not None:
            for character in text:
                scan = self.user32.VkKeyScanW(ord(character))
                vk = scan & 0xFF
                modifiers = (scan >> 8) & 0xFF
                held: list[int] = []
                for flag, code in ((1, self.WIN_VK["SHIFT"]), (2, self.WIN_VK["CTRL"]), (4, self.WIN_VK["ALT"])):
                    if modifiers & flag:
                        s = self.user32.MapVirtualKeyW(code, 0)
                        self.user32.keybd_event(code, s, 0, 0)
                        held.append(code)
                s = self.user32.MapVirtualKeyW(vk, 0)
                self.user32.keybd_event(vk, s, 0, 0)
                self.user32.keybd_event(vk, s, 0x0002, 0)
                for code in reversed(held):
                    s = self.user32.MapVirtualKeyW(code, 0)
                    self.user32.keybd_event(code, s, 0x0002, 0)
        elif self.is_linux:
            try:
                subprocess.run(["xdotool", "type", "--", text], capture_output=True, timeout=1.5)
            except Exception:
                pass

    def palette(self, command: str) -> None:
        self.shortcut(["CTRL_OR_CMD", "SHIFT", "P"], focus=False)
        time.sleep(0.15)
        self.type_text(command)
        time.sleep(0.10)
        self.shortcut(["ENTER"], focus=False)

    def slash(self, command: str, focus: bool = False) -> None:
        if self.is_mac:
            escaped = command.replace('\\', '\\\\').replace('"', '\\"')
            script = (
                f'tell application "System Events"\n'
                f'  keystroke "{escaped}"\n'
                f'  delay 0.05\n'
                f'  key code 36\n'
                f'end tell'
            )
            try:
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=1.5)
            except Exception:
                pass
        else:
            self.type_text(command)
            time.sleep(0.05)
            self.shortcut(["ENTER"], focus=False)
