#!/usr/bin/env bash
set -euo pipefail

# Automated setup for QMK CLI and firmware build for macOS/Linux

export PATH="$HOME/Library/Python/3.9/bin:$HOME/Library/Python/3.10/bin:$HOME/Library/Python/3.11/bin:$HOME/Library/Python/3.12/bin:$HOME/.local/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QMK_DIR="${QMK_HOME:-$HOME/qmk_firmware}"
SOURCE_DIR="$REPO_ROOT/firmware/keymaps/codex_micro"
TARGET_DIR="$QMK_DIR/keyboards/magic_force/mf17/keymaps/codex_micro"
ACTION="${1:-compile}" # 'compile' or 'flash'

echo "=== 1. Checking QMK CLI ==="
if ! command -v qmk &> /dev/null; then
    echo "Installing qmk via pip..."
    python3 -m pip install --user qmk
fi

echo "QMK CLI version: $(qmk --version)"

echo "=== 2. Checking ARM Toolchain ==="
if ! command -v arm-none-eabi-gcc &> /dev/null; then
    echo "Warning: arm-none-eabi-gcc is not installed on your system."
    echo "To compile ARM firmware on macOS, install the ARM toolchain via Homebrew:"
    echo "  brew install arm-none-eabi-gcc dfu-util"
    echo "Or download from: https://developer.arm.com/downloads/-/gnu-rm"
    echo ""
    if ! command -v brew &> /dev/null; then
        echo "Please install Homebrew (https://brew.sh) or the ARM toolchain to compile firmware."
        exit 1
    else
        echo "Attempting to install arm-none-eabi-gcc via Homebrew..."
        brew install arm-none-eabi-gcc dfu-util || true
    fi
fi

echo "=== 3. Checking QMK Firmware Repository ==="
if [[ ! -d "$QMK_DIR" ]]; then
    echo "Cloning qmk/qmk_firmware to $QMK_DIR..."
    git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/qmk/qmk_firmware.git "$QMK_DIR"
fi

# Clean any refstorage reftable lines from git config to maintain Apple git compatibility
python3 -c "
from pathlib import Path
qmk = Path('$QMK_DIR')
if qmk.exists():
    for config in qmk.rglob('config'):
        if config.is_file():
            txt = config.read_text(encoding='utf-8', errors='ignore')
            if 'refstorage' in txt:
                new_txt = '\n'.join([line for line in txt.splitlines() if 'refstorage' not in line and '[extensions]' not in line])
                config.write_text(new_txt)
"

echo "=== 4. Copying 10-Agent Keymap ==="
mkdir -p "$TARGET_DIR"
cp -f "$SOURCE_DIR/keymap.c" "$TARGET_DIR/"
cp -f "$SOURCE_DIR/config.h" "$TARGET_DIR/"
cp -f "$SOURCE_DIR/rules.mk" "$TARGET_DIR/"

cd "$QMK_DIR"

if [[ "$ACTION" == "flash" ]]; then
    echo "=== 5. Flashing Keypad ==="
    echo "Please ensure the keypad is plugged in and put into DFU mode."
    echo "(Hold Down/Top-Right and + together for 3 seconds, or reconnect while holding the boot key)"
    qmk flash -kb magic_force/mf17 -km codex_micro
else
    echo "=== 5. Compiling Firmware ==="
    qmk compile -kb magic_force/mf17 -km codex_micro
    echo ""
    echo "Compilation successful! Firmware binary generated in $QMK_DIR"
    echo "To flash to the keypad, run:"
    echo "  ./scripts/setup-qmk-and-build.sh flash"
fi
