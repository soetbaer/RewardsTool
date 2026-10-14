@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo RewardsTool ist noch nicht eingerichtet. Bitte zuerst Setup.bat starten.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" webui.py
rem Port aus config.json (webui.port), sonst 3333
set "PORT=3333"
for /f "usebackq delims=" %%p in (`.venv\Scripts\python.exe -c "import json;print(int(json.load(open('config.json',encoding='utf-8')).get('webui',{}).get('port',3333)))" 2^>nul`) do set "PORT=%%p"
timeout /t 3 /nobreak >nul
start "" http://localhost:%PORT%
