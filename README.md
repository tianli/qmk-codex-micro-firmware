# MF17 Codex Micro preset

This project turns the **MagicForce/KISNT MF17** (`VID 3A54`, `PID 4F5D`) into a
Codex Micro-inspired control surface. It has three parts:

1. a QMK keymap with bidirectional Raw HID and complete per-key RGB control;
2. a small Windows bridge that turns MF17 actions into ChatGPT/Codex commands;
3. global Codex lifecycle hooks that send live task state to the six Agent Key
   LEDs while leaving approval prompts owned by Codex's native UI.

The connected keypad in this workspace was positively identified by Windows as
`MF17`, USB `3A54:4F5D`. Its factory bootloader identifies as **APM32 DFU ISP
Mode**, USB `314B:0106`, and exposes 128 KiB of internal flash. Do not flash this
build onto a different 17-key keypad.

## Layout

```text
+----------+----------+----------+----------+
| Agent 1  | Agent 2  | Agent 3  | Agent 4  |
+----------+----------+----------+----------+
| Agent 5  |   UP 8   | Agent 6  | Fast     |
+----------+----------+----------+          |
|  LEFT 4  | Command  | RIGHT 6  |          |
+----------+----------+----------+----------+
| Approve  |  DOWN 2  | Decline  | Send     |
+----------+----------+----------+          |
| Continue/new chat   | Dictate  |          |
+---------------------+----------+----------+
```

The four requested joystick directions match the Codex Micro defaults:

- `8` / up: toggle Plan mode
- `6` / right: navigate forward
- `2` / down: toggle the sidebar
- `4` / left: navigate back

The six Agent Keys track the six most recently active Codex tasks. Each task's
light is physically aligned with its Agent key: `Num Lock`, `/`, `*`, `-`, `7`,
and `9` are Agents 1 through 6. Their colors use the exact Codex Micro status
codes: white idle (`#FFFFFF`), blue working (`#304FFE`), green unread/complete
(`#00FF4C`), amber awaiting approval/response (`#FF6D00`), red error
(`#FF0033`), and off when unassigned. Pressing one opens its task with the
documented `codex://threads/<thread-id>` deep link.

The other eleven keys provide local press feedback: the pressed key lights
white immediately, then fades smoothly to its normal off state over 900 ms.
This feedback is rendered by the firmware and does not depend on the bridge;
Agent status colors retain priority on the six Agent keys.

## 1. Build and flash QMK

