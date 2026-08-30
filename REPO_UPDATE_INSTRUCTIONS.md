# Updating `rekurin/qmk-codex-micro-firmware`

This export was prepared against target commit
`8ee63d0ad2a7068a43ebb406c526b82f3371546a` (`main`, 2026-08-28).
It contains a complete repository tree without `.git`, virtual environments,
build caches, logs, or local Codex session data.

## What this update changes

- Numeric keys follow recency in natural order: `0` is the most recent active
  task, `1` is the next most recent, continuing through `9`.
- Archived Codex tasks are excluded using Codex's local task database. Guardian,
  reviewer, voice-only, and subagent threads are excluded as well.
- Slot assignments are rebuilt every five seconds, so newly archived tasks are
  removed without restarting the bridge.
- Codex and Antigravity/Jetski remain supported by the same bridge.
- Page scrolling, desktop microphone shortcut handling, new-chat handling,
  prompt/waiting status lighting, and the per-key Raw HID lighting API are
  included.

## Add these files

```text
REPO_UPDATE_INSTRUCTIONS.md
docs/lighting-api.md
firmware/magic_force_mf17_codex_micro_10_agent_layout.bin
scripts/run-bridge-interactive.py
```

## Replace these files

```text
.codex/hooks.json
README.md
SHA256SUMS.txt
firmware/keymaps/codex_micro/keymap.c
firmware/magic_force_mf17_codex_micro.bin
host/actions.json
host/codex_status_hook.py
host/jetski_status_hook.py
host/lighting_api.py
host/mf17_bridge.py
scripts/create-release-zip.ps1
scripts/run-bridge.sh
scripts/set-lighting.py
tests/test_bridge.py
tests/test_firmware_layout.py
tests/test_hook.py
tests/test_jetski_transcript.py
tests/test_lighting_api.py
```

## Remove these files

None. Do not delete unrelated files or the target repository's `.git`
directory.

## Apply the export on Windows

1. Extract the ZIP. The directory containing this document is the package root.
2. Open PowerShell in the target repository and create a branch:

   ```powershell
   git switch -c codex/active-task-key-order
   ```

3. Copy the package contents into the target repository, merging directories
   and replacing files with the same paths. If the extracted package and target
   repository are siblings, this PowerShell performs that copy without deleting
   anything:

   ```powershell
   $PackageRoot = (Resolve-Path -LiteralPath '..\qmk-codex-micro-firmware-repo-update-2026-08-29').Path
   $TargetRepo = (Get-Location).Path
   Get-ChildItem -LiteralPath $PackageRoot -Force |
       Copy-Item -Destination $TargetRepo -Recurse -Force
   ```

4. Review the result:

   ```powershell
   git status --short
   git diff --check
   git diff --stat
   ```

5. Create the bridge environment and run the tests:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r host\requirements.txt
   .\.venv\Scripts\python.exe -m unittest discover -s tests
   ```

   The expected result for this export is `Ran 50 tests` followed by `OK`.

6. Verify the package files against `SHA256SUMS.txt`:

   ```powershell
   $Failed = @()
   Get-Content -LiteralPath SHA256SUMS.txt | ForEach-Object {
       if ($_ -match '^([0-9a-f]{64})  (.+)$') {
           $ExpectedHash = $Matches[1]
           $RelativePath = $Matches[2].Replace('/', '\')
           $ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $RelativePath).Hash.ToLowerInvariant()
           if ($ActualHash -ne $ExpectedHash) { $Failed += $RelativePath }
       }
   }
   if ($Failed.Count) { throw "Hash mismatch: $($Failed -join ', ')" }
   'All package hashes verified.'
   ```

7. Commit and push when the review is satisfactory:

   ```powershell
   git add --all
   git commit -m "Update MF17 active-task controls and bridge"
   git push -u origin codex/active-task-key-order
   ```

## Run the updated bridge

For a visible Windows desktop session, use:

```powershell
.\.venv\Scripts\python.exe .\scripts\run-bridge-interactive.py
```

The bridge log should report `loaded numeric task slots` with `0=<most recent
task ID>` and should then report `connected to MF17 Raw HID`.

## Firmware guidance

The `0`-through-`9` recency order and archived-task filtering are bridge-side
changes, so a keypad already running the included 10-agent Raw HID v2 firmware
does not need to be reflashed. For an older keypad, use
`firmware/magic_force_mf17_codex_micro.bin`. Hold the physical top-right key and
the tall `+` key together for three seconds to enter DFU, then flash only after
confirming the expected MF17/APM32 DFU device.

