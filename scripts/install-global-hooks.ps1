param()

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$HookScript = Join-Path $RepoRoot 'host\codex_status_hook.py'
$GlobalHooks = Join-Path $env:USERPROFILE '.codex\hooks.json'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing bridge Python runtime: $Python"
}

if (Test-Path -LiteralPath $GlobalHooks) {
    $Config = Get-Content -Raw -LiteralPath $GlobalHooks | ConvertFrom-Json
} else {
    $Config = [pscustomobject]@{ hooks = [pscustomobject]@{} }
}

if (-not $Config.PSObject.Properties['hooks']) {
    $Config | Add-Member -NotePropertyName hooks -NotePropertyValue ([pscustomobject]@{})
}

$EscapedPython = $Python.Replace("'", "''")
$EscapedHook = $HookScript.Replace("'", "''")
$CommandWindows = "powershell.exe -NoProfile -Command `"& '$EscapedPython' '$EscapedHook'`""
$Command = 'python3 "' + $HookScript.Replace('\', '/') + '"'
$Events = @('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop', 'SessionEnd')

# Remove any prior MF17 PermissionRequest handler before adding the current
# non-blocking, status-only version below. Codex still owns the native prompt.
$PermissionProperty = $Config.hooks.PSObject.Properties['PermissionRequest']
if ($PermissionProperty) {
    $RemainingPermissionHooks = @($PermissionProperty.Value) | Where-Object {
        $Commands = @($_.hooks) | ForEach-Object { $_.commandWindows }
        -not ($Commands -match 'host\\codex_status_hook\.py')
    }
    if ($RemainingPermissionHooks.Count -gt 0) {
        $PermissionProperty.Value = $RemainingPermissionHooks
    } else {
        $Config.hooks.PSObject.Properties.Remove('PermissionRequest')
    }
}

foreach ($EventName in $Events) {
    $Existing = @()
    $Property = $Config.hooks.PSObject.Properties[$EventName]
    if ($Property) {
        $Existing = @($Property.Value) | Where-Object {
            $Commands = @($_.hooks) | ForEach-Object { $_.commandWindows }
            -not ($Commands -match 'host\\codex_status_hook\.py')
        }
    }

    $Handler = [ordered]@{
        type = 'command'
        command = $Command
        commandWindows = $CommandWindows
        timeout = 3
    }
    $Group = [pscustomobject]@{ hooks = @([pscustomobject]$Handler) }
    $Combined = @($Existing) + @($Group)

    if ($Property) {
        $Property.Value = $Combined
    } else {
        $Config.hooks | Add-Member -NotePropertyName $EventName -NotePropertyValue $Combined
    }
}

$Json = $Config | ConvertTo-Json -Depth 20
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($GlobalHooks, $Json, $Utf8NoBom)
Write-Output "Installed MF17 lifecycle hooks in $GlobalHooks"
