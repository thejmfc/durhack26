# Skyline: Windows Setup Quick Start

A quick reference for running the Skyline lunar photo localization app on Windows.

## Prerequisites

Install these first:
1. **Python 3.13+** - Download from [python.org](https://www.python.org/downloads/) or Microsoft Store
   - During installation, check "Add Python to PATH"
2. **Node.js 20+** - Download from [nodejs.org](https://nodejs.org/)
3. **~35 GB free disk space** for the full global setup (detailed-site mode needs ~2 GB)

Verify installation:
```powershell
python --version
node --version
npm --version
```

## Quick Start: Global Explorer (Moon surface + photo matching)

Open PowerShell in the repository folder and run:

```powershell
# 1. Download NASA data (~750 MB, takes a few minutes)
.\scripts\fetch_data.ps1

# 2. Create Python environment and install dependencies
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 3. Build the Moon index (first time ~30 minutes, reused on restart)
.\.venv\Scripts\python scripts\build_moon.py

# 4. Install frontend dependencies
cd frontend && npm install && cd ..

# 5. Start the development server
.\scripts\dev.ps1
```

The frontend opens automatically at `http://localhost:5277`, with the backend running on `http://127.0.0.1:8077`.

## Alternative: Backend with `uv` (Optional)

If you prefer using the backend's own `uv` project environment:

```powershell
cd backend
uv sync
$env:WATCHFILES_FORCE_POLLING = 'true'
uv run fastapi dev app.py --host 127.0.0.1 --port 8077
```

In another PowerShell window:
```powershell
cd frontend
$env:SKYLINE_API_TARGET = 'http://127.0.0.1:8077'
npm run dev -- --port 5277
```

## Quick Start: Detailed-Site Mode (South Pole sites)

For the five high-resolution south-pole sites (~2 GB total):

```powershell
# Steps 1-2 same as above (fetch_data and venv)

# But install more dependencies:
.\.venv\Scripts\pip install numpy numba rasterio pyproj fastapi "uvicorn[standard]" python-multipart pillow scipy matplotlib

# Build horizon databases for all sites (~2 min each, ~260 MB each)
foreach ($site in @('Site01', 'Site04', 'Site07', 'Site11', 'Site20')) {
    .\.venv\Scripts\python scripts\build_horizon_db.py --site $site --spacing 50
}

# Prepare browser assets
.\.venv\Scripts\python scripts\prep_assets.py

# Install frontend and start
cd frontend && npm install && cd ..
.\scripts\dev.ps1
```

## Environment Variables (Optional)

Set these before running scripts to customize:

```powershell
# Use a different Python executable
$env:SKYLINE_PYTHON = 'C:\path\to\python.exe'

# Use different ports
$env:SKYLINE_BACKEND_PORT = '8077'     # default
$env:SKYLINE_FRONTEND_PORT = '5277'    # default
```

## Troubleshooting

**"python: command not found"**
- Make sure Python is in your PATH
- Use full path: `C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe`
- Or install from Microsoft Store instead

**"npm not found"**
- Restart PowerShell after installing Node.js to update PATH
- Or use full path: `C:\Program Files\nodejs\npm.cmd`

**"WATCHFILES_FORCE_POLLING not working"**
- The `$env:WATCHFILES_FORCE_POLLING = 'true'` is mainly for the `uv run` method
- The regular `dev.ps1` script sets this automatically

**Port already in use**
- Find what's using the port: `netstat -ano | findstr :8077`
- Kill the process: `taskkill /PID <PID> /F`
- Or set different ports with environment variables above

**Data download fails**
- Check your internet connection
- If `curl` is not available, manually download files from:
  - https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/
  - https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/
  - Save them to `data/raw/`

## What's Running

- **Backend API** (FastAPI) on http://127.0.0.1:8077
  - Serves elevation data, computes horizons, matches photos
  - Auto-reloads on Python code changes
- **Frontend** (Vite + React) on http://127.0.0.1:5277
  - 3D Moon viewer in WebGL (three.js)
  - Photo upload and analysis UI
  - Auto-reloads on TypeScript/CSS changes

Both are needed for the full experience. The globe works without the backend, but photo matching requires it.

## Next Steps

See [README.md](../README.md) for:
- Detailed global retrieval diagnostics
- Test commands and code validation
- Evaluation scripts and performance metrics
- Architecture and code layout

## Questions?

Check the main README.md for:
- How global photo matching works
- Evaluation results and known limitations
- File structure and project layout
