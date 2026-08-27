param(
    [string]$Backup = (Join-Path $PSScriptRoot '..\backups\mf17-factory-apm32-2026-08-04.bin'),
    [string]$DfuUtil = (Join-Path $PSScriptRoot '..\.downloads\dfu-util-0.11\dfu-util-0.11-binaries\win64\dfu-util.exe'),
    [switch]$IUnderstandThisWritesFirmware
)

$ErrorActionPreference = 'Stop'
$ExpectedSha256 = 'FA73DA21FE313F4E3D9B78E6BD3F46069B1AC8FFF83B13D78B7CE2A6C85897C5'
$ExpectedLength = 131072

if (-not $IUnderstandThisWritesFirmware) {
    throw 'Restore not started. Rerun with -IUnderstandThisWritesFirmware after entering APM32 DFU mode.'
}

$Backup = (Resolve-Path -LiteralPath $Backup).Path
$DfuUtil = (Resolve-Path -LiteralPath $DfuUtil).Path
$backupItem = Get-Item -LiteralPath $Backup
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Backup).Hash

if ($backupItem.Length -ne $ExpectedLength) {
    throw "Factory backup length is $($backupItem.Length), expected $ExpectedLength bytes."
}

if ($actualHash -ne $ExpectedSha256) {
    throw "Factory backup hash mismatch. Expected $ExpectedSha256, got $actualHash."
}

$devices = (& $DfuUtil -l 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $devices -notmatch '\[314b:0106\]') {
    throw 'APM32 DFU device 314B:0106 is not connected. Hold physical - and + to enter recovery mode.'
}

Write-Host 'Restoring the verified 128 KiB factory image to APM32 internal flash...'
& $DfuUtil -d 314b:0106 -a 0 -s 0x08000000:leave -D $Backup
if ($LASTEXITCODE -ne 0) {
    throw "Factory restore failed with exit code $LASTEXITCODE. Keep the keypad in DFU mode and do not unplug it."
}

Write-Host 'Factory firmware restored. Unplug and reconnect the keypad if it did not restart automatically.'
