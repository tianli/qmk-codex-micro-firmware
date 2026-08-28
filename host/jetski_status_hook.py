"""Translate Antigravity/Jetski lifecycle hook JSON into a local status event."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any


HOST = "127.0.0.1"
PORT = 41717

# Tools that typically prompt the user for permission or input in Jetski
PERMISSION_TOOL_NAMES = {
    "run_command",
    "write_to_file",
    "replace_file_content",
    "notebook_edit",
    "ask_question",
    "ask_custom_permission",
    "ask_user",
    "request_user_input",
    "functions__request_user_input",
    "generate_image",
    "manage_task",
    "manage_subagents",
}


def _contains_error(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("isError") is True:
            return True
        exit_code = value.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return True
        if value.get("error"):
            return True
        return any(_contains_error(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_error(item) for item in value)
    if isinstance(value, str) and value.lower().startswith("error"):
        return True
    return False


def event_to_status(event: dict[str, Any], explicit_event: str | None = None) -> tuple[str, bool]:
    """Map hook event payload to (status, remove_slot).
    
    Status can be: 'off', 'idle', 'thinking', 'complete', 'waiting', 'error'.
    """
    name = explicit_event or event.get("hook_event_name") or event.get("eventName") or ""
    has_error = bool(event.get("error")) or _contains_error(event.get("tool_response"))

    # SessionEnd -> off
    if name in {"SessionEnd", "session_end"}:
        return "off", True

    # Stop -> complete (or error if error occurred)
    if name in {"Stop", "SubagentStop", "stop"}:
        if has_error:
            return "error", False
        reason = str(event.get("terminationReason", "")).lower()
        if "error" in reason:
            return "error", False
        return "complete", False

    # PermissionRequest
    if name in {"PermissionRequest", "permission_request"}:
        return "waiting", False

    # PreToolUse: Agent is about to execute a tool (or prompt the user for permission)
    if name in {"PreToolUse", "pre_tool_use"}:
        tool_call = event.get("toolCall") or {}
        tool_name = str(tool_call.get("name") or event.get("tool_name", "")).lower()
        base_tool = tool_name.rsplit("__", 1)[-1]
        
        # If the tool is a permission/interactive tool or contains matching keywords -> amber waiting
        if (
            base_tool in PERMISSION_TOOL_NAMES
            or "ask" in base_tool
            or "permission" in base_tool
            or "command" in base_tool
            or "write" in base_tool
            or "replace" in base_tool
        ):
            return "waiting", False
        return "thinking", False

    # PostToolUse: Tool completed execution
    if name in {"PostToolUse", "post_tool_use"}:
        if has_error:
            return "error", False
        return "thinking", False

    # PreInvocation: Model is generating / thinking
    if name in {"PreInvocation", "pre_invocation", "UserPromptSubmit", "SubagentStart"}:
        return "thinking", False

    if name in {"PostInvocation", "post_invocation"}:
        return "thinking", False

    # Fallback inspection if event name wasn't explicitly supplied
    if "toolCall" in event:
        tool_name = str(event["toolCall"].get("name", "")).lower()
        base_tool = tool_name.rsplit("__", 1)[-1]
        if (
            base_tool in PERMISSION_TOOL_NAMES
            or "ask" in base_tool
            or "permission" in base_tool
            or "command" in base_tool
            or "write" in base_tool
            or "replace" in base_tool
        ):
            return "waiting", False
        return "thinking", False

    if "terminationReason" in event:
        if has_error or "error" in str(event.get("terminationReason", "")).lower():
            return "error", False
        return "complete", False

    if has_error:
        return "error", False

    return "idle", False


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity/Jetski MF17 lifecycle status hook")
    parser.add_argument("--event", type=str, default=None, help="Explicit event name (e.g. PreInvocation, PreToolUse, Stop)")
    args, _ = parser.parse_known_args()

    payload: dict[str, Any] = {}
    if not sys.stdin.isatty():
        try:
            raw_input = sys.stdin.read()
            if raw_input.strip():
                payload = json.loads(raw_input)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            payload = {}

    status, remove = event_to_status(payload, explicit_event=args.event)
    
    session_id = str(
        payload.get("conversationId")
        or payload.get("conversation_id")
        or payload.get("session_id")
        or "unknown"
    )
    transcript_path = str(payload.get("transcriptPath") or payload.get("transcript_path") or "")

    message = {
        "kind": "jetski_status",
        "provider": "antigravity",
        "session_id": session_id,
        "status": status,
        "remove": remove,
        "transcript_path": transcript_path,
        "event": args.event or payload.get("hook_event_name") or "",
    }

    try:
        encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.sendto(encoded, (HOST, PORT))
    except OSError:
        pass

    # For Jetski hooks:
    # Always return {} so Jetski keeps full ownership of native approval popups & prompts
    print("{\"decision\": \"allow\"}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
