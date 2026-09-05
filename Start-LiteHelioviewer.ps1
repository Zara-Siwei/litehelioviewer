$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:8765"
$version = "0.4.4"

Set-Location $root
$env:PYTHONIOENCODING = "utf-8"

function Test-Python([string]$exe) {
    if (-not $exe) { return $false }
    try {
        & $exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Python resolution order: explicit override, project venv, known local
# installs, then whatever "python" is on PATH.
$candidates = @()
if ($env:LITEHELIOVIEWER_PYTHON) { $candidates += $env:LITEHELIOVIEWER_PYTHON }
$candidates += Join-Path $root ".venv\Scripts\python.exe"
$candidates += "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe"
$pathPython = Get-Command python -ErrorAction SilentlyContinue
if ($pathPython) { $candidates += $pathPython.Source }

$python = $null
foreach ($candidate in $candidates) {
    if (Test-Python $candidate) { $python = $candidate; break }
}
if (-not $python) {
    Write-Host "Python 3.9+ was not found. Install Python, create a .venv next to this script,"
    Write-Host "or point LITEHELIOVIEWER_PYTHON at your interpreter."
    Read-Host "Press Enter to close this launcher"
    exit 1
}

& $python -c "import fastapi, uvicorn, astropy, PIL, requests, numpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python requirements (first run only)..."
    & $python -m pip install -r (Join-Path $root "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip install failed. Check your network connection and Python environment."
        Read-Host "Press Enter to close this launcher"
        exit 1
    }
}

Write-Host "LiteHelioviewer"
Write-Host "URL: $url"
Write-Host "Data: $root\data"
Write-Host "Python: $python"
Write-Host ""
Write-Host "This window is the backend. Close it to stop LiteHelioviewer."
Write-Host ""

$existing = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($existing.OwningProcess)" -ErrorAction SilentlyContinue
    $cmd = if ($owner) { [string]$owner.CommandLine } else { "" }
    Write-Host "Port 8765 is already in use by PID $($existing.OwningProcess)."
    Write-Host "Command: $cmd"

    $health = $null
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
    } catch {}

    $isLite = $cmd.Contains("litehelioviewer") -or ($health -and $health.name -eq "LiteHelioviewer")
    $isCurrent = $health -and $health.version -eq $version

    if ($isCurrent) {
        Write-Host "Current LiteHelioviewer $version is already running. Opening browser."
        Start-Process "$url/?v=$version"
        Read-Host "Press Enter to close this launcher"
        exit 0
    }

    if ($isLite) {
        Write-Host "Stopping stale LiteHelioviewer backend and starting $version..."
        Stop-Process -Id $existing.OwningProcess -Force
        Start-Sleep -Seconds 1
    } else {
        Write-Host "Another program is using port 8765. Stop that program or change LiteHelioviewer port."
        Read-Host "Press Enter to close this launcher"
        exit 1
    }
}

Start-Job -ScriptBlock {
    param($targetUrl, $targetVersion)
    Start-Sleep -Seconds 2
    Start-Process "$targetUrl/?v=$targetVersion"
} -ArgumentList $url, $version | Out-Null

& $python -m litehelioviewer_app.cli serve --host 127.0.0.1 --port 8765
