#!/usr/bin/env python3
"""Clear specific or inactive Agent slots from the MF17 keypad."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import socket
import sys

UDP_HOST = "127.0.0.1"
UDP_PORT = 41717
LOG_PATH = Path(__file__).resolve().parent.parent / "host" / "mf17_bridge.log"


def send_remove_signal(session_id: str) -> None:
    message = {
        "kind": "jetski_status",
        "session_id": session_id,
        "status": "off",
        "remove": True,
    }
    encoded = json.dumps(message).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(encoded, (UDP_HOST, UDP_PORT))


def get_current_slots() -> list[str]:
    if not LOG_PATH.exists():
        return []
    recent_line = ""
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "loaded recent task slots:" in line:
                recent_line = line
    if not recent_line:
        return []
    raw_ids = recent_line.split("loaded recent task slots:")[-1].strip()
    return [s.strip() for s in raw_ids.split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove inactive conversation slots from the keypad.")
    parser.add_argument(
        "slots",
        type=int,
        nargs="*",
        help="Slot numbers to clear (e.g. 2 3 for Agent 2 and Agent 3)",
    )
    parser.add_argument(
        "--clean-stale",
        action="store_true",
        help="Remove stale/empty brain folders from ~/.gemini/jetski/brain",
    )
    args = parser.parse_args()

    current_slots = get_current_slots()
    brain_dir = Path.home() / ".gemini" / "jetski" / "brain"

    if args.slots:
        for slot_num in args.slots:
            idx = slot_num - 1
            if 0 <= idx < len(current_slots):
                sid = current_slots[idx]
                send_remove_signal(sid)
                print(f"Sent remove signal for Slot {slot_num} ({sid})")
            else:
                print(f"Slot {slot_num} is not currently assigned.")

    if args.clean_stale and brain_dir.exists():
        for d in brain_dir.iterdir():
            if d.is_dir():
                transcript = d / ".system_generated" / "logs" / "transcript.jsonl"
                if not transcript.exists() or transcript.stat().st_size == 0:
                    send_remove_signal(d.name)
                    try:
                        shutil.rmtree(d)
                        print(f"Cleaned up empty session folder: {d.name}")
                    except Exception as e:
                        print(f"Could not remove {d.name}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
