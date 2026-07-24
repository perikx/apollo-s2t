@echo off
cd /d "%~dp0.."
title Apollo s2t - build exe
rem ---------------------------------------------------------------------------
rem Optional: build a single Apollo.exe (with the logo) from the source.
rem Everyday users don't need this - Apollo.bat already runs the app. This is for
rem shipping a double-click .exe that needs no Python install on the target PC.
rem ---------------------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Run Apollo.bat once first to create the environment.
  pause
  exit /b 1
)
echo Installing PyInstaller (one-time) ...
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
echo.
echo Building Apollo.exe ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean packaging\apollo.spec
echo.
if exist "dist\Apollo.exe" (
  echo Done. Your exe is here:  dist\Apollo.exe
  echo Copy it anywhere and double-click it - it creates config.json next to itself
  echo and runs the setup wizard on first launch.
) else (
  echo Build finished but dist\Apollo.exe was not found - check the output above.
)
echo.
pause
