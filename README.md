# MF17 Codex / Antigravity Keypad Controller & macOS Menu Bar Status Pills

This project provides two live control surfaces for **Codex and Antigravity** (coding agents) with **10 task status channels** (usable together or independently):

1. **Physical Keypad**: Turns the **MagicForce / KISNT MF17** (`VID 3A54`, `PID 4F5D`) into a dedicated backlit control surface. You can buy the hardware keyboard for ~$25 on Amazon, and spend another $15 for a set of translucent keycaps for lighting effects.
2. **macOS Menu Bar Colored Number Pills** (no hardware required): A native Swift macOS menu bar app ([`AgentStatusBar.swift`](host/menubar/AgentStatusBar.swift)) that displays individual colored numbered pills (`1`–`9`, `0`) across every display's menu bar, with one-click window/tab switching across macOS Spaces.

The bridge accepts both providers (local and remote over UDP + TCP) and tracks both primary coding agents and spawned subagents, automatically keeping a slot Amber whenever the parent agent or any subagent is blocked waiting for user input.

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

- **Agent 0 – 9** (Keys `0` through `9`): Key `1` – `9` and `0` (slot 10) select and focus the corresponding active coding agent conversation across local or remote Antigravity (Desktop IDE, browser IDE tab, or iTerm2/Terminal CLI) and Codex sessions, even across different macOS Desktops/Spaces. When no live hook session is bound to a slot, `0` selects the most recent active conversation, `1` selects the next most recent, continuing through `9`. The physical LED under each numeric key reflects that task's live status.
- **Top Row Left (`Num Lock` / Top 1)**: **Prev Window** — switches back to your previously focused non-agent window or browser tab (across macOS Desktops/Spaces).
- **Top Row Mid-Left (`/` / Top 2)**: **Scroll Up** — context-aware Page Up (`Shift+Page Up` in terminal emulators; `Page Up` in desktop/browser views).
- **Top Row Mid-Right (`*` / Top 3)**: **Scroll Down** — context-aware Page Down (`Shift+Page Down` in terminal emulators; `Page Down` in desktop/browser views).
- **Top Row Right (`-` / Top 4)**: **Reject** (`Escape`) — declines tool execution, rejects diff/permission prompts, or dismisses dialogs.
- **New Chat** (`+` key): In Codex, opens the command palette's stable **New chat** command; in other hosts, sends `Cmd/Ctrl+Shift+L`.
- **Dictate (Hold)** (`.` key): **Hold-to-Talk Voice Dictation** — holding `.` starts macOS built-in Dictation (`Edit → Start Dictation`) in the active window (Chrome, Antigravity, iTerm2, etc.) and releasing `.` automatically stops Dictation. In Codex (or when `MF17_VOICE_KEYS` is set), triggers the configured voice shortcut.
- **Approve / Send** (`Enter` key): **Approve** (`Enter`) to confirm tool permissions / diff prompts, or **Send** to submit the current prompt.

### Agent Status Colors (Keypad LEDs & Menu Bar Pills)

- 🔵 **Blue** (`#304FFE`): Working / Generating / Thinking
- 🟠 **Amber** (`#FF6D00`): Waiting for user input / tool permission / diff approval *(pulses on menu bar pills; also triggered when any spawned subagent is blocked)*
- 🟣 **Purple** (`#B400FF`): External tool call / executing command
- 🟢 **Green** (`#00FF4C`): Turn complete
- 🔴 **Red** (`#FF0033`): Error
- ⚫ **Off**: Slot unassigned

The 7 outer control keys on the keypad feature a low-power ambient backlight so they are easy to locate in the dark (**Reject** `-` glows dim red; the other 6 control keys glow dim emerald) while preserving instant white press-and-fade hardware feedback over 900 ms.

---

## macOS Menu Bar Colored Number Pills

Don't have the MF17 keypad plugged in — or want on-screen status pills alongside the keypad? [`AgentStatusBar.swift`](host/menubar/AgentStatusBar.swift) renders **individual native `NSStatusItem` colored number pills** (`1`–`9`, `0`) in the macOS top-right menu bar:

