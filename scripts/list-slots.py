#!/usr/bin/env python3
"""Show the current Agent slot assignments on the MF17 keypad (10 Agents)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

# Numeric keys in recency order: 0 is newest, 1 is 2nd newest, ..., 9 is 10th
DIGIT_LABELS = {
    0: "Key 0 (Most Recent)",
    1: "Key 1 (Rank 2)",
    2: "Key 2 (Rank 3)",
    3: "Key 3 (Rank 4)",
    4: "Key 4 (Rank 5)",
    5: "Key 5 (Rank 6)",
    6: "Key 6 (Rank 7)",
    7: "Key 7 (Rank 8)",
    8: "Key 8 (Rank 9)",
    9: "Key 9 (Rank 10)",
}

LOG_PATH = Path(__file__).resolve().parent.parent / "host" / "mf17_bridge.log"


def get_conversation_workspace(session_id: str) -> str:
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

    transcript = jetski_brain / session_id / ".system_generated" / "logs" / "transcript.jsonl"
    if transcript.exists():
        try:
            with open(transcript, "r", encoding="utf-8") as f:
                first_line = json.loads(f.readline())
                content = str(first_line.get("content", "")).strip().split("\n")[0]
                if content:
                    return f"Prompt: {content[:42]}..."
        except Exception:
            pass

    if shutil.which("agentapi"):
        try:
            res = subprocess.run(
                ["agentapi", "get-conversation-metadata", session_id],
                capture_output=True,
                text=True,
                timeout=0.5,
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

    return "(active task)"


def main() -> int:
    if not LOG_PATH.exists():
        print("No bridge log found at:", LOG_PATH)
        return 1

    slot_map: dict[int, str] = {}
    status_map: dict[str, str] = {}

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "loaded numeric task slots:" in line:
                # Format: 0=id1, 1=id2, ...
                raw = line.split("loaded numeric task slots:")[-1].strip()
                pairs = [p.strip() for p in raw.split(",") if "=" in p]
                for p in pairs:
                    k, v = p.split("=", 1)
                    try:
                        slot_map[int(k.strip())] = v.strip()
                    except ValueError:
                        pass
            elif "loaded recent task slots:" in line:
                raw_ids = line.split("loaded recent task slots:")[-1].strip()
                session_ids = [s.strip() for s in raw_ids.split(",") if s.strip()]
                for idx, sid in enumerate(session_ids[:10]):
                    slot_map[idx] = sid

            match = re.search(r"status session=([0-9a-f-]+) state=(\w+)", line)
            if match:
                sid, state = match.groups()
                status_map[sid] = state

    if not slot_map:
        print("No task slots have been assigned yet.")
        return 0

    print(f"\n{'Key / Recency':<24} | {'Status':<10} | {'Workspace / Info':<48} | {'Conversation ID'}")
    print("-" * 120)

    for digit in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9):
        key_label = DIGIT_LABELS.get(digit, f"Key {digit}")
        sid = slot_map.get(digit)
        if sid:
            status = status_map.get(sid, "idle")
            workspace = get_conversation_workspace(sid)
            print(f"{key_label:<24} | {status:<10} | {workspace:<48} | {sid}")
        else:
            print(f"{key_label:<24} | {'off':<10} | {'(unassigned)':<48} | -")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
