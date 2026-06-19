@echo off
cd /d "%~dp0"
rem Starts with a visible console + live logs for testing/debugging.
".venv\Scripts\python.exe" "apollo.py"
echo Press any key to close . . .
pause >nul
