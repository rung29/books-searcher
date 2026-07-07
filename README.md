# books-searcher (讀步彰化飛閱雲端書籍清單爬蟲)

用來抓取「讀步彰化飛閱雲端 閱讀線上認證系統」書籍清單的 Python 爬蟲腳本。
支援**搜尋參數篩選**（適讀年段、認證狀態、關鍵字），所有產出的 `.html` 檔案均會**自動儲存於專案目錄下**。

## 環境安裝（首次執行）

```bash
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
```

## 使用步驟

```bash
.venv\Scripts\python.exe crawler.py
```

1. **選擇適讀年段**：輸入 `0`~`5`（例如 `1` = 國小低年級）
2. **選擇認證狀態**：輸入 `0`~`2`（例如 `1` = 可認證）
3. **輸入關鍵字**：可直接按 Enter 跳過
4. **輸入驗證碼**：程式會將驗證碼圖片下載至專案目錄下的 `captcha.png`，請開啟圖片查看 4 位數驗證碼並輸入
5. **選擇頁碼**：驗證通過後可持續輸入頁碼下載，輸入 `q` 退出

### 整合圖書館館藏狀態

如果您想查詢剛抓下來的書籍在「伸港鄉立圖書館」是否有館藏、索書號及借閱狀態，請在抓取完清單後執行：

```bash
.venv\Scripts\python.exe integrate.py
```

這會自動掃描目錄下所有的 `books_page_*.html`，為每本書向圖書館系統查詢，並產生對應的 `books_page_*_with_library.html`。
