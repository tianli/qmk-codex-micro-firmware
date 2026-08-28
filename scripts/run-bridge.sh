#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure virtualenv exists
if [ ! -d "$REPO_ROOT/.venv" ]; then
    echo "Creating Python virtual environment in .venv..."
    python3 -m venv "$REPO_ROOT/.venv"
    "$REPO_ROOT/.venv/bin/pip" install --upgrade pip
    "$REPO_ROOT/.venv/bin/pip" install -r "$REPO_ROOT/host/requirements.txt"
fi

PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
echo "Starting MF17 Jetski Bridge..."
exec "$PYTHON_BIN" "$REPO_ROOT/host/mf17_bridge.py" "$@"
