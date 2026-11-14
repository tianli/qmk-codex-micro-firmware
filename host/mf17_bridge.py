"""Raw HID bridge between an MF17 QMK keymap and Codex/Antigravity."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback
from typing import Any

# Modular subcomponents (with relative/top-level fallback imports)
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
    from host.status_common import (
        DEFAULT_STATUS_HOST,
        DEFAULT_STATUS_PORT,
        PERMISSION_REQUIRED_TOOLS,
        PERMISSION_TOOL_NAMES,
        PROVIDER_ANTIGRAVITY,
        PROVIDER_CODEX,
        STATUS_IDS,
        USER_PROMPT_PATTERNS,
        WAITING_NAME_MARKERS,
        WAITING_REASON_MARKERS,
        base_tool_name,
        conversation_uri,
        is_interactive_tool,
        is_permission_tool,
        looks_like_user_prompt,
        _looks_like_user_prompt,
        normalize_provider,
        normalize_task_id,
        scan_transcript_background_tasks,
    )
    from host.session_slots import (
        AGENT_SLOT_COUNT,
        NUMERIC_KEY_BY_SLOT,
        RECENT_RANK_TO_SLOT,
        RECONCILIATION_HOOK_COOLDOWN_SECS,
        STALE_THINKING_TIMEOUT_SECS,
        SessionSlots,
        is_pid_alive,
    )
    from host.os_automation import (
        OSController,
        POINT,
        RECT,
    )
    from host.transcript_scanner import (
        SESSION_FILE_PATTERN,
        TRANSCRIPT_TAIL_BYTES,
        UUID_PATTERN,
        active_codex_sessions,
        archived_codex_session_ids,
        latest_transcript_state,
        root_codex_session_id,
        visible_codex_session_ids,
    )
except ModuleNotFoundError:
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
    from status_common import (  # type: ignore[no-redef]
        DEFAULT_STATUS_HOST,
        DEFAULT_STATUS_PORT,
        PERMISSION_REQUIRED_TOOLS,
        PERMISSION_TOOL_NAMES,
        PROVIDER_ANTIGRAVITY,
        PROVIDER_CODEX,
        STATUS_IDS,
        USER_PROMPT_PATTERNS,
        WAITING_NAME_MARKERS,
        WAITING_REASON_MARKERS,
        base_tool_name,
        conversation_uri,
        is_interactive_tool,
        is_permission_tool,
        looks_like_user_prompt,
        _looks_like_user_prompt,
        normalize_provider,
        normalize_task_id,
        scan_transcript_background_tasks,
    )
    from session_slots import (  # type: ignore[no-redef]
        AGENT_SLOT_COUNT,
        NUMERIC_KEY_BY_SLOT,
        RECENT_RANK_TO_SLOT,
        RECONCILIATION_HOOK_COOLDOWN_SECS,
        STALE_THINKING_TIMEOUT_SECS,
        SessionSlots,
        is_pid_alive,
    )
    from os_automation import (  # type: ignore[no-redef]
        OSController,
        POINT,
        RECT,
    )
    from transcript_scanner import (  # type: ignore[no-redef]
        SESSION_FILE_PATTERN,
        TRANSCRIPT_TAIL_BYTES,
        UUID_PATTERN,
        active_codex_sessions,
        archived_codex_session_ids,
        latest_transcript_state,
        root_codex_session_id,
        visible_codex_session_ids,
    )

VID = 0x3A54
PID = 0x4F5D
USAGE_PAGE = 0xFF60
USAGE = 0x61
REPORT_SIZE = 32
UDP_HOST = DEFAULT_STATUS_HOST
UDP_PORT = DEFAULT_STATUS_PORT
LOG_PATH = Path(__file__).with_name("mf17_bridge.log")

MSG_ACTION = 0x10
MSG_ACTION_RELEASE = 0x11
MSG_SET_STATUS = 0x20
MSG_SET_SELECTED = 0x21
MSG_PING = 0x7E
MSG_PONG = 0x7F

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

# Default low-power ambient glow on the 7 outer control keys (using the
# firmware's base pixel layer so white press-and-fade feedback is preserved).
CONTROL_KEY_GLOW: tuple[tuple[int, tuple[int, int, int]], ...] = (
    (13, (0, 28, 10)),   # Prev Window (num_lock): dim emerald
    (14, (0, 28, 10)),   # Scroll Up (divide): dim emerald
    (15, (0, 28, 10)),   # Scroll Down (multiply): dim emerald
    (16, (32, 0, 0)),    # Reject (minus): dim red
    (9,  (0, 28, 10)),   # New Chat (plus): dim emerald
    (1,  (0, 28, 10)),   # Dictate (decimal): dim emerald
    (2,  (0, 28, 10)),   # Approve / Send (enter): dim emerald
)


class Bridge:
    def __init__(
        self,
        actions_path: Path,
        dry_run: bool = False,
        provider: str | None = None,
        active_only: bool = True,
    ) -> None:
        self.actions = json.loads(actions_path.read_text(encoding="utf-8"))
        self.sessions = SessionSlots()
        self.controller = OSController(self.sessions)
        self.device: Any = None
        self.dry_run = dry_run
        self.active_only = active_only
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
        self.completed_override_keys: set[str] = set()

    def log(self, message: str) -> None:
        if self.dry_run:
            return
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with LOG_PATH.open("a", encoding="utf-8") as stream:
                stream.write(f"{timestamp} {message}\n")
        except OSError:
            pass

    def find_transcript_for_session(self, session_id: str) -> Path | None:
        jetski_brain = Path.home() / ".gemini" / "jetski" / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"
        if jetski_brain.exists():
            return jetski_brain
        codex_path = Path.home() / ".codex" / "sessions"
        if codex_path.exists():
            for p in codex_path.rglob(f"*{session_id}*.jsonl"):
                if p.exists():
                    return p
        return None

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
                def _transcript_mtime(p: Path) -> float:
                    t = p / ".system_generated" / "logs" / "transcript.jsonl"
                    try:
                        return t.stat().st_mtime if t.exists() and t.stat().st_size > 0 else 0.0
                    except OSError:
                        return 0.0

                valid_dirs = [p for p in jetski_brain.iterdir() if p.is_dir() and UUID_PATTERN.match(p.name)]
                subdirs = sorted(
                    valid_dirs,
                    key=_transcript_mtime,
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
        # Check if any active local session's parent process (Jetski window / CLI) has terminated
        for slot, item in enumerate(list(self.sessions.slots)):
            if item and item.get("status") not in {"off"} and not item.get("is_remote", False):
                pid = item.get("pid")
                if pid and not is_pid_alive(pid):
                    sid = item["session_id"]
                    self.sessions.update(sid, "off", remove=True)
                    self.send_report([MSG_SET_STATUS, slot, STATUS_IDS["off"]])
                    self.update_slot_lighting(slot, "off")
                    self.log(f"session {sid} window (PID {pid}) closed -> turned off slot {slot}")

        if self.active_only:
            if self.sessions.populate_recent([], active_only=True):
                self.sync_leds()

            for item in self.sessions.slots:
                if not item:
                    continue
                sid = item["session_id"]
                if sid not in self.session_files:
                    path = self.find_transcript_for_session(sid)
                    if path:
                        self.session_files[sid] = path
                        self.session_providers[sid] = item.get("provider", PROVIDER_ANTIGRAVITY)

            for session_id, path in list(self.session_files.items()):
                state = latest_transcript_state(path)
                if state is None:
                    continue
                slot = self.sessions.reconcile_terminal(session_id, state)
                if slot is not None:
                    self.send_report([MSG_SET_STATUS, slot, STATUS_IDS[state]])
                    self.update_slot_lighting(slot, state)
                    self.log(
                        f"reconciled session={session_id} state={state} "
                        f"key={NUMERIC_KEY_BY_SLOT[slot]}"
                    )
            return

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

        if self.sessions.populate_recent(session_ids, providers=providers, active_only=False):
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
                self.update_slot_lighting(slot, state)
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
        self.send_report([MSG_CLEAR_ALL_KEY_RGB, 0])
        self.apply_control_key_glow()
        self.sync_leds()
        self.send_report([MSG_GET_CAPABILITIES, 0])
        self.send_report([MSG_PING])
        return True

    def apply_control_key_glow(self) -> None:
        """Set the default low-power ambient glow on the 7 outer control keys."""
        for pixel, (r, g, b) in CONTROL_KEY_GLOW:
            self.send_report([MSG_SET_PIXEL, pixel, r, g, b])

    def send_report(self, values: list[int]) -> None:
        report = bytes((values + [0] * REPORT_SIZE)[:REPORT_SIZE])
        if self.dry_run:
            print("HID OUT", list(report[:5]))
        elif self.device:
            self.device.write(bytes([0]) + report)

    def update_slot_lighting(self, slot: int, status: str) -> None:
        key_name = str(NUMERIC_KEY_BY_SLOT[slot])
        if status == "complete":
            self.completed_override_keys.add(key_name)
            try:
                for r in self.lighting.encode({"key": key_name, "rgb": [0, 255, 0]}):
                    self.send_report(r)
            except Exception:
                pass
        elif status == "executing":
            self.completed_override_keys.add(key_name)
            try:
                for r in self.lighting.encode({"key": key_name, "rgb": [180, 0, 255]}):
                    self.send_report(r)
            except Exception:
                pass
        elif key_name in self.completed_override_keys:
            self.completed_override_keys.remove(key_name)
            try:
                for r in self.lighting.encode({"clear_key": key_name}):
                    self.send_report(r)
            except Exception:
                pass

    def sync_leds(self) -> None:
        for slot, item in enumerate(self.sessions.slots):
            state = item["status"] if item else "off"
            self.send_report([MSG_SET_STATUS, slot, STATUS_IDS[state]])
            self.update_slot_lighting(slot, state)

    def process_status(self, payload: dict[str, Any], source: str = "udp") -> None:
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
        pid = payload.get("pid")
        is_remote = bool(payload.get("is_remote", False)) or source == "tcp"
        if not is_remote and isinstance(pid, (int, str)) and str(pid).isdigit():
            # Standard macOS PID_MAX is 99999. PIDs > 100000 are Linux / Cloudtop processes
            if int(pid) > 100000:
                is_remote = True
        workspace = str(payload.get("workspace") or "")
        client_app = payload.get("client_app")
        terminal_app = payload.get("terminal_app")
        iterm_session_id = payload.get("iterm_session_id")
        tty = payload.get("tty")
        slot = self.sessions.update(
            session_id,
            status,
            bool(payload.get("remove", False)),
            from_hook=True,
            provider=provider,
            pid=int(pid) if isinstance(pid, (int, str)) and str(pid).isdigit() else None,
            is_remote=is_remote,
            workspace=workspace,
            client_app=str(client_app) if client_app else None,
            terminal_app=str(terminal_app) if terminal_app else None,
            iterm_session_id=str(iterm_session_id) if iterm_session_id else None,
            tty=str(tty) if tty else None,
        )
        if slot is not None:
            eff_status = "off" if payload.get("remove") else status
            self.send_report([MSG_SET_STATUS, slot, STATUS_IDS[eff_status]])
            self.update_slot_lighting(slot, eff_status)
            ev = payload.get("event") or ""
            ev_str = f" event={ev}" if ev else ""
            ws_str = f" workspace={workspace}" if workspace else ""
            client_str = f" client={client_app}" if client_app else ""
            self.log(
                f"status provider={provider} session={session_id} state={status}{ev_str}{ws_str}{client_str} "
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
            hold_capable = len(report) >= 3 and report[2] == 1
            self.perform_action(report[1], pressed=True, hold_capable=hold_capable)
        elif report[0] == MSG_ACTION_RELEASE and len(report) >= 2:
            self.perform_action(report[1], pressed=False, hold_capable=True)
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

    def perform_action(self, action_id: int, pressed: bool = True, hold_capable: bool = False) -> None:
        action_names = LEGACY_ACTION_NAMES if self.action_protocol_version == 1 else ACTION_NAMES
        name = action_names.get(action_id)
        action = self.actions.get(name) if name else None
        if not action:
            print(f"Unmapped action {action_id}", file=sys.stderr)
            self.log(f"action id={action_id} unmapped")
            return

        kind = action.get("type")
        if not pressed:
            if kind == "microphone":
                self.log(f"action release id={action_id} name={name} type={kind}")
                self.controller.microphone(
                    keys=list(action.get("keys", [])) or None,
                    terminal_keys=list(action.get("terminal_keys", [])) or None,
                    mode="stop",
                )
            return

        self.log(f"action id={action_id} name={name} type={kind}")
        if kind == "agent":
            slot = int(action["slot"])
            session_id = self.sessions.session_for_slot(slot)
            provider = self.sessions.provider_for_slot(slot)
            self.send_report([MSG_SET_SELECTED, slot])
            if session_id:
                self.controller.open_thread(session_id, slot=slot, provider=provider)
                self.log(
                    f"selected task key={NUMERIC_KEY_BY_SLOT[slot]} "
                    f"provider={provider} session={session_id}"
                )
            else:
                self.log(f"task key={NUMERIC_KEY_BY_SLOT[slot]} is empty")
        elif kind in {"previous_window", "switch_back"}:
            self.controller.restore_previous_window()
            self.log("switched back to previous window")
        elif kind == "shortcut":
            self.controller.shortcut(list(action["keys"]), focus=bool(action.get("focus", False)))
            if action.get("switch_back") or action.get("restore_previous_window"):
                time.sleep(0.05)
                self.controller.restore_previous_window()
                self.log("switched back to previous window after shortcut")
        elif kind == "context_scroll":
            self.controller.context_scroll(str(action.get("direction", "up")))
        elif kind == "new_chat":
            self.controller.new_chat()
        elif kind == "microphone":
            if hold_capable:
                self.controller.microphone(
                    keys=list(action.get("keys", [])) or None,
                    terminal_keys=list(action.get("terminal_keys", [])) or None,
                    mode="start",
                )
            else:
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
                                        self.process_status(payload, source="tcp")
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
        "--active-only",
        action="store_true",
        default=True,
        help="only allocate slots for actively open/running sessions (default: True)",
    )
    parser.add_argument(
        "--include-history",
        dest="active_only",
        action="store_false",
        help="pre-populate slots with the 10 most recent past sessions from disk",
    )
    parser.add_argument(
        "--actions",
        type=Path,
        default=Path(__file__).with_name("actions.json"),
    )
    args = parser.parse_args()
    while True:
        bridge = Bridge(
            args.actions,
            dry_run=args.dry_run,
            provider=args.provider,
            active_only=args.active_only,
        )
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
