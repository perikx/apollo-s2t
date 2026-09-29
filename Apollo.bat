@echo off
setlocal
cd /d "%~dp0"
title Apollo s2t
call bootstrap.bat
if errorlevel 1 goto failed
if not exist "config.json" (
  ".venv\Scripts\python.exe" apollo.py --setup
  if errorlevel 1 goto failed
)
rem Preflight also upgrades existing configurations before a hidden launch.
".venv\Scripts\python.exe" apollo.py --check
if errorlevel 1 goto failed
start "" ".venv\Scripts\pythonw.exe" apollo.py
exit /b 0
:failed
echo.
echo Apollo did not start. Fix the error above, then run Apollo.bat again.
pause
exit /b 1
