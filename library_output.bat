@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"

if not defined INTEGRATE_OUTPUT_PAGE_SIZE set "INTEGRATE_OUTPUT_PAGE_SIZE=25"

.venv\Scripts\python.exe library_output.py %*
pause
