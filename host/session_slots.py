"""Session slot tracking and lifecycle state management."""

from __future__ import annotations

import os
import time
from typing import Any

try:
    from host.status_common import (
        PROVIDER_ANTIGRAVITY,
        normalize_provider,
    )
except ModuleNotFoundError:
    from status_common import (  # type: ignore[no-redef]
        PROVIDER_ANTIGRAVITY,
        normalize_provider,
    )

AGENT_SLOT_COUNT = 10
# Firmware status slots follow the physical action order 1..9,0. Recent tasks
# are assigned by numeric key order 0..9, so rank 0 maps to firmware slot 9.
NUMERIC_KEY_BY_SLOT = (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)
RECENT_RANK_TO_SLOT = (9, 0, 1, 2, 3, 4, 5, 6, 7, 8)
STALE_THINKING_TIMEOUT_SECS = 45.0
RECONCILIATION_HOOK_COOLDOWN_SECS = 10.0
STALE_SUBAGENT_ACTIVE_SECS = 600.0
STALE_SUBAGENT_WAITING_SECS = 1800.0


def _compute_effective_status(
    parent_status: str,
    subagents: dict[str, dict[str, Any]],
    now: float | None = None,
) -> str:
    """Aggregate parent status and active subagent statuses into a single LED state."""
    ts = now if now is not None else time.time()
    stale_keys = [
        sub_id
        for sub_id, info in subagents.items()
        if (
            ts - float(info.get("updated", 0))
            > (STALE_SUBAGENT_WAITING_SECS if info.get("status") == "waiting" else STALE_SUBAGENT_ACTIVE_SECS)
        )
    ]
    for sub_id in stale_keys:
        subagents.pop(sub_id, None)

    if parent_status == "waiting" or any(info.get("status") == "waiting" for info in subagents.values()):
        return "waiting"
    if subagents:
        return "executing"
    return parent_status


def is_pid_alive(pid: int | None) -> bool:
    """Check if a process ID is currently running on the local system."""
    if not pid or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


