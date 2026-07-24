@echo off
cd /d "%~dp0"
title Apollo s2t - debug
rem Runs Apollo with a visible console + live logs, for troubleshooting.
if not exist ".venv\Scripts\python.exe" (
  echo Run Apollo.bat first to install.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" apollo.py
echo.
echo Apollo exited. Press any key to close . . .
pause >nul
