param(
    [Parameter(Mandatory = $true)]
    [string]$QmkHome,
    [switch]$Flash
)

$ErrorActionPreference = 'Stop'
$QmkHome = (Resolve-Path -LiteralPath $QmkHome).Path
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Source = Join-Path $RepoRoot 'firmware\keymaps\codex_micro'
$Target = Join-Path $QmkHome 'keyboards\magic_force\mf17\keymaps\codex_micro'

if (-not (Test-Path -LiteralPath (Join-Path $QmkHome 'keyboards\magic_force\mf17\keyboard.json'))) {
    throw "QMK checkout does not contain the upstream magic_force/mf17 target: $QmkHome"
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -LiteralPath (Join-Path $Source 'keymap.c') -Destination $Target -Force
Copy-Item -LiteralPath (Join-Path $Source 'config.h') -Destination $Target -Force
Copy-Item -LiteralPath (Join-Path $Source 'rules.mk') -Destination $Target -Force

Push-Location $QmkHome
try {
    if ($Flash) {
        qmk flash -kb magic_force/mf17 -km codex_micro
    } else {
        qmk compile -kb magic_force/mf17 -km codex_micro
    }
} finally {
    Pop-Location
}