class SessionSlots:
    def __init__(self) -> None:
        self.slots: list[dict[str, Any] | None] = [None] * AGENT_SLOT_COUNT
        self.active_session: str | None = None

    def update(
        self,
        session_id: str,
        status: str,
        remove: bool = False,
        from_hook: bool = True,
        provider: str | None = None,
        pid: int | None = None,
        is_remote: bool = False,
        workspace: str | None = None,
        client_app: str | None = None,
        terminal_app: str | None = None,
        iterm_session_id: str | None = None,
        tty: str | None = None,
        actor_id: str | None = None,
        is_subagent: bool = False,
        fully_idle: bool | None = None,
    ) -> int | None:
        now = time.time()
        existing = next(
            (index for index, item in enumerate(self.slots) if item and item["session_id"] == session_id),
            None,
        )
        sub_actor = (
            actor_id.strip()
            if actor_id and actor_id.strip() and (actor_id.strip() != session_id or is_subagent)
            else ("_subagent" if is_subagent else None)
        )

        if remove and not sub_actor:
            if existing is not None:
                self.slots[existing] = None
            if self.active_session == session_id:
                remaining = [item for item in self.slots if item]
                self.active_session = max(remaining, key=lambda item: item["updated"])["session_id"] if remaining else None
            return existing

        if existing is None:
            if remove and sub_actor:
                return None
            existing = next((index for index, item in enumerate(self.slots) if item is None), None)
        if existing is None:
            existing = min(range(AGENT_SLOT_COUNT), key=lambda index: self.slots[index]["updated"])

        previous = self.slots[existing]
        if previous and previous.get("session_id") != session_id:
            previous = None

        subagents: dict[str, dict[str, Any]] = dict(previous.get("subagents") or {}) if previous else {}
        if sub_actor:
            parent_status = (
                str(previous.get("parent_status") or previous.get("status") or "executing")
                if previous
                else "executing"
            )
            if remove or status in {"complete", "off", "idle"}:
                subagents.pop(sub_actor, None)
            else:
                subagents[sub_actor] = {"status": status, "updated": now}
        else:
            parent_status = status
            if fully_idle is True and status in {"complete", "off", "idle"}:
                subagents = {
                    k: v for k, v in subagents.items() if v.get("status") == "waiting"
                }

        effective_status = _compute_effective_status(parent_status, subagents, now=now)
        self.slots[existing] = {
            "session_id": session_id,
            "status": effective_status,
            "parent_status": parent_status,
            "subagents": subagents,
            "updated": now,
            "last_hook_time": now if from_hook else previous.get("last_hook_time", 0) if previous else 0,
            "provider": normalize_provider(
                provider,
                previous.get("provider", PROVIDER_ANTIGRAVITY) if previous else PROVIDER_ANTIGRAVITY,
            ),
            "pid": pid if pid is not None else previous.get("pid") if previous else None,
            "is_remote": is_remote if is_remote else previous.get("is_remote", False) if previous else False,
            "workspace": workspace if workspace else previous.get("workspace", "") if previous else "",
            "client_app": client_app if client_app else previous.get("client_app") if previous else None,
            "terminal_app": terminal_app if terminal_app else previous.get("terminal_app") if previous else None,
            "iterm_session_id": iterm_session_id if iterm_session_id else previous.get("iterm_session_id") if previous else None,
            "tty": tty if tty else previous.get("tty") if previous else None,
        }
        self.active_session = session_id
        return existing

    def session_for_slot(self, slot: int) -> str | None:
        item = self.slots[slot] if 0 <= slot < len(self.slots) else None
        return item["session_id"] if item else None

    def provider_for_slot(self, slot: int) -> str:
        item = self.slots[slot] if 0 <= slot < len(self.slots) else None
        return normalize_provider(item.get("provider") if item else None)

    def reconcile_terminal(self, session_id: str, status: str) -> int | None:
        """Resolve a stale thinking state ONLY when an active session was left in thinking state."""
        existing = next(
            (index for index, item in enumerate(self.slots) if item and item["session_id"] == session_id),
            None,
        )
        if existing is None:
            return None

        slot_item = self.slots[existing]
        current_status = slot_item["status"]
        if current_status == status or current_status == "error":
            return None

        now = time.time()
        last_hook = slot_item.get("last_hook_time", 0)

        # Hook priority cooldown: if an authoritative hook event arrived recently,
        # suppress disk-based reconciliation from overriding it.
        if last_hook > 0 and (now - last_hook) < RECONCILIATION_HOOK_COOLDOWN_SECS:
            return None

        subagents: dict[str, dict[str, Any]] = dict(slot_item.get("subagents") or {})

        # Premature complete recovery: if background tasks or subagents are actively running/waiting in transcript
        if current_status == "complete":
            if status in {"executing", "waiting"}:
                slot_item["parent_status"] = status
                slot_item["status"] = _compute_effective_status(status, subagents, now=now)
                slot_item["updated"] = now
                return existing if slot_item["status"] != current_status else None
            return None

        if current_status not in {"thinking", "executing"}:
            return None

        # If an active thinking/executing session has a waiting or executing signal in transcript:
        if status in {"waiting", "executing"}:
            slot_item["parent_status"] = status
            new_eff = _compute_effective_status(status, subagents, now=now)
            if new_eff != current_status:
                slot_item["status"] = new_eff
                slot_item["updated"] = now
                return existing
            return None

        # Resolve stale thinking after timeout
        if (now - last_hook) >= STALE_THINKING_TIMEOUT_SECS:
            slot_item["parent_status"] = status
            new_eff = _compute_effective_status(status, subagents, now=now)
            if new_eff != current_status:
                slot_item["status"] = new_eff
                slot_item["updated"] = now
                return existing

        return None

    def populate_recent(
        self,
        session_ids: list[str],
        providers: dict[str, str] | None = None,
        active_only: bool = False,
    ) -> bool:
        """Map recency ranks to numeric keys 0..9 while preserving active sessions."""
        previous_by_id = {
            item["session_id"]: item for item in self.slots if item
        }

        if active_only:
            # Only keep sessions with live runtime activity (last_hook_time > 0 and status != "off")
            new_slots: list[dict[str, Any] | None] = [None] * AGENT_SLOT_COUNT
            for slot, item in enumerate(self.slots):
                if item and item.get("last_hook_time", 0) > 0 and item.get("status") not in {"off"}:
                    new_slots[slot] = item

            changed = [item["session_id"] if item else None for item in self.slots] != [
                item["session_id"] if item else None for item in new_slots
            ]
            self.slots = new_slots
            active_ids = {item["session_id"] for item in self.slots if item}
            if self.active_session not in active_ids:
                self.active_session = next(iter(active_ids), None)
            return changed

        # Retain active remote/hook sessions that may not be on the local filesystem (e.g. Cloudtop)
        all_candidate_ids = list(session_ids)
        for prev_id, prev_item in sorted(
            previous_by_id.items(),
            key=lambda x: x[1].get("updated", 0),
            reverse=True,
        ):
            if (
                prev_id not in all_candidate_ids
                and prev_item.get("last_hook_time", 0) > 0
                and prev_item.get("status") not in {"off"}
            ):
                all_candidate_ids.append(prev_id)

        new_slots: list[dict[str, Any] | None] = [None] * AGENT_SLOT_COUNT
        for rank, session_id in enumerate(all_candidate_ids[:AGENT_SLOT_COUNT]):
            slot = RECENT_RANK_TO_SLOT[rank]
            previous = previous_by_id.get(session_id)
            if previous:
                item = dict(previous)
                item["provider"] = normalize_provider(
                    (providers or {}).get(session_id, item.get("provider"))
                )
            else:
                item = {
                    "session_id": session_id,
                    "status": "idle",
                    "updated": time.time(),
                    "last_hook_time": 0,
                    "provider": normalize_provider((providers or {}).get(session_id)),
                }
            new_slots[slot] = item

        changed = [item["session_id"] if item else None for item in self.slots] != [
            item["session_id"] if item else None for item in new_slots
        ]
        self.slots = new_slots
        desired_ids = set(all_candidate_ids[:AGENT_SLOT_COUNT])
        if self.active_session not in desired_ids:
            self.active_session = all_candidate_ids[0] if all_candidate_ids else None
        return changed
