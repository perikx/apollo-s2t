@echo off
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v ApolloS2T /f
echo.
echo Autostart disabled.
echo Press any key to close . . .
pause >nul
