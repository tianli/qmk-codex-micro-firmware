"""Translate Antigravity/Jetski lifecycle hook JSON into a local status event."""

from __future__ import annotations

import argparse
import json
import re
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
    "requestuserinput",
    "user_input",
    "userinput",
    "elicitation",
    "confirm",
    "confirmation",
}

WAITING_NAME_MARKERS = (
    "ask",
    "question",
    "permission",
    "approval",
    "confirm",
    "elicitation",
    "user_input",
    "userinput",
    "awaiting_input",
    "needs_input",
)
WAITING_REASON_MARKERS = (
    "awaiting",
    "waiting",
    "user_input",
    "user input",
    "needs_input",
    "needs input",
    "question",
    "permission",
    "approval",
    "elicitation",
)

# Codex's normal conversational question is delivered as a Stop hook with
# ``last_assistant_message`` rather than PermissionRequest. Keep this list
# deliberately narrow: a question at the end of the assistant's message or an
# explicit request for a choice/input means the task is waiting for the user.
USER_PROMPT_PATTERNS = (
    r"\b(?:please|could you|can you|would you|will you|which|what|where|when|how|do you|should i|shall i|choose|select|confirm|tell me|let me know)\b",
    r"\b(?:i need|waiting for|waiting on|provide|send me|reply with|respond with)\b.{0,80}\b(?:input|answer|choice|selection|details|response)\b",
)


def _event_name(event: dict[str, Any], explicit_event: str | None = None) -> str:
    value = (
        explicit_event
        or event.get("hook_event_name")
        or event.get("eventName")
        or event.get("event_name")
        or event.get("event")
        or ""
    )
    return str(value).strip().replace("-", "_").lower()


def _tool_name(event: dict[str, Any]) -> str:
    """Extract a tool name across Codex and Antigravity hook schemas."""
    candidates: list[Any] = [
        event.get("tool_name"),
        event.get("toolName"),
        event.get("function_name"),
        event.get("functionName"),
        event.get("tool"),
        event.get("name"),
    ]
    for key in ("toolCall", "tool_call", "toolRequest", "tool_request", "function"):
        nested = event.get(key)
        if isinstance(nested, dict):
            candidates.extend(
                nested.get(field)
                for field in ("name", "tool_name", "toolName", "function_name", "functionName")
            )
        elif isinstance(nested, str):
            candidates.append(nested)

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return ""


def _base_tool_name(tool_name: str) -> str:
    # Handle names such as functions__request_user_input,
    # functions.request_user_input, and mcp/request_user_input.
    parts = [part for part in re.split(r"[^a-z0-9_]+", tool_name.lower()) if part]
    name = parts[-1] if parts else ""
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    return name


def _is_interactive_tool(tool_name: str) -> bool:
    base_tool = _base_tool_name(tool_name)
    compact = base_tool.replace("_", "")
    return (
        base_tool in PERMISSION_TOOL_NAMES
        or compact in PERMISSION_TOOL_NAMES
        or any(marker in base_tool for marker in WAITING_NAME_MARKERS)
    )


def _contains_waiting_signal(value: Any) -> bool:
    if isinstance(value, dict):
        explicit_keys = (
            "awaiting_user_input",
            "awaiting_input",
            "needs_user_input",
            "needs_input",
            "waiting_for_user",
            "requires_user_input",
        )
        for key in explicit_keys:
            if value.get(key) is True:
                return True
        # Only inspect human-readable status/reason fields. Scanning every
        # message string would misclassify a normal completed turn whose text
        # happens to contain words like “question” or “user input”.
        for key in ("status", "state", "reason", "terminationReason", "termination_reason", "stop_reason", "stopReason"):
            candidate = value.get(key)
            if isinstance(candidate, str) and _contains_waiting_signal(candidate):
                return True
        return any(_contains_waiting_signal(item) for item in value.values() if isinstance(item, (dict, list)))
    if isinstance(value, list):
        return any(_contains_waiting_signal(item) for item in value)
    if isinstance(value, str):
        normalized = value.lower().replace("-", "_")
        return any(marker in normalized for marker in WAITING_REASON_MARKERS)
    return False


