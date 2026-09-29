@echo off
setlocal
cd /d "%~dp0"
title Apollo s2t - debug
call bootstrap.bat
if errorlevel 1 goto failed
if not exist "config.json" (
  ".venv\Scripts\python.exe" apollo.py --setup
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" apollo.py
if errorlevel 1 goto failed
pause
exit /b 0
:failed
pause
exit /b 1
