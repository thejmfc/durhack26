# Windows Commands Reference

Quick lookup for Windows equivalents of Linux/macOS commands used in Skyline.

## Virtual Environment

| Task | Linux/macOS | Windows |
|------|------------|---------|
| Create venv | `python3.13 -m venv .venv` | `python -m venv .venv` |
| Activate | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| Run Python | `.venv/bin/python script.py` | `.\.venv\Scripts\python script.py` |
| Run pip | `.venv/bin/pip install foo` | `.\.venv\Scripts\pip install foo` |

## Common Operations

| Task | Linux/macOS | Windows |
|------|------------|---------|
| Download data | `./scripts/fetch_data.sh` | `.\scripts\fetch_data.ps1` |
| Build Moon | `.venv/bin/python scripts/build_moon.py` | `.\.venv\Scripts\python scripts\build_moon.py` |
| Start dev server | `./scripts/dev.sh` | `.\scripts\dev.ps1` |
| Run tests | `.venv/bin/python -m unittest discover -s tests` | `.\.venv\Scripts\python -m unittest discover -s tests` |

## Directory Navigation

| Task | Command |
|------|---------|
| Current directory | `.` (both) |
| Parent directory | `..` (both) |
| Change directory | `cd frontend` (both) |
| List files | `ls -lh` (Linux/Mac) vs `Get-ChildItem` (Windows) |
| Make directory | `mkdir foo` (both) or `New-Item -Path foo -ItemType Directory` (Windows) |

## Environment Variables

| Task | Linux/macOS | Windows |
|------|------------|---------|
| Set variable | `export VAR=value` | `$env:VAR = 'value'` |
| Check variable | `echo $VAR` | `Write-Host $env:VAR` |
| Conditional Python | `if [ -x .venv/bin/python ]` | `if (Test-Path '.venv\Scripts\python.exe')` |

## Multi-Step Setup

**Linux/macOS:**
```bash
./scripts/fetch_data.sh
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/build_moon.py
(cd frontend && npm install)
./scripts/dev.sh
```

**Windows (PowerShell):**
```powershell
.\scripts\fetch_data.ps1
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python scripts\build_moon.py
cd frontend; npm install; cd ..
.\scripts\dev.ps1
```

## Background Processes

| Task | Linux/macOS | Windows |
|------|------------|---------|
| Run in background | `command &` | `Start-Process -FilePath command -NoNewWindow` |
| Kill process | `kill $PID` | `Stop-Process -Id $PID` |
| List processes | `ps aux` | `Get-Process` |

## File Operations

| Task | Linux/macOS | Windows |
|------|------------|---------|
| Copy file | `cp src dest` | `Copy-Item -Path src -Destination dest` |
| Move file | `mv src dest` | `Move-Item -Path src -Destination dest` |
| Delete file | `rm file` | `Remove-Item -Path file` |
| Delete directory | `rm -r dir` | `Remove-Item -Path dir -Recurse` |
| File exists | `[ -f file ]` | `Test-Path file` |
| Directory exists | `[ -d dir ]` | `Test-Path dir` |

## Loops

**Loop over array (Linux/macOS bash):**
```bash
for site in Site01 Site04 Site07 Site11 Site20; do
  echo $site
done
```

**Loop over array (Windows PowerShell):**
```powershell
foreach ($site in @('Site01', 'Site04', 'Site07', 'Site11', 'Site20')) {
  Write-Host $site
}
```

## Redirection

| Task | Linux/macOS | Windows |
|------|------------|---------|
| Redirect stdout | `command > output.txt` | `command > output.txt` (same) |
| Redirect stderr | `command 2>&1` | `command 2>&1` (same) |
| Pipe | `command \| grep text` | `command \| Select-String text` |
| Suppress output | `command > /dev/null` | `command > $null` |

## Useful PowerShell Shortcuts

| Shortcut | Full Command |
|----------|--------------|
| `cd` | Set-Location |
| `ls` | Get-ChildItem |
| `cat` | Get-Content |
| `pwd` | Get-Location |
| `rm` | Remove-Item |
| `mv` | Move-Item |
| `cp` | Copy-Item |
| `where` | Get-Command |

Use either the shortcut or full name — both work.

## Line Endings

If you see errors like "cannot execute binary file" when running scripts, your Git configuration might have converted line endings:

```powershell
# Verify actual line endings
file scripts/dev.ps1

# If needed, convert back to LF (for shell scripts)
git config --global core.safecrlf warn
```

PowerShell scripts should use CRLF (Windows), bash scripts should use LF (Unix).

## Getting Help

**PowerShell:**
```powershell
Get-Help Command-Name
Get-Command -Name *keyword*
Get-Help about_*
```

**Python:**
```powershell
python --help
python -c "import module; help(module.function)"
```

**Node/npm:**
```powershell
npm help
npm help <command>
```
