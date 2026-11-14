"""Shared heuristics, constants, and utilities for agent lifecycle status."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

# Network defaults for local bridge status messages
DEFAULT_STATUS_HOST = "127.0.0.1"
DEFAULT_STATUS_PORT = 41717

# Provider identifiers
PROVIDER_CODEX = "codex"
PROVIDER_ANTIGRAVITY = "antigravity"

# Firmware status values mapped to numeric IDs
STATUS_IDS = {
    "off": 0,
    "idle": 1,
    "thinking": 2,
    "complete": 3,
    "waiting": 4,
    "error": 5,
    "executing": 2,
}

# Tools that prompt the user for interactive input, questions, or explicit permission
PERMISSION_TOOL_NAMES = {
    "ask_question",
    "ask_custom_permission",
    "ask_user",
    "request_user_input",
    "functions__request_user_input",
    "requestuserinput",
    "user_input",
    "userinput",
    "elicitation",
    "confirm",
    "confirmation",
    "permission",
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

# Tools requiring user authorization in standard permission models
PERMISSION_REQUIRED_TOOLS = {
    "run_command",
    "edit_file",
    "write_file",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "create_file",
    "delete_file",
    "apply_patch",
    "notebook_edit",
    "ask_custom_permission",
}

# Narrow patterns for detecting when the assistant explicitly asks the user to choose or respond
USER_PROMPT_PATTERNS = (
    r"\b(?:should\s+i|would\s+you\s+like|do\s+you\s+want|shall\s+i)\b",
    r"\b(?:which\s+(?:one|option|approach|path|file|directory)\s+would\s+you)\b",
    r"\b(?:please\s+(?:let\s+me\s+know|confirm|choose|specify|clarify|reply|provide|select))\b",
    r"\b(?:let\s+me\s+know\s+(?:which|what|if\s+you\s+would\s+like|how\s+you\s+would\s+like))\b",
    r"\b(?:i\s+need|waiting\s+for|waiting\s+on)\b.{0,60}\b(?:input|answer|choice|selection|details|response)\b",
)


def looks_like_user_prompt(message: Any) -> bool:
    """Return true for a final assistant message that asks the user to respond."""
    if not isinstance(message, str):
        return False

    # Strip code blocks, inline code, and markdown links so that regex/code symbols like '?' don't trigger false positives
    clean_text = re.sub(r"```[\s\S]*?```", " ", message)
    clean_text = re.sub(r"`[^`\n]*?`", " ", clean_text)
    clean_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean_text)
    text = " ".join(clean_text.strip().split())
    if not text:
        return False

    # 1. Does the message conclude with a question mark?
    if text.rstrip(" *_\t\n\r.)").endswith("?"):
        return True

    # 2. Check for explicit prompt/instruction phrases in the final block
    tail = text[-500:]
    return any(
        re.search(pattern, tail, flags=re.IGNORECASE)
        for pattern in USER_PROMPT_PATTERNS
    )


# Backward-compatibility alias
_looks_like_user_prompt = looks_like_user_prompt


def normalize_task_id(tid: str) -> str:
    """Normalize background task ID from transcript or event payload."""
    return tid.strip("\"'").split("/")[-1]


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


def base_tool_name(tool_name: str) -> str:
    """Extract unqualified tool/function name from namespaces or MCP prefixes."""
    parts = [part for part in re.split(r"[^a-z0-9_]+", tool_name.lower()) if part]
    name = parts[-1] if parts else ""
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    return name


def is_interactive_tool(tool_name: str) -> bool:
    """Return true if tool prompts for user decision or interactive confirmation."""
    base = base_tool_name(tool_name)
    compact = base.replace("_", "")
    return (
        base in PERMISSION_TOOL_NAMES
        or compact in PERMISSION_TOOL_NAMES
        or any(marker in base for marker in WAITING_NAME_MARKERS)
    )


def is_permission_tool(tool_name: str) -> bool:
    """Return true if tool execution requires interactive or authorization approval."""
    base = base_tool_name(tool_name)
    compact = base.replace("_", "")
    return (
        base in PERMISSION_REQUIRED_TOOLS
        or compact in PERMISSION_REQUIRED_TOOLS
        or is_interactive_tool(tool_name)
    )


def scan_transcript_background_tasks(lines: list[bytes | str]) -> tuple[set[str], set[str]]:
    """Scan transcript lines for completed and active background tasks."""
    completed_tasks: set[str] = set()
    running_tasks: set[str] = set()

    for raw_line in reversed(lines):
        if isinstance(raw_line, bytes):
            try:
                line_str = raw_line.decode("utf-8", errors="replace")
            except Exception:
                continue
        else:
            line_str = raw_line

        try:
            record = json.loads(line_str)
        except (json.JSONDecodeError, TypeError):
            continue

        content = str(record.get("content") or "")
        for m in re.finditer(
            r"Task(?:\s+id)?\s+[\"']?([\w\-/]+)[\"']?\s+(?:was\s+)?(?:finished|completed|failed|killed|timed\s*out|terminated|canceled|cancelled)",
            content,
            re.IGNORECASE,
        ):
            completed_tasks.add(normalize_task_id(m.group(1)))

        if record.get("status") == "RUNNING" and record.get("type") in {"GENERIC", "TOOL_USE", "TOOL_CALL"}:
            m = re.search(r"task id:\s*[\"']?([\w\-/]+)", content, re.IGNORECASE)
            if m:
                task_id = normalize_task_id(m.group(1))
                if task_id not in completed_tasks:
                    running_tasks.add(task_id)
            elif "running as a background task" in content.lower():
                running_tasks.add("generic_running_task")

    return completed_tasks, running_tasks
