#!/bin/bash
# 啟動圖書館館藏整合查詢
cd "$(dirname "$0")"
.venv/Scripts/python.exe integrate.py
