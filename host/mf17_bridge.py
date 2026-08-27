"""Raw HID bridge between an MF17 QMK keymap and the ChatGPT/Codex desktop app."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback
from typing import Any
from urllib.parse import quote
import re


VID = 0x3A54
PID = 0x4F5D
USAGE_PAGE = 0xFF60
USAGE = 0x61
REPORT_SIZE = 32
UDP_HOST = "127.0.0.1"
UDP_PORT = 41717
ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LOG_PATH = Path(__file__).with_name("mf17_bridge.log")
SESSION_ID_PATTERN = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl$", re.IGNORECASE)
TRANSCRIPT_TAIL_BYTES = 256 * 1024

MSG_ACTION = 0x10
MSG_SET_STATUS = 0x20
MSG_SET_SELECTED = 0x21
MSG_SET_PIXEL = 0x22
MSG_SET_ALL = 0x23
MSG_CLEAR_PIXELS = 0x24
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


def latest_transcript_state(path: Path) -> str | None:
    """Return a terminal fallback state from the newest Codex turn marker."""
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - TRANSCRIPT_TAIL_BYTES)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.readlines()
    except OSError:
        return None

    for raw_line in reversed(lines):
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if record.get("type") != "event_msg":
            continue
        event_type = record.get("payload", {}).get("type")
        if event_type == "task_complete":
            return "complete"
        if event_type == "turn_aborted":
            return "idle"
        if event_type == "task_started":
            return None
    return None

ACTION_NAMES = {
    1: "agent_1",
    2: "agent_2",
    3: "agent_3",
    4: "agent_4",
    5: "agent_5",
    6: "agent_6",
    7: "joystick_up",
    8: "joystick_right",
    9: "joystick_down",
    10: "joystick_left",
    11: "center",
    12: "fast_mode",
    13: "approve",
    14: "decline",
    15: "continue_new",
    16: "dictation",
    17: "send",
}


class SessionSlots:
    def __init__(self) -> None:
        self.slots: list[dict[str, Any] | None] = [None] * 6
        self.active_session: str | None = None

    def update(self, session_id: str, status: str, remove: bool = False) -> int | None:
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
            existing = min(range(6), key=lambda index: self.slots[index]["updated"])

        self.slots[existing] = {
            "session_id": session_id,
            "status": status,
            "updated": now,
        }
        self.active_session = session_id
        return existing

    def session_for_slot(self, slot: int) -> str | None:
        item = self.slots[slot] if 0 <= slot < len(self.slots) else None
        return item["session_id"] if item else None

    def reconcile_terminal(self, session_id: str, status: str) -> int | None:
        """Resolve a stale thinking state after a missed Stop/interrupt hook."""
        existing = next(
            (index for index, item in enumerate(self.slots) if item and item["session_id"] == session_id),
            None,
        )
        if existing is None or self.slots[existing]["status"] != "thinking":
            return None
        self.slots[existing]["status"] = status
        self.slots[existing]["updated"] = time.time()
        return existing

    def populate_recent(self, session_ids: list[str]) -> bool:
        """Fill unused slots from recent Codex task files without disturbing live status."""
        changed = False
        known = {item["session_id"] for item in self.slots if item}
        for session_id in session_ids:
            if session_id in known:
                continue
            empty = next((index for index, item in enumerate(self.slots) if item is None), None)
            if empty is None:
                break
            self.slots[empty] = {
                "session_id": session_id,
                "status": "idle",
                "updated": time.time(),
            }
            known.add(session_id)
            changed = True
        if self.active_session is None:
            first = next((item for item in self.slots if item), None)
            if first:
                self.active_session = first["session_id"]
        return changed


class WindowsController:
    VK = {
        "CTRL": 0x11,
        "SHIFT": 0x10,
        "ALT": 0x12,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "ESC": 0x1B,
        "DOWN": 0x28,
        "B": 0x42,
        "D": 0x44,
        "K": 0x4B,
        "N": 0x4E,
        "P": 0x50,
        "[": 0xDB,
        "]": 0xDD,
    }

    def __init__(self, sessions: SessionSlots) -> None:
        self.sessions = sessions
        self.user32 = ctypes.windll.user32 if sys.platform == "win32" else None
        self.shell32 = ctypes.windll.shell32 if sys.platform == "win32" else None
        if self.user32 is not None:
            self.user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
            self.user32.MapVirtualKeyW.restype = wintypes.UINT
            self.user32.keybd_event.argtypes = [
                wintypes.BYTE,
                wintypes.BYTE,
                wintypes.DWORD,
                ULONG_PTR,
            ]
            self.user32.keybd_event.restype = None
        if self.shell32 is not None:
            self.shell32.ShellExecuteW.argtypes = [
                wintypes.HWND,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                ctypes.c_int,
            ]
            self.shell32.ShellExecuteW.restype = wintypes.HINSTANCE

    def open_thread(self, session_id: str) -> None:
        url = f"codex://threads/{quote(session_id, safe='')}"
        if sys.platform == "win32":
            self.shell32.ShellExecuteW(None, "open", url, None, None, 1)
        else:
            print(f"Open {url}")

    def focus_active(self) -> None:
        if self.sessions.active_session:
            self.open_thread(self.sessions.active_session)
            time.sleep(0.30)

    def _key(self, vk: int, up: bool = False) -> None:
        # Supplying a real scan code matters for Electron's composer and
        # command palette. Accelerator-only shortcuts worked with scan=0,
        # but Enter/Tab/Escape were ignored by DOM keyboard handlers.
        scan = self.user32.MapVirtualKeyW(vk, 0)
        self.user32.keybd_event(vk, scan, 0x0002 if up else 0, 0)

    def shortcut(self, keys: list[str], focus: bool = True) -> None:
        if sys.platform != "win32":
            print("Shortcut:", "+".join(keys))
            return
        if focus:
            self.focus_active()
        codes = [self.VK[key.upper()] for key in keys]
        for code in codes:
            self._key(code)
        for code in reversed(codes):
            self._key(code, up=True)

    def type_text(self, text: str) -> None:
        if sys.platform != "win32":
            return
        for character in text:
            scan = self.user32.VkKeyScanW(ord(character))
            vk = scan & 0xFF
            modifiers = (scan >> 8) & 0xFF
            held: list[int] = []
            for flag, code in ((1, self.VK["SHIFT"]), (2, self.VK["CTRL"]), (4, self.VK["ALT"])):
                if modifiers & flag:
                    self._key(code)
                    held.append(code)
            self._key(vk)
            self._key(vk, up=True)
            for code in reversed(held):
                self._key(code, up=True)

    def palette(self, command: str) -> None:
        self.shortcut(["CTRL", "SHIFT", "P"])
        time.sleep(0.20)
        self.type_text(command)
        time.sleep(0.15)
        self.shortcut(["ENTER"], focus=False)

    def slash(self, command: str, focus: bool = False) -> None:
        if focus:
            self.focus_active()
        self.type_text(command)
        time.sleep(0.08)
        self.shortcut(["ENTER"], focus=False)

    def fork_new_chat(self) -> None:
        """Run /fork and choose the normal-chat option below new worktree."""
        self.slash("/fork", focus=True)
        time.sleep(0.45)
        self.shortcut(["DOWN"], focus=False)
        time.sleep(0.08)
        self.shortcut(["ENTER"], focus=False)


class Bridge:
    def __init__(self, actions_path: Path, dry_run: bool = False) -> None:
        self.actions = json.loads(actions_path.read_text(encoding="utf-8"))
        self.sessions = SessionSlots()
        self.controller = WindowsController(self.sessions)
        self.device: Any = None
        self.dry_run = dry_run
        self.udp_socket: socket.socket | None = None
        self.last_session_scan = 0.0
        self.session_files: dict[str, Path] = {}

    def log(self, message: str) -> None:
        if self.dry_run:
            return
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with LOG_PATH.open("a", encoding="utf-8") as stream:
                stream.write(f"{timestamp} {message}\n")
        except OSError:
            pass

    def refresh_recent_sessions(self) -> None:
        sessions_root = Path.home() / ".codex" / "sessions"
        try:
            candidates = sorted(
                sessions_root.rglob("*.jsonl"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return

        session_ids: list[str] = []
        for path in candidates:
            match = SESSION_ID_PATTERN.search(path.name)
            if match:
                session_id = match.group(1)
                session_ids.append(session_id)
                self.session_files[session_id] = path
            if len(session_ids) >= 12:
                break
        if self.sessions.populate_recent(session_ids):
            self.sync_leds()
            self.log("loaded recent task slots: " + ", ".join(
                item["session_id"] for item in self.sessions.slots if item
            ))

        for session_id, path in self.session_files.items():
            state = latest_transcript_state(path)
            if state is None:
                continue
            slot = self.sessions.reconcile_terminal(session_id, state)
            if slot is not None:
                self.send_report([MSG_SET_STATUS, slot, STATUS_IDS[state]])
                self.log(f"reconciled session={session_id} state={state} slot={slot + 1}")

    def connect(self) -> bool:
        if self.dry_run:
            return True
        try:
            import hid
        except ImportError:
            raise SystemExit("hidapi is missing; install host/requirements.txt")

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
        self.sync_leds()
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
        status = payload.get("status", "idle")
        if status not in STATUS_IDS:
            status = "idle"
        slot = self.sessions.update(
            str(payload.get("session_id", "unknown")),
            status,
            bool(payload.get("remove", False)),
        )
        if slot is not None:
            self.send_report([MSG_SET_STATUS, slot, STATUS_IDS["off" if payload.get("remove") else status]])
            self.log(f"status session={payload.get('session_id', 'unknown')} state={status} slot={slot + 1}")

    def process_lighting(self, payload: dict[str, Any]) -> None:
        if payload.get("clear") is True:
            self.send_report([MSG_CLEAR_PIXELS])
            return
        if "all" in payload:
            rgb = [max(0, min(255, int(value))) for value in payload["all"][:3]]
            if len(rgb) == 3:
                self.send_report([MSG_SET_ALL, *rgb])
            return
        if "pixel" in payload and "rgb" in payload:
            pixel = max(0, min(16, int(payload["pixel"])))
            rgb = [max(0, min(255, int(value))) for value in payload["rgb"][:3]]
            if len(rgb) == 3:
                self.send_report([MSG_SET_PIXEL, pixel, *rgb])

    def perform_action(self, action_id: int) -> None:
        name = ACTION_NAMES.get(action_id)
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
            if session_id:
                self.send_report([MSG_SET_SELECTED, slot])
                self.controller.open_thread(session_id)
                self.log(f"opened task slot={slot + 1} session={session_id}")
            else:
                self.log(f"task slot={slot + 1} is empty")
        elif kind == "shortcut":
            self.controller.shortcut(list(action["keys"]), focus=bool(action.get("focus", True)))
        elif kind == "palette":
            self.controller.palette(str(action["command"]))
        elif kind == "slash":
            self.controller.slash(str(action["command"]), focus=bool(action.get("focus", False)))
        elif kind == "fork_new_chat":
            self.controller.fork_new_chat()

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_HOST, UDP_PORT))
        sock.setblocking(False)
        self.udp_socket = sock
        last_connect_attempt = 0.0
        print(f"MF17 Codex bridge listening on udp://{UDP_HOST}:{UDP_PORT}")
        self.log("bridge started")
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

            try:
                while True:
                    data, _ = sock.recvfrom(4096)
                    payload = json.loads(data.decode("utf-8"))
                    if payload.get("kind") == "codex_status":
                        self.process_status(payload)
                    elif payload.get("kind") == "lighting":
                        self.process_lighting(payload)
            except BlockingIOError:
                pass
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"Ignored malformed status packet: {exc}", file=sys.stderr)

            if self.device is not None:
                try:
                    report = self.device.read(REPORT_SIZE)
                    if report and report[0] == MSG_ACTION:
                        self.perform_action(report[1])
                    elif report and report[0] == MSG_PONG:
                        self.log(f"received MF17 pong protocol={report[1] if len(report) > 1 else 'unknown'}")
                except OSError as exc:
                    print(f"MF17 disconnected: {exc}", file=sys.stderr)
                    self.log(f"MF17 disconnected: {exc!r}")
                    try:
                        self.device.close()
                    finally:
                        self.device = None

            time.sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="run without opening HID")
    parser.add_argument(
        "--actions",
        type=Path,
        default=Path(__file__).with_name("actions.json"),
    )
    args = parser.parse_args()
    while True:
        bridge = Bridge(args.actions, dry_run=args.dry_run)
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
