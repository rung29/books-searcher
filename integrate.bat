@echo off
REM 啟動圖書館館藏整合查詢
cd /d "%~dp0"
.venv\Scripts\python.exe integrate.py
pause
