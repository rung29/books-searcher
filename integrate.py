import os
import sys
import time
import urllib.parse
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
import re
import requests
import glob

# 強制使用 UTF-8 輸出，避免 Windows cp950 編碼造成罕見字元崩潰
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 圖書館查詢共用設定
LIB_BASE = "https://library.toread.bocach.gov.tw/webpac_rwd"
LIB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

MAX_CONTENT_PAGES = int(os.getenv("INTEGRATE_MAX_CONTENT_PAGES", "9"))
MAX_SEARCH_CANDIDATES = int(os.getenv("INTEGRATE_MAX_SEARCH_CANDIDATES", "10"))
PAGE_SLEEP_SECONDS = float(os.getenv("INTEGRATE_PAGE_SLEEP_SECONDS", "0.2"))
BOOK_SLEEP_SECONDS = float(os.getenv("INTEGRATE_BOOK_SLEEP_SECONDS", "0.2"))
LIB_REQUEST_TIMEOUT = float(os.getenv("LIB_REQUEST_TIMEOUT", "5"))

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


class LibraryStructureError(RuntimeError):
    """Raised when a library page is neither a recognized result nor no-result page."""


def _compact_text(value):
    return re.sub(r"[\s\u3000:：,，.。．、\-—_()（）\[\]【】《》「」『』]+", "", value or "").casefold()


def _chinese_length(value):
    return len(re.findall(r"[\u4e00-\u9fff]", value or ""))


def _main_title(title):
    cleaned = re.sub(r"\s+", " ", title or "").strip()
    # Subtitle/version notes commonly follow these separators in recommendation lists.
    for pattern in (r"\s*[：:]\s*", r"\s+[—–-]\s+", r"\s*[（(](?:新版|修訂版|增訂版|第.+版).*[）)]\s*$"):
        parts = re.split(pattern, cleaned, maxsplit=1)
        if parts and parts[0].strip():
            cleaned = parts[0].strip()
    return cleaned


