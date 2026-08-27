param(
    [string]$InputBin = (Join-Path $PSScriptRoot '..\firmware\magic_force_mf17_codex_micro_matrixfix.bin'),
    [string]$OutputBin = (Join-Path $PSScriptRoot '..\firmware\magic_force_mf17_codex_micro_matrixfix_status.bin')
)

$ErrorActionPreference = 'Stop'
$ExpectedHash = '911519142349F86A098C62891F9743D8481A52ED1B719C19CD00C57458B3C486'
$ExpectedLength = 41624

$InputBin = [System.IO.Path]::GetFullPath($InputBin)
$OutputBin = [System.IO.Path]::GetFullPath($OutputBin)
$ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $InputBin).Hash
if ($ActualHash -ne $ExpectedHash) {
    throw "Refusing to patch unexpected firmware. Expected $ExpectedHash, got $ActualHash"
}

$Bytes = [System.IO.File]::ReadAllBytes($InputBin)
if ($Bytes.Length -ne $ExpectedLength) {
    throw "Refusing to patch firmware with length $($Bytes.Length); expected $ExpectedLength"
}

# Guarded substitutions in status_color(). Values are stored as Thumb MOVS
# immediates. The error green channel changes from MOVS r6,r4 to MOVS r6,#0.
$Patches = @(
    @{ Offset = 0x07D4; Before = @(0xFF);       After = @(0xFE) },
    @{ Offset = 0x07D6; Before = @(0x64);       After = @(0x4F) },
    @{ Offset = 0x07D8; Before = @(0x19);       After = @(0x30) },
    @{ Offset = 0x081C; Before = @(0x55);       After = @(0x4C) },
    @{ Offset = 0x0820; Before = @(0x14);       After = @(0x00) },
    @{ Offset = 0x0824; Before = @(0x14);       After = @(0x00) },
    @{ Offset = 0x0826; Before = @(0x91);       After = @(0x6D) },
    @{ Offset = 0x082C; Before = @(0x14);       After = @(0x33) },
    @{ Offset = 0x082E; Before = @(0x26, 0x00); After = @(0x00, 0x26) }
)

foreach ($Patch in $Patches) {
    for ($Index = 0; $Index -lt $Patch.Before.Count; $Index++) {
        $Offset = $Patch.Offset + $Index
        if ($Bytes[$Offset] -ne $Patch.Before[$Index]) {
            throw ('Unexpected byte at 0x{0:X}: expected 0x{1:X2}, got 0x{2:X2}' -f $Offset, $Patch.Before[$Index], $Bytes[$Offset])
        }
        $Bytes[$Offset] = $Patch.After[$Index]
    }
}

[System.IO.File]::WriteAllBytes($OutputBin, $Bytes)
$OutputHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputBin).Hash
Write-Output "Created $OutputBin"
Write-Output "SHA-256 $OutputHash"
