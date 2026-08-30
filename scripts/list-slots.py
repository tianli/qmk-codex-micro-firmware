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

# Numeric keys in logical keypad order: 1..9, and 0 (10th)
DIGIT_LABELS = {
    1: "Key 1 (Agent 1)",
    2: "Key 2 (Agent 2)",
    3: "Key 3 (Agent 3)",
    4: "Key 4 (Agent 4)",
    5: "Key 5 (Agent 5)",
    6: "Key 6 (Agent 6)",
    7: "Key 7 (Agent 7)",
    8: "Key 8 (Agent 8)",
    9: "Key 9 (Agent 9)",
    0: "Key 0 (Agent 10)",
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
                for line in f:
                    rec = json.loads(line)
                    if rec.get("type") == "USER_INPUT" or rec.get("role") == "user":
                        content = str(rec.get("content", "") or rec.get("text", "")).strip()
                        clean = re.sub(r"<[^>]+>", "", content).strip()
                        clean = " ".join(clean.split())
                        if clean:
                            return f"Prompt: {clean[:42]}..."
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

    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()

    # Find the most recent bridge start line
    start_idx = 0
    for idx, line in enumerate(lines):
        if "bridge started" in line:
            start_idx = idx

    slot_map: dict[int, str] = {}
    status_map: dict[str, str] = {}

    for line in lines[start_idx:]:
        if "loaded numeric task slots:" in line:
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

        # Check status updates
        match = re.search(r"status.*session=([0-9a-f-]+).*state=(\w+).*key=(\d)", line)
        if match:
            sid, state, key_str = match.groups()
            key_num = int(key_str)
            if state == "off":
                slot_map.pop(key_num, None)
            else:
                slot_map[key_num] = sid
                status_map[sid] = state

        # Check reconciled terminal state updates
        match_rec = re.search(r"reconciled session=([0-9a-f-]+) state=(\w+) key=(\d)", line)
        if match_rec:
            sid, state, key_str = match_rec.groups()
            key_num = int(key_str)
            if state == "off":
                slot_map.pop(key_num, None)
            else:
                slot_map[key_num] = sid
                status_map[sid] = state

        # Check window/process terminations
        m_close = re.search(r"session ([0-9a-f-]+).*turned off slot (\d+)", line)
        if m_close:
            sid, slot_str = m_close.groups()
            for k, v in list(slot_map.items()):
                if v == sid:
                    slot_map.pop(k, None)

    print(f"\n{'Key / Recency':<24} | {'Status':<10} | {'Workspace / Info':<48} | {'Conversation ID'}")
    print("-" * 120)

    # Display in logical keypad key order: Key 1..9, Key 0
    ordered_keys = (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)
    for digit in ordered_keys:
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
