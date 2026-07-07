@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"

if not defined INTEGRATE_MAX_CONTENT_PAGES set "INTEGRATE_MAX_CONTENT_PAGES=9"
if not defined INTEGRATE_PAGE_SLEEP_SECONDS set "INTEGRATE_PAGE_SLEEP_SECONDS=0.3"
if not defined INTEGRATE_BOOK_SLEEP_SECONDS set "INTEGRATE_BOOK_SLEEP_SECONDS=0.5"

.venv\Scripts\python.exe integrate.py
pause
