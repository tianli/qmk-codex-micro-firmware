#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$REPO_ROOT/host/menubar/AgentStatusBar.swift"
APP_DIR="$REPO_ROOT/host/menubar/AgentStatusBar.app"
BIN="$APP_DIR/Contents/MacOS/AgentStatusBar"
HTTP_PORT="${MF17_STATUS_HTTP_PORT:-41718}"

# Build app bundle if missing or source changed
if [ ! -x "$BIN" ] || [ "$SRC" -nt "$BIN" ]; then
    "$SCRIPT_DIR/build-menubar.sh"
fi

# Start the status daemon in --no-hid mode automatically if nothing is listening on HTTP_PORT yet
BRIDGE_PID=""
if ! curl -fsS "http://127.0.0.1:${HTTP_PORT}/health" >/dev/null 2>&1; then
    if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
        PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
    else
        PYTHON_BIN="$(which python3)"
    fi
    echo "No bridge detected on port ${HTTP_PORT}; starting status daemon (--no-hid)..."
    "$PYTHON_BIN" "$REPO_ROOT/host/mf17_bridge.py" --no-hid --http-port "$HTTP_PORT" &
    BRIDGE_PID=$!
    trap 'if [ -n "$BRIDGE_PID" ]; then kill "$BRIDGE_PID" 2>/dev/null || true; fi' EXIT INT TERM
    sleep 0.5
    if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
        echo "Failed to start status daemon on port ${HTTP_PORT}." >&2
        echo "If an older mf17_bridge.py is already running on UDP 41717, restart it first." >&2
        exit 1
    fi
fi

# Terminate any stale AgentStatusBar instance before launching the new one
pkill -f "AgentStatusBar.app/Contents/MacOS/AgentStatusBar" 2>/dev/null || true

echo "Launching macOS Menu Bar Agent Status pills (API: http://127.0.0.1:${HTTP_PORT})..."
if [ -n "$BRIDGE_PID" ]; then
    "$BIN" --port "$HTTP_PORT"
else
    open -n "$APP_DIR" --args --port "$HTTP_PORT"
fi
