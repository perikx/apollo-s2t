@echo off
cd /d "%~dp0"
rem Starts in the background without a console window (pythonw).
start "" ".venv\Scripts\pythonw.exe" "apollo.py"
