"""Raw HID bridge between an MF17 QMK keymap and Codex/Antigravity."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from contextlib import closing
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from typing import Any
from urllib.parse import quote

try:
    from host.lighting_api import (
        ACK_OK,
        CAP_LOGICAL_KEYS,
        KEY_COUNT,
        LightingReportEncoder,
        MSG_ACK,
        MSG_CAPABILITIES,
        MSG_CLEAR_ALL_KEY_RGB,
        MSG_CLEAR_KEY_RGB,
        MSG_CLEAR_PIXELS,
        MSG_GET_CAPABILITIES,
        MSG_SET_ALL,
        MSG_SET_ALL_KEYS_RGB,
        MSG_SET_KEY_RGB,
        MSG_SET_KEYS_RGB,
        MSG_SET_PIXEL,
        PROTOCOL_VERSION,
    )
except ModuleNotFoundError:
    # Support `python host/mf17_bridge.py` as documented.
    from lighting_api import (  # type: ignore[no-redef]
        ACK_OK,
        CAP_LOGICAL_KEYS,
        KEY_COUNT,
        LightingReportEncoder,
        MSG_ACK,
        MSG_CAPABILITIES,
        MSG_CLEAR_ALL_KEY_RGB,
        MSG_CLEAR_KEY_RGB,
        MSG_CLEAR_PIXELS,
        MSG_GET_CAPABILITIES,
        MSG_SET_ALL,
        MSG_SET_ALL_KEYS_RGB,
        MSG_SET_KEY_RGB,
        MSG_SET_KEYS_RGB,
        MSG_SET_PIXEL,
        PROTOCOL_VERSION,
    )


VID = 0x3A54
PID = 0x4F5D
USAGE_PAGE = 0xFF60
USAGE = 0x61
REPORT_SIZE = 32
UDP_HOST = "127.0.0.1"
UDP_PORT = 41717
LOG_PATH = Path(__file__).with_name("mf17_bridge.log")

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)
SESSION_FILE_PATTERN = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl$", re.IGNORECASE)
TRANSCRIPT_TAIL_BYTES = 256 * 1024
STALE_THINKING_TIMEOUT_SECS = 45.0
AGENT_SLOT_COUNT = 10
# Firmware status slots follow the physical action order 1..9,0. Recent tasks
# are assigned by numeric key order 0..9, so rank 0 maps to firmware slot 9.
NUMERIC_KEY_BY_SLOT = (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)
RECENT_RANK_TO_SLOT = (9, 0, 1, 2, 3, 4, 5, 6, 7, 8)
PROVIDER_CODEX = "codex"
PROVIDER_ANTIGRAVITY = "antigravity"

MSG_ACTION = 0x10
MSG_SET_STATUS = 0x20
MSG_SET_SELECTED = 0x21
MSG_PING = 0x7E
MSG_PONG = 0x7F

STATUS_IDS = {
    "off": 0,
    "idle": 1,
    "thinking": 2,
    "complete": 3,
    "waiting": 4,
    "error": 5,
}


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def archived_codex_session_ids(
    state_path: Path | None = None,
    archive_dir: Path | None = None,
) -> set[str]:
    """Return IDs Codex has archived, using both current and legacy storage."""
    codex_home = Path.home() / ".codex"
    database = state_path or (codex_home / "state_5.sqlite")
    archived: set[str] = set()
    try:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as connection:
            for (session_id,) in connection.execute(
                "SELECT id FROM threads WHERE archived != 0"
            ):
                value = str(session_id or "")
                if UUID_PATTERN.fullmatch(value):
                    archived.add(value)
    except (OSError, sqlite3.Error):
        pass

    directory = archive_dir or (codex_home / "archived_sessions")
    try:
        for path in directory.glob("*.jsonl"):
            match = SESSION_FILE_PATTERN.search(path.name)
            if match:
                archived.add(match.group(1))
    except OSError:
        pass
    return archived


def active_codex_sessions(
    state_path: Path | None = None,
    limit: int = AGENT_SLOT_COUNT,
) -> list[tuple[str, Path]] | None:
    """Return active root tasks in the same recency order as Codex Desktop."""
    database = state_path or (Path.home() / ".codex" / "state_5.sqlite")
    try:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as connection:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")
            }
            if not {"id", "rollout_path", "archived", "source"}.issubset(columns):
                return None
            recency_column = next(
                (name for name in ("recency_at_ms", "updated_at_ms", "updated_at") if name in columns),
                None,
            )
            order_clause = f" ORDER BY {recency_column} DESC" if recency_column else ""
            root_clause = (
                " AND (thread_source IS NULL OR thread_source = 'user')"
                if "thread_source" in columns
                else ""
            )
            query = (
                "SELECT id, rollout_path FROM threads "
                "WHERE archived = 0 AND source = 'vscode'"
                f"{root_clause}{order_clause} LIMIT ?"
            )
            rows = connection.execute(query, (max(0, int(limit)),)).fetchall()
    except (OSError, sqlite3.Error):
        return None

    sessions: list[tuple[str, Path]] = []
    for session_id, rollout_path in rows:
        value = str(session_id or "")
        path = Path(str(rollout_path or ""))
        if UUID_PATTERN.fullmatch(value) and path.is_file():
            sessions.append((value, path))
    return sessions


def visible_codex_session_ids(
    index_path: Path | None = None,
    state_path: Path | None = None,
    archive_dir: Path | None = None,
) -> set[str]:
    """Return task IDs recorded in Codex Desktop's visible session index."""
    path = index_path or (Path.home() / ".codex" / "session_index.jsonl")
    visible: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    session_id = str(json.loads(line).get("id") or "")
                except (json.JSONDecodeError, TypeError):
                    continue
                if UUID_PATTERN.fullmatch(session_id):
                    visible.add(session_id)
    except OSError:
        pass
    return visible - archived_codex_session_ids(state_path, archive_dir)