- **Visible on every desktop & display**: Uses native `NSStatusItem` images so macOS replicates the pills across all monitors and Spaces in strict `1`–`9`, `0` order.
- **Left-click any pill**: Flashes briefly and switches straight to that coding agent's browser IDE tab, Antigravity Desktop window, or iTerm2 pane — even across different macOS Desktops/Spaces, identical to pressing the matching keypad key.
- **Hover tooltip**: Shows each pill's `[key] workspace — status (host app)` individually.
- **Right-click (or Control/Option-click)**: Opens the session dropdown menu:
  - Click any session row directly to focus it
  - **Clear Slot** submenu to dismiss individual slots
  - **Switch to Previous Window**, **Clear Completed Slots**, **Clear All Slots**, and **Quit**
- **Subagent indicator**: A corner dot appears on any pill with active subagents (and turns bright yellow while the pill pulses Amber if any subagent is waiting for approval/input).
- **Idle / disconnected state**: Displays a compact monochrome ring when no sessions are active (dashed while reconnecting to the bridge).

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

For Antigravity, install the lifecycle hooks into `~/.gemini/config/hooks.json`:

```bash
./scripts/install-antigravity-hooks.sh
```

For Codex, install the provider-tagged hooks into `~/.codex/hooks.json`:

```powershell
./scripts/install-global-hooks.ps1
```

Both hook adapters send status-only UDP/TCP packets to the bridge and preserve the
native approval/prompt behavior of their host application. Codex task IDs are
opened with `codex://threads/<id>`, while Antigravity task IDs route directly to
the corresponding desktop IDE window, browser IDE tab, or terminal session.

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

### 3b. Run the macOS Menu Bar App (With or Without Keypad)

`mf17_bridge.py` exposes a localhost HTTP + Server-Sent Events (SSE) API on `http://127.0.0.1:41718` (`--http-port`, or `0` to disable), which powers the native macOS menu bar pills:

- **With MF17 keypad (keypad LEDs + menu bar pills simultaneously)**:
  1. Start `./scripts/run-bridge.sh` as usual.
  2. Launch the menu bar pills in another command (or background):
     ```bash
     ./scripts/run-menubar.sh
     ```
- **Without MF17 keypad (software-only menu bar pills)**:
  ```bash
  ./scripts/run-menubar.sh
  ```
  If no bridge is listening on port `41718`, `run-menubar.sh` automatically compiles `host/menubar/AgentStatusBar.app` (using Xcode Command Line Tools `swiftc`), starts `mf17_bridge.py --no-hid`, and launches the menu bar app.

To rebuild the app bundle manually after editing `AgentStatusBar.swift`:
```bash
./scripts/build-menubar.sh
```

#### Local HTTP / SSE Status API (`127.0.0.1:41718`)
- `GET /health` — liveness check
- `GET /slots` — JSON snapshot of active slots, effective/parent status, subagent counts, workspace, and host app
- `GET /events` — live SSE stream pushing snapshots on every state change
- `POST /slots/<slot>/focus` & `POST /keys/<key>/focus` — focus the slot's window/tab
- `POST /slots/<slot>/clear`, `POST /slots/clear-completed`, `POST /slots/clear-all` — clear slots
- `POST /previous-window` — switch back to the previously active non-agent window

#### Remote Coding Agents over SSH
To forward status updates from a remote host to your local Mac bridge, add a reverse TCP forward in `~/.ssh/config`:
```sshconfig
Host <your-remote-host>
    RemoteForward 41717 127.0.0.1:41717
```
If the tunnel ever drops after sleep/wake on a `ControlMaster` session, re-attach it live without closing your SSH session:
```bash
ssh -O cancel -R 41717:127.0.0.1:41717 <your-remote-host>
ssh -O forward -R 127.0.0.1:41717:127.0.0.1:41717 <your-remote-host>
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
