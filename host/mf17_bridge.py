"""Raw HID bridge between an MF17 QMK keymap and Jetski (with 10-agent support)."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import traceback
from typing import Any
from urllib.parse import quote


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

    for raw_line in reversed(lines):
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        step_type = record.get("type", "")
        status = record.get("status", "")

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

    def update(self, session_id: str, status: str, remove: bool = False, from_hook: bool = True) -> int | None:
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

        self.slots[existing] = {
            "session_id": session_id,
            "status": status,
            "updated": now,
            "last_hook_time": now if from_hook else self.slots[existing].get("last_hook_time", 0) if self.slots[existing] else 0,
        }
        self.active_session = session_id
        return existing

    def session_for_slot(self, slot: int) -> str | None:
        item = self.slots[slot] if 0 <= slot < len(self.slots) else None
        return item["session_id"] if item else None

    def reconcile_terminal(self, session_id: str, status: str) -> int | None:
        """Resolve a stale thinking state ONLY after STALE_THINKING_TIMEOUT_SECS without hook updates."""
        existing = next(
            (index for index, item in enumerate(self.slots) if item and item["session_id"] == session_id),
            None,
        )
        if existing is None:
            return None

        current_status = self.slots[existing]["status"]
        if current_status != "thinking":
            return None

        now = time.time()
        last_hook = self.slots[existing].get("last_hook_time", 0)
        if (now - last_hook) < STALE_THINKING_TIMEOUT_SECS:
            return None

        self.slots[existing]["status"] = status
        self.slots[existing]["updated"] = now
        return existing

    def populate_recent(self, session_ids: list[str]) -> bool:
        """Fill unused slots from recent task files without disturbing live status."""
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
                "last_hook_time": 0,
            }
            known.add(session_id)
            changed = True
        if self.active_session is None:
            first = next((item for item in self.slots if item), None)
            if first:
                self.active_session = first["session_id"]
        return changed


class OSController:
    """Cross-platform controller for macOS, Windows, and Linux."""

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
        self.is_mac = sys.platform == "darwin"
        self.is_win = sys.platform == "win32"
        self.is_linux = sys.platform.startswith("linux")

        if self.is_win:
            self._init_windows()
        else:
            self.user32 = None
            self.shell32 = None

    def _init_windows(self) -> None:
        from ctypes import wintypes
        ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        self.user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self.user32.MapVirtualKeyW.restype = wintypes.UINT
        self.user32.keybd_event.argtypes = [
            wintypes.BYTE,
            wintypes.BYTE,
            wintypes.DWORD,
            ULONG_PTR,
        ]
        self.user32.keybd_event.restype = None
        self.shell32.ShellExecuteW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        self.shell32.ShellExecuteW.restype = wintypes.HINSTANCE

    def open_thread(self, session_id: str | None, slot: int = 0) -> None:
        """Activate Jetski and focus/switch to the selected conversation."""
        if self.is_mac:
            try:
                subprocess.run(["open", "-b", "com.google.jetski"], capture_output=True, timeout=1.5)
            except Exception:
                pass

            if session_id:
                try:
                    subprocess.run(["open", "-u", f"jetski-ide://conversation/{session_id}"], capture_output=True, timeout=1.5)
                except Exception:
                    pass
        elif self.is_win:
            if self.shell32 and session_id:
                url = f"jetski-ide://conversation/{quote(session_id, safe='')}"
                self.shell32.ShellExecuteW(None, "open", url, None, None, 1)
        elif self.is_linux:
            if session_id:
                try:
                    subprocess.run(["xdg-open", f"jetski-ide://conversation/{session_id}"], capture_output=True, timeout=1.5)
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
                    self.user32.keybd_event(code, scan, 0, 0)
            for code in reversed(codes):
                if self.user32:
                    scan = self.user32.MapVirtualKeyW(code, 0)
                    self.user32.keybd_event(code, scan, 0x0002, 0)

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
    def __init__(self, actions_path: Path, dry_run: bool = False) -> None:
        self.actions = json.loads(actions_path.read_text(encoding="utf-8"))
        self.sessions = SessionSlots()
        self.controller = OSController(self.sessions)
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

    def find_recent_sessions(self) -> list[tuple[str, Path]]:
        results: list[tuple[str, Path]] = []
        found_ids: set[str] = set()

        app_data_dir = os.environ.get("JETSKI_APP_DATA_DIR")
        jetski_brain = Path(app_data_dir) / "brain" if app_data_dir else Path.home() / ".gemini" / "jetski" / "brain"
        
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
                        results.append((session_id, transcript))
                        found_ids.add(session_id)
            except OSError:
                pass

        codex_sessions = Path.home() / ".codex" / "sessions"
        if codex_sessions.exists() and codex_sessions.is_dir():
            try:
                candidates = sorted(
                    codex_sessions.rglob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for path in candidates:
                    match = SESSION_FILE_PATTERN.search(path.name)
                    if match:
                        session_id = match.group(1)
                        if session_id not in found_ids:
                            results.append((session_id, path))
                            found_ids.add(session_id)
                    if len(results) >= AGENT_SLOT_COUNT:
                        break
            except OSError:
                pass

        return results

    def refresh_recent_sessions(self) -> None:
        recent = self.find_recent_sessions()
        session_ids: list[str] = []
        for session_id, path in recent:
            session_ids.append(session_id)
            self.session_files[session_id] = path

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
        )
        if slot is not None:
            self.send_report([MSG_SET_STATUS, slot, STATUS_IDS["off" if payload.get("remove") else status]])
            self.log(f"status session={session_id} state={status} slot={slot + 1}")

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
            self.send_report([MSG_SET_SELECTED, slot])
            self.controller.open_thread(session_id, slot=slot)
            if session_id:
                self.log(f"selected task slot={slot + 1} session={session_id}")
            else:
                self.log(f"task slot={slot + 1} is empty")
        elif kind == "shortcut":
            self.controller.shortcut(list(action["keys"]), focus=bool(action.get("focus", False)))
        elif kind == "palette":
            self.controller.palette(str(action["command"]))
        elif kind == "slash":
            self.controller.slash(str(action["command"]), focus=bool(action.get("focus", False)))

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_HOST, UDP_PORT))
        sock.setblocking(False)
        self.udp_socket = sock
        last_connect_attempt = 0.0
        print(f"MF17 Jetski bridge listening on udp://{UDP_HOST}:{UDP_PORT} (10 Agent slots)")
        self.log("bridge started (10 Agent slots)")
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
                    kind = payload.get("kind")
                    if kind in {"jetski_status", "codex_status"}:
                        self.process_status(payload)
                    elif kind == "lighting":
                        self.process_lighting(payload)
            except BlockingIOError:
                pass
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"Ignored malformed packet: {exc}", file=sys.stderr)

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
    parser = argparse.ArgumentParser(description="MF17 Jetski Keypad Bridge")
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
