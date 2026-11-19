# MF17 Codex / Antigravity Keypad Controller

This project turns the **MagicForce / KISNT MF17** (`VID 3A54`, `PID 4F5D`) into a dedicated physical control surface for **Codex and Antigravity** with **10 task status channels**. The bridge accepts both providers on the same UDP protocol and routes task selection to the appropriate desktop deep link. You can buy the hardware keyboard for ~$25 on Amazon, and spend another $15 for a set of translucent keycaps for lighting effects.

---

## Keypad Layout (10-Agent Configuration)

```text
+-------------------+-------------------+-------------------+-------------------+
|    Prev Window    |     Scroll Up     |    Scroll Down    |      Reject       |
|   (Key 'NumLk')   |     (Key '/')     |     (Key '*')     |     (Key '-')     |
+-------------------+-------------------+-------------------+-------------------+
|      Agent 7      |      Agent 8      |      Agent 9      |     New Chat      |
|     (Key '7')     |     (Key '8')     |     (Key '9')     |     (Key '+')     |
+-------------------+-------------------+-------------------+                   |
|      Agent 4      |      Agent 5      |      Agent 6      |                   |
|     (Key '4')     |     (Key '5')     |     (Key '6')     |                   |
+-------------------+-------------------+-------------------+-------------------+
|      Agent 1      |      Agent 2      |      Agent 3      |  Approve / Send   |
|     (Key '1')     |     (Key '2')     |     (Key '3')     |   (Key 'Enter')   |
+-------------------+-------------------+-------------------+                   |
|               Agent 0 / 10            |  Dictate (Hold)   |                   |
|                (Key '0')              |     (Key '.')     |                   |
+---------------------------------------+-------------------+-------------------+
```

### Controls

- **Agent 0 – 9** (Keys `0` through `9`): Key `1` – `9` and `0` (slot 10) select and focus the corresponding active agent conversation across local or remote Antigravity/Jetski (Desktop IDE, Chrome Cider tab, or iTerm2/Terminal CLI) and Codex sessions, even across different macOS Desktops/Spaces. When no live hook session is bound to a slot, `0` selects the most recent active conversation, `1` selects the next most recent, continuing through `9`. The physical LED under each numeric key reflects that task's live status.
- **Top Row Left (`Num Lock` / Top 1)**: **Prev Window** — switches back to your previously focused non-agent window or browser tab (across macOS Desktops/Spaces).
- **Top Row Mid-Left (`/` / Top 2)**: **Scroll Up** — context-aware Page Up (`Shift+Page Up` in terminal emulators; `Page Up` in desktop/browser views).
- **Top Row Mid-Right (`*` / Top 3)**: **Scroll Down** — context-aware Page Down (`Shift+Page Down` in terminal emulators; `Page Down` in desktop/browser views).
- **Top Row Right (`-` / Top 4)**: **Reject** (`Escape`) — declines tool execution, rejects diff/permission prompts, or dismisses dialogs.
- **New Chat** (`+` key): In Codex, opens the command palette's stable **New chat** command; in other hosts, sends `Cmd/Ctrl+Shift+L`.
- **Dictate (Hold)** (`.` key): **Hold-to-Talk Voice Dictation** — holding `.` starts macOS built-in Dictation (`Edit → Start Dictation`) in the active window (Chrome, Jetski, iTerm2, etc.) and releasing `.` automatically stops Dictation. In Codex (or when `MF17_VOICE_KEYS` is set), triggers the configured voice shortcut.
- **Approve / Send** (`Enter` key): **Approve** (`Enter`) to confirm tool permissions / diff prompts, or **Send** to submit the current prompt.

### Agent Status & Control Key LED Colors

- 🔵 **Blue** (`#304FFE`): Working / Generating / Thinking
- 🟠 **Amber** (`#FF6D00`): Waiting for user input / tool permission / diff approval
- 🟣 **Purple** (`#FF6D00`): External tool call
- 🟢 **Green** (`#00FF4C`): Turn complete
- 🔴 **Red** (`#FF0033`): Error
- ⚫ **Off**: Slot unassigned

The 7 outer control keys feature a low-power ambient backlight so they are easy to locate in the dark (**Reject** `-` glows dim red; the other 6 control keys glow dim emerald) while preserving instant white press-and-fade hardware feedback over 900 ms.

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

On Windows, if you launch the bridge from a Codex command runner, use the
interactive-desktop launcher so its key events reach the visible Codex or
Antigravity window:

```powershell
.\.venv\Scripts\python.exe .\scripts\run-bridge-interactive.py
```

To run automatically in the background at system boot:
```bash
./scripts/install-startup.sh
```

---

### 3b. macOS Menu Bar Colored Number Pills (With or Without Keypad)

`mf17_bridge.py` also exposes a local HTTP + Server-Sent Events (SSE) API on `http://127.0.0.1:41718` (`--http-port`, or `0` to disable), paired with a native Swift macOS Menu Bar app ([`host/menubar/AgentStatusBar.swift`](host/menubar/AgentStatusBar.swift)) that renders individual colored numbered pills (`1`–`9`, `0`) in the top-right menu bar:

- **With MF17 keypad (both LEDs + menu bar pills simultaneously)**:
  1. Run `./scripts/run-bridge.sh` as usual.
  2. Launch `./scripts/run-menubar.sh` to attach the menu bar pills to the running bridge.
- **Without MF17 keypad (menu bar pills only)**:
  ```bash
  ./scripts/run-menubar.sh
  ```
  If no bridge is listening on port `41718`, `run-menubar.sh` builds `AgentStatusBar.app` (if needed), automatically starts `mf17_bridge.py --no-hid`, and launches the menu bar app.

**Menu bar interactions:**
- **Left-click any pill**: immediately focuses that agent's Cider Chrome tab, Jetski IDE window, or iTerm2 pane (even across macOS Spaces).
- **Right-click (or Control/Option-click)**: opens the session menu showing workspace names, live status (including blocked subagent counts), per-slot Focus / Clear actions, Switch to Previous Window, Clear Completed Slots, Clear All Slots, and Quit.
- **Subagent badge**: a small corner dot appears on any pill with active subagents (and turns bright yellow while pulsing Amber if a subagent is blocked for user input).

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
