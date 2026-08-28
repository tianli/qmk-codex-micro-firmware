#!/usr/bin/env python3
"""Show the current Agent slot assignments on the MF17 keypad (10 Agents)."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

SLOT_KEYS = {
    0: "Agent 1 (Key 1)",
    1: "Agent 2 (Key 2)",
    2: "Agent 3 (Key 3)",
    3: "Agent 4 (Key 4)",
    4: "Agent 5 (Key 5)",
    5: "Agent 6 (Key 6)",
    6: "Agent 7 (Key 7)",
    7: "Agent 8 (Key 8)",
    8: "Agent 9 (Key 9)",
    9: "Agent 0 (Key 0)",
}

LOG_PATH = Path(__file__).resolve().parent.parent / "host" / "mf17_bridge.log"


def get_conversation_workspace(session_id: str) -> str:
    try:
        res = subprocess.run(
            ["agentapi", "get-conversation-metadata", session_id],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0:
            json_start = res.stdout.find("{")
            if json_start != -1:
                data = json.loads(res.stdout[json_start:])
                meta = data.get("response", {}).get("conversationMetadata", {}).get("metadata", {})
                workspaces = meta.get("workspaces", [])
                if workspaces and "workspaceFolderAbsoluteUri" in workspaces[0]:
                    return workspaces[0]["workspaceFolderAbsoluteUri"].replace("file://", "")
                uris = meta.get("workspaceUris", [])
                if uris:
                    return uris[0].replace("file://", "")
    except Exception:
        pass

    transcript = Path.home() / ".gemini" / "jetski" / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"
    if transcript.exists():
        try:
            with open(transcript, "r", encoding="utf-8") as f:
                first_line = json.loads(f.readline())
                content = str(first_line.get("content", "")).strip().split("\n")[0]
                return f"Prompt: {content[:40]}..."
        except Exception:
            pass

    return "(no active workspace recorded)"


def main() -> int:
    if not LOG_PATH.exists():
        print("No bridge log found at:", LOG_PATH)
        return 1

    recent_line = ""
    status_map: dict[str, str] = {}

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "loaded recent task slots:" in line:
                recent_line = line
            match = re.search(r"status session=([0-9a-f-]+) state=(\w+) slot=(\d+)", line)
            if match:
                sid, state, slot_num = match.groups()
                status_map[sid] = state

    if not recent_line:
        print("No task slots have been assigned yet.")
        return 0

    raw_ids = recent_line.split("loaded recent task slots:")[-1].strip()
    session_ids = [s.strip() for s in raw_ids.split(",") if s.strip()]

    print(f"\n{'Key / Slot':<22} | {'Status':<10} | {'Workspace / Info':<45} | {'Conversation ID'}")
    print("-" * 115)

    for slot_idx in range(10):
        key_label = SLOT_KEYS.get(slot_idx, f"Agent {slot_idx + 1}")
        if slot_idx < len(session_ids):
            sid = session_ids[slot_idx]
            status = status_map.get(sid, "idle")
            workspace = get_conversation_workspace(sid)
            print(f"{key_label:<22} | {status:<10} | {workspace:<45} | {sid}")
        else:
            print(f"{key_label:<22} | {'off':<10} | {'(unassigned)':<45} | -")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
