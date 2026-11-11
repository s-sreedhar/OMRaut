@echo off
rem One-command build: double-click or run  build.bat
rem Creates the Windows installer at dist\velopack\OMRaut-win-Setup.exe
setlocal
cd /d "%~dp0"

rem ---- 1. Find a supported Python (3.11 - 3.13); the build script rejects 3.14+ ----
set "PYEXE="
if exist ".venv-build\Scripts\python.exe" set "PYEXE=%CD%\.venv-build\Scripts\python.exe"
if not defined PYEXE if exist ".venv\Scripts\python.exe" set "PYEXE=%CD%\.venv\Scripts\python.exe"
if not defined PYEXE (
  for %%V in (3.13 3.12 3.11) do (
    if not defined PYEXE (
      for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)" 2^>nul') do set "BASEPY=%%P"
      if defined BASEPY (
        echo Creating virtual environment with Python %%V ...
        "%BASEPY%" -m venv .venv || goto :fail
        set "PYEXE=%CD%\.venv\Scripts\python.exe"
      )
    )
  )
)
if not defined PYEXE (
  echo ERROR: Python 3.11, 3.12 or 3.13 was not found. Install one from https://www.python.org/downloads/ and run again.
  goto :fail
)
echo Using Python: %PYEXE%
set "OMR_PYTHON=%PYEXE%"

rem ---- 2. Make sure the Velopack packager (vpk) is installed ----
where vpk >nul 2>nul
if errorlevel 1 (
  where dotnet >nul 2>nul
  if errorlevel 1 (
    echo ERROR: .NET SDK is required. Install it from https://dotnet.microsoft.com/download then run again.
    goto :fail
  )
  echo Installing Velopack CLI ...
  dotnet tool install --global vpk --version 1.2.161 || goto :fail
  set "PATH=%PATH%;%USERPROFILE%\.dotnet\tools"
)

rem ---- 3. Build ----
rem Stop running OMRaut instances if any so PyInstaller can overwrite dist
powershell -NoProfile -Command "Get-Process -Name 'OMRaut','OMRExamApp' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue" 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build.ps1" || goto :fail

echo.
echo ============================================================
echo  BUILD OK
echo  Installer: %CD%\dist\velopack\OMRaut-win-Setup.exe
echo  Portable : %CD%\dist\velopack\OMRaut-win-Portable.zip
echo ============================================================
if exist "dist\velopack" start "" "dist\velopack"
endlocal
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
endlocal
exit /b 1
