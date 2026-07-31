@echo off
REM 啟動 Web App
cd /d "%~dp0"
.venv\Scripts\python.exe web_app.py
pause
