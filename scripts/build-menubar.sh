#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$REPO_ROOT/host/menubar/AgentStatusBar.swift"
APP_DIR="$REPO_ROOT/host/menubar/AgentStatusBar.app"
BIN="$APP_DIR/Contents/MacOS/AgentStatusBar"
PLIST="$APP_DIR/Contents/Info.plist"

mkdir -p "$APP_DIR/Contents/MacOS"

cat > "$PLIST" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>AgentStatusBar</string>
  <key>CFBundleIdentifier</key>
  <string>com.codexmicro.agentstatusbar</string>
  <key>CFBundleName</key>
  <string>Agent Status Bar</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

echo "Compiling $SRC -> $BIN ..."
swiftc -O -framework Cocoa "$SRC" -o "$BIN"
echo "Built $APP_DIR"
