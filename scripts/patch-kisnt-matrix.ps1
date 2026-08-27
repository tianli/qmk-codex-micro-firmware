param(
    [string]$InputFirmware = (Join-Path $PSScriptRoot '..\firmware\magic_force_mf17_codex_micro.bin'),
    [string]$OutputFirmware = (Join-Path $PSScriptRoot '..\firmware\magic_force_mf17_codex_micro_matrixfix.bin')
)

$ErrorActionPreference = 'Stop'
$ExpectedSha256 = '839ACF1ACC729D6B56AC05C5D6D81F6EC2F2704FAD5F49C3C30CC7D847C4E08A'
$KeymapOffset = 0x96CC

# Existing logical matrix from the verified build. Values are little-endian
# QMK keycodes; 0x0000 is KC_NO.
[byte[]]$ExpectedMatrix = @(
    0x40,0x7E, 0x41,0x7E, 0x42,0x7E, 0x43,0x7E,
    0x44,0x7E, 0x46,0x7E, 0x45,0x7E, 0x4B,0x7E,
    0x49,0x7E, 0x4A,0x7E, 0x47,0x7E, 0x00,0x00,
    0x4C,0x7E, 0x48,0x7E, 0x4D,0x7E, 0x50,0x7E,
    0x4E,0x7E, 0x00,0x00, 0x4F,0x7E, 0x00,0x00
)

# KISNT physical matrix. Move Fast (+), Continue (0), and Send (Enter) to
# [2][3], [4][1], and [4][3], respectively.
[byte[]]$PatchedMatrix = @(
    0x40,0x7E, 0x41,0x7E, 0x42,0x7E, 0x43,0x7E,
    0x44,0x7E, 0x46,0x7E, 0x45,0x7E, 0x00,0x00,
    0x49,0x7E, 0x4A,0x7E, 0x47,0x7E, 0x4B,0x7E,
    0x4C,0x7E, 0x48,0x7E, 0x4D,0x7E, 0x00,0x00,
    0x00,0x00, 0x4E,0x7E, 0x4F,0x7E, 0x50,0x7E
)

$InputFirmware = (Resolve-Path -LiteralPath $InputFirmware).Path
$actualHash = (Get-FileHash -LiteralPath $InputFirmware -Algorithm SHA256).Hash
if ($actualHash -ne $ExpectedSha256) {
    throw "Refusing to patch unexpected firmware: SHA-256 $actualHash"
}

$bytes = [IO.File]::ReadAllBytes($InputFirmware)
if ($bytes.Length -lt ($KeymapOffset + $ExpectedMatrix.Length)) {
    throw 'Firmware is too short to contain the expected key matrix.'
}

for ($i = 0; $i -lt $ExpectedMatrix.Length; $i++) {
    if ($bytes[$KeymapOffset + $i] -ne $ExpectedMatrix[$i]) {
        throw ('Key matrix mismatch at firmware offset 0x{0:X}: expected 0x{1:X2}, got 0x{2:X2}' -f ($KeymapOffset + $i), $ExpectedMatrix[$i], $bytes[$KeymapOffset + $i])
    }
    $bytes[$KeymapOffset + $i] = $PatchedMatrix[$i]
}

$OutputFirmware = [IO.Path]::GetFullPath($OutputFirmware)
[IO.File]::WriteAllBytes($OutputFirmware, $bytes)
$outputHash = (Get-FileHash -LiteralPath $OutputFirmware -Algorithm SHA256).Hash
Write-Output "Wrote $OutputFirmware"
Write-Output "SHA-256 $outputHash"
