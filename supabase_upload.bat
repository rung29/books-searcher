@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"

.venv\Scripts\python.exe supabase_upload.py
pause