def _extract_author(container_text):
    match = re.search(
        r"作者\s*[：:]\s*(.+?)(?=\s*(?:出版者|出版社|ISBN|ISSN|主題|分類號|版本項)\s*[：:]|$)",
        container_text or "",
        flags=re.S,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _extract_search_candidates(search_soup):
    candidates = []
    seen_mids = set()
    for a_tag in search_soup.find_all("a", href=True):
        href = a_tag["href"]
        if "content.cfm" not in href or "mid=" not in href:
            continue
        mid_match = re.search(r"[?&]mid=(\d+)", href)
        if not mid_match or mid_match.group(1) in seen_mids:
            continue
        mid = mid_match.group(1)
        seen_mids.add(mid)
        container = a_tag.find_parent("div", class_="book")
        if container is None:
            container = a_tag.find_parent(["li", "tr", "article"])
        if container is None:
            container = a_tag.find_parent("div")
        container_text = container.get_text(" ", strip=True) if container else ""
        title = (a_tag.get("title") or "").strip()
        if not title and container:
            title_tag = container.select_one(".book-text h3")
            if title_tag:
                title_copy = BeautifulSoup(str(title_tag), "html.parser")
                for rank in title_copy.find_all("span"):
                    rank.decompose()
                title = title_copy.get_text(" ", strip=True)
        if not title:
            title = a_tag.get_text(" ", strip=True)
        candidates.append(
            {
                "mid": mid,
                "title": title,
                "author": _extract_author(container_text),
            }
        )
    return candidates


def _author_matches(expected, actual):
    expected_key = _compact_text(expected)
    actual_key = _compact_text(actual)
    if not expected_key or not actual_key:
        return False
    return expected_key in actual_key or actual_key in expected_key


def _search_candidates(query_title):
    encoded_title = urllib.parse.quote(query_title)
    search_url = (
        f"{LIB_BASE}/search.cfm?"
        f"m=as&k0={encoded_title}&t0=t&c0=and&y10=&y20=&cat0=&dt0=&l0=&lv0="
        f"&lc0=%E4%BC%B8%E6%B8%AF%E9%84%89%E7%AB%8B%E5%9C%96%E6%9B%B8%E9%A4%A8"
        f"&list_num={MAX_SEARCH_CANDIDATES}&current_page=1"
    )
    response = requests.get(
        search_url, headers=LIB_HEADERS, timeout=LIB_REQUEST_TIMEOUT, verify=False
    )
    if response.status_code != 200:
        raise requests.exceptions.RequestException(f"search returned HTTP {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = _extract_search_candidates(soup)
    page_text = soup.get_text(" ", strip=True)
    if not candidates and not any(marker in page_text for marker in ("查無", "0 筆", "無符合")):
        raise LibraryStructureError("搜尋結果頁缺少預期的書目連結或查無結果標記")
    return candidates


def _candidate_sort_key(candidate, author):
    return 0 if _author_matches(author, candidate.get("author")) else 1


def _lookup_candidate(candidate):
    mid = candidate["mid"]
    items = []
    has_holding = False
    seen_barcodes = set()
    content_url = f"{LIB_BASE}/content.cfm?mid={mid}"

    for page in range(1, MAX_CONTENT_PAGES + 1):
        page_url = f"{content_url}&contentlistcurrent_page={page}"
        response = requests.get(
            page_url, headers=LIB_HEADERS, timeout=LIB_REQUEST_TIMEOUT, verify=False
        )
        if response.status_code != 200:
            raise requests.exceptions.RequestException(
                f"content returned HTTP {response.status_code}"
            )

        content_soup = BeautifulSoup(response.text, "html.parser")
        if page == 1:
            headers = {
                re.sub(r"\s+", "", th.get_text(" ", strip=True))
                for th in content_soup.find_all("th")
            }
            options = [option.get_text(" ", strip=True) for option in content_soup.find_all("option")]
            recognized = {"條碼號", "館藏地", "索書號", "館藏狀態"}.issubset(headers)
            if not recognized:
                raise LibraryStructureError(f"館藏內容頁 mid={mid} 缺少預期欄位")
            has_holding = any("伸港" in option for option in options)
            if not has_holding:
                return None

        found_new_row = False
        for tr in content_soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            barcode = re.sub(r"\s+", "", tds[0].get_text(strip=True))
            if not barcode or barcode in seen_barcodes:
                continue
            seen_barcodes.add(barcode)
            found_new_row = True
            location = tds[1].get_text(strip=True)
            if "伸港" in location:
                items.append(
                    {
                        "call_number": tds[3].get_text(strip=True),
                        "status": tds[4].get_text(strip=True),
                    }
                )

        if not found_new_row:
            break
        if PAGE_SLEEP_SECONDS > 0:
            time.sleep(PAGE_SLEEP_SECONDS)

    return {
        "has_holding": has_holding,
        "items": items,
        "matched_title": candidate.get("title") or "",
        "matched_author": candidate.get("author") or "",
        "detail_url": content_url,
    }


def search_library_status(book_title, book_author=""):
    try:
        queries = [(book_title, "完整書名")]
        main_title = _main_title(book_title)
        if main_title and _compact_text(main_title) != _compact_text(book_title):
            queries.append((main_title, "主書名"))

        for query_title, match_type in queries:
            candidates = _search_candidates(query_title)
            if not candidates:
                continue
            candidates.sort(key=lambda candidate: _candidate_sort_key(candidate, book_author))

            short_main_title = match_type == "主書名" and _chinese_length(query_title) < 4
            for candidate in candidates[:MAX_SEARCH_CANDIDATES]:
                if short_main_title and not _author_matches(book_author, candidate.get("author")):
                    continue
                result = _lookup_candidate(candidate)
                if result:
                    result["match_type"] = match_type
                    return result
            # The agreed fallback is only for a full-title search with no candidates.
            if match_type == "完整書名":
                return {"has_holding": False, "items": []}

        return {"has_holding": False, "items": []}

    except requests.exceptions.ConnectTimeout as exc:
        print(f"library status lookup failed for {book_title}: {exc}", file=sys.stderr)
        return {"has_holding": False, "items": [], "error": "圖書館連線逾時"}
    except requests.exceptions.Timeout as exc:
        print(f"library status lookup failed for {book_title}: {exc}", file=sys.stderr)
        return {"has_holding": False, "items": [], "error": "圖書館回應逾時"}
    except requests.exceptions.RequestException as exc:
        print(f"library status lookup failed for {book_title}: {exc}", file=sys.stderr)
        return {"has_holding": False, "items": [], "error": "圖書館連線失敗"}
    except LibraryStructureError as exc:
        print(f"library structure changed for {book_title}: {exc}", file=sys.stderr)
        return {
            "has_holding": False,
            "items": [],
            "error": "來源格式異常，未完成查詢",
            "error_type": "structure",
        }
    except Exception as exc:
        print(f"library status lookup failed for {book_title}: {exc}", file=sys.stderr)
        return {"has_holding": False, "items": [], "error": "館藏解析失敗"}


def process_file(input_file):
    output_file = input_file.replace(".html", "_with_library.html")
    print(f"\n開始解析來源檔案: {input_file}...")

    with open(input_file, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    thead = soup.find("thead")
    tbody = soup.find("tbody")
    if not thead or not tbody or not thead.find("tr"):
        raise LibraryStructureError(f"來源檔案 {input_file} 缺少預期的書目表格")
    thead_tr = thead.find("tr")

    for th in thead_tr.find_all("th"):
        if th.get_text() in ["伸港館藏狀態", "館藏情形", "命中館藏", "索書號", "館藏狀態"]:
            th.decompose()

    for header in ["館藏情形", "命中館藏", "索書號", "館藏狀態"]:
        new_th = soup.new_tag("th")
        new_th.string = header
        thead_tr.append(new_th)

    tbody_rows = tbody.find_all("tr")
    total_books = len(tbody_rows)
    print(f"共偵測到 {total_books} 本書籍，開始線上查詢圖書館狀態...")
    consecutive_structure_errors = 0

    for index, row in enumerate(tbody_rows, 1):
        title_td = row.find("td", {"data-label": "書名"})
        if not title_td:
            continue

        book_title = title_td.get_text(strip=True)
        author_td = row.find("td", {"data-label": "作者"})
        book_author = author_td.get_text(" ", strip=True) if author_td else ""
        print(f"[{index}/{total_books}] 正在查詢: {book_title}...")

        result = search_library_status(book_title, book_author)
        if result.get("error_type") == "structure":
            consecutive_structure_errors += 1
        else:
            consecutive_structure_errors = 0

        for label in ["伸港館藏狀態", "館藏情形", "命中館藏", "索書號", "館藏狀態"]:
            for old_td in row.find_all("td", {"data-label": label}):
                old_td.decompose()

        # 1. 館藏情形
        td_has = soup.new_tag("td")
        td_has["data-label"] = "館藏情形"
        span_has = soup.new_tag("span")
        if result.get("error_type") == "structure":
            span_has["class"] = "status"
            span_has.string = "未完成"
        elif result.get("has_holding"):
            span_has["class"] = "status status-yes"
            span_has.string = "有館藏"
        else:
            span_has["class"] = "status status-no"
            span_has.string = "無館藏"
        td_has.append(span_has)
        row.append(td_has)

        # 2. 實際命中的館藏版本
        td_match = soup.new_tag("td")
        td_match["data-label"] = "命中館藏"
        if result.get("matched_title"):
            match_link = soup.new_tag("a", href=result.get("detail_url") or "#")
            match_link["target"] = "_blank"
            match_link["rel"] = "noopener"
            match_link.string = result["matched_title"]
            td_match.append(match_link)
            if result.get("match_type") == "主書名":
                note = soup.new_tag("small")
                note.string = "（主書名匹配）"
                td_match.append(note)
        else:
            td_match.string = "-"
        row.append(td_match)

        # 3. 索書號
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

        # 4. 館藏狀態
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

        if consecutive_structure_errors >= 3:
            warning = soup.new_tag("div")
            warning["class"] = "info"
            warning.string = "圖書館來源格式連續異常，已停止後續查詢；先前成功結果予以保留。"
            table = soup.find("table")
            if table:
                table.insert_before(warning)
            for pending_row in tbody_rows[index:]:
                for label in ["伸港館藏狀態", "館藏情形", "命中館藏", "索書號", "館藏狀態"]:
                    for old_td in pending_row.find_all("td", {"data-label": label}):
                        old_td.decompose()
                for label, text in [
                    ("館藏情形", "未完成"),
                    ("命中館藏", "-"),
                    ("索書號", "-"),
                    ("館藏狀態", "來源格式異常，未完成查詢"),
                ]:
                    pending_td = soup.new_tag("td")
                    pending_td["data-label"] = label
                    pending_td.string = text
                    pending_row.append(pending_td)
            print("來源格式連續異常 3 次，停止本檔案後續查詢。", file=sys.stderr)
            break

        if BOOK_SLEEP_SECONDS > 0:
            time.sleep(BOOK_SLEEP_SECONDS)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(str(soup))
    print(f"整合完成！全新網頁已生成至: {output_file}")


def main():
    # 以程式所在目錄為準，避免從其他工作目錄啟動時找不到檔案。
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pattern = os.path.join(script_dir, "books_page_*.html")
    html_files = [
        f for f in glob.glob(pattern) if not f.endswith("_with_library.html")
    ]
    if not html_files:
        print("找不到任何 books_page_*.html 檔案。")
        return

    for file in html_files:
        try:
            process_file(file)
        except LibraryStructureError as exc:
            print(f"來源格式異常：{exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
