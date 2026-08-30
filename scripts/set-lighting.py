#!/usr/bin/env python3
"""Control MF17 per-key lighting through the running local bridge."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from host.lighting_api import KEY_NAMES, LightingClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    key_parser = subparsers.add_parser("key", help="set one logical key")
    key_parser.add_argument("key", help="physical label/action alias or 0-based key ID")
    key_parser.add_argument("rgb", type=int, nargs=3, metavar=("R", "G", "B"))

    all_parser = subparsers.add_parser("all", help="set every key")
    all_parser.add_argument("rgb", type=int, nargs=3, metavar=("R", "G", "B"))

    clear_parser = subparsers.add_parser("clear", help="clear one key or all API overrides")
    clear_parser.add_argument("key", nargs="?")

    subparsers.add_parser("list", help="list canonical logical key IDs")
    args = parser.parse_args()
    client = LightingClient()

    if args.command == "key":
        key: str | int = int(args.key) if args.key.isdecimal() and int(args.key) >= 10 else args.key
        client.set_key(key, args.rgb)
    elif args.command == "all":
        client.set_all(args.rgb)
    elif args.command == "clear":
        if args.key is None:
            client.clear_all()
        else:
            client.clear_key(args.key)
    else:
        for key_id, name in enumerate(KEY_NAMES):
            print(f"{key_id:2}: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