The open-source board support is upstream in
[`qmk/qmk_firmware/keyboards/magic_force/mf17`](https://github.com/qmk/qmk_firmware/tree/master/keyboards/magic_force/mf17).
It targets the STM32F072 and its 17 WS2812 LEDs.

From PowerShell, with a working QMK checkout:

```powershell
.\scripts\build-firmware.ps1 -QmkHome C:\path\to\qmk_firmware
```

The script copies this preset into the QMK checkout and runs:

```text
qmk compile -kb magic_force/mf17 -km codex_micro
```

The KISNT factory firmware enters DFU without opening the case: hold the
physical **`-`** key and tap the tall **`+`** key. The LEDs can remain blue even
though the keyboard interface has stopped. Windows should then identify
`APM32 DFU ISP Mode` (`314B:0106`). QMK requires the WinUSB driver for this
device.

The Codex Micro firmware preserves a protected recovery path. Hold the same
physical **`-` + `+`** positions for three seconds; all LEDs fill blue while the
timer advances, then the keypad enters APM32 DFU. The keymap selects
`BOOTLOADER = apm32-dfu`, so `qmk flash` uses the correct APM32 identity.

## Factory backup and undo

A verified, device-specific factory backup is stored locally at
`backups/mf17-factory-apm32-2026-08-04.bin`. It is 131,072 bytes with SHA-256:

```text
FA73DA21FE313F4E3D9B78E6BD3F46069B1AC8FFF83B13D78B7CE2A6C85897C5
```

The separate 16-byte option-byte read is stored beside it, but normal firmware
restore does not write option bytes. Both the backup and downloaded tools are
excluded from Git because the image is specific to this physical keypad.

To undo the custom flash, enter APM32 DFU and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\restore-stock.ps1 -IUnderstandThisWritesFirmware
```

The script refuses to write unless the backup has the exact expected length
and hash and USB device `314B:0106` is connected.

A verified build is included at
[`firmware/magic_force_mf17_codex_micro.bin`](firmware/magic_force_mf17_codex_micro.bin).
It was compiled from QMK commit `54881c6845b3c0df101f72fcef2394d62e1a0143`,
is 41,624 bytes, and has SHA-256
`839ACF1ACC729D6B56AC05C5D6D81F6EC2F2704FAD5F49C3C30CC7D847C4E08A`.

The image for this KISNT unit, including the corrected oversized-key matrix and
exact Codex Micro status colors, is
[`firmware/magic_force_mf17_codex_micro_matrixfix_status.bin`](firmware/magic_force_mf17_codex_micro_matrixfix_status.bin).
It is 41,624 bytes with SHA-256:

```text
956DF2118702A63002BD537708C7D31758E988522E38CD0DD210B3FC9FC70855
```

The earlier RGB startup image forces QMK's RGB render pass on at every boot so
stale RGB EEPROM state cannot suppress Agent indicators:
[`firmware/magic_force_mf17_codex_micro_matrixfix_status_rgbfix.bin`](firmware/magic_force_mf17_codex_micro_matrixfix_status_rgbfix.bin).
It is 41,664 bytes with SHA-256:

```text
9BA38FA365815F0C4501ECF9F11F410E0116BAC9F1A1E45256D5AAB92E42187F
```

The direct-renderer image removes that dependency entirely. It disables QMK's effect
renderer and writes the host/status colors directly to the WS2812 buffer from
the post-task housekeeping phase, so disabled or stale RGB EEPROM state cannot
prevent updates:
[`firmware/magic_force_mf17_codex_micro_direct_rgb.bin`](firmware/magic_force_mf17_codex_micro_direct_rgb.bin).
It is 46,196 bytes with SHA-256:

```text
020E8A25A609EE3F2A97F567B32776A9F22E0CA3FA47F213732E7A5499EC2522
```

The current candidate adds the 900 ms fading white press feedback for all
eleven non-Agent keys on top of the direct renderer:
[`firmware/magic_force_mf17_codex_micro_press_fade.bin`](firmware/magic_force_mf17_codex_micro_press_fade.bin).
It is 46,388 bytes with SHA-256:

```text
EB4BE3652447AC0D94B0657E88272097F007D3301A1CDB8049BBC9DF194BD78F
```

`scripts/patch-kisnt-matrix.ps1`,
`scripts/patch-codex-micro-status-colors.ps1`, and
`scripts/patch-rgb-overlay-bootstrap.ps1` reproduce the guarded changes for the
older binary-patched images and refuse an unexpected input hash. Normal future
QMK builds use the direct renderer in `housekeeping_task_user()`.

## 2. Install and run the bridge

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r host\requirements.txt
.\.venv\Scripts\python.exe host\mf17_bridge.py
```

Leave the bridge running. For automatic startup, run:

```powershell
.\scripts\install-startup.ps1
```

The bridge communicates only with Raw HID usage page `0xFF60`, usage `0x61`,
on `3A54:4F5D`. Ordinary numpad input from other keyboards is untouched.

## 3. Enable the Codex hooks

Install the MF17 handlers alongside any existing user hooks:

```powershell
.\scripts\install-global-hooks.ps1
```

Restart Codex, run `/hooks`, review the changed user-level definition, and
trust it. Hooks send status only over `127.0.0.1:41717`. The
`PermissionRequest` hook immediately returns `{}` after lighting the task key
amber; it never chooses or waits for a decision. Codex therefore retains its
native approval popup and notification, and keypad `1`/`3` answer it with
Enter/Escape. A completed task turns green, a running task blue, a failure red,
an idle task white, and a closed task turns off.

`request_user_input` cards also turn the task amber until the user responds.
If Codex interrupts a turn without emitting `Stop`, the bridge reconciles the
local transcript's terminal marker: completed turns become green and aborted
turns return to idle white instead of remaining stale blue.

## Customizing actions

Edit [`host/actions.json`](host/actions.json). Plan mode uses `/plan` because
the current Windows app has no default keybinding for its internal Plan
command. Fast mode uses `/fast`. Continue runs `/fork`,
selects the second option, and confirms the normal same-worktree chat rather
than creating a new worktree. Approve and Decline invoke Codex's native
Enter/Escape approval bindings while its normal popup remains visible.

The bridge also accepts local UDP lighting commands, so scripts can control all
17 LEDs without changing firmware. For example:

```powershell
$udp = New-Object System.Net.Sockets.UdpClient
$bytes = [Text.Encoding]::UTF8.GetBytes('{"kind":"lighting","all":[32,0,80]}')
$udp.Send($bytes, $bytes.Length, '127.0.0.1', 41717)
$udp.Dispose()
```

Use `{"kind":"lighting","pixel":8,"rgb":[255,0,0]}` for one LED, or
`{"kind":"lighting","clear":true}` to return non-Agent LEDs to QMK's base
effect. Agent status colors remain the top overlay on their six assigned LEDs.

## Limits

This emulates the Micro workflow but does not impersonate Work Louder hardware.
The ChatGPT app reserves its native device settings and private HID integration
for supported Micro devices. The MF17 also has no analog stick, rotary encoder,
touch strip, Bluetooth, battery, or microphone. Its number keys act as four
digital joystick directions, and the host bridge supplies the missing app
integration.
