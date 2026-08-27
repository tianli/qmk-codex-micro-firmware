"""Translate Codex hook JSON on stdin into a non-blocking local status event."""

from __future__ import annotations

import json
import socket
import sys
from typing import Any


HOST = "127.0.0.1"
PORT = 41717


def _contains_error(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("isError") is True:
            return True
        exit_code = value.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return True
        return any(_contains_error(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_error(item) for item in value)
    return False


def event_to_status(event: dict[str, Any]) -> tuple[str, bool]:
    name = event.get("hook_event_name", "")
    if name == "SessionEnd":
        return "off", True
    if name == "PermissionRequest":
        # Status-only: return immediately so Codex still owns the native prompt.
        return "waiting", False
    if name == "PreToolUse":
        tool_name = str(event.get("tool_name", "")).lower()
        if tool_name.rsplit("__", 1)[-1] == "request_user_input":
            return "waiting", False
    if name == "PostToolUse" and _contains_error(event.get("tool_response")):
        return "error", False
    if name in {"UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart"}:
        return "thinking", False
    if name in {"Stop", "SubagentStop"}:
        return "complete", False
    return "idle", False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        payload = {}

    status, remove = event_to_status(payload)
    message = {
        "kind": "codex_status",
        "session_id": str(payload.get("session_id", "unknown")),
        "status": status,
        "remove": remove,
        "cwd": str(payload.get("cwd", "")),
    }

    try:
        encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(encoded, (HOST, PORT))
    except OSError:
        pass

    # Empty JSON is valid for every configured hook, including Stop.
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
