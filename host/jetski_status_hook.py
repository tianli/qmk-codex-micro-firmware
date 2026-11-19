"""Translate Antigravity/Jetski lifecycle hook JSON into a local status event."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any

try:
    from host.status_common import (
        COMMAND_PERMISSION_TOOLS,
        DEFAULT_STATUS_HOST as HOST,
        DEFAULT_STATUS_PORT as PORT,
        PERMISSION_REQUIRED_TOOLS,
        PERMISSION_TOOL_NAMES,
        PROVIDER_ANTIGRAVITY,
        SUBAGENT_SPAWN_TOOLS,
        USER_PROMPT_PATTERNS,
        WAITING_NAME_MARKERS,
        WAITING_REASON_MARKERS,
        aggregate_subagent_transcript_states,
        base_tool_name as _base_tool_name,
        is_interactive_tool as _is_interactive_tool,
        is_permission_tool as _is_permission_tool,
        looks_like_user_prompt as _looks_like_user_prompt,
        normalize_task_id as _normalize_task_id,
        scan_transcript_background_tasks,
    )
except ModuleNotFoundError:
    try:
        from status_common import (  # type: ignore[no-redef]
            COMMAND_PERMISSION_TOOLS,
            DEFAULT_STATUS_HOST as HOST,
            DEFAULT_STATUS_PORT as PORT,
            PERMISSION_REQUIRED_TOOLS,
            PERMISSION_TOOL_NAMES,
            PROVIDER_ANTIGRAVITY,
            SUBAGENT_SPAWN_TOOLS,
            USER_PROMPT_PATTERNS,
            WAITING_NAME_MARKERS,
            WAITING_REASON_MARKERS,
            aggregate_subagent_transcript_states,
            base_tool_name as _base_tool_name,
            is_interactive_tool as _is_interactive_tool,
            is_permission_tool as _is_permission_tool,
            looks_like_user_prompt as _looks_like_user_prompt,
            normalize_task_id as _normalize_task_id,
            scan_transcript_background_tasks,
        )
    except ModuleNotFoundError:
        # Standalone deployment fallback (e.g. single file scp to remote Cloudtop)
        HOST = "127.0.0.1"
        PORT = 41717
        PROVIDER_ANTIGRAVITY = "antigravity"

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

        USER_PROMPT_PATTERNS = (
            r"\b(?:should\s+i|would\s+you\s+like|do\s+you\s+want|shall\s+i)\b",
            r"\b(?:which\s+(?:one|option|approach|path|file|directory)\s+would\s+you)\b",
            r"\b(?:please\s+(?:let\s+me\s+know|confirm|choose|specify|clarify|reply|provide|select))\b",
            r"\b(?:let\s+me\s+know\s+(?:which|what|if\s+you\s+would\s+like|how\s+you\s+would\s+like))\b",
            r"\b(?:i\s+need|waiting\s+for|waiting\s+on)\b.{0,60}\b(?:input|answer|choice|selection|details|response)\b",
        )

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

        def _looks_like_user_prompt(message: Any) -> bool:
            if not isinstance(message, str):
                return False
            clean_text = re.sub(r"```[\s\S]*?```", " ", message)
            clean_text = re.sub(r"`[^`\n]*?`", " ", clean_text)
            clean_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean_text)
            text = " ".join(clean_text.strip().split())
            if not text:
                return False
            if text.rstrip(" *_\t\n\r.)").endswith("?"):
                return True
            tail = text[-500:]
            return any(
                re.search(pattern, tail, flags=re.IGNORECASE)
                for pattern in USER_PROMPT_PATTERNS
            )

        def _normalize_task_id(tid: str) -> str:
            return tid.strip("\"'").split("/")[-1]

        def _base_tool_name(tool_name: str) -> str:
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

        def _is_permission_tool(tool_name: str) -> bool:
            base_tool = _base_tool_name(tool_name)
            compact = base_tool.replace("_", "")
            return (
                base_tool in PERMISSION_REQUIRED_TOOLS
                or compact in PERMISSION_REQUIRED_TOOLS
                or _is_interactive_tool(tool_name)
            )

        def scan_transcript_background_tasks(lines: list[bytes | str]) -> tuple[set[str], set[str]]:
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
                    rec = json.loads(line_str)
                except Exception:
                    continue
                content = str(rec.get("content") or "")
                for m in re.finditer(
                    r"Task(?:\s+id)?\s+[\"']?([\w\-/]+)[\"']?\s+(?:was\s+)?(?:finished|completed|failed|killed|timed\s*out|terminated|canceled|cancelled)",
                    content,
                    re.IGNORECASE,
                ):
                    completed_tasks.add(_normalize_task_id(m.group(1)))
                if rec.get("status") == "RUNNING" and rec.get("type") in {"GENERIC", "TOOL_USE", "TOOL_CALL"}:
                    m = re.search(r"task id:\s*[\"']?([\w\-/]+)", content, re.IGNORECASE)
                    if m:
                        task_id = _normalize_task_id(m.group(1))
                        if task_id not in completed_tasks:
                            running_tasks.add(task_id)
                    elif "running as a background task" in content.lower():
                        running_tasks.add("generic_running_task")
            return completed_tasks, running_tasks

        def parse_iso_timestamp(value: Any) -> float | None:
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
                if not is_subagent and _looks_like_user_prompt(planner.get("content")):
                    return "waiting"
                if planner_status == "DONE":
                    return "complete"
                return "executing" if is_subagent else "thinking"

            result_steps = [
                r for r in after if r.get("type") in {"GENERIC", "TOOL_USE", "TOOL_CALL", "TOOL_RESULT"}
            ]
            if len(result_steps) >= len(tool_calls):
                return "executing"

            now_ts = now if now is not None else time.time()
            planner_ts = parse_iso_timestamp(planner.get("created_at"))
            if planner_ts is None:
                planner_ts = stat.st_mtime
            elapsed = max(0.0, now_ts - planner_ts)

            for tc in tool_calls:
                if isinstance(tc, dict) and _is_interactive_tool(str(tc.get("name") or "")):
                    return "waiting"

            perm_tools = [
                tc
                for tc in tool_calls
                if isinstance(tc, dict) and _is_permission_tool(str(tc.get("name") or ""))
            ]
            if perm_tools:
                fast_perm = [
                    tc
                    for tc in perm_tools
                    if _base_tool_name(str(tc.get("name") or "")) not in COMMAND_PERMISSION_TOOLS
                ]
                cmd_perm = [
                    tc
                    for tc in perm_tools
                    if _base_tool_name(str(tc.get("name") or "")) in COMMAND_PERMISSION_TOOLS
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
        # happens to contain words like "question" or "user input".
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


def _get_last_assistant_message(event: dict[str, Any]) -> str:
    """Extract the assistant's final response from payload or transcript."""
    for key in ("last_assistant_message", "lastAssistantMessage", "message", "content"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    tpath = event.get("transcriptPath") or event.get("transcript_path")
    if tpath:
        try:
            p = Path(tpath)
            if p.exists():
                with open(p, "rb") as f:
                    size = f.seek(0, 2)
                    f.seek(max(0, size - 512 * 1024))
                    lines = f.readlines()
                for line in reversed(lines):
                    try:
                        rec = json.loads(line)
                        if (
                            rec.get("type") in {"PLANNER_RESPONSE", "assistant_message", "message"}
                            or rec.get("role") == "assistant"
                            or (rec.get("source") == "MODEL" and rec.get("type") != "GENERIC")
                        ):
                            content = rec.get("content") or rec.get("text")
                            if isinstance(content, str) and content.strip():
                                return content.strip()
                    except Exception:
                        continue
        except Exception:
            pass
    return ""


def _resolve_transcript_path(event: dict[str, Any], session_id: str | None = None) -> Path | None:
    tpath = event.get("transcriptPath") or event.get("transcript_path")
    if tpath:
        p = Path(str(tpath))
        if p.exists():
            return p
    conv_id = session_id or event.get("conversationId") or event.get("conversation_id") or event.get("session_id")
    if conv_id:
        cand = Path.home() / ".gemini" / "jetski" / "brain" / str(conv_id) / ".system_generated" / "logs" / "transcript.jsonl"
        if cand.exists():
            return cand
    return None


def _read_tail_lines(path: Path, max_bytes: int = 256 * 1024) -> list[bytes]:
    try:
        with path.open("rb") as f:
            size = f.seek(0, 2)
            start = max(0, size - max_bytes)
            f.seek(start)
            if start:
                f.readline()
            return f.readlines()
    except OSError:
        return []


def _get_subagent_states_for_event(event: dict[str, Any], session_id: str | None = None) -> dict[str, str]:
    p = _resolve_transcript_path(event, session_id=session_id)
    if not p or p.parent.name != "logs" or p.parent.parent.name != ".system_generated":
        return {}
    try:
        lines = _read_tail_lines(p)
        if not lines:
            return {}
        return aggregate_subagent_transcript_states(
            p.parents[3],
            lines,
            parent_session_id=p.parents[2].name,
        )
    except Exception:
        return {}


def _latest_planner_has_tool_calls(event: dict[str, Any]) -> bool:
    """Return True if the latest PLANNER_RESPONSE in transcript emitted tool calls."""
    p = _resolve_transcript_path(event)
    if not p:
        return False
    lines = _read_tail_lines(p, max_bytes=128 * 1024)
    for raw_line in reversed(lines):
        try:
            rec = json.loads(raw_line)
        except Exception:
            continue
        if rec.get("type") in {"PLANNER_RESPONSE", "assistant_message"}:
            return bool(rec.get("tool_calls"))
    return False


def _has_running_background_tasks(event: dict[str, Any]) -> bool:
    """Return True if background tasks or subagents are still running."""
    # 1. Antigravity protojson explicitly passes fullyIdle: false when background tasks/subagents run
    if event.get("fullyIdle") is False or event.get("fully_idle") is False:
        return True

    # 2. Check if the current tool_response or event itself indicates a background task or subagent spawn
    tool_name = _base_tool_name(_tool_name(event))
    if tool_name in SUBAGENT_SPAWN_TOOLS:
        return True
    if tool_name in {"run_command", "bash", "execute_command", "terminal"}:
        tool_resp = str(event.get("tool_response") or event.get("result") or "")
        if "running as a background task" in tool_resp.lower() or "sent to the background as a task" in tool_resp.lower():
            return True

    # 3. Inspect transcript tail for any uncompleted background tasks or active subagents
    p = _resolve_transcript_path(event)
    if p:
        lines = _read_tail_lines(p)
        if lines:
            _, running_tasks = scan_transcript_background_tasks(lines)
            if running_tasks:
                return True
            if p.parent.name == "logs" and p.parent.parent.name == ".system_generated":
                try:
                    sub_states = aggregate_subagent_transcript_states(
                        p.parents[3],
                        lines,
                        parent_session_id=p.parents[2].name,
                    )
                    if any(st not in {"complete", "off", "idle"} for st in sub_states.values()):
                        return True
                except Exception:
                    pass
    return False


def event_to_status(event: dict[str, Any], explicit_event: str | None = None) -> tuple[str, bool]:
    """Map hook event payload to (status, remove_slot).
    
    Status can be: 'off', 'idle', 'thinking', 'complete', 'waiting', 'error', 'executing'.
    """
    name = _event_name(event, explicit_event)
    has_error = bool(event.get("error")) or _contains_error(event.get("tool_response"))

    # SessionEnd -> off
    if name in {"sessionend", "session_end"}:
        return "off", True

    # Stop -> complete (Green) unless explicit question/prompt was asked (Amber)
    # or background tasks/subagents are still running (Purple executing)
    if name in {"stop", "subagentstop", "subagent_stop"}:
        if has_error:
            return "error", False
        reason = _termination_reason(event)
        if "error" in reason:
            return "error", False
        if _contains_waiting_signal(event) or any(marker in reason for marker in WAITING_REASON_MARKERS):
            return "waiting", False
        
        last_msg = _get_last_assistant_message(event)
        if _looks_like_user_prompt(last_msg):
            return "waiting", False

        if _has_running_background_tasks(event):
            return "executing", False

        return "complete", False

    # PermissionRequest
    if name in {"permissionrequest", "permission_request"}:
        return "waiting", False

    # PreToolUse: Agent is about to execute a tool
    if name in {"pretooluse", "pre_tool_use"}:
        tool_name = _tool_name(event)
        
        # If the tool requires user permission (commands, file edits, etc.) or interactive input -> amber waiting
        if _is_permission_tool(tool_name):
            return "waiting", False
        # Tool calls that DO NOT need user permission (view_file, list_dir, grep_search, search_web, etc.) -> purple executing
        return "executing", False

    # PostToolUse: Tool completed execution
    if name in {"posttooluse", "post_tool_use"}:
        if has_error:
            return "error", False
        tool_name = _tool_name(event)
        if _is_interactive_tool(tool_name) or _contains_waiting_signal(event.get("tool_response")):
            return "waiting", False
        if _has_running_background_tasks(event):
            return "executing", False
        return "thinking", False

    # PreInvocation: Model is generating / thinking
    if name in {"preinvocation", "pre_invocation", "userpromptsubmit", "user_prompt_submit", "subagentstart", "subagent_start"}:
        if _has_running_background_tasks(event):
            return "executing", False
        return "thinking", False

    if name in {"postinvocation", "post_invocation"}:
        if has_error:
            return "error", False
        if _latest_planner_has_tool_calls(event):
            if _has_running_background_tasks(event):
                return "executing", False
            return "thinking", False
        last_msg = _get_last_assistant_message(event)
        if _looks_like_user_prompt(last_msg):
            return "waiting", False
        if _has_running_background_tasks(event):
            return "executing", False
        return "complete", False

    # Fallback inspection if event name wasn't explicitly supplied
    if any(key in event for key in ("toolCall", "tool_call", "tool_name", "toolName", "tool")):
        tool_name = _tool_name(event)
        if _is_interactive_tool(tool_name):
            return "waiting", False
        return "thinking", False

    if any(key in event for key in ("terminationReason", "termination_reason", "stop_reason", "stopReason")):
        reason = _termination_reason(event)
        if has_error or "error" in reason:
            return "error", False
        if _contains_waiting_signal(event) or any(marker in reason for marker in WAITING_REASON_MARKERS):
            return "waiting", False
        if _has_running_background_tasks(event):
            return "executing", False
        return "complete", False

    if has_error:
        return "error", False

    if _has_running_background_tasks(event):
        return "executing", False

    return "idle", False


def extract_workspace_name(payload: dict[str, Any], transcript_path: str = "") -> str:
    """Extract workspace or CitC client name from hook payload or transcript."""
    workspace_paths = payload.get("workspacePaths") or payload.get("workspace_paths") or []
    candidates: list[str] = []
    if isinstance(workspace_paths, list):
        candidates.extend(str(p) for p in workspace_paths if p)
    elif isinstance(workspace_paths, str) and workspace_paths.strip():
        candidates.append(workspace_paths.strip())

    cwd = str(payload.get("cwd") or "").strip()
    if cwd:
        candidates.append(cwd)

    citc_pattern = re.compile(r"/google/src/cloud/[^/]+/([^/\s\"'\\>]+)")
    for raw_path in candidates:
        clean = raw_path.strip()
        if clean.startswith("file://"):
            clean = clean[len("file://") :]
        m = citc_pattern.search(clean)
        if m:
            return m.group(1)
        base = os.path.basename(clean.rstrip("/\\"))
        if base and base != "google3":
            return base

    if transcript_path:
        try:
            p = Path(transcript_path)
            if p.exists():
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    head = f.read(4096)
                m = citc_pattern.search(head)
                if m:
                    return m.group(1)
        except Exception:
            pass

    return ""


def detect_client_app(environ: dict[str, str] | None = None, ppid: int | None = None) -> str | None:
    """Detect whether the hook is invoked from Cider (jetski-hub-server) or IDE."""
    env = environ if environ is not None else os.environ
    hub_markers = ("jetski/cmd/hub/server", "jetski-hub-server", "jetski-hub.service")
    for env_key in ("TOOLLOG_TARGET", "TOOLLOG_PATH", "SAR_ARGV0", "MEMORY_PRESSURE_WATCH"):
        val = str(env.get(env_key) or "").lower()
        if any(marker in val for marker in hub_markers):
            return "cider"

    proc_Paths = ["/proc/self/cgroup"]
    if ppid and ppid > 0:
        proc_Paths.extend([f"/proc/{ppid}/cmdline", f"/proc/{ppid}/cgroup"])
    for proc_path in proc_Paths:
        try:
            p = Path(proc_path)
            if p.exists():
                text = p.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").lower()
                if any(marker in text for marker in hub_markers):
                    return "cider"
        except Exception:
            pass

    if env.get("ANTIGRAVITY_EDITOR_APP_ROOT") or env.get("VSCODE_IPC_HOOK_CLI"):
        return "ide"

    return None


def _send_status_message(message: dict[str, Any]) -> None:
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


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _watcher_pid_file(session_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    return Path(tempfile.gettempdir()) / f"mf17_subwatch_{safe_id}.pid"


def _maybe_spawn_subagent_watcher(
    session_id: str,
    transcript_path: str,
    workspace_name: str,
    client_app: str | None,
    parent_pid: int,
    is_remote: bool,
    force: bool = False,
) -> None:
    if os.environ.get("MF17_DISABLE_SUBAGENT_WATCHER") == "1":
        return
    if not session_id or session_id == "unknown":
        return

    resolved = Path(transcript_path) if transcript_path else (
        Path.home() / ".gemini" / "jetski" / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"
    )
    if not force and not resolved.exists():
        return

    pid_file = _watcher_pid_file(session_id)
    try:
        if pid_file.exists():
            raw_pid = pid_file.read_text(encoding="utf-8", errors="replace").strip()
            if raw_pid.isdigit() and _is_pid_running(int(raw_pid)):
                return
    except OSError:
        pass

    try:
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--watch-subagents",
            session_id,
            "--transcript",
            str(resolved),
            "--workspace",
            workspace_name or "",
            "--client-app",
            client_app or "",
            "--parent-pid",
            str(parent_pid or 0),
        ]
        if is_remote:
            cmd.append("--is-remote")
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except Exception:
        pass


def _run_subagent_watcher(
    session_id: str,
    transcript_path: str,
    workspace_name: str,
    client_app: str | None,
    parent_pid: int,
    is_remote: bool,
    poll_interval: float = 1.0,
    max_duration: float = 1800.0,
) -> int:
    pid_file = _watcher_pid_file(session_id)
    try:
        if pid_file.exists():
            raw_pid = pid_file.read_text(encoding="utf-8", errors="replace").strip()
            if raw_pid.isdigit() and int(raw_pid) != os.getpid() and _is_pid_running(int(raw_pid)):
                return 0
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    p = Path(transcript_path) if transcript_path else (
        Path.home() / ".gemini" / "jetski" / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"
    )
    brain_dir = (
        p.parents[3]
        if p.parent.name == "logs" and p.parent.parent.name == ".system_generated" and len(p.parents) >= 4
        else Path.home() / ".gemini" / "jetski" / "brain"
    )

    last_sent: dict[str, str] = {}
    last_sent_ts: dict[str, float] = {}
    start_time = time.monotonic()
    idle_ticks = 0

    try:
        while (time.monotonic() - start_time) < max_duration:
            now_wall = time.time()
            lines = _read_tail_lines(p) if p.exists() else []
            sub_states = (
                aggregate_subagent_transcript_states(
                    brain_dir,
                    lines,
                    parent_session_id=session_id,
                    now=now_wall,
                )
                if lines
                else {}
            )

            has_active = False
            for sub_id, st in sub_states.items():
                is_active = st not in {"complete", "off", "idle"}
                if is_active:
                    has_active = True
                prev = last_sent.get(sub_id)
                prev_ts = last_sent_ts.get(sub_id, 0.0)
                if prev != st or (is_active and (now_wall - prev_ts) >= 15.0):
                    _send_status_message({
                        "kind": "jetski_status",
                        "provider": "antigravity",
                        "session_id": session_id,
                        "actor_id": sub_id,
                        "is_subagent": True,
                        "status": st,
                        "remove": st in {"complete", "off"},
                        "transcript_path": str(p),
                        "event": f"SubagentWatch:{st}",
                        "pid": parent_pid or None,
                        "is_remote": is_remote,
                        "workspace": workspace_name,
                        "client_app": client_app or None,
                    })
                    last_sent[sub_id] = st
                    last_sent_ts[sub_id] = now_wall

            if has_active:
                idle_ticks = 0
            else:
                idle_ticks += 1
                if idle_ticks >= 8:
                    break

            time.sleep(poll_interval)
    finally:
        try:
            if pid_file.exists() and pid_file.read_text(encoding="utf-8", errors="replace").strip() == str(os.getpid()):
                pid_file.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity/Jetski MF17 lifecycle status hook")
    parser.add_argument("--event", type=str, default=None, help="Explicit event name (e.g. PreInvocation, PreToolUse, Stop)")
    parser.add_argument("--watch-subagents", type=str, default=None, help="Internal: watch subagent transcripts for parent session")
    parser.add_argument("--transcript", type=str, default="", help="Internal: parent transcript path")
    parser.add_argument("--workspace", type=str, default="", help="Internal: workspace name")
    parser.add_argument("--client-app", type=str, default="", help="Internal: client app label")
    parser.add_argument("--parent-pid", type=int, default=0, help="Internal: parent process PID")
    parser.add_argument("--is-remote", action="store_true", help="Internal: mark events as remote")
    args, _ = parser.parse_known_args()

    if args.watch_subagents:
        return _run_subagent_watcher(
            session_id=args.watch_subagents,
            transcript_path=args.transcript,
            workspace_name=args.workspace,
            client_app=args.client_app or None,
            parent_pid=args.parent_pid,
            is_remote=args.is_remote,
        )

    payload: dict[str, Any] = {}
    if not sys.stdin.isatty():
        try:
            raw_input = sys.stdin.read()
            if raw_input.strip():
                payload = json.loads(raw_input)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            payload = {}

    status, remove = event_to_status(payload, explicit_event=args.event)
    
    parent_id = str(
        payload.get("parentConversationId")
        or payload.get("parent_conversation_id")
        or payload.get("parentId")
        or payload.get("parent_id")
        or ""
    ).strip()

    raw_session_id = str(
        payload.get("conversationId")
        or payload.get("conversation_id")
        or payload.get("session_id")
        or "unknown"
    ).strip()

    is_subagent = bool(
        parent_id
        or payload.get("isSubagent")
        or payload.get("subagentName")
        or (args.event and "subagent" in args.event.lower())
    )

    # Option 1: Route subagent status directly into the parent conversation's key slot
    if parent_id and parent_id != raw_session_id:
        session_id = parent_id
        # Subagent stopping clears only the subagent actor, not the parent slot
        if is_subagent and status in {"complete", "off"}:
            remove = True
        elif is_subagent:
            remove = False
    else:
        session_id = raw_session_id
        # If an unparented subagent completes, immediately clear its slot
        if is_subagent and status in {"complete", "off"}:
            remove = True

    transcript_path = str(payload.get("transcriptPath") or payload.get("transcript_path") or "")
    
    ppid = os.getppid()
    is_remote = bool(
        os.environ.get("SSH_CLIENT")
        or os.environ.get("SSH_TTY")
        or os.environ.get("SSH_CONNECTION")
        or os.environ.get("VSCODE_REMOTE_CONTAINERS_SESSION")
        or os.environ.get("REMOTE_SSH")
        or os.path.exists("/etc/cloudtop-release")
        or sys.platform.startswith("linux")
    )

    workspace_name = extract_workspace_name(payload, transcript_path=transcript_path)
    client_app = detect_client_app(ppid=ppid)

    term_prog = f"{os.environ.get('TERM_PROGRAM') or ''} {os.environ.get('LC_TERMINAL') or ''}".lower()
    terminal_app = None
    iterm_raw = os.environ.get("ITERM_SESSION_ID", "")
    iterm_session_id = ""
    if ":" in iterm_raw:
        iterm_session_id = iterm_raw.split(":")[-1]
    elif iterm_raw:
        iterm_session_id = iterm_raw

    if client_app != "cider":
        if "iterm" in term_prog or iterm_raw:
            terminal_app = "iTerm"
        elif "apple_terminal" in term_prog:
            terminal_app = "Terminal"
        elif "alacritty" in term_prog:
            terminal_app = "Alacritty"
        elif "kitty" in term_prog:
            terminal_app = "kitty"
        elif "warp" in term_prog:
            terminal_app = "Warp"
        elif "wezterm" in term_prog:
            terminal_app = "WezTerm"

    tty = ""
    if ppid and ppid > 0 and sys.platform.startswith("darwin"):
        try:
            res = subprocess.run(
                ["ps", "-p", str(ppid), "-o", "tty="],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            t = res.stdout.strip()
            if t and t not in {"??", "-"}:
                tty = t
        except Exception:
            pass

    raw_fully_idle = payload.get("fullyIdle")
    if not isinstance(raw_fully_idle, bool):
        raw_fully_idle = payload.get("fully_idle")
    fully_idle = raw_fully_idle if isinstance(raw_fully_idle, bool) else None

    message = {
        "kind": "jetski_status",
        "provider": "antigravity",
        "session_id": session_id,
        "actor_id": raw_session_id,
        "is_subagent": is_subagent,
        "fully_idle": fully_idle,
        "status": status,
        "remove": remove,
        "transcript_path": transcript_path,
        "event": args.event or payload.get("hook_event_name") or "",
        "pid": ppid,
        "is_remote": is_remote,
        "workspace": workspace_name,
        "client_app": client_app,
        "terminal_app": terminal_app,
        "iterm_session_id": iterm_session_id,
        "tty": tty,
    }

    _send_status_message(message)

    # For root sessions that spawn subagents (including subagents with inheritCustomizations: false
    # that do not run hooks.json), ensure a detached lightweight watcher monitors their transcripts.
    if not is_subagent and session_id != "unknown":
        tool_base = _base_tool_name(_tool_name(payload))
        sub_states = _get_subagent_states_for_event(payload, session_id=session_id)
        has_active_subs = any(st not in {"complete", "off", "idle"} for st in sub_states.values())
        if tool_base in SUBAGENT_SPAWN_TOOLS or has_active_subs:
            for sub_id, sub_st in sub_states.items():
                if sub_st not in {"complete", "off", "idle"}:
                    _send_status_message({
                        "kind": "jetski_status",
                        "provider": "antigravity",
                        "session_id": session_id,
                        "actor_id": sub_id,
                        "is_subagent": True,
                        "status": sub_st,
                        "remove": False,
                        "transcript_path": transcript_path,
                        "event": f"SubagentWatch:{sub_st}",
                        "pid": ppid,
                        "is_remote": is_remote,
                        "workspace": workspace_name,
                        "client_app": client_app,
                    })
            _maybe_spawn_subagent_watcher(
                session_id=session_id,
                transcript_path=transcript_path,
                workspace_name=workspace_name,
                client_app=client_app,
                parent_pid=ppid,
                is_remote=is_remote,
                force=(tool_base in SUBAGENT_SPAWN_TOOLS),
            )

    # Return expected protojson output schema based on lifecycle event
    event_name = _event_name(payload, args.event)
    if event_name in {"pretooluse", "pre_tool_use"}:
        print('{"decision": "allow"}')
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
