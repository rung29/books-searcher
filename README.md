# books-searcher（讀步彰化飛閱雲端書籍清單爬蟲）

本專案會先從「讀步彰化飛閱雲端 閱讀線上認證系統」下載推薦書單，再依需求查詢伸港鄉立圖書館館藏或電子書網路資源，最後產生可放在 GitHub Pages 的靜態查詢頁。

## 快速選擇

| 想做的事 | 使用程式 | 前置資料 | 主要輸出 |
| --- | --- | --- | --- |
| 下載最新推薦書單 | `crawler.py` | 無 | `books_page_*.html` |
| 從完整推薦書單找出伸港有館藏的書 | `integrate.py` | `books_page_*.html` | `books_with_library_page_*.html`、`books_with_library_data.json`、`index.html` |
| 更新既有書單目前的館藏狀態 | `recheck.py` | `books_with_library_page_*.html` | 更新後的館藏頁、JSON 與 `index.html` |
| 不連線圖書館，只重新產生館藏查詢頁 | `library_output.py` | `books_with_library_page_*.html` | `books_with_library_data.json`、`index.html` |
| 從完整推薦書單找有網路資源的電子書 | `ebook_finder.py` | `books_page_*.html` | `ebooks_data.json`、`ebooks_index.html` |
| 將兩份 JSON 更新到 Supabase | `supabase_upload.py` | 兩份本機 JSON 與 Supabase 設定 | 覆寫 Storage 資料並更新公開 URL 設定 |
| 在本機瀏覽器即時搜尋與查館藏 | `web_app.py` | 無需先產生 HTML | Flask 網頁，不寫入靜態書單 |
| 讓 Vercel 載入 Flask app | `api/index.py` | `web_app.py` | Vercel 入口，不需手動執行 |

`*.bat` 是對應 Python 程式的 Windows 啟動捷徑；`integrate.sh`、`webapp.sh` 則可在 Git Bash 類環境使用。

## 建議工作流程

### 第一次建立或完整更新書單

```text
crawler.py
   ├─ integrate.py ──────> index.html + books_with_library_data.json
   └─ ebook_finder.py ───> ebooks_index.html + ebooks_data.json
```

1. 執行 `crawler.py`，選擇年段、認證狀態及要下載的頁碼。
2. 執行 `integrate.py`，從完整書單篩出伸港目前有館藏的書。
3. 如果也要電子書頁，再執行 `ebook_finder.py`。
4. 執行 `supabase_upload.py`，把兩份 JSON 更新到 Supabase Storage。
5. 確認靜態首頁後，再把需要的檔案部署到 GitHub Pages。

`integrate.py` 已經會呼叫 `library_output.py` 的輸出功能，所以完整查詢後不必再執行一次 `library_output.py`。

### 只更新既有館藏狀態

```text
既有 books_with_library_page_*.html
                 │
                 └─ recheck.py ─> 更新館藏頁、JSON 與 index.html
```

使用 `recheck.py` 即可，不必重新執行 `crawler.py` 或 `integrate.py`。它只重查上一次已收入的書，因此適合更新「在架／借出」狀態，但不會發現推薦書單中新增加的書，也不會重新檢查先前沒有館藏而被排除的書。

### 只重建靜態查詢頁

如果館藏資料沒有改變，只是調整了 `library_output.py` 的前端樣式、搜尋或分頁功能，執行：

```bash
.venv\Scripts\python.exe library_output.py
```

它只解析本機既有的 `books_with_library_page_*.html`，不會連線圖書館，因此速度最快。

### 只更新電子書清單

如果 `books_page_*.html` 已是最新資料，直接執行 `ebook_finder.py`，不需要先執行 `integrate.py`。館藏查詢與電子書查詢是兩條獨立流程，只是共用 `crawler.py` 的來源檔。

## 首次安裝

在專案目錄建立虛擬環境並安裝套件：

```bash
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
```

後續命令都應在專案目錄執行。Windows 使用者也可以直接執行對應的 `.bat`。

## 各程式使用說明

### 1. `crawler.py`：下載推薦書單

使用時機：第一次建立資料、讀步彰化的推薦書單更新，或要改查不同年段、認證狀態及關鍵字時。

執行：

```bash
.venv\Scripts\python.exe crawler.py
```

Windows 捷徑：

