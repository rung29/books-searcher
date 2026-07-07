import os
import time
import urllib.parse
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
import re
import requests
import glob

# 圖書館查詢共用設定
LIB_BASE = "https://library.toread.bocach.gov.tw/webpac_rwd"
LIB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

def search_library_status(book_title):
    encoded_title = urllib.parse.quote(book_title)
    
    try:
        # Step 1: 搜尋書名取得 mid
        search_url = (
            f"{LIB_BASE}/search.cfm?"
            f"m=as&k0={encoded_title}&t0=t&c0=and&y10=&y20=&cat0=&dt0=&l0=&lv0="
            f"&lc0=%E4%BC%B8%E6%B8%AF%E9%84%89%E7%AB%8B%E5%9C%96%E6%9B%B8%E9%A4%A8"
            f"&list_num=10&current_page=1"
        )
        res = requests.get(search_url, headers=LIB_HEADERS, timeout=15, verify=False)
        if res.status_code != 200:
            return {"has_holding": False, "items": [], "error": "連線失敗"}
            
        search_soup = BeautifulSoup(res.text, "html.parser")
        page_text = search_soup.get_text()
        if "查無" in page_text or "0 筆" in page_text:
            return {"has_holding": False, "items": []}
            
        mid = None
        for a_tag in search_soup.find_all("a", href=True):
            href = a_tag["href"]
            if "content.cfm" in href and "mid=" in href:
                mid_match = re.search(r'mid=(\d+)', href)
                if mid_match:
                    mid = mid_match.group(1)
                    break
                    
        if not mid:
            return {"has_holding": False, "items": []}

        # Step 2: 確認是否有伸港館藏，有的話翻頁抓取
        items = []
        has_holding = False
        seen_barcodes = set()
        
        for page in range(1, 10): # 最多找9頁
            content_url = (
                f"{LIB_BASE}/content.cfm?"
                f"mid={mid}&contentlistcurrent_page={page}"
            )
            res2 = requests.get(content_url, headers=LIB_HEADERS, timeout=15, verify=False)
            if res2.status_code != 200:
                break
                
            content_soup = BeautifulSoup(res2.text, "html.parser")
            
            # 第一頁先檢查 option 確認是否有館藏
            if page == 1:
                for option in content_soup.find_all("option"):
                    if "伸港" in (option.get_text(strip=True) or ""):
                        has_holding = True
                        break
                if not has_holding:
                    return {"has_holding": False, "items": []}
            
            # 抓取表格
            trs = content_soup.find_all("tr")
            found_new_row = False
            
            for tr in trs:
                tds = tr.find_all("td")
                if len(tds) >= 6:
                    barcode = tds[0].get_text(strip=True)
                    barcode = re.sub(r'\s+', '', barcode)
                    
                    if barcode in seen_barcodes:
                        continue
                        
                    seen_barcodes.add(barcode)
                    found_new_row = True
                    
                    location = tds[1].get_text(strip=True)
                    if "伸港" in location:
                        call_number = tds[3].get_text(strip=True)
                        book_status = tds[4].get_text(strip=True)
                        items.append({"call_number": call_number, "status": book_status})
            
            # 如果這頁沒有新的資料行，代表已經到底或出現重複頁面
            if not found_new_row:
                break
                
            # 為了避免頻繁請求，翻頁也加上延遲
            time.sleep(1.0)
            
        return {"has_holding": has_holding, "items": items}
        
    except Exception as e:
        return {"has_holding": False, "items": [], "error": "查詢出錯"}

def process_file(input_file):
    output_file = input_file.replace(".html", "_with_library.html")
    print(f"\n開始解析來源檔案: {input_file}...")
    
    with open(input_file, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
        
    thead_tr = soup.find("thead").find("tr")
    if not thead_tr:
        return
        
    for th in thead_tr.find_all("th"):
        if th.get_text() in ["伸港館藏狀態", "館藏情形", "索書號", "館藏狀態"]:
            th.decompose()
            
    for header in ["館藏情形", "索書號", "館藏狀態"]:
        new_th = soup.new_tag("th")
        new_th.string = header
        thead_tr.append(new_th)
        
    tbody_rows = soup.find("tbody").find_all("tr")
    total_books = len(tbody_rows)
    print(f"共偵測到 {total_books} 本書籍，開始線上查詢圖書館狀態...")
    
    for index, row in enumerate(tbody_rows, 1):
        title_td = row.find("td", {"data-label": "書名"})
        if not title_td:
            continue
            
        book_title = title_td.get_text(strip=True)
        print(f"[{index}/{total_books}] 正在查詢: {book_title}...")
        
        result = search_library_status(book_title)
        
        for label in ["伸港館藏狀態", "館藏情形", "索書號", "館藏狀態"]:
            for old_td in row.find_all("td", {"data-label": label}):
                old_td.decompose()
                
        # 1. 館藏情形
        td_has = soup.new_tag("td")
        td_has["data-label"] = "館藏情形"
        span_has = soup.new_tag("span")
        if result.get("has_holding"):
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
        if not result.get("items"):
            td_call.string = "-"
        else:
            for item in result.get("items"):
                div = soup.new_tag("div")
                div["style"] = "margin-bottom: 4px;"
                div.string = item["call_number"] if item["call_number"] else "-"
                td_call.append(div)
        row.append(td_call)
        
        # 3. 館藏狀態
        td_status = soup.new_tag("td")
        td_status["data-label"] = "館藏狀態"
        if not result.get("items"):
            if result.get("error"):
                td_status.string = result["error"]
            else:
                td_status.string = "-"
        else:
            for item in result.get("items"):
                div = soup.new_tag("div")
                div["style"] = "margin-bottom: 4px;"
                span = soup.new_tag("span")
                if "在架" in item["status"]:
                    span["class"] = "status status-yes"
                else:
                    span["class"] = "status status-no"
                span.string = item["status"]
                div.append(span)
                td_status.append(div)
        row.append(td_status)
        
        time.sleep(1.5)
        
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(str(soup))
    print(f"整合完成！全新網頁已生成至: {output_file}")

def main():
    # 尋找所有 books_page_*.html，排除 _with_library.html
    html_files = [f for f in glob.glob("books_page_*.html") if not f.endswith("_with_library.html")]
    if not html_files:
        print("找不到任何 books_page_*.html 檔案。")
        return
        
    for file in html_files:
        process_file(file)

if __name__ == "__main__":
    main()
