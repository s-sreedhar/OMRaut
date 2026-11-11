# Build & Run Cheat Sheet (Windows, PowerShell)

## Quickest way: one command

```powershell
.\build.bat
```

It finds a supported Python (3.11-3.13), installs the packager if needed, builds, and opens the output folder. The details below are only needed if you want to do it step by step.

Run all commands from the project folder: `E:\Freelancing\OMRaut`

## 1. One-time setup

Install Python 3.11, 3.12 or 3.13 (not 3.14/3.15), then:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 2. Run the app (for testing)

```powershell
omraut
```

## 3. Run the tests

```powershell
python -m pytest -q
omr-harness --allow-empty      # accuracy check on labeled scans in tests/samples/
```

If pytest fails with "Access is denied" on the temp folder, add `--basetemp .\.pt -p no:cacheprovider`.

## 4. Build the Windows installer (Setup.exe)

One-time: install the .NET SDK, then the Velopack tool:

```powershell
dotnet tool install --global vpk --version 1.2.161
```

Build (uses the default `python`; or set `OMR_PYTHON` to Python 3.11-3.13):

```powershell
# optional: point at a specific Python
$env:OMR_PYTHON = "E:\Freelancing\OMRaut\.venv\Scripts\python.exe"

.\installer\build.ps1
```

If PowerShell blocks the script: `powershell -ExecutionPolicy Bypass -File .\installer\build.ps1`

## 5. Where the output is

| What | Path |
|------|------|
| Installer to give to users | `dist\velopack\OMRaut-win-Setup.exe` |
| Portable zip (no install) | `dist\velopack\OMRaut-win-Portable.zip` |
| App folder (direct testing) | `dist\OMRaut\OMRaut.exe` |

## 6. Quick check of a built app

```powershell
.\dist\OMRaut\OMRaut.exe --smoke-test   # exit code 0 = healthy
```

## Release checklist

1. Bump the version in `src\omr_exam\__init__.py` (`__version__`).
2. Run the tests (step 3), then build (step 4).
3. Install `OMRaut-win-Setup.exe` on a clean PC and try: create test, key, print, scan, review, export.
4. For auto-updates, set `$env:OMR_UPDATE_SOURCE_URL = "https://your-host/omr/"` before building and upload everything in `dist\velopack\` (upload `releases.win.json` last). See `docs\release.md`.
