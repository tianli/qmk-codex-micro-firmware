#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Use active virtual environment python if present, otherwise python3
if [ -f "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
elif [ -f "$REPO_ROOT/.venv/bin/python3" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python3"
else
    PYTHON_BIN="$(which python3)"
fi

echo "Installing Jetski MF17 lifecycle hooks using Python: $PYTHON_BIN"
"$PYTHON_BIN" "$REPO_ROOT/scripts/install_jetski_hooks.py" --python "$PYTHON_BIN"
