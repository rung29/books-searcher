import os
import time
import urllib.parse
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
import re
import requests

# 設定檔案名稱
INPUT_HTML = "books_page_2.html"
OUTPUT_HTML = "books_page_2_with_library.html"


# 圖書館查詢共用設定
LIB_BASE = "https://library.toread.bocach.gov.tw/webpac_rwd"
LIB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# 啟動時關閉 SSL 憑證警告（圖書館系統使用自簽憑證）
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


def search_library_status(book_title):
    """
    兩步驟查詢伸港鄉立圖書館的館藏狀態：
    Step 1: 用 search.cfm 搜尋書名，從搜尋結果中取得該書的 mid（書目 ID）
    Step 2: 用 content.cfm?mid=xxx 查詢詳細館藏，檢查伸港是否有館藏及在架狀態
    """
    encoded_title = urllib.parse.quote(book_title)

    try:
        # ===== Step 1：搜尋列表頁，取得書目 ID (mid) =====
        search_url = (
            f"{LIB_BASE}/search.cfm?"
            f"m=as&k0={encoded_title}&t0=t&c0=and&y10=&y20=&cat0=&dt0=&l0=&lv0="
            f"&lc0=%E4%BC%B8%E6%B8%AF%E9%84%89%E7%AB%8B%E5%9C%96%E6%9B%B8%E9%A4%A8"
            f"&list_num=10&current_page=1"
        )

        res = requests.get(search_url, headers=LIB_HEADERS, timeout=15, verify=False)
        if res.status_code != 200:
            return {"has_holding": False, "call_number": "", "status": "連線失敗"}

        search_soup = BeautifulSoup(res.text, "html.parser")

        # 檢查是否無搜尋結果
        page_text = search_soup.get_text()
        if "查無" in page_text or "0 筆" in page_text:
            return {"has_holding": False, "call_number": "", "status": ""}

        # 從搜尋結果中提取第一筆書目的 mid（content.cfm?mid=XXXXXX 的連結）
        mid = None
        for a_tag in search_soup.find_all("a", href=True):
            href = a_tag["href"]
            if "content.cfm" in href and "mid=" in href:
                mid_match = re.search(r'mid=(\d+)', href)
                if mid_match:
                    mid = mid_match.group(1)
                    break

        if not mid:
            return {"has_holding": False, "call_number": "", "status": ""}

        # ===== Step 2：用正確的 mid 查詢詳細館藏 =====
        content_url = (
            f"{LIB_BASE}/content.cfm?"
            f"mid={mid}&m=as&k0={encoded_title}&t0=t&c0=and&y10=&y20=&cat0=&dt0=&l0=&lv0="
            f"&lc0=%E4%BC%B8%E6%B8%AF%E9%84%89%E7%AB%8B%E5%9C%96%E6%9B%B8%E9%A4%A8"
            f"&list_num=10&current_page=1&mt=&at=&sj=&py=&pr=&it=&lr=&lg=&si="
        )

        res2 = requests.get(content_url, headers=LIB_HEADERS, timeout=15, verify=False)
        if res2.status_code != 200:
            return {"has_holding": False, "call_number": "", "status": "連線失敗"}

        content_soup = BeautifulSoup(res2.text, "html.parser")

        # 方法 A：檢查「館藏地」下拉選單中是否有伸港（最可靠，不受分頁影響）
        has_holding = False
        call_number = ""
        book_status = ""

        # 方法 A：檢查「館藏地」下拉選單中是否有伸港
        for option in content_soup.find_all("option"):
            if "伸港" in (option.get_text(strip=True) or ""):
                has_holding = True
                break

        if not has_holding:
            return {"has_holding": False, "call_number": "", "status": ""}

        # 方法 B：遍歷表格列，找伸港的在架狀態與索書號
        for tr in content_soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 6:
                location = tds[1].get_text(strip=True)
                if "伸港" in location:
                    call_number = tds[3].get_text(strip=True)
                    book_status = tds[4].get_text(strip=True)
                    return {"has_holding": True, "call_number": call_number, "status": book_status}

        # 館藏地下拉有伸港，但在第一頁找不到伸港列（分頁問題）
        # 試著抓取其他館的索書號作為參考，狀態設為提示文字
        for tr in content_soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 6:
                call_number = tds[3].get_text(strip=True)
                break
        
        return {"has_holding": True, "call_number": call_number, "status": "在其他分頁"}

    except requests.exceptions.Timeout:
        return {"has_holding": False, "call_number": "", "status": "查詢超時"}
    except Exception as e:
        return {"has_holding": False, "call_number": "", "status": "查詢出錯"}


def main():
    if not os.path.exists(INPUT_HTML):
        print(f"錯誤：找不到來源檔案 {INPUT_HTML}，請確認檔案路徑。")
        return

    print(f"開始解析來源檔案: {INPUT_HTML}...")

    with open(INPUT_HTML, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    # 修改表頭 (Thead)
    thead_tr = soup.find("thead").find("tr")
    # 移除舊的伸港館藏狀態表頭(如果有的話)
    for th in thead_tr.find_all("th"):
        if th.get_text() in ["伸港館藏狀態", "館藏情形", "索書號", "館藏狀態"]:
            th.decompose()
            
    # 新增三個表頭
    for header in ["館藏情形", "索書號", "館藏狀態"]:
        new_th = soup.new_tag("th")
        new_th.string = header
        thead_tr.append(new_th)

    # 遍歷表格每行資料
    tbody_rows = soup.find("tbody").find_all("tr")
    total_books = len(tbody_rows)

    print(f"共偵測到 {total_books} 本書籍，開始線上查詢圖書館狀態...")

    for index, row in enumerate(tbody_rows, 1):
        title_td = row.find("td", {"data-label": "書名"})
        if not title_td:
            continue

        book_title = title_td.get_text(strip=True)
        print(f"[{index}/{total_books}] 正在查詢: {book_title}...")

        # 前往圖書館爬取狀態
        status_result = search_library_status(book_title)

        # 移除舊的相同欄位避免重複添加
        for label in ["伸港館藏狀態", "館藏情形", "索書號", "館藏狀態"]:
            old_td = row.find("td", {"data-label": label})
            if old_td:
                old_td.decompose()

        # 1. 館藏情形
        td_has = soup.new_tag("td")
        td_has["data-label"] = "館藏情形"
        span_has = soup.new_tag("span")
        if status_result["has_holding"]:
            span_has["class"] = "status status-yes"
            span_has.string = "有館藏"
        else:
            span_has["class"] = "status status-no"
            span_has.string = "無館藏"
        td_has.append(span_has)
        row.append(td_has)
        
        # 2. 索書號
        td_call = soup.new_tag("td")
        td_call["data-label"] = "索書號"
        td_call.string = status_result["call_number"] if status_result["call_number"] else "-"
        row.append(td_call)
        
        # 3. 館藏狀態
        td_status = soup.new_tag("td")
        td_status["data-label"] = "館藏狀態"
        if status_result["status"]:
            span_status = soup.new_tag("span")
            if "在架" in status_result["status"]:
                span_status["class"] = "status status-yes"
            else:
                span_status["class"] = "status status-no"
            span_status.string = status_result["status"]
            td_status.append(span_status)
        else:
            td_status.string = "-"
        row.append(td_status)

        # 【重要安全機制】每次查詢後休息 1.5 秒，避免連線過快被圖書館防爬蟲機制封鎖 IP
        time.sleep(1.5)

    # 寫入新檔案
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(str(soup))

    print(f"\n整合完成！全新網頁已生成至: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()