@echo off
cd /d "%~dp0"
rem Re-run the setup wizard any time to change your key, engine, language or hotkeys.
if not exist ".venv\Scripts\python.exe" (
  echo Run Apollo.bat first to install.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" apollo.py --setup
echo.
echo Press any key to close . . .
pause >nul
