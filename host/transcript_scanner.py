"""Transcript scanner and Codex session discovery."""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

try:
    from host.status_common import (
        looks_like_user_prompt,
        scan_transcript_background_tasks,
    )
    from host.session_slots import AGENT_SLOT_COUNT
except ModuleNotFoundError:
    from status_common import (  # type: ignore[no-redef]
        looks_like_user_prompt,
        scan_transcript_background_tasks,
    )
    from session_slots import AGENT_SLOT_COUNT  # type: ignore[no-redef]

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)
SESSION_FILE_PATTERN = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl$", re.IGNORECASE)
TRANSCRIPT_TAIL_BYTES = 256 * 1024


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

    completed_tasks, running_tasks = scan_transcript_background_tasks(lines)
    completed_codex_calls: set[str] = set()
    latest_terminal_state: str | None = None

    for raw_line in reversed(lines):
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        step_type = record.get("type", "")
        status = record.get("status", "")
        content = str(record.get("content") or "")

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
            if running_tasks:
                return "executing"
            return latest_terminal_state

        if step_type in {"PLANNER_RESPONSE", "assistant_message"}:
            tool_calls = record.get("tool_calls") or []
            for tc in tool_calls:
                tname = str(tc.get("name", "")).lower()
                if "ask_question" in tname or "ask_custom_permission" in tname or "request_user_input" in tname:
                    return "waiting"
            if status == "ERROR" or record.get("error"):
                return "error"

            if looks_like_user_prompt(content):
                return "waiting"

            if status == "DONE" and not tool_calls:
                if latest_terminal_state is None:
                    latest_terminal_state = "complete"
            elif status == "DONE" and tool_calls:
                if latest_terminal_state is None:
                    latest_terminal_state = None

        # Codex transcript fallback
        if record.get("type") == "event_msg":
            event_type = record.get("payload", {}).get("type")
            if event_type == "task_complete":
                return "complete"
            if event_type == "turn_aborted":
                return "idle"
            if event_type == "task_started":
                return None

    if running_tasks:
        return "executing"

    return latest_terminal_state
