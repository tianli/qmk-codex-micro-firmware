param(
    [string]$InputBin = (Join-Path $PSScriptRoot '..\firmware\magic_force_mf17_codex_micro_matrixfix_status.bin'),
    [string]$OutputBin = (Join-Path $PSScriptRoot '..\firmware\magic_force_mf17_codex_micro_matrixfix_status_rgbfix.bin')
)

$ErrorActionPreference = 'Stop'
$ExpectedHash = '956DF2118702A63002BD537708C7D31758E988522E38CD0DD210B3FC9FC70855'
$ExpectedLength = 0xA298
$CallsiteOffset = 0x1C4A
$ExpectedCallsite = [byte[]]@(0xFF, 0xF7, 0xFC, 0xFF)
$PatchedCallsite = [byte[]]@(0x08, 0xF0, 0x25, 0xFB)

# Thumb bootstrap assembled from scripts/rgb-overlay-bootstrap.S at 0x0800A298.
# It forces RGB Matrix on, selects SOLID_COLOR, sets the base brightness to
# zero, then returns to keyboard_post_init_kb. Agent indicators render above it.
$Bootstrap = [byte[]]@(
    0x10, 0xB5, 0x06, 0x4B, 0x98, 0x47, 0x01, 0x20,
    0x00, 0x21, 0x05, 0x4B, 0x98, 0x47, 0x00, 0x20,
    0x00, 0x21, 0x00, 0x22, 0x00, 0x23, 0x03, 0x4C,
    0xA0, 0x47, 0x10, 0xBD, 0x61, 0x4F, 0x00, 0x08,
    0xC9, 0x4F, 0x00, 0x08, 0x5D, 0x50, 0x00, 0x08
)

$InputBin = [System.IO.Path]::GetFullPath($InputBin)
$OutputBin = [System.IO.Path]::GetFullPath($OutputBin)
$ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $InputBin).Hash
if ($ActualHash -ne $ExpectedHash) {
    throw "Refusing to patch unexpected firmware. Expected $ExpectedHash, got $ActualHash"
}

$Bytes = [System.IO.File]::ReadAllBytes($InputBin)
if ($Bytes.Length -ne $ExpectedLength) {
    throw ('Refusing length 0x{0:X}; expected 0x{1:X}' -f $Bytes.Length, $ExpectedLength)
}

for ($Index = 0; $Index -lt $ExpectedCallsite.Length; $Index++) {
    $Offset = $CallsiteOffset + $Index
    if ($Bytes[$Offset] -ne $ExpectedCallsite[$Index]) {
        throw ('Unexpected callsite byte at 0x{0:X}: expected 0x{1:X2}, got 0x{2:X2}' -f $Offset, $ExpectedCallsite[$Index], $Bytes[$Offset])
    }
    $Bytes[$Offset] = $PatchedCallsite[$Index]
}

$Output = New-Object byte[] ($Bytes.Length + $Bootstrap.Length)
[Array]::Copy($Bytes, 0, $Output, 0, $Bytes.Length)
[Array]::Copy($Bootstrap, 0, $Output, $Bytes.Length, $Bootstrap.Length)
[System.IO.File]::WriteAllBytes($OutputBin, $Output)

$OutputHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputBin).Hash
Write-Output "Created $OutputBin"
Write-Output "Length $($Output.Length) bytes"
Write-Output "SHA-256 $OutputHash"