```bat
crawler.bat
```

操作步驟：

1. 選擇適讀年段，可用空格複選，例如 `1 2` 表示國小低、中年級。
2. 選擇認證狀態。
3. 視需要輸入書名關鍵字，也可直接按 Enter。
4. 開啟程式產生的 `captcha.png`，輸入四位數驗證碼。
5. 輸入單頁 `1` 或範圍 `1-5` 下載；輸入 `q` 結束。

輸出為 `books_page_1.html`、`books_page_2.html` 等來源頁面。這些是後續 `integrate.py` 與 `ebook_finder.py` 的共同輸入，也是本機中間資料，不需部署到 GitHub Pages。

重新下載相同頁碼會覆寫該頁，但不會自動刪除其他舊頁。若改用完全不同的篩選條件，應先自行整理舊的 `books_page_*.html`，避免新舊條件被後續程式一起處理。

### 2. `integrate.py`：建立伸港館藏書單

使用時機：`books_page_*.html` 已更新，需要從完整推薦書單重新找出目前在伸港鄉立圖書館有館藏的書。

執行：

```bash
.venv\Scripts\python.exe integrate.py
```

Windows 捷徑：

```bat
integrate.bat
```

處理方式：

1. 掃描專案目錄中的全部 `books_page_*.html`。
2. 逐本搜尋圖書館書目，並確認伸港館藏、索書號及借閱狀態。
3. 只保留已確認有伸港館藏的書。
4. 產生館藏中間頁、JSON 資料及 GitHub Pages 首頁。

輸出：

- `books_with_library_page_*.html`：保留查詢結果的本機中間頁，供 `recheck.py` 與 `library_output.py` 使用。
- `books_with_library_data.json`：館藏前端資料。
- `index.html`：館藏搜尋首頁，支援適讀年級、相似書名群組、文字／萬用字元／多條件搜尋、語音輸入及分頁。切換年級時，書單與相似書名群組會一起重新篩選。

這是查詢量最大的流程。只有來源書單改變、需要重新納入過去未找到館藏的書，或比對邏輯更新時，才需要完整重跑。

### 3. `recheck.py`：重查既有館藏

使用時機：推薦書單沒有變，只想更新既有館藏書籍的在架、借出或館藏狀態。

執行專案根目錄的結果：

```bash
.venv\Scripts\python.exe recheck.py
```

也可以指定結果目錄或該目錄內的首頁：

```bash
.venv\Scripts\python.exe recheck.py 中年級
.venv\Scripts\python.exe recheck.py 中年級\index.html
```

Windows 捷徑同樣支援參數：

```bat
recheck.bat 中年級
```

處理方式：

1. 讀取既有 `books_with_library_page_*.html`。
2. 優先使用已記錄的圖書館書目 `mid` 更新館藏。
3. 原書目失效時，才重新用書名與作者搜尋。
4. 移除本次已確認無館藏的書。
5. 覆寫館藏中間頁，並同步重建 JSON 與 `index.html`。

`recheck.py` 不會讀取完整的 `books_page_*.html`，所以執行速度通常比 `integrate.py` 快，但範圍只限既有館藏清單。

### 4. `library_output.py`：由館藏中間頁重建前端

使用時機：已經有正確的 `books_with_library_page_*.html`，只想重新整理 JSON、分頁及 `index.html`，不想再次查詢圖書館。

執行專案根目錄的結果：

```bash
.venv\Scripts\python.exe library_output.py
```

指定其他結果目錄或首頁：

```bash
.venv\Scripts\python.exe library_output.py 中年級
.venv\Scripts\python.exe library_output.py 中年級\index.html
```

Windows 捷徑：

```bat
library_output.bat
library_output.bat 中年級
```

它會重新解析書名、作者、出版社、適讀年段、命中館藏、索書號與館藏狀態，並按照 `INTEGRATE_OUTPUT_PAGE_SIZE` 重建所有館藏分頁、`books_with_library_data.json` 與 `index.html`。

### 5. `ebook_finder.py`：建立電子書資源清單

使用時機：要從推薦書單找出圖書館搜尋結果中「網路資源」欄位有資料的書。

執行：

```bash
.venv\Scripts\python.exe ebook_finder.py
```

Windows 捷徑：

```bat
ebook_finder.bat
```

處理方式：

