"""Shared heuristics, constants, and utilities for agent lifecycle status."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
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

SUBAGENT_SPAWN_TOOLS = {
    "invoke_subagent",
    "swarm",
    "run_workflow",
}

COMMAND_PERMISSION_TOOLS = {
    "run_command",
    "bash",
    "execute_command",
    "terminal",
}

_SUBAGENT_CONV_ID_RE = re.compile(
    r'"conversationId"\s*:\s*"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"',
    re.IGNORECASE,
)
_SUBAGENT_SENDER_RE = re.compile(
    r"\bsender=([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:\s|$)",
    re.IGNORECASE,
)
_CONVERSATION_URI_RE = re.compile(
    r"conversation://([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",
    re.IGNORECASE,
)

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


def parse_iso_timestamp(value: Any) -> float | None:
    """Parse an ISO-8601 timestamp string (including Z and nanosecond precision) to epoch seconds."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def extract_subagent_ids_from_lines(
    lines: list[bytes | str],
    parent_session_id: str | None = None,
) -> list[str]:
    """Extract spawned subagent conversation UUIDs from a parent transcript's JSONL lines."""
    parent_lower = (parent_session_id or "").strip().lower()
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(cid: str) -> None:
        norm = cid.strip().lower()
        if norm and norm != parent_lower and norm not in seen:
            seen.add(norm)
            ordered.append(cid.strip())

    for raw_line in lines:
        if isinstance(raw_line, bytes):
            try:
                line_str = raw_line.decode("utf-8", errors="replace")
            except Exception:
                continue
        else:
            line_str = raw_line

        if "conversationId" not in line_str and "sender=" not in line_str and "conversation://" not in line_str:
            continue

        try:
            record = json.loads(line_str)
        except (json.JSONDecodeError, TypeError):
            continue

        step_type = str(record.get("type") or "")
        content = str(record.get("content") or "")
        if step_type in {"GENERIC", "TOOL_USE", "TOOL_CALL", "TOOL_RESULT"}:
            lower_content = content.lower()
            if "subagent" in lower_content or "swarm" in lower_content or "worker" in lower_content:
                for m in _SUBAGENT_CONV_ID_RE.finditer(content):
                    _add(m.group(1))
                for m in _CONVERSATION_URI_RE.finditer(content):
                    _add(m.group(1))
        elif step_type == "SYSTEM_MESSAGE":
            for m in _SUBAGENT_SENDER_RE.finditer(content):
                _add(m.group(1))

    return ordered


def inspect_jetski_transcript_actor_state(
    path: Path,
    now: float | None = None,
    is_subagent: bool = True,
) -> str | None:
    """Inspect a Jetski transcript.jsonl and return its live actor state.

    Detects blocked permission prompts (e.g. run_command, write_file, edit_file,
    ask_question) even for subagents configured with inheritCustomizations: false
    that do not execute lifecycle hooks.
    """
    try:
        if not path.exists():
            return None
        stat = path.stat()
        if stat.st_size <= 0:
            return None
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - 256 * 1024)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.readlines()
    except OSError:
        return None

    if not lines:
        return None

    _, running_tasks = scan_transcript_background_tasks(lines)
    records: list[dict[str, Any]] = []
    for raw_line in lines:
        try:
            rec = json.loads(raw_line)
            if isinstance(rec, dict):
                records.append(rec)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            continue

    if not records:
        return None

    last_planner_idx: int | None = None
    for idx in range(len(records) - 1, -1, -1):
        if records[idx].get("type") in {"PLANNER_RESPONSE", "assistant_message"}:
            last_planner_idx = idx
            break

    if last_planner_idx is None:
        return "executing" if is_subagent else "thinking"

    planner = records[last_planner_idx]
    after = records[last_planner_idx + 1 :]
    tool_calls = planner.get("tool_calls") or []
    planner_status = str(planner.get("status") or "")

    if not tool_calls:
        if any(r.get("type") in {"USER_INPUT", "SYSTEM_MESSAGE"} for r in after):
            return "executing" if (is_subagent or running_tasks) else "thinking"
        if running_tasks:
            return "executing"
        if planner_status == "ERROR" or planner.get("error"):
            return "error"
        if not is_subagent and looks_like_user_prompt(planner.get("content")):
            return "waiting"
        if planner_status == "DONE":
            return "complete"
        return "executing" if is_subagent else "thinking"

    result_steps = [
        r for r in after if r.get("type") in {"GENERIC", "TOOL_USE", "TOOL_CALL", "TOOL_RESULT"}
    ]
    if len(result_steps) >= len(tool_calls):
        return "executing"

    # Fewer tool results than requested tool_calls -> at least one tool call is still pending/running
    now_ts = now if now is not None else time.time()
    planner_ts = parse_iso_timestamp(planner.get("created_at"))
    if planner_ts is None:
        planner_ts = stat.st_mtime
    elapsed = max(0.0, now_ts - planner_ts)

    for tc in tool_calls:
        if isinstance(tc, dict) and is_interactive_tool(str(tc.get("name") or "")):
            return "waiting"

    perm_tools = [
        tc
        for tc in tool_calls
        if isinstance(tc, dict) and is_permission_tool(str(tc.get("name") or ""))
    ]
    if perm_tools:
        fast_perm = [
            tc
            for tc in perm_tools
            if base_tool_name(str(tc.get("name") or "")) not in COMMAND_PERMISSION_TOOLS
        ]
        cmd_perm = [
            tc
            for tc in perm_tools
            if base_tool_name(str(tc.get("name") or "")) in COMMAND_PERMISSION_TOOLS
        ]
        if fast_perm and elapsed >= 1.0:
            return "waiting"
        if cmd_perm and elapsed >= 3.5:
            return "waiting"

    return "executing"


def aggregate_subagent_transcript_states(
    brain_dir: Path,
    parent_lines: list[bytes | str],
    parent_session_id: str | None = None,
    now: float | None = None,
) -> dict[str, str]:
    """Inspect all subagents spawned in parent_lines (up to depth 2) and return {sub_id: state}."""
    if not brain_dir.exists():
        return {}
    states: dict[str, str] = {}
    visited: set[str] = {(parent_session_id or "").strip().lower()}
    queue: list[tuple[str, int]] = [
        (sub_id, 1)
        for sub_id in extract_subagent_ids_from_lines(parent_lines, parent_session_id=parent_session_id)
    ]

    while queue:
        sub_id, depth = queue.pop(0)
        norm = sub_id.lower()
        if norm in visited:
            continue
        visited.add(norm)

        sub_transcript = brain_dir / sub_id / ".system_generated" / "logs" / "transcript.jsonl"
        if not sub_transcript.exists():
            continue

        state = inspect_jetski_transcript_actor_state(sub_transcript, now=now, is_subagent=True)
        if state is not None:
            states[sub_id] = state

        if depth < 2:
            try:
                with sub_transcript.open("rb") as f:
                    size = f.seek(0, os.SEEK_END)
                    f.seek(max(0, size - 128 * 1024))
                    nested_lines = f.readlines()
                for child_id in extract_subagent_ids_from_lines(nested_lines, parent_session_id=sub_id):
                    if child_id.lower() not in visited:
                        queue.append((child_id, depth + 1))
            except OSError:
                pass

    return states
