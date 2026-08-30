#!/usr/bin/env bash
set -euo pipefail

# Build and optionally flash the MF17 firmware using QMK CLI on macOS/Linux.

QMK_HOME="${1:-${QMK_HOME:-$HOME/qmk_firmware}}"
ACTION="${2:-compile}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/firmware/keymaps/codex_micro"
TARGET_DIR="$QMK_HOME/keyboards/magic_force/mf17/keymaps/codex_micro"

if [[ ! -d "$QMK_HOME" ]]; then
    echo "QMK firmware directory not found at: $QMK_HOME"
    echo "Usage: ./scripts/build-firmware.sh [/path/to/qmk_firmware] [compile|flash]"
    exit 1
fi

echo "Copying keymap files to $TARGET_DIR..."
mkdir -p "$TARGET_DIR"
cp -f "$SOURCE_DIR/keymap.c" "$TARGET_DIR/"
cp -f "$SOURCE_DIR/config.h" "$TARGET_DIR/"
cp -f "$SOURCE_DIR/rules.mk" "$TARGET_DIR/"

cd "$QMK_HOME"
if [[ "$ACTION" == "flash" ]]; then
    echo "Flashing firmware to MF17 keypad..."
    qmk flash -kb magic_force/mf17 -km codex_micro
else
    echo "Compiling firmware for MF17 keypad..."
    qmk compile -kb magic_force/mf17 -km codex_micro
fi
