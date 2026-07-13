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

## 網頁版與雲端部署限制

目前 `web-version` 分支提供 Flask 網頁版，可在本機或同一個區網內執行：

```bash
.venv\Scripts\python.exe web_app.py
```

網頁版支援輸入搜尋條件、驗證碼、指定頁數，並可顯示伸港鄉立圖書館的館藏情形、索書號與館藏狀態。不過實測後有以下限制：

- **Vercel 部署限制**：網頁本身可以部署並開啟，但館藏查詢 API 由 Vercel Function 連線到 `library.toread.bocach.gov.tw` 時會發生 `ConnectTimeout`。即使改用東京區域 `hnd1`、延長 timeout、降低並行數，仍會逾時。推測是對方圖書館網站阻擋或無法接受 Vercel 雲端出口連線。
- **GitHub Pages 限制**：GitHub Pages 只能提供靜態 HTML，不能執行 Flask/Python 後端，因此無法提供即時查詢、驗證碼流程與館藏查詢 API。
- **GitHub Actions 限制**：GitHub Actions 可以用來測試或定期產生靜態結果，但不適合作為互動式即時查詢網站。
- **Zeabur 嘗試結果**：曾嘗試改為 Zeabur 部署，但目前帳號沒有可用 server/region/credit，因此未能完成實際部署測試。

因此目前最穩定的使用方式仍是：

- 在本機執行命令列版 `crawler.py` + `integrate.py`
- 或在本機/區網執行 Flask 網頁版 `web_app.py`

若要公開成可用的網頁服務，建議部署到一般 VPS 或可自訂出口網路的主機，並確認該主機可以連到：

```text
https://library.toread.bocach.gov.tw
```

### 整合圖書館館藏狀態

如果您想查詢剛抓下來的書籍在「伸港鄉立圖書館」是否有館藏、索書號及借閱狀態，請在抓取完清單後執行：

```bash
.venv\Scripts\python.exe integrate.py
```

這會自動掃描目錄下所有的 `books_page_*.html`，為每本書向圖書館系統查詢，並產生對應的 `books_page_*_with_library.html`。

### 效能調整

如果你覺得執行太慢，可以透過環境變數調整查詢速度。`integrate.sh` 和 `integrate.bat` 已經提供預設值，你也可以在執行前自行覆蓋：

```bash
set INTEGRATE_BOOK_SLEEP_SECONDS=0
set INTEGRATE_PAGE_SLEEP_SECONDS=0.3
```

可調參數如下：

- `INTEGRATE_MAX_CONTENT_PAGES`：單筆書目最多查幾頁，預設 `9`
- `INTEGRATE_PAGE_SLEEP_SECONDS`：同一本書的頁面之間等待秒數，預設 `1.0`
- `INTEGRATE_BOOK_SLEEP_SECONDS`：每本書查完後等待秒數，預設 `1.5`
