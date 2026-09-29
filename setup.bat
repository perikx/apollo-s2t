@echo off
setlocal
cd /d "%~dp0"
title Apollo s2t - setup
call bootstrap.bat
if errorlevel 1 goto failed
".venv\Scripts\python.exe" apollo.py --setup
if errorlevel 1 goto failed
pause
exit /b 0
:failed
pause
exit /b 1