def root_codex_session_id(path: Path, visible_ids: set[str] | None = None) -> str | None:
    """Resolve a real desktop task ID and reject reviewers/subagents."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            record = json.loads(stream.readline())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if record.get("type") != "session_meta":
        return None
    payload = record.get("payload") or {}
    source = payload.get("source")
    thread_source = payload.get("thread_source")
    if not isinstance(source, str) or source.lower() != "vscode":
        return None
    if thread_source not in {None, "user"} or payload.get("parent_thread_id"):
        return None
    session_id = str(payload.get("id") or payload.get("session_id") or "")
    if not UUID_PATTERN.fullmatch(session_id):
        return None
    if visible_ids and session_id not in visible_ids:
        return None
    return session_id


def normalize_provider(value: Any, default: str = PROVIDER_ANTIGRAVITY) -> str:
    """Normalize provider labels used by Codex and Antigravity/Jetski hooks."""
    name = str(value or "").strip().lower().replace("-", "_")
    if name in {"codex", "openai", "openai_codex"}:
        return PROVIDER_CODEX
    if name in {"antigravity", "jetski", "google", "gemini", "google_jetski"}:
        return PROVIDER_ANTIGRAVITY
    return default


def conversation_uri(session_id: str, provider: str) -> str:
    """Build the deep link understood by the selected desktop app."""
    normalized = normalize_provider(provider)
    if normalized == PROVIDER_CODEX:
        return f"codex://threads/{quote(session_id, safe='')}"
    return f"jetski-ide://conversation/{quote(session_id, safe='')}"

ACTION_NAMES = {
    1: "agent_1",
    2: "agent_2",
    3: "agent_3",
    4: "agent_4",
    5: "agent_5",
    6: "agent_6",
    7: "agent_7",
    8: "agent_8",
    9: "agent_9",
    10: "agent_0",
    11: "approve",
    12: "reject",
    13: "up",
    14: "down",
    15: "new_chat",
    16: "dictation",
    17: "send",
}

# Protocol v1 firmware uses the original six-agent/joystick keymap.  Keep this
# table separate so a restored keypad cannot be interpreted as the newer
# ten-agent layout by the current cross-compatible bridge.
LEGACY_ACTION_NAMES = {
    1: "agent_1",
    2: "agent_2",
    3: "agent_3",
    4: "agent_4",
    5: "agent_5",
    6: "agent_6",
    7: "up",
    8: "legacy_right",
    9: "down",
    10: "legacy_left",
    11: "legacy_center",
    12: "legacy_fast",
    13: "approve",
    14: "reject",
    15: "new_chat",
    16: "dictation",
    17: "send",
}


def latest_transcript_state(path: Path) -> str | None:
    """Return a terminal fallback state from the newest turn marker in a transcript."""
    try:
        if not path.exists():
            return None
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - TRANSCRIPT_TAIL_BYTES)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.readlines()
    except OSError:
        return None

    completed_codex_calls: set[str] = set()
    for raw_line in reversed(lines):
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        step_type = record.get("type", "")
        status = record.get("status", "")

        # Codex app transcripts record a pending request_user_input as a
        # response_item/function_call. It does not necessarily dispatch a
        # PermissionRequest hook, so detect the unresolved call directly.
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        payload_type = str(payload.get("type", "")).lower()
        if payload_type in {"function_call_output", "tool_result", "tool_output"}:
            call_id = str(payload.get("call_id") or payload.get("callId") or payload.get("id") or "")
            if call_id:
                completed_codex_calls.add(call_id)
        elif payload_type in {"function_call", "tool_call", "server_request"}:
            tool_name = str(
                payload.get("name")
                or payload.get("tool_name")
                or payload.get("toolName")
                or payload.get("method")
                or ""
            ).lower()
            if any(marker in tool_name for marker in ("request_user_input", "requestuserinput", "elicitation")):
                call_id = str(payload.get("call_id") or payload.get("callId") or payload.get("id") or "")
                if not call_id or call_id not in completed_codex_calls:
                    return "waiting"
        elif payload_type in {"request_user_input", "elicitation_request", "mcp_elicitation_request"}:
            return "waiting"

        if str(step_type).lower() in {"request_user_input", "elicitation_request", "mcp_elicitation_request"}:
            return "waiting"

        if step_type == "USER_INPUT":
            return None

        if step_type == "PLANNER_RESPONSE" or status in {"DONE", "ERROR"}:
            tool_calls = record.get("tool_calls") or []
            for tc in tool_calls:
                tname = str(tc.get("name", "")).lower()
                if "ask_question" in tname or "ask_custom_permission" in tname or "request_user_input" in tname:
                    return "waiting"
            if status == "ERROR" or record.get("error"):
                return "error"
            if status == "DONE" and not tool_calls:
                return "complete"
            if status == "DONE" and tool_calls:
                return None

        # Codex transcript fallback
        if record.get("type") == "event_msg":
            event_type = record.get("payload", {}).get("type")
            if event_type == "task_complete":
                return "complete"
            if event_type == "turn_aborted":
                return "idle"
            if event_type == "task_started":
                return None

    return None


class SessionSlots:
    def __init__(self) -> None:
        self.slots: list[dict[str, Any] | None] = [None] * AGENT_SLOT_COUNT
        self.active_session: str | None = None

    def update(
        self,
        session_id: str,
        status: str,
        remove: bool = False,
        from_hook: bool = True,
        provider: str | None = None,
    ) -> int | None:
        now = time.time()
        existing = next(
            (index for index, item in enumerate(self.slots) if item and item["session_id"] == session_id),
            None,
        )

        if remove:
            if existing is not None:
                self.slots[existing] = None
            if self.active_session == session_id:
                remaining = [item for item in self.slots if item]
                self.active_session = max(remaining, key=lambda item: item["updated"])["session_id"] if remaining else None
            return existing

        if existing is None:
            existing = next((index for index, item in enumerate(self.slots) if item is None), None)
        if existing is None:
            existing = min(range(AGENT_SLOT_COUNT), key=lambda index: self.slots[index]["updated"])

        previous = self.slots[existing]
        self.slots[existing] = {
            "session_id": session_id,
            "status": status,
            "updated": now,
            "last_hook_time": now if from_hook else previous.get("last_hook_time", 0) if previous else 0,
            "provider": normalize_provider(
                provider,
                previous.get("provider", PROVIDER_ANTIGRAVITY) if previous else PROVIDER_ANTIGRAVITY,
            ),
        }
        self.active_session = session_id
        return existing

    def session_for_slot(self, slot: int) -> str | None:
        item = self.slots[slot] if 0 <= slot < len(self.slots) else None
        return item["session_id"] if item else None

    def provider_for_slot(self, slot: int) -> str:
        item = self.slots[slot] if 0 <= slot < len(self.slots) else None
        return normalize_provider(item.get("provider") if item else None)

    def reconcile_terminal(self, session_id: str, status: str) -> int | None:
        """Resolve a stale thinking state ONLY after STALE_THINKING_TIMEOUT_SECS without hook updates."""
        existing = next(
            (index for index, item in enumerate(self.slots) if item and item["session_id"] == session_id),
            None,
        )
        if existing is None:
            return None

        current_status = self.slots[existing]["status"]
        # A pending Codex request_user_input is visible in the transcript even
        # when the CLI/Desktop hook surface does not emit PermissionRequest.
        # Promote it immediately so the amber LED is not delayed by the stale
        # thinking timeout. Other transcript fallbacks remain conservative.
        if status == "waiting" and current_status in {"idle", "thinking"}:
            self.slots[existing]["status"] = status
            self.slots[existing]["updated"] = time.time()
            return existing
        if current_status != "thinking":
            return None

        now = time.time()
        last_hook = self.slots[existing].get("last_hook_time", 0)
        if (now - last_hook) < STALE_THINKING_TIMEOUT_SECS:
            return None

        self.slots[existing]["status"] = status
        self.slots[existing]["updated"] = now
        return existing

    def populate_recent(self, session_ids: list[str], providers: dict[str, str] | None = None) -> bool:
        """Map recency ranks to numeric keys 0..9 and remove stale tasks while preserving active remote sessions."""
        previous_by_id = {
            item["session_id"]: item for item in self.slots if item
        }

        # Retain active remote/hook sessions that may not be on the local filesystem (e.g. Cloudtop)
        all_candidate_ids = list(session_ids)
        for prev_id, prev_item in sorted(
            previous_by_id.items(),
            key=lambda x: x[1].get("updated", 0),
            reverse=True,
        ):
            if (
                prev_id not in all_candidate_ids
                and prev_item.get("last_hook_time", 0) > 0
                and prev_item.get("status") not in {"off"}
            ):
                all_candidate_ids.append(prev_id)

        new_slots: list[dict[str, Any] | None] = [None] * AGENT_SLOT_COUNT
        for rank, session_id in enumerate(all_candidate_ids[:AGENT_SLOT_COUNT]):
            slot = RECENT_RANK_TO_SLOT[rank]
            previous = previous_by_id.get(session_id)
            if previous:
                item = dict(previous)
                item["provider"] = normalize_provider(
                    (providers or {}).get(session_id, item.get("provider"))
                )
            else:
                item = {
                    "session_id": session_id,
                    "status": "idle",
                    "updated": time.time(),
                    "last_hook_time": 0,
                    "provider": normalize_provider((providers or {}).get(session_id)),
                }
            new_slots[slot] = item

        changed = [item["session_id"] if item else None for item in self.slots] != [
            item["session_id"] if item else None for item in new_slots
        ]
        self.slots = new_slots
        desired_ids = set(all_candidate_ids[:AGENT_SLOT_COUNT])
        if self.active_session not in desired_ids:
            self.active_session = all_candidate_ids[0] if all_candidate_ids else None
        return changed


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

    def open_thread(self, session_id: str | None, slot: int = 0, provider: str = PROVIDER_ANTIGRAVITY) -> None:
        """Activate Codex or Antigravity and focus the selected conversation."""
        provider = normalize_provider(provider)
        uri = conversation_uri(session_id, provider) if session_id else None
        if self.is_mac:
            if provider == PROVIDER_ANTIGRAVITY:
                try:
                    subprocess.run(["open", "-b", "com.google.jetski"], capture_output=True, timeout=1.5)
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


class Bridge:
    def __init__(self, actions_path: Path, dry_run: bool = False, provider: str | None = None) -> None:
        self.actions = json.loads(actions_path.read_text(encoding="utf-8"))
        self.sessions = SessionSlots()
        self.controller = OSController(self.sessions)
        self.device: Any = None
        self.dry_run = dry_run
        self.udp_socket: socket.socket | None = None
        self.last_session_scan = 0.0
        self.session_files: dict[str, Path] = {}
        self.session_providers: dict[str, str] = {}
        raw_prov = provider if provider and provider != "auto" else os.environ.get("MF17_PROVIDER")
        self.default_provider = normalize_provider(raw_prov) if raw_prov else None
        self.lighting = LightingReportEncoder()
        self.lighting_capabilities = 0
        self.lighting_key_count = KEY_COUNT
        self.action_protocol_version = PROTOCOL_VERSION

    def log(self, message: str) -> None:
        if self.dry_run:
            return
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with LOG_PATH.open("a", encoding="utf-8") as stream:
                stream.write(f"{timestamp} {message}\n")
        except OSError:
            pass

    def find_recent_sessions(self) -> list[tuple[str, Path, str]]:
        results: list[tuple[str, Path, str]] = []
        found_ids: set[str] = set()

        app_data_dir = os.environ.get("JETSKI_APP_DATA_DIR")
        jetski_brain = None
        if app_data_dir:
            candidate = Path(app_data_dir).expanduser()
            if (candidate / "brain").exists():
                jetski_brain = candidate / "brain"
            elif candidate.name == "brain" and candidate.exists():
                jetski_brain = candidate
        if not jetski_brain or not jetski_brain.exists():
            jetski_brain = Path.home() / ".gemini" / "jetski" / "brain"
        
        if jetski_brain.exists() and jetski_brain.is_dir():
            try:
                subdirs = sorted(
                    [p for p in jetski_brain.iterdir() if p.is_dir() and UUID_PATTERN.match(p.name)],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for d in subdirs[:AGENT_SLOT_COUNT]:
                    session_id = d.name
                    transcript = d / ".system_generated" / "logs" / "transcript.jsonl"
                    if transcript.exists() and transcript.stat().st_size > 0:
                        results.append((session_id, transcript, PROVIDER_ANTIGRAVITY))
                        found_ids.add(session_id)
            except OSError:
                pass

        active_codex = active_codex_sessions(limit=AGENT_SLOT_COUNT - len(results))
        if active_codex is not None:
            for session_id, path in active_codex:
                if session_id not in found_ids:
                    results.append((session_id, path, PROVIDER_CODEX))
                    found_ids.add(session_id)
                if len(results) >= AGENT_SLOT_COUNT:
                    break
            return results

        codex_sessions = Path.home() / ".codex" / "sessions"
        visible_codex_ids = visible_codex_session_ids()
        if codex_sessions.exists() and codex_sessions.is_dir():
            try:
                candidates = sorted(
                    codex_sessions.rglob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for path in candidates:
                    session_id = root_codex_session_id(path, visible_codex_ids)
                    if session_id and session_id not in found_ids:
                        results.append((session_id, path, PROVIDER_CODEX))
                        found_ids.add(session_id)
                    if len(results) >= AGENT_SLOT_COUNT:
                        break
            except OSError:
                pass

        return results

    def refresh_recent_sessions(self) -> None:
        recent = self.find_recent_sessions()
        session_ids: list[str] = []
        providers: dict[str, str] = {}
        session_files: dict[str, Path] = {}
        for session_id, path, provider in recent:
            session_ids.append(session_id)
            session_files[session_id] = path
            providers[session_id] = provider
        self.session_files = session_files
        self.session_providers = providers

        if self.sessions.populate_recent(session_ids, providers=providers):
            self.sync_leds()
            self.log("loaded numeric task slots: " + ", ".join(
                f"{NUMERIC_KEY_BY_SLOT[slot]}={item['session_id']}"
                for slot, item in enumerate(self.sessions.slots) if item
            ))

        for session_id, path in self.session_files.items():
            state = latest_transcript_state(path)
            if state is None:
                continue
            slot = self.sessions.reconcile_terminal(session_id, state)
            if slot is not None:
                self.send_report([MSG_SET_STATUS, slot, STATUS_IDS[state]])
                self.log(
                    f"reconciled session={session_id} state={state} "
                    f"key={NUMERIC_KEY_BY_SLOT[slot]}"
                )

    def connect(self) -> bool:
        if self.dry_run:
            return True
        try:
            import hid
        except ImportError:
            raise SystemExit("hidapi is missing; install host/requirements.txt (pip install hidapi)")

        candidates = [
            item
            for item in hid.enumerate(VID, PID)
            if item.get("usage_page") == USAGE_PAGE and item.get("usage") == USAGE
        ]
        if not candidates:
            self.device = None
            return False

        device = hid.device()
        device.open_path(candidates[0]["path"])
        device.set_nonblocking(True)
        self.device = device
        self.lighting.protocol_version = 1
        self.action_protocol_version = 1
        self.lighting_capabilities = 0
        self.sync_leds()
        self.send_report([MSG_GET_CAPABILITIES, 0])
        self.send_report([MSG_PING])
        return True

    def send_report(self, values: list[int]) -> None:
        report = bytes((values + [0] * REPORT_SIZE)[:REPORT_SIZE])
        if self.dry_run:
            print("HID OUT", list(report[:5]))
        elif self.device:
            self.device.write(bytes([0]) + report)

    def sync_leds(self) -> None:
        for slot, item in enumerate(self.sessions.slots):
            state = item["status"] if item else "off"
            self.send_report([MSG_SET_STATUS, slot, STATUS_IDS[state]])

    def process_status(self, payload: dict[str, Any]) -> None:
        raw_status = payload.get("status", "idle")
        status = raw_status if isinstance(raw_status, str) and raw_status in STATUS_IDS else "idle"
        kind = str(payload.get("kind") or "")
        provider_default = PROVIDER_CODEX if kind == "codex_status" else self.default_provider
        provider = normalize_provider(payload.get("provider"), provider_default)
        session_id = str(
            payload.get("session_id")
            or payload.get("conversationId")
            or payload.get("conversation_id")
            or "unknown"
        )
        slot = self.sessions.update(
            session_id,
            status,
            bool(payload.get("remove", False)),
            from_hook=True,
            provider=provider,
        )
        if slot is not None:
            self.send_report([MSG_SET_STATUS, slot, STATUS_IDS["off" if payload.get("remove") else status]])
            self.log(
                f"status provider={provider} session={session_id} state={status} "
                f"key={NUMERIC_KEY_BY_SLOT[slot]}"
            )

    def process_lighting(self, payload: dict[str, Any]) -> None:
        try:
            reports = self.lighting.encode(payload)
        except (TypeError, ValueError) as exc:
            print(f"Ignored invalid lighting request: {exc}", file=sys.stderr)
            self.log(f"invalid lighting request: {exc}")
            return
        for report in reports:
            self.send_report(report)

    def process_hid_report(self, report: list[int]) -> None:
        if not report:
            return
        if report[0] == MSG_ACTION and len(report) >= 2:
            self.perform_action(report[1])
        elif report[0] == MSG_CAPABILITIES and len(report) >= 7:
            version = int(report[2])
            key_count = int(report[3])
            capabilities = int(report[4]) | (int(report[5]) << 8)
            action_layout = int(report[7]) if len(report) > 7 else 0
            if version >= PROTOCOL_VERSION and key_count == KEY_COUNT and capabilities & CAP_LOGICAL_KEYS:
                self.lighting.protocol_version = version
                self.action_protocol_version = 1 if action_layout == 1 else version
                self.lighting_key_count = key_count
                self.lighting_capabilities = capabilities
                self.log(
                    f"lighting API v{version} keys={key_count} capabilities=0x{capabilities:04x} "
                    f"action_layout={action_layout or 'protocol-default'}"
                )
        elif report[0] == MSG_ACK and len(report) >= 4:
            request_id, command, status = report[1:4]
            level = "ok" if status == ACK_OK else f"error={status}"
            self.log(f"lighting ack request={request_id} command=0x{command:02x} {level}")
        elif report[0] == MSG_PONG:
            version = report[1] if len(report) > 1 else "unknown"
            action_layout = int(report[2]) if len(report) > 2 else 0
            if isinstance(version, int) and version >= 1:
                self.action_protocol_version = 1 if action_layout == 1 else version
            self.log(
                f"received MF17 pong protocol={version} "
                f"action_layout={action_layout or 'protocol-default'}"
            )

    def perform_action(self, action_id: int) -> None:
        action_names = LEGACY_ACTION_NAMES if self.action_protocol_version == 1 else ACTION_NAMES
        name = action_names.get(action_id)
        action = self.actions.get(name) if name else None
        if not action:
            print(f"Unmapped action {action_id}", file=sys.stderr)
            self.log(f"action id={action_id} unmapped")
            return

        kind = action.get("type")
        self.log(f"action id={action_id} name={name} type={kind}")
        if kind == "agent":
            slot = int(action["slot"])
            session_id = self.sessions.session_for_slot(slot)
            provider = self.sessions.provider_for_slot(slot)
            self.send_report([MSG_SET_SELECTED, slot])
            self.controller.open_thread(session_id, slot=slot, provider=provider)
            if session_id:
                self.log(
                    f"selected task key={NUMERIC_KEY_BY_SLOT[slot]} "
                    f"provider={provider} session={session_id}"
                )
            else:
                self.log(f"task key={NUMERIC_KEY_BY_SLOT[slot]} is empty")
        elif kind == "shortcut":
            self.controller.shortcut(list(action["keys"]), focus=bool(action.get("focus", False)))
        elif kind == "context_scroll":
            self.controller.context_scroll(str(action.get("direction", "up")))
        elif kind == "new_chat":
            self.controller.new_chat()
        elif kind == "microphone":
            self.controller.microphone(
                keys=list(action.get("keys", [])) or None,
                terminal_keys=list(action.get("terminal_keys", [])) or None,
            )
        elif kind == "palette":
            self.controller.palette(str(action["command"]))
        elif kind == "slash":
            self.controller.slash(str(action["command"]), focus=bool(action.get("focus", False)))

    def run(self) -> None:
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.bind((UDP_HOST, UDP_PORT))
        udp_sock.setblocking(False)
        self.udp_socket = udp_sock

        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_sock.bind((UDP_HOST, UDP_PORT))
        tcp_sock.listen(5)
        tcp_sock.setblocking(False)

        last_connect_attempt = 0.0
        print(f"MF17 Codex/Antigravity bridge listening on udp+tcp://{UDP_HOST}:{UDP_PORT} (10 task slots)")
        self.log("bridge started (10 Agent slots, UDP+TCP)")
        self.refresh_recent_sessions()

        while True:
            now = time.monotonic()
            if now - self.last_session_scan >= 5.0:
                self.last_session_scan = now
                self.refresh_recent_sessions()
            if self.device is None and now - last_connect_attempt >= 2.0:
                last_connect_attempt = now
                try:
                    if self.connect():
                        print("Connected to MF17 Raw HID")
                        self.log("connected to MF17 Raw HID")
                except OSError as exc:
                    print(f"MF17 connect failed: {exc}", file=sys.stderr)
                    self.log(f"connect failed: {exc!r}")

            # 1. Process UDP status packets (fast local datagrams)
            try:
                while True:
                    data, _ = udp_sock.recvfrom(4096)
                    payload = json.loads(data.decode("utf-8"))
                    kind = payload.get("kind")
                    if kind in {"jetski_status", "antigravity_status", "codex_status"}:
                        self.process_status(payload)
                    elif kind == "lighting":
                        self.process_lighting(payload)
            except BlockingIOError:
                pass
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"Ignored malformed UDP packet: {exc}", file=sys.stderr)

            # 2. Process TCP connections (SSH RemoteForward from Cloudtop or remote hosts)
            try:
                while True:
                    conn, _ = tcp_sock.accept()
                    with conn:
                        conn.settimeout(0.5)
                        data = conn.recv(8192)
                        if data:
                            for line in data.decode("utf-8", errors="replace").splitlines():
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    payload = json.loads(line)
                                    kind = payload.get("kind")
                                    if kind in {"jetski_status", "antigravity_status", "codex_status"}:
                                        self.process_status(payload)
                                    elif kind == "lighting":
                                        self.process_lighting(payload)
                                except json.JSONDecodeError:
                                    pass
            except BlockingIOError:
                pass
            except OSError:
                pass

            if self.device is not None:
                try:
                    report = self.device.read(REPORT_SIZE)
                    self.process_hid_report(report)
                except OSError as exc:
                    print(f"MF17 disconnected: {exc}", file=sys.stderr)
                    self.log(f"MF17 disconnected: {exc!r}")
                    try:
                        self.device.close()
                    finally:
                        self.device = None

            time.sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser(description="MF17 Codex/Antigravity Keypad Bridge")
    parser.add_argument("--dry-run", action="store_true", help="run without opening HID")
    parser.add_argument(
        "--provider",
        type=str,
        default="auto",
        choices=["auto", "jetski", "antigravity", "codex"],
        help="active application provider (jetski, codex, or auto)",
    )
    parser.add_argument(
        "--actions",
        type=Path,
        default=Path(__file__).with_name("actions.json"),
    )
    args = parser.parse_args()
    while True:
        bridge = Bridge(args.actions, dry_run=args.dry_run, provider=args.provider)
        try:
            bridge.run()
        except KeyboardInterrupt:
            return 0
        except Exception:
            details = traceback.format_exc()
            try:
                with LOG_PATH.open("a", encoding="utf-8") as stream:
                    stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} bridge crashed\n{details}\n")
            except OSError:
                pass
            if args.dry_run:
                raise
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
