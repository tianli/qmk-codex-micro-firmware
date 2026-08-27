param(
    [string]$Version = (Get-Date -Format 'yyyy-MM-dd')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$DistRoot = Join-Path $RepoRoot 'dist'
$PackageName = "mf17-codex-micro-windows11-$Version"
$StageRoot = Join-Path $DistRoot (".stage-{0}" -f $PID)
$PackageRoot = Join-Path $StageRoot $PackageName
$ZipPath = Join-Path $DistRoot "$PackageName.zip"

New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null

$ResolvedDist = [System.IO.Path]::GetFullPath($DistRoot).TrimEnd('\')
$ResolvedStage = [System.IO.Path]::GetFullPath($StageRoot)
if (-not $ResolvedStage.StartsWith($ResolvedDist + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe staging path outside dist: $ResolvedStage"
}

if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null

$RootFiles = @('.gitignore', 'README.md')
foreach ($RelativePath in $RootFiles) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot $RelativePath) -Destination $PackageRoot
}

$Directories = @('.codex', 'firmware', 'host', 'scripts', 'tests')
foreach ($Directory in $Directories) {
    $Destination = Join-Path $PackageRoot $Directory
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $RepoRoot $Directory) -Recurse -File -Force |
        Where-Object {
            $_.Name -notlike '*.log' -and
            $_.Extension -notin @('.pyc', '.pyo') -and
            $_.FullName -notmatch '[\\/]__pycache__[\\/]'
        } |
        ForEach-Object {
            $Relative = $_.FullName.Substring((Join-Path $RepoRoot $Directory).Length).TrimStart('\')
            $Target = Join-Path $Destination $Relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $Target
        }
}

$ManifestPath = Join-Path $PackageRoot 'SHA256SUMS.txt'
$ManifestLines = Get-ChildItem -LiteralPath $PackageRoot -Recurse -File |
    Where-Object { $_.FullName -ne $ManifestPath } |
    Sort-Object FullName |
    ForEach-Object {
        $Relative = $_.FullName.Substring($PackageRoot.Length + 1).Replace('\', '/')
        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        "$Hash  $Relative"
    }
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($ManifestPath, $ManifestLines, $Utf8NoBom)

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $PackageRoot -DestinationPath $ZipPath -CompressionLevel Optimal

$Archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
    $Forbidden = @($Archive.Entries | Where-Object {
        $_.FullName -match '(^|/)(\.git|\.venv|\.qmk-build|\.downloads|\.build-rgb|backups|__pycache__)(/|$)' -or
        $_.FullName -like '*.log' -or
        $_.FullName -like '*.pyc'
    })
    if ($Forbidden.Count -gt 0) {
        throw "Archive contains forbidden entries: $($Forbidden.FullName -join ', ')"
    }
    $EntryCount = $Archive.Entries.Count
} finally {
    $Archive.Dispose()
}

Remove-Item -LiteralPath $StageRoot -Recurse -Force
$ZipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash
Write-Output "Created $ZipPath"
Write-Output "Entries $EntryCount"
Write-Output "SHA-256 $ZipHash"
