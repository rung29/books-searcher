import os
import sys
import urllib.parse
import requests
from bs4 import BeautifulSoup

def fetch_search_session():
    """
    初始化 Session，獲取 CSRF Token 並下載驗證碼圖片
    """
    base_url = "https://read.chc.edu.tw/index.php"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # 關閉 SSL 憑證警告訊息
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
    
    session = requests.Session()
    
    print("1. 正在初始化網站連線，取得安全金鑰 (CSRF Token)...")
    try:
        res = session.get(base_url, params={"inter": "books", "kind": "cht"}, headers=headers, verify=False, timeout=15)
        res.raise_for_status()
    except requests.RequestException as e:
        print(f"初始化失敗：{e}", file=sys.stderr)
        return None, None
        
    soup = BeautifulSoup(res.text, "html.parser")
    csrf_token_input = soup.find("input", {"name": "csrf_token"})
    csrf_token = csrf_token_input["value"] if csrf_token_input else ""
    
    # 尋找驗證碼圖片
    captcha_img = soup.find("img", {"id": "captcha_image"})
    if not captcha_img:
        print("找不到驗證碼圖片！伺服器結構可能已變更。", file=sys.stderr)
        return None, None
        
    captcha_src = captcha_img["src"]
    captcha_url = urllib.parse.urljoin(base_url, captcha_src)
    
    # 下載驗證碼圖片
    print("2. 正在下載驗證碼圖片...")
    try:
        captcha_res = session.get(captcha_url, headers=headers, verify=False, timeout=15)
        captcha_res.raise_for_status()
    except requests.RequestException as e:
        print(f"驗證碼圖片下載失敗：{e}", file=sys.stderr)
        return None, None
        
    script_dir = os.path.dirname(os.path.abspath(__file__))
    captcha_path = os.path.join(script_dir, "captcha.png")
    with open(captcha_path, "wb") as f:
        f.write(captcha_res.content)
        
    print(f"\n=======================================================")
    print(f"【驗證碼已儲存】已將驗證碼圖片存至您的本機目錄：")
    print(f"👉 {captcha_path}")
    print(f"請使用圖片檢視器打開它以查看 4 位數驗證碼。")
    print(f"=======================================================\n")
    
    return session, csrf_token

def perform_search(session, csrf_token, readrang, testing, keywords, captcha_code):
    """
    發送 POST 搜尋請求以設定伺服器 Session 中的搜尋過濾條件
    """
    base_url = "https://read.chc.edu.tw/index.php"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    post_url = f"{base_url}?inter=books&page=1&kind=cht&search=1"
    data = {
        "csrf_token": csrf_token,
        "inter": "books",
        "field": "title",
        "readrang": readrang,
        "testing": testing,
        "keywords": keywords,
        "captcha_code": captcha_code,
        "search_act": "search"
    }
    
    print("3. 正在送出搜尋條件與驗證碼...")
    try:
        response = session.post(post_url, data=data, headers=headers, verify=False, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"發送搜尋請求失敗：{e}", file=sys.stderr)
        return False
        
    # 檢查是否彈出驗證碼錯誤的 JavaScript 警告
    soup = BeautifulSoup(response.text, "html.parser")
    script_tags = soup.find_all("script")
    for s in script_tags:
        if s.string and "alert" in s.string:
            # 取得警告內容，例如 "請輸入驗證碼" 或 "驗證碼錯誤"
            alert_text = s.string.strip()
            print(f"\n❌ 搜尋被拒絕，伺服器訊息：{alert_text}", file=sys.stderr)
            return False
            
    return True

