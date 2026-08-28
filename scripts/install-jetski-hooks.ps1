param(
    [string]$Target = $null
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { 'python' }
$InstallerScript = Join-Path $RepoRoot 'scripts\install_jetski_hooks.py'

if ($Target) {
    & $Python $InstallerScript --target $Target --python $Python
} else {
    & $Python $InstallerScript --python $Python
}
