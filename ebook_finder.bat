@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"

if not defined EBOOK_OUTPUT_PAGE_SIZE set "EBOOK_OUTPUT_PAGE_SIZE=25"
if not defined INTEGRATE_MAX_SEARCH_CANDIDATES set "INTEGRATE_MAX_SEARCH_CANDIDATES=10"
if not defined INTEGRATE_BOOK_SLEEP_SECONDS set "INTEGRATE_BOOK_SLEEP_SECONDS=0.5"

.venv\Scripts\python.exe ebook_finder.py
pause
