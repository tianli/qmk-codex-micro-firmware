param()

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $RepoRoot '.venv\Scripts\pythonw.exe'
$Bridge = Join-Path $RepoRoot 'host\mf17_bridge.py'
$Startup = [Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $Startup 'MF17 Codex Bridge.lnk'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Create .venv and install host/requirements.txt first. Missing: $Python"
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Python
$Shortcut.Arguments = '"' + $Bridge + '"'
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description = 'MF17 Codex Micro bridge'
$Shortcut.Save()

Write-Output "Installed startup shortcut: $ShortcutPath"

