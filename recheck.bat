@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"

if not defined RECHECK_MAX_RETRIES set "RECHECK_MAX_RETRIES=2"
if not defined RECHECK_RETRY_SECONDS set "RECHECK_RETRY_SECONDS=1"

.venv\Scripts\python.exe recheck.py %*
pause