def _looks_like_user_prompt(message: Any) -> bool:
    """Return true for a final assistant message that asks the user to respond."""
    if not isinstance(message, str):
        return False
    text = " ".join(message.strip().split())
    if not text:
        return False

    # A final question mark is the strongest signal. Allow common closing
    # quotes/brackets and markdown punctuation after it.
    if re.search(r"\?\s*[\"'`\)\]]*\s*$", text):
        return True

    # Some prompts are phrased as imperatives without a question mark. Only
    # inspect the final sentence to avoid matching quoted/code content or a
    # harmless “when/what” in the middle of a completed explanation.
    tail = text[-600:]
    final_sentence = re.split(r"(?<=[.!?])\s+", tail)[-1]
    return any(
        re.search(pattern, final_sentence, flags=re.IGNORECASE)
        for pattern in USER_PROMPT_PATTERNS
    )


def _termination_reason(event: dict[str, Any]) -> str:
    for key in ("terminationReason", "termination_reason", "stop_reason", "stopReason", "reason"):
        value = event.get(key)
        if value is not None:
            return str(value).strip().lower().replace("-", "_")
    return ""


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
    name = _event_name(event, explicit_event)
    has_error = bool(event.get("error")) or _contains_error(event.get("tool_response"))

    # SessionEnd -> off
    if name in {"sessionend", "session_end"}:
        return "off", True

    # Stop -> complete (or error if error occurred)
    if name in {"stop", "subagentstop", "subagent_stop"}:
        if has_error:
            return "error", False
        reason = _termination_reason(event)
        if "error" in reason:
            return "error", False
        if _contains_waiting_signal(event) or any(marker in reason for marker in WAITING_REASON_MARKERS):
            return "waiting", False
        if _looks_like_user_prompt(event.get("last_assistant_message")):
            return "waiting", False
        return "complete", False

    # PermissionRequest
    if name in {"permissionrequest", "permission_request"}:
        return "waiting", False

    # PreToolUse: Agent is about to execute a tool (or prompt the user for permission)
    if name in {"pretooluse", "pre_tool_use"}:
        tool_name = _tool_name(event)
        
        # If the tool is a permission/interactive tool or contains matching keywords -> amber waiting
        if (
            _is_interactive_tool(tool_name)
            or "command" in _base_tool_name(tool_name)
            or "write" in _base_tool_name(tool_name)
            or "replace" in _base_tool_name(tool_name)
        ):
            return "waiting", False
        return "thinking", False

    # PostToolUse: Tool completed execution
    if name in {"posttooluse", "post_tool_use"}:
        if has_error:
            return "error", False
        if _contains_waiting_signal(event.get("tool_response")):
            return "waiting", False
        return "thinking", False

    # PreInvocation: Model is generating / thinking
    if name in {"preinvocation", "pre_invocation", "userpromptsubmit", "user_prompt_submit", "subagentstart", "subagent_start"}:
        return "thinking", False

    if name in {"postinvocation", "post_invocation"}:
        return "thinking", False

    # Fallback inspection if event name wasn't explicitly supplied
    if any(key in event for key in ("toolCall", "tool_call", "tool_name", "toolName", "tool")):
        tool_name = _tool_name(event)
        if (
            _is_interactive_tool(tool_name)
            or "command" in _base_tool_name(tool_name)
            or "write" in _base_tool_name(tool_name)
            or "replace" in _base_tool_name(tool_name)
        ):
            return "waiting", False
        return "thinking", False

    if any(key in event for key in ("terminationReason", "termination_reason", "stop_reason", "stopReason")):
        reason = _termination_reason(event)
        if has_error or "error" in reason:
            return "error", False
        if _contains_waiting_signal(event) or any(marker in reason for marker in WAITING_REASON_MARKERS):
            return "waiting", False
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
        # 1. Fast local UDP broadcast
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.2)
                sock.sendto(encoded, (HOST, PORT))
        except OSError:
            pass

        # 2. TCP socket fallback for SSH RemoteForward tunneling (e.g. Cloudtop)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.25)
                sock.connect((HOST, PORT))
                sock.sendall(encoded + b"\n")
        except OSError:
            pass
    except Exception:
        pass

    # For Jetski hooks:
    # Always return {} so Jetski keeps full ownership of native approval popups & prompts
    print("{\"decision\": \"allow\"}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
