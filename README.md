# MF17 Codex / Antigravity Keypad Controller

This project turns the **MagicForce / KISNT MF17** (`VID 3A54`, `PID 4F5D`) into a dedicated physical control surface for **Codex and Antigravity** with **10 task status channels**. The bridge accepts both providers on the same UDP protocol and routes task selection to the appropriate desktop deep link.

---

## Keypad Layout (10-Agent Configuration)

```text
+-------------------+-------------------+-------------------+-------------------+
|      Approve      |      Reject       |      Page Up      |     Page Down     |
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

- **Agent 1 – 9, Agent 0** (Keys `1` through `9`, and Key `0`): Selects and focuses that conversation in Codex or Antigravity. The physical LED under each numeric key reflects that task's live status.
- **Top Row Left (Top 1)**: **Approve** (`Enter`) to confirm tool permissions or choices.
- **Top Row Mid-Left (Top 2)**: **Reject** (`Escape`) to decline tool execution or dismiss prompts.
- **Top Row Mid-Right (Top 3)**: Context-aware Page Up. The bridge detects a terminal foreground window and sends `Shift+Page Up`; in the Codex desktop thread it sends plain `Page Up`.
- **Top Row Right (Top 4)**: Context-aware Page Down, using the matching terminal (`Shift+Page Down`) or desktop (`Page Down`) behavior.
- **New Chat** (`+` key): In Codex, opens the command palette's stable **New chat** command; other supported hosts retain the legacy `Cmd/Ctrl+Shift+L` fallback.
- **Dictate / Mic** (`.` key): Sends the configured desktop dictation shortcut, or `m` when a terminal/TUI is focused. Set `MF17_MICROPHONE_KEYS` to a comma-separated shortcut (for example `CTRL_OR_CMD,SHIFT,D`) if you changed Codex's shortcut in Settings. Set `MF17_SCROLL_CONTEXT=terminal` or `desktop` to override detection when an embedded terminal does not expose a distinct window title.
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

### 2. Enable lifecycle hooks

For Antigravity/Jetski, install the lifecycle hooks into `~/.gemini/config/hooks.json`:

```bash
./scripts/install-jetski-hooks.sh
```

For Codex, install the provider-tagged hooks into `~/.codex/hooks.json`:

```powershell
./scripts/install-global-hooks.ps1
```

Both hook adapters send status-only UDP packets to the bridge and preserve the
native approval/prompt behavior of their host application. Codex task IDs are
opened with `codex://threads/<id>`; Antigravity task IDs use
`jetski-ide://conversation/<id>`.

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

### 5. Per-key lighting API

The firmware exposes a versioned Raw HID v2 API that can override the RGB
backlight on every physical key, including Agent keys. The bridge accepts
logical key names and maps them to the correct MF17 LED automatically:

```bash
python scripts/set-lighting.py key agent_1 255 0 0
python scripts/set-lighting.py key approve 0 255 0
python scripts/set-lighting.py all 32 0 80
python scripts/set-lighting.py clear
```

Existing bridges remain compatible with the original LED-index commands. The
new bridge capability-probes the firmware and falls back to those messages
when it connects to an older image. See
[`docs/lighting-api.md`](docs/lighting-api.md) for the JSON and Raw HID APIs,
logical key IDs, batching, acknowledgements, and precedence rules.

---

## Building & Flashing Firmware

The source code for the 10-agent keymap is in [`firmware/keymaps/codex_micro/keymap.c`](firmware/keymaps/codex_micro/keymap.c).

The included v2 API image is
[`firmware/magic_force_mf17_codex_micro.bin`](firmware/magic_force_mf17_codex_micro.bin).
It is 47,024 bytes with SHA-256:

```text
1DEC2693839C97836BCAC9147952359ECA29E33E0D90176D36E8FA141122FF1C
```

To compile and flash using QMK:

```bash
# Compile
./scripts/build-firmware.sh ~/qmk_firmware compile

# Flash
./scripts/build-firmware.sh ~/qmk_firmware flash
```

*Recovery bootloader reset: Hold `Down` (Top-Right) + `+` (New Chat) together for 3 seconds to enter DFU mode.*
