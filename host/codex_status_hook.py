"""Translate Codex lifecycle hook JSON into a local MF17 bridge status event.

Codex hooks must keep their native response contract, so this adapter emits an
empty JSON object after sending the best-effort UDP status packet. The bridge
uses the explicit ``provider`` field to route selected tasks through Codex's
``codex://threads/<id>`` URI scheme.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any

try:
    from host.jetski_status_hook import event_to_status
except ModuleNotFoundError:  # Support execution with this directory on sys.path.
    from jetski_status_hook import event_to_status  # type: ignore[no-redef]


HOST = "127.0.0.1"
PORT = 41717


def status_message(event: dict[str, Any], explicit_event: str | None = None) -> dict[str, Any]:
    """Build the provider-tagged UDP payload sent to the local bridge."""
    status, remove = event_to_status(event, explicit_event=explicit_event)
    session_id = str(
        event.get("session_id")
        or event.get("conversation_id")
        or event.get("conversationId")
        or "unknown"
    )
    transcript_path = str(event.get("transcript_path") or event.get("transcriptPath") or "")
    return {
        "kind": "codex_status",
        "provider": "codex",
        "session_id": session_id,
        "status": status,
        "remove": remove,
        "cwd": str(event.get("cwd") or ""),
        "transcript_path": transcript_path,
        "event": explicit_event or event.get("hook_event_name") or event.get("event") or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex MF17 lifecycle status hook")
    parser.add_argument("--event", type=str, default=None, help="Explicit event name")
    args, _ = parser.parse_known_args()

    payload: dict[str, Any] = {}
    if not sys.stdin.isatty():
        try:
            raw_input = sys.stdin.read()
            if raw_input.strip():
                decoded = json.loads(raw_input)
                if isinstance(decoded, dict):
                    payload = decoded
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            payload = {}

    message = status_message(payload, explicit_event=args.event)
    try:
        encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.sendto(encoded, (HOST, PORT))
    except OSError:
        pass

    # Preserve Codex's native hook contract; Codex expects a neutral {}.
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
