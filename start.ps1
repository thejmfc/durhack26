$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

function Test-PythonDependencies {
    $ErrorActionPreference = 'Continue'
    $output = & $env:SKYLINE_PYTHON -c 'import numpy, numba, rasterio, certifi, pyproj, fastapi, uvicorn, multipart, PIL, scipy, matplotlib' 2>&1
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "Python dependency check failed: $($output -join "`n")"
    return $false
}

try {
    foreach ($command in @('node', 'npm')) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "Could not find $command. Install Python 3.13+ and Node.js 20+ and reopen PowerShell."
        }
    }
    & node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 20 or newer is required.' }

    Write-Host "`n[1/4] Checking Python dependencies..."
    if (-not $env:SKYLINE_PYTHON) {
        if (-not (Test-Path '.venv\Scripts\python.exe')) {
            if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
                throw 'Could not find Python. Install Python 3.13+ and reopen PowerShell.'
            }
            & python -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
        }
        $env:SKYLINE_PYTHON = Join-Path $root '.venv\Scripts\python.exe'
        $installPythonDependencies = $true
    } else {
        $installPythonDependencies = $false
    }
    & $env:SKYLINE_PYTHON -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'The selected Python environment must use Python 3.13 or newer.' }
    if (-not (Test-PythonDependencies)) {
        if (-not $installPythonDependencies) {
            throw 'SKYLINE_PYTHON is missing dependencies. Install requirements.txt in that environment and retry.'
        }
        & $env:SKYLINE_PYTHON -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
        if (-not (Test-PythonDependencies)) { throw 'Python dependencies still cannot be imported.' }
    }

    Write-Host "`n[2/4] Checking frontend dependencies..."
    Push-Location frontend
    try {
        & node -e "try { for (const name of ['vite', 'three', 'typescript', 'puppeteer-core']) require.resolve(name); } catch (error) { console.log(error.message); process.exit(1); }"
        if ($LASTEXITCODE -ne 0) {
            & npm install
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            & node -e "try { for (const name of ['vite', 'three', 'typescript', 'puppeteer-core']) require.resolve(name); } catch (error) { console.log(error.message); process.exit(1); }"
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies still cannot be resolved.' }
        }
    } finally { Pop-Location }

    Write-Host "`n[3/4] Preparing the Moon explorer..."
    $assets = @('frontend\public\data\moon.json', 'frontend\public\data\moon_elevation.bin', 'frontend\public\data\moon_color.jpg')
    if (@($assets | Where-Object { -not (Test-Path $_) }).Count -gt 0) {
        Write-Host 'Downloading the global overview if needed; this can take a while on the first run.'
        & "$root\scripts\fetch_data.ps1" -GlobalOnly
        & $env:SKYLINE_PYTHON scripts\build_moon.py --assets-only
        if ($LASTEXITCODE -ne 0) { throw 'Moon explorer preparation failed.' }
    } else {
        Write-Host 'Using the existing Moon explorer data.'
    }

    Write-Host "`n[4/4] Starting the explorer and checking the global search index..."
    $state = & $env:SKYLINE_PYTHON -c "import json, sys, time; from pathlib import Path; sys.path.insert(0, 'backend'); from skyline import moon; p = moon.DB_DIR / 'build.json'; b = json.loads(p.read_text()) if p.exists() else {}; print('ready' if moon.database_ready() else 'building' if b.get('phase') in ('elevation', 'index') and time.time() - b.get('updated', 0) < 180 else 'needed')"
    if ($LASTEXITCODE -ne 0) { throw 'Could not check the Moon search index.' }
    if ($state -eq 'needed') {
        $logs = Join-Path $root 'data\logs'
        New-Item -ItemType Directory -Force -Path $logs | Out-Null
        Start-Process -FilePath $env:SKYLINE_PYTHON -ArgumentList 'scripts\build_moon.py', '--index-only', '--continuous' -WorkingDirectory $root `
            -RedirectStandardOutput (Join-Path $logs 'moon-build.log') `
            -RedirectStandardError (Join-Path $logs 'moon-build-error.log') -NoNewWindow | Out-Null
        Write-Host 'The complete search index is building in the background. Progress appears in the app.'
        Write-Host 'Allow about 45 GB of free disk space. Build logs are in data\logs\.'
    } elseif ($state -eq 'building') {
        Write-Host 'A Moon search build is already in progress.'
    } elseif ($state -eq 'ready') {
        Write-Host 'The Moon search index is ready.'
    } else {
        throw "Unexpected Moon search status: $state"
    }
    Write-Host 'Keep this window open while using the app. Press Ctrl+C to stop the servers.'
    & "$root\scripts\dev.ps1" -OpenBrowser
    if ($LASTEXITCODE -ne 0) { throw "The development server exited with code $LASTEXITCODE." }
} catch {
    Write-Host "`nStartup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
