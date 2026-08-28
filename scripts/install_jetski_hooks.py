#!/usr/bin/env python3
"""Installer script for Jetski MF17 lifecycle hooks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def install_hooks(target_file: Path | None = None, python_exec: str | None = None) -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    hook_script = repo_root / "host" / "jetski_status_hook.py"

    if not hook_script.exists():
        raise FileNotFoundError(f"Missing hook script: {hook_script}")

    python_path = python_exec or sys.executable or "python3"

    if target_file is None:
        gemini_config_dir = Path.home() / ".gemini" / "config"
        gemini_config_dir.mkdir(parents=True, exist_ok=True)
        target_file = gemini_config_dir / "hooks.json"
    else:
        target_file.parent.mkdir(parents=True, exist_ok=True)

    config: dict = {}
    if target_file.exists():
        try:
            config = json.loads(target_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            config = {}

    hook_str = str(hook_script).replace("\\", "/")
    py_str = str(python_path).replace("\\", "/")

    def make_cmd(event: str) -> str:
        return f'"{py_str}" "{hook_str}" --event {event}'

    # Build the Jetski hook definition
    jetski_hook = {
        "PreInvocation": [
            {
                "type": "command",
                "command": make_cmd("PreInvocation"),
                "timeout": 3,
            }
        ],
        "PreToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": make_cmd("PreToolUse"),
                        "timeout": 3,
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": make_cmd("PostToolUse"),
                        "timeout": 3,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": make_cmd("Stop"),
                "timeout": 3,
            }
        ],
    }

    config["jetski-mf17-status"] = jetski_hook

    target_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Successfully installed MF17 lifecycle hooks into: {target_file}")
    return target_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Jetski MF17 status hooks")
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Custom path to hooks.json (defaults to ~/.gemini/config/hooks.json)",
    )
    parser.add_argument(
        "--python",
        type=str,
        default=None,
        help="Path to Python interpreter to use in hook commands",
    )
    args = parser.parse_args()

    try:
        install_hooks(target_file=args.target, python_exec=args.python)
        return 0
    except Exception as exc:
        print(f"Error installing hooks: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
