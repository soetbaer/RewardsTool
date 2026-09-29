@echo off
cd /d "%~dp0.."
choice /C JN /M "RewardsTool wirklich entfernen (taeglicher Lauf, Autostart, Verknuepfungen)"
if errorlevel 2 exit /b 0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
echo.
pause
