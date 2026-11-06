# Start the native Qdrant server for local development (Windows).
#   powershell -ExecutionPolicy Bypass -File infrastructure\qdrant\start-qdrant.ps1
# Dashboard: http://localhost:6333/dashboard
#
# Data lives in %LOCALAPPDATA%\DocuMind\qdrant by default. qdrant.exe is not long-path aware, and
# its segment directories nest deeply, so a short base path keeps every file under Windows' 260-char
# limit. Override with QDRANT__STORAGE__STORAGE_PATH / QDRANT__STORAGE__SNAPSHOTS_PATH.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $here "bin\qdrant.exe"
if (-not (Test-Path $exe)) {
    throw "Qdrant binary not found at $exe. Download qdrant-x86_64-pc-windows-msvc.zip (v1.20.0) from https://github.com/qdrant/qdrant/releases and extract it into infrastructure\qdrant\bin."
}

$dataRoot = Join-Path $env:LOCALAPPDATA "DocuMind\qdrant"
if (-not $env:QDRANT__STORAGE__STORAGE_PATH) { $env:QDRANT__STORAGE__STORAGE_PATH = Join-Path $dataRoot "storage" }
if (-not $env:QDRANT__STORAGE__SNAPSHOTS_PATH) { $env:QDRANT__STORAGE__SNAPSHOTS_PATH = Join-Path $dataRoot "snapshots" }
New-Item -ItemType Directory -Force $env:QDRANT__STORAGE__STORAGE_PATH, $env:QDRANT__STORAGE__SNAPSHOTS_PATH | Out-Null
Write-Host "Qdrant data: $env:QDRANT__STORAGE__STORAGE_PATH"

# Use the same API key as the app, if one is configured in the repo .env.
$envFile = Join-Path $here "..\..\.env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^\s*QDRANT_API_KEY\s*=\s*(\S+)' } | Select-Object -First 1
    if ($line -and $Matches[1]) { $env:QDRANT__SERVICE__API_KEY = $Matches[1] }
}

Set-Location $here
& $exe --config-path (Join-Path $here "local.yaml")