def fetch_books_by_page(session, page_number):
    """
    使用已搜尋認證的 Session 抓取指定頁數的書籍資料
    """
    base_url = "https://read.chc.edu.tw/index.php"
    params = {
        "inter": "books",
        "kind": "cht",
        "search": "1",
        "page": str(page_number)
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = session.get(base_url, params=params, headers=headers, verify=False, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"抓取第 {page_number} 頁失敗：{e}", file=sys.stderr)
        return None
        
    soup = BeautifulSoup(response.text, "html.parser")
    book_elements = soup.select("div.book-group")
    
    books = []
    for elem in book_elements:
        book_data = {}
        
        # 1. 取得詳細頁面連結
        a_tag = elem.find("a")
        if a_tag and "href" in a_tag.attrs:
            relative_url = a_tag["href"]
            book_data["url"] = urllib.parse.urljoin(base_url, relative_url)
        else:
            book_data["url"] = ""
            
        # 2. 取得書名
        h3_tag = elem.find("h3")
        if h3_tag:
            book_data["title"] = h3_tag.get_text(strip=True)
        else:
            continue  # 若無書名則跳過
            
        # 3. 取得詳細屬性
        book_text_div = elem.find("div", class_="book-text")
        if book_text_div:
            p_tags = book_text_div.find_all("p")
            for p in p_tags:
                text = p.get_text(strip=True)
                if text.startswith("作者："):
                    book_data["author"] = text.replace("作者：", "").strip()
                elif text.startswith("出版社："):
                    book_data["publisher"] = text.replace("出版社：", "").strip()
                elif text.startswith("適讀年段："):
                    book_data["range"] = text.replace("適讀年段：", "").strip()
                    
            # 認證狀態
            status_span = book_text_div.find("span")
            if status_span:
                book_data["status"] = status_span.get_text(strip=True)
            else:
                book_data["status"] = "未知"
        else:
            book_data["author"] = ""
            book_data["publisher"] = ""
            book_data["range"] = ""
            book_data["status"] = ""
            
        books.append(book_data)
        
    return books

def save_to_markdown(books, page_number, readrang_name, testing_name, keywords):
    """
    將書籍清單存入 Markdown 檔案
    """
    if not books:
        print(f"\n第 {page_number} 頁沒有任何書籍資料，不進行儲存。")
        return None
        
    filename = f"books_page_{page_number}.md"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(script_dir, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# 群書博覽 - 書籍清單 (第 {page_number} 頁)\n\n")
        f.write(f"### 篩選條件：\n")
        f.write(f"- 適讀年段：{readrang_name}\n")
        f.write(f"- 認證狀態：{testing_name}\n")
        if keywords:
            f.write(f"- 關鍵字：{keywords}\n")
        f.write(f"\n資料來源：[讀步彰化飛閱雲端 閱讀線上認證系統](https://read.chc.edu.tw/index.php?inter=books&kind=cht&search=1&page={page_number})\n\n")
        
        # 寫入表格
        f.write("| 序號 | 書名 | 作者 | 出版社 | 適讀年段 | 認證狀態 | 詳細連結 |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        
        for i, book in enumerate(books, 1):
            title = book.get("title", "")
            author = book.get("author", "")
            publisher = book.get("publisher", "")
            readers_range = book.get("range", "")
            status = book.get("status", "")
            url = book.get("url", "")
            
            # 同時提供 Markdown 連結與完整網址，方便手機在各種閱讀器（含純文字）下直接點擊
            title_link = f"[{title}]({url})<br>{url}" if url else title
            f.write(f"| {i} | {title_link} | {author} | {publisher} | {readers_range} | {status} | [詳細頁面]({url}) |\n")
            
    print(f"\n🎉 成功將第 {page_number} 頁的書籍儲存至：\n👉 {filepath}")
    return filepath

def main():
    # 強制主控台輸出為 UTF-8 (特別在 Windows 上)
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("=======================================================")
    print(" 讀步彰化飛閱雲端 - 書籍清單下載爬蟲")
    print("=======================================================\n")
    
    # 選擇年段
    print("【1】請選擇適讀年段：")
    print(" 0. 全部年段 (預設)")
    print(" 1. 國小低年級")
    print(" 2. 國小中年級")
    print(" 3. 國小高年級")
    print(" 4. 國中")
    print(" 5. 其他")
    rang_choice = input("請選擇 (0-5)：").strip()
    
    readrang_map = {
        "0": "all",
        "1": "1",
        "2": "2",
        "3": "3",
        "4": "4",
        "5": "5"
    }
    readrang_names = {
        "all": "全部年段",
        "1": "國小低年級",
        "2": "國小中年級",
        "3": "國小高年級",
        "4": "國中",
        "5": "其他"
    }
    readrang = readrang_map.get(rang_choice, "all")
    readrang_name = readrang_names[readrang]
    
    # 選擇認證狀態
    print("\n【2】請選擇認證狀態：")
    print(" 0. 全部書籍 (預設)")
    print(" 1. 可認證")
    print(" 2. 不可認證")
    test_choice = input("請選擇 (0-2)：").strip()
    
    testing_map = {
        "0": "all",
        "1": "1",
        "2": "2"
    }
    testing_names = {
        "all": "全部書籍",
        "1": "可認證",
        "2": "不可認證"
    }
    testing = testing_map.get(test_choice, "all")
    testing_name = testing_names[testing]
    
    # 關鍵字
    keywords = input("\n【3】請輸入書籍關鍵字 (選填，直接按 Enter 跳過)：").strip()
    
    # 獲取 Session 與 CSRF Token 並下載驗證碼
    session, csrf_token = fetch_search_session()
    if not session:
        sys.exit(1)
        
    # 輸入驗證碼
    captcha_code = input("請輸入 4 位數驗證碼：").strip()
    
    # 執行搜尋
    success = perform_search(session, csrf_token, readrang, testing, keywords, captcha_code)
    if not success:
        print("\n❌ 搜尋初始化失敗，程式結束。請確認驗證碼是否輸入正確，然後重新執行程式。")
        sys.exit(1)
        
    print("\n✅ 搜尋初始化成功！已成功套用您的篩選條件。")
    
    # 迴圈讓使用者抓取多個頁碼
    while True:
        page_input = input("\n請輸入要下載的頁碼 (例如：1，或輸入 q 退出)：").strip()
        if page_input.lower() == 'q':
            print("感謝使用！程式已結束。")
            break
            
        try:
            page_number = int(page_input)
            if page_number <= 0:
                raise ValueError
        except ValueError:
            print("請輸入有效的正整數頁碼！")
            continue
            
        print(f"正在抓取第 {page_number} 頁...")
        books = fetch_books_by_page(session, page_number)
        if books:
            save_to_markdown(books, page_number, readrang_name, testing_name, keywords)
            # 在抓取成功後，刪除暫存的驗證碼圖片檔案
            captcha_path = os.path.join(os.getcwd(), "captcha.png")
            if os.path.exists(captcha_path):
                try:
                    os.remove(captcha_path)
                except Exception:
                    pass
        else:
            print(f"抓取第 {page_number} 頁失敗。可能該頁面超出範圍，或者連線已逾期。")

if __name__ == "__main__":
    main()
