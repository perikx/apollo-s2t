@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install.bat first.
  echo Press any key to close . . .
  pause >nul
  exit /b 1
)
".venv\Scripts\python.exe" apollo.py --setup
echo Press any key to close . . .
pause >nul
