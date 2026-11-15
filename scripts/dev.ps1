# Starts the global explorer and the optional high-resolution site workflows.
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

# Determine Python executable
if (-not $env:SKYLINE_PYTHON) {
    if (Test-Path 'backend\.venv\Scripts\python.exe') {
        $env:SKYLINE_PYTHON = 'backend\.venv\Scripts\python.exe'
    } elseif (Test-Path '.venv\Scripts\python.exe') {
        $env:SKYLINE_PYTHON = '.venv\Scripts\python.exe'
    } elseif (Test-Path 'scripts\.venv\Scripts\python.exe') {
        $env:SKYLINE_PYTHON = 'scripts\.venv\Scripts\python.exe'
    } else {
        $env:SKYLINE_PYTHON = 'python'
    }
}

# Check if moon.json exists
if (-not (Test-Path 'frontend\public\data\moon.json')) {
    Write-Host "ERROR: Prepare terrain first with: $($env:SKYLINE_PYTHON) scripts/build_moon.py"
    exit 1
}

# Verify dependencies
Write-Host "Checking dependencies..."
& $env:SKYLINE_PYTHON -c 'import uvicorn, multipart, rasterio, pyproj, certifi' 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Sync backend dependencies with: cd backend && uv sync"
    Write-Host "Or install requirements.txt into $($env:SKYLINE_PYTHON)"
    exit 1
}

$BACKEND_PORT = if ($env:SKYLINE_BACKEND_PORT) { $env:SKYLINE_BACKEND_PORT } else { 8077 }
$FRONTEND_PORT = if ($env:SKYLINE_FRONTEND_PORT) { $env:SKYLINE_FRONTEND_PORT } else { 5277 }
if (-not $env:WATCHFILES_FORCE_POLLING) {
    $env:WATCHFILES_FORCE_POLLING = 'true'
}

Write-Host "Starting backend on port $BACKEND_PORT..."
Write-Host "Starting frontend on port $FRONTEND_PORT..."
Write-Host ""

# Start backend in background
$backendProcess = Start-Process `
    -FilePath $env:SKYLINE_PYTHON `
    -ArgumentList "-m", "uvicorn", "app:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", $BACKEND_PORT.ToString(), "--reload", "--reload-dir", "backend" `
    -NoNewWindow `
    -PassThru

# Register cleanup on exit
$cleanupScript = {
    if ($backendProcess) {
        try { Stop-Process -Id $backendProcess.Id -ErrorAction SilentlyContinue }
        catch { }
    }
}
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action $cleanupScript

# Start frontend
$env:SKYLINE_API_TARGET = "http://127.0.0.1:$BACKEND_PORT"
Set-Location frontend
& npm run dev -- --port $FRONTEND_PORT

# Cleanup
& $cleanupScript