1. 掃描 `crawler.py` 產生的全部 `books_page_*.html`。
2. 以書名搜尋圖書館，不限定伸港館藏地。
3. 解析搜尋結果中的「網路資源」。
4. 只收入至少有一個網路資源連結的書。

輸出：

- `ebooks_data.json`：電子書資料。
- `ebooks_index.html`：電子書搜尋首頁，啟動時讀取 JSON，並提供適讀年級篩選、搜尋、語音輸入及分頁。年級與搜尋文字會同時套用。

此流程不依賴 `integrate.py` 或 `books_with_library_page_*.html`。首頁會優先讀取 Supabase 的 `ebooks_data.json`，失敗時才讀取 GitHub Pages 同目錄下的備援檔。

### 6. `supabase_upload.py`：更新遠端 JSON

使用時機：`integrate.py`、`recheck.py` 或 `ebook_finder.py` 產生新資料後，要讓已部署的首頁直接取得新資料而不重新部署 GitHub Pages。

第一次使用前，請先到 Supabase Dashboard 的 Storage 建立名為 `book-data` 的 **Public bucket**。接著以 `.env.local.example` 為格式，在專案根目錄建立不會提交到 Git 的 `.env.local`：

```dotenv
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_SECRET_KEY=sb_secret_YOUR_SECRET_KEY
SUPABASE_BUCKET=book-data
SUPABASE_DATA_PREFIX=
```

執行上傳：

```bash
.venv\Scripts\python.exe supabase_upload.py
```

Windows 捷徑：

```bat
supabase_upload.bat
```

程式會：

1. 驗證兩份 JSON 根節點都是陣列。
2. 覆寫 Storage 中的 `books_with_library_data.json` 與 `ebooks_data.json`。
3. 更新不含金鑰的 `data_source_config.js` 公開讀取網址。

第一次設定 Supabase 後，需要把 `data_source_config.js` 與 `data_loader.js` 部署到 GitHub Pages 一次。之後只要重新執行 `supabase_upload.py`，已部署首頁就會讀到新的遠端 JSON，不需要再次提交資料或重新部署。

`SUPABASE_SECRET_KEY` 只供本機上傳使用，不能寫入 HTML、JavaScript、README、Git 或傳給瀏覽器。`data_source_config.js` 只包含可公開的 Storage URL，可以安全部署。

如果想把 JSON 放在 bucket 子目錄，可設定：

```dotenv
SUPABASE_DATA_PREFIX=latest
```

### 7. `web_app.py`：本機互動式網頁版

使用時機：不想先產生靜態檔案，而要直接在瀏覽器輸入條件、驗證碼、頁碼並查看即時結果。

執行：

```bash
.venv\Scripts\python.exe web_app.py
```

Windows 捷徑：

```bat
webapp.bat
```

啟動後開啟：

```text
http://127.0.0.1:5000
```

手機與電腦在同一個 Wi-Fi 時，可以改用電腦區網 IP，例如：

```text
http://192.168.x.x:5000
```

網頁版與靜態輸出流程互相獨立：

- 每次開啟新的結果頁或切換頁碼，都會向讀步彰化網站取得該頁書單。
- 勾選館藏查詢時，瀏覽器會逐本呼叫本機 `/api/library-status` 即時查詢；已查結果會保存在瀏覽器本機快取，重設搜尋時才會清除。
- 它不會更新 `books_page_*.html`、`books_with_library_data.json` 或 `ebooks_data.json`。
- `api/index.py` 只是 Vercel 載入 `web_app.py` 的入口，不需要手動執行。

## 產出檔案與部署

| 檔案 | 來源 | 用途 | 建議部署 |
| --- | --- | --- | --- |
| `books_page_*.html` | `crawler.py` | 完整推薦書單中間檔 | 否 |
| `books_with_library_page_*.html` | `integrate.py`／`recheck.py`／`library_output.py` | 館藏重查與重建前端的中間檔 | 否 |
| `books_with_library_data.json` | 館藏輸出流程 | 館藏結構化資料 | 是 |
| `index.html` | 館藏輸出流程 | GitHub Pages 首頁 | 是 |
| `ebooks_data.json` | `ebook_finder.py` | 電子書結構化資料 | 是 |
| `ebooks_index.html` | `ebook_finder.py` | 電子書查詢頁 | 是 |
| `data_source_config.js` | `supabase_upload.py`／手動設定 | Supabase 公開資料位置 | 是 |
| `data_loader.js` | 專案資源 | Supabase 優先與 GitHub 備援載入 | 是 |
| `catalog_filters.js` | 專案資源 | 兩個首頁共用的適讀年級篩選與相似書名分組 | 是 |
| `icon.svg` | 專案資源 | 網頁圖示 | 是 |

