# MF17 Jetski Keypad Controller

This project turns the **MagicForce / KISNT MF17** (`VID 3A54`, `PID 4F5D`) into a dedicated physical control surface for **Jetski** with **10 Agent status channels**.

---

## Keypad Layout (10-Agent Configuration)

```text
+-------------------+-------------------+-------------------+-------------------+
|      Approve      |      Reject       |        UP         |       DOWN        |
|      (Top 1)      |      (Top 2)      |      (Top 3)      |      (Top 4)      |
+-------------------+-------------------+-------------------+-------------------+
|      Agent 7      |      Agent 8      |      Agent 9      |     New Chat      |
|     (Key '7')     |     (Key '8')     |     (Key '9')     |     (Key '+')     |
+-------------------+-------------------+-------------------+                   |
|      Agent 4      |      Agent 5      |      Agent 6      |                   |
|     (Key '4')     |     (Key '5')     |     (Key '6')     |                   |
+-------------------+-------------------+-------------------+-------------------+
|      Agent 1      |      Agent 2      |      Agent 3      |       Send        |
|     (Key '1')     |     (Key '2')     |     (Key '3')     |   (Key 'Enter')   |
+-------------------+-------------------+-------------------+                   |
|               Agent 0 / 10            |     Dictate       |                   |
|                (Key '0')              |    (Key '.')      |                   |
+---------------------------------------+-------------------+-------------------+
```

### Controls

- **Agent 1 – 9, Agent 0** (Keys `1` through `9`, and Key `0`): Selects and focuses that conversation in Jetski. The physical LED under each numeric key reflects that task's live status.
- **Top Row Left (Top 1)**: **Approve** (`Enter`) to confirm tool permissions or choices.
- **Top Row Mid-Left (Top 2)**: **Reject** (`Escape`) to decline tool execution or dismiss prompts.
- **Top Row Mid-Right (Top 3)**: **UP Arrow** (`Up`) to navigate selection menus.
- **Top Row Right (Top 4)**: **DOWN Arrow** (`Down`) to navigate selection menus.
- **New Chat** (`+` key): Start a new conversation in Jetski (`Cmd+Shift+L`).
- **Dictate / Mic** (`.` key): Activate microphone / dictation (`Cmd+Shift+D`).
- **Send** (`Enter` key): Submit the current prompt or input (`Enter`).

### Agent Status LED Colors

- ⚪ **White** (`#FFFFFF`): Idle
- 🔵 **Blue** (`#304FFE`): Working / Generating / Thinking
- 🟠 **Amber** (`#FF6D00`): Waiting for user input / tool permission
- 🟢 **Green** (`#00FF4C`): Turn complete
- 🔴 **Red** (`#FF0033`): Error
- ⚫ **Off**: Slot unassigned

Non-agent control keys feature instant hardware press feedback: pressing any control key illuminates it white and smoothly fades over 900 ms.

---

## Installation & Quick Start

### 1. Requirements

Ensure you have Python 3.9+ installed:

```bash
# Set up Python virtual environment and install dependencies
python3 -m venv .venv
./.venv/bin/pip install -r host/requirements.txt
```

---

### 2. Enable Jetski Lifecycle Hooks

Install the lifecycle hooks into your global Jetski configuration (`~/.gemini/config/hooks.json`):

```bash
./scripts/install-jetski-hooks.sh
```

---

### 3. Run the Bridge

```bash
./scripts/run-bridge.sh
```

To run automatically in the background at system boot:
```bash
./scripts/install-startup.sh
```

---

### 4. Utilities

- **Check active conversation slots**:
  ```bash
  python3 scripts/list-slots.py
  ```
- **Clear inactive slots on demand**:
  ```bash
  python3 scripts/clear-slots.py 2 3
  ```

---

## Building & Flashing Firmware

The source code for the 10-agent keymap is in [`firmware/keymaps/codex_micro/keymap.c`](firmware/keymaps/codex_micro/keymap.c).

To compile and flash using QMK:

```bash
# Compile
./scripts/build-firmware.sh ~/qmk_firmware compile

# Flash
./scripts/build-firmware.sh ~/qmk_firmware flash
```

*Recovery bootloader reset: Hold `Down` (Top-Right) + `+` (New Chat) together for 3 seconds to enter DFU mode.*
