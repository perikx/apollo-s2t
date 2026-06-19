@echo off
set "DIR=%~dp0"
set "PYW=%DIR%.venv\Scripts\pythonw.exe"
set "APP=%DIR%apollo.py"
if not exist "%PYW%" (
  echo ERROR: .venv not found. Please run install.bat first.
  echo Press any key to close . . .
  pause >nul
  exit /b 1
)
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v ApolloS2T /t REG_SZ /d "\"%PYW%\" \"%APP%\"" /f
echo.
echo Autostart enabled. Apollo s2t will now start at Windows login.
echo Press any key to close . . .
pause >nul
