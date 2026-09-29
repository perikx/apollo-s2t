@echo off
setlocal
cd /d "%~dp0.."
title Apollo s2t - build exe
call bootstrap.bat
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6,<7"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean packaging\apollo.spec
if errorlevel 1 goto failed
if not exist "dist\Apollo.exe" goto failed
echo Built dist\Apollo.exe. Copy it into a writable folder on Windows.
echo First launch asks for an OpenRouter key in a small dialog.
echo config.json, apollo.log and custom prompts live next to the exe.
pause
exit /b 0
:failed
echo Build failed. Check the errors above; any older dist\Apollo.exe is not this build.
pause
exit /b 1