GitHub Pages 會以 `index.html` 作為目錄首頁。`ebooks_index.html` 可由館藏首頁連入，不需要改名為另一個 `index.html`；若要把不同資料集放在不同子目錄，每個子目錄則可以各自放一份 `index.html`。

目前 `.gitignore` 已排除來源與館藏中間 HTML。部署靜態查詢頁時，不需要提交 `books_page_*.html`、`books_with_library_page_*.html` 或舊的 `ebooks_page_*.html`。

兩個首頁的資料載入順序都是：

1. 讀取 `data_source_config.js` 指定的 Supabase Public Storage URL。
2. Supabase 逾時、HTTP 失敗或 JSON 格式不正確時，改讀 GitHub Pages 同目錄的 JSON。
3. 頁面載入完成後，畫面頂端會短暫顯示資料來自 Supabase 或 GitHub Pages 備援。

因此 GitHub 上仍建議保留兩份 JSON 作為備援；日常資料更新只需上傳 Supabase，備援資料可在需要時再更新。

## 查詢速度與重試設定

直接執行 Python 時可用環境變數調整：

```bat
set INTEGRATE_BOOK_SLEEP_SECONDS=0
set INTEGRATE_PAGE_SLEEP_SECONDS=0.3
.venv\Scripts\python.exe integrate.py
```

| 變數 | 作用 | Python 預設值 |
| --- | --- | --- |
| `INTEGRATE_MAX_CONTENT_PAGES` | 單筆書目最多檢查幾頁館藏內容 | `9` |
| `INTEGRATE_MAX_SEARCH_CANDIDATES` | 每本書最多檢查幾個搜尋候選版本 | `10` |
| `INTEGRATE_OUTPUT_PAGE_SIZE` | 館藏中間頁每頁幾本 | `25` |
| `INTEGRATE_PAGE_SLEEP_SECONDS` | 同一本書的內容頁之間等待秒數 | `0.2` |
| `INTEGRATE_BOOK_SLEEP_SECONDS` | 每本書查詢後等待秒數 | `0.2` |
| `LIB_REQUEST_TIMEOUT` | 單次圖書館請求逾時秒數 | `5` |
| `RECHECK_MAX_RETRIES` | `recheck.py` 查詢失敗時的重試次數 | `2` |
| `RECHECK_RETRY_SECONDS` | `recheck.py` 每次重試前等待秒數 | `1` |

`integrate.bat` 為了兼顧速度與穩定性，預設將頁面等待設為 `0.3` 秒、每本書等待設為 `0.5` 秒；如果執行前已自行設定環境變數，批次檔會保留你的設定。

若圖書館開始回應失敗、HTTP 500 或來源結構異常，應增加等待時間，不建議用大量並行請求。

## 網頁版與雲端限制

- GitHub Pages 只能提供靜態檔案，不能執行 Flask、Python、驗證碼或即時館藏 API；適合部署產生好的 `index.html`、`ebooks_index.html` 與 JSON。
- Vercel 可以載入 Flask app，但實測從 Vercel Function 連線 `library.toread.bocach.gov.tw` 會發生 `ConnectTimeout`，即使更換區域、延長逾時及降低並行數仍可能失敗。
- GitHub Actions 適合測試或定期產生靜態結果，不適合作為需要驗證碼的互動式爬蟲。
- 目前最穩定的方式仍是在本機執行命令列流程，或在本機／同一區網使用 `web_app.py`。

如果未來部署到 VPS 或其他後端主機，應先確認該主機可以連線：

```text
https://library.toread.bocach.gov.tw
```

## 測試

修改館藏、重查、電子書或前端產生邏輯後，可執行：

```bash
.venv\Scripts\python.exe -m unittest test_integrate.py test_recheck.py test_ebook_finder.py test_supabase_upload.py
node --test test_catalog_filters.js
```
