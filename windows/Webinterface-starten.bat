@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo RewardsTool ist noch nicht eingerichtet. Bitte zuerst Setup.bat starten.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" webui.py
timeout /t 3 /nobreak >nul
start "" http://localhost:3333
