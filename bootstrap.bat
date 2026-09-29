@echo off
setlocal
cd /d "%~dp0"
rem Shared by run/setup/debug/build. Install only after requirements change.
if not exist ".venv\Scripts\python.exe" (
  echo Creating Apollo's Python environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Install Python 3.10+ from https://www.python.org/downloads/
  echo Enable "Add Python to PATH", then run Apollo.bat again.
  exit /b 1
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.10+ required. Install it and recreate the .venv folder.
  exit /b 1
)
fc /b requirements.txt ".venv\apollo-requirements.txt" >nul 2>&1
if errorlevel 1 (
  echo Installing updated dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERROR: Dependency installation failed. Check the output and retry.
    exit /b 1
  )
  copy /y requirements.txt ".venv\apollo-requirements.txt" >nul
  if errorlevel 1 exit /b 1
)
exit /b 0
