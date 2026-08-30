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
BRIDGE_SCRIPT="$REPO_ROOT/host/mf17_bridge.py"

if [[ "$OSTYPE" == "darwin"* ]]; then
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_PATH="$PLIST_DIR/com.jetski.mf17bridge.plist"
    mkdir -p "$PLIST_DIR"

    cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.jetski.mf17bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$BRIDGE_SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$REPO_ROOT/host/mf17_bridge.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$REPO_ROOT/host/mf17_bridge.stderr.log</string>
    <key>WorkingDirectory</key>
    <string>$REPO_ROOT</string>
</dict>
</plist>
EOF

    # Load launch agent
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load "$PLIST_PATH"
    echo "Installed and started macOS LaunchAgent: $PLIST_PATH"
    echo "Check logs at: $REPO_ROOT/host/mf17_bridge.log"

elif [[ "$OSTYPE" == "linux"* ]]; then
    SERVICE_DIR="$HOME/.config/systemd/user"
    SERVICE_PATH="$SERVICE_DIR/mf17bridge.service"
    mkdir -p "$SERVICE_DIR"

    cat <<EOF > "$SERVICE_PATH"
[Unit]
Description=MF17 Jetski Keypad Bridge
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
ExecStart=$PYTHON_BIN $BRIDGE_SCRIPT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now mf17bridge.service
    echo "Installed and started systemd user service: $SERVICE_PATH"
fi
