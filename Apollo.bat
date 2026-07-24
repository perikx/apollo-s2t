@echo off
cd /d "%~dp0"
title Apollo s2t
set "FIRST=0"

rem ---------------------------------------------------------------------------
rem One-click launcher. First run: sets up Python + asks for your key, then
rem starts Apollo in the background (system tray). Later runs: just start it.
rem ---------------------------------------------------------------------------

rem --- First run: create the virtual environment and install packages --------
if not exist ".venv\Scripts\python.exe" (
  set "FIRST=1"
  echo Setting up Apollo s2t for the first time...
  echo Creating Python environment ^(.venv^) ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: could not create the Python environment.
    echo Install Python from https://www.python.org/downloads/ ^(tick "Add to PATH"^),
    echo then double-click Apollo.bat again.
    pause
    exit /b 1
  )
  echo Installing dependencies ^(one-time, about a minute^) ...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

rem --- First run: no config yet -> quick setup wizard ------------------------
if not exist "config.json" (
  set "FIRST=1"
  ".venv\Scripts\python.exe" apollo.py --setup
)

rem --- If setup was cancelled, stop here so we don't start half-configured ---
if not exist "config.json" (
  echo.
  echo Setup was not completed. Double-click Apollo.bat again to finish.
  pause
  exit /b 1
)

rem --- Start Apollo hidden (no console window) -------------------------------
start "" ".venv\Scripts\pythonw.exe" apollo.py

if "%FIRST%"=="1" (
  echo.
  echo Apollo s2t is now running in the background.
  echo Look for the gold microphone icon next to the clock ^(system tray^).
  echo It will start automatically the next time you log in.
  echo.
  echo Try it: click into any text box, hold F8, say a sentence, release.
  echo.
  pause
)
