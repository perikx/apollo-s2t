@echo off
cd /d "%~dp0"
echo ============================================
echo  Apollo s2t - Installation
echo ============================================
echo.
echo Creating virtual environment (.venv) ...
py -3 -m venv .venv
if errorlevel 1 (
  echo py launcher failed, trying "python" ...
  python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo ERROR: could not create venv.
  echo Install Python from https://www.python.org/downloads/ and try again.
  echo Press any key to close . . .
  pause >nul
  exit /b 1
)

echo.
echo Installing dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo ============================================
echo  Done!
echo  1) Run setup.bat to enter your API keys
echo  2) Test:        start-debug.bat (shows logs)
echo  3) Background:  start.bat
echo  4) Autostart:   autostart-enable.bat
echo ============================================
echo Press any key to close . . .
pause >nul
