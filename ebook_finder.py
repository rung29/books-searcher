import glob
import json
import os
import re
import sys
import time
import urllib.parse
from copy import deepcopy

import requests
from bs4 import BeautifulSoup

from integrate import (
    LIB_BASE,
    LIB_HEADERS,
    LIB_REQUEST_TIMEOUT,
    MAX_SEARCH_CANDIDATES,
    BOOK_SLEEP_SECONDS,
    _author_matches,
    _candidate_sort_key,
    _chinese_length,
    _extract_author,
    _title_similarity,
)
from library_output import LibraryStructureError


EBOOK_OUTPUT_PAGE_SIZE = max(1, int(os.getenv("EBOOK_OUTPUT_PAGE_SIZE", "25")))


def _source_page_number(path):
    match = re.search(r"books_page_(\d+)\.html$", os.path.basename(path))
    return int(match.group(1)) if match else sys.maxsize


def _cell_text(row, label):
    cell = row.find("td", {"data-label": label})
    return cell.get_text(" ", strip=True) if cell else ""


def _first_link(row, label):
    cell = row.find("td", {"data-label": label})
    link = cell.find("a", href=True) if cell else None
    return link["href"] if link else ""


def _extract_source_books(html_files):
    books = []
    for input_file in sorted(html_files, key=_source_page_number):
        print(f"\n開始解析來源檔案: {input_file}...")
        with open(input_file, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        tbody = soup.find("tbody")
        if not soup.find("thead") or not tbody:
            raise LibraryStructureError(f"來源檔案 {input_file} 缺少預期的書目表格")

        for row in tbody.find_all("tr"):
            title = _cell_text(row, "書名")
            if not title:
                continue
            books.append(
                {
                    "number": _cell_text(row, "序號"),
                    "title": title,
                    "url": _first_link(row, "書名"),
                    "author": _cell_text(row, "作者"),
                    "publisher": _cell_text(row, "出版社"),
                    "range": _cell_text(row, "適讀年段"),
                    "certification": _cell_text(row, "認證狀態"),
                }
            )
    return books


def _absolute_url(url):
    if not url:
        return ""
    return urllib.parse.urljoin(f"{LIB_BASE}/", url)


def _extract_online_resources(container):
    if not container:
        return []

    resources = []
    seen = set()

    def add_resource(href):
        href = _absolute_url(href)
        if not href or "content.cfm" in href:
            return
        if href not in seen:
            seen.add(href)
            resources.append(href)

    label_nodes = container.find_all(
        string=lambda value: value and "網路資源" in value
    )
    for label in label_nodes:
        parent = label.parent
        for link in parent.find_all("a", href=True):
            add_resource(link["href"])

        for sibling in parent.next_siblings:
            if getattr(sibling, "name", None):
                for link in sibling.find_all("a", href=True):
                    add_resource(link["href"])
                if sibling.name in {"p", "div", "li", "tr"}:
                    break

        text = parent.get_text(" ", strip=True)
        for url in re.findall(r"https?://[^\s<>'\"]+", text):
            url = url.rstrip("。；,，")
            add_resource(url)

    if not resources:
        for link in container.find_all("a", href=True):
            href = _absolute_url(link["href"])
            context = link.parent.get_text(" ", strip=True) if link.parent else ""
            if "網路資源" not in context and "ebook" not in href.lower():
                continue
            add_resource(href)

    return resources


def _candidate_title(a_tag, container):
    title = (a_tag.get("title") or "").strip()
    if not title and container:
        title_tag = container.select_one(".book-text h3")
        if title_tag:
            title_copy = BeautifulSoup(str(title_tag), "html.parser")
            for rank in title_copy.find_all("span"):
                rank.decompose()
            title = title_copy.get_text(" ", strip=True)
    return title or a_tag.get_text(" ", strip=True)


def _extract_ebook_candidates(search_soup):
    candidates = []
    seen_mids = set()
    for a_tag in search_soup.find_all("a", href=True):
        href = a_tag["href"]
        if "content.cfm" not in href or "mid=" not in href:
            continue
        mid_match = re.search(r"[?&]mid=(\d+)", href)
        if not mid_match or mid_match.group(1) in seen_mids:
            continue

        container = a_tag.find_parent("div", class_="book")
        if container is None:
            container = a_tag.find_parent(["li", "tr", "article"])
        if container is None:
            container = a_tag.find_parent("div")

        resources = _extract_online_resources(container)
        if not resources:
            continue

        mid = mid_match.group(1)
        seen_mids.add(mid)
        container_text = container.get_text(" ", strip=True) if container else ""
        candidates.append(
            {
                "mid": mid,
                "title": _candidate_title(a_tag, container),
                "author": _extract_author(container_text),
                "detail_url": _absolute_url(href),
                "online_resources": resources,
            }
        )
    return candidates


def _search_ebook_candidates(query_title):
    encoded_title = urllib.parse.quote(query_title)
    search_url = (
        f"{LIB_BASE}/search.cfm?"
        f"m=ss&k0={encoded_title}&t0=k&c0=and&y10=&y20=&cat0=&dt0=&l0=&lv0="
        f"&list_num={MAX_SEARCH_CANDIDATES}&current_page=1"
    )
    response = requests.get(
        search_url, headers=LIB_HEADERS, timeout=LIB_REQUEST_TIMEOUT, verify=False
    )
    if response.status_code != 200:
        raise requests.exceptions.RequestException(f"search returned HTTP {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = _extract_ebook_candidates(soup)
    page_text = soup.get_text(" ", strip=True)
    if not candidates and not any(marker in page_text for marker in ("查無", "0 筆", "無資料")):
        # Some pages can be valid but simply have no online resource. Only raise
        # when no recognizable book result exists either.
        if not soup.find("a", href=re.compile(r"content\.cfm.*mid=")):
            raise LibraryStructureError("搜尋結果頁缺少預期的書目連結或查無結果標記")
    return candidates


def _ebook_title_variants(title):
    variants = []
    normalized = re.sub(r"\s+", " ", (title or "").strip())
    if normalized:
        variants.append((normalized, "完整書名"))

    punctuation_spaced = re.sub(
        r"[^\w\u4e00-\u9fff]+", " ", normalized
    )
    punctuation_spaced = re.sub(r"\s+", " ", punctuation_spaced).strip()
    if punctuation_spaced and punctuation_spaced != normalized:
        variants.append((punctuation_spaced, "標準化書名"))

    main = re.split(r"[:：,，]", normalized, maxsplit=1)[0].strip()
    if main and main != normalized:
        variants.append((main, "主書名"))

    unique = []
    seen = set()
    for query, match_type in variants:
        key = query.casefold()
        if key and key not in seen:
            seen.add(key)
            unique.append((query, match_type))
    return unique


def search_ebook_status(book_title, book_author=""):
    try:
        checked_mids = set()
        for query_title, match_type in _ebook_title_variants(book_title):
            candidates = _search_ebook_candidates(query_title)
            if not candidates:
                continue
            candidates.sort(
                key=lambda candidate: _candidate_sort_key(
                    candidate, query_title, book_author
                )
            )

            short_fallback = match_type != "完整書名" and _chinese_length(query_title) < 4
            for candidate in candidates:
                if candidate["mid"] in checked_mids:
                    continue
                if len(checked_mids) >= MAX_SEARCH_CANDIDATES:
                    break
                title_score = _title_similarity(query_title, candidate.get("title"))
                author_match = _author_matches(book_author, candidate.get("author"))
                if short_fallback and not author_match:
                    continue
                if title_score < 0.72:
                    continue
                checked_mids.add(candidate["mid"])
                candidate["match_type"] = match_type
                candidate["has_online_resource"] = True
                return candidate
            if len(checked_mids) >= MAX_SEARCH_CANDIDATES:
                break

        return {"has_online_resource": False, "online_resources": []}
    except requests.exceptions.Timeout as exc:
        print(f"ebook lookup timed out for {book_title}: {exc}", file=sys.stderr)
        return {"has_online_resource": False, "online_resources": [], "error": "圖書館回應逾時"}
    except requests.exceptions.RequestException as exc:
        print(f"ebook lookup failed for {book_title}: {exc}", file=sys.stderr)
        return {"has_online_resource": False, "online_resources": [], "error": "圖書館連線失敗"}
    except LibraryStructureError as exc:
        print(f"ebook lookup structure changed for {book_title}: {exc}", file=sys.stderr)
        return {
            "has_online_resource": False,
            "online_resources": [],
            "error": "頁面格式異常",
            "error_type": "structure",
        }


def collect_ebooks(html_files):
    source_books = _extract_source_books(html_files)
    ebooks = []
    total = len(source_books)
    print(f"共偵測到 {total} 本書籍，只保留有網路資源的結果...")

    for index, book in enumerate(source_books, 1):
        print(f"[{index}/{total}] 正在查詢電子資源: {book['title']}...")
        result = search_ebook_status(book["title"], book.get("author", ""))
        if result.get("has_online_resource"):
            record = deepcopy(book)
            record.update(
                {
                    "matched_title": result.get("title") or "",
                    "matched_author": result.get("author") or "",
                    "matched_url": result.get("detail_url") or "",
                    "match_type": result.get("match_type") or "",
                    "online_resources": result.get("online_resources") or [],
                }
            )
            ebooks.append(record)
            print(f"  已收入電子書結果（目前 {len(ebooks)} 本）")
        if BOOK_SLEEP_SECONDS > 0:
            time.sleep(BOOK_SLEEP_SECONDS)

    return ebooks


def _ebook_page_html(records, page_number, page_count):
    rows = []
    for record in records:
        resource_links = "<br>".join(
            f'<a href="{resource}" target="_blank" rel="noopener">{resource}</a>'
            for resource in record.get("online_resources", [])
        )
        matched = record.get("matched_title") or "-"
        if record.get("matched_url"):
            matched = (
                f'<a href="{record["matched_url"]}" target="_blank" rel="noopener">'
                f"{matched}</a>"
            )
        rows.append(
            f"""
            <tr id="ebook-{record["output_number"]}">
              <td data-label="序號">{record["output_number"]}</td>
              <td data-label="書名"><a href="{record.get("url") or "#"}">{record["title"]}</a></td>
              <td data-label="作者">{record.get("author") or ""}</td>
              <td data-label="出版社">{record.get("publisher") or ""}</td>
              <td data-label="適讀年段">{record.get("range") or ""}</td>
              <td data-label="命中館藏">{matched}</td>
              <td data-label="網路資源">{resource_links or "-"}</td>
            </tr>
            """
        )
    nav_links = "　".join(
        f"<strong>第 {i} 頁</strong>" if i == page_number else f'<a href="ebooks_page_{i}.html">第 {i} 頁</a>'
        for i in range(1, page_count + 1)
    )
    return f"""<!doctype html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>電子書資源 - 第 {page_number} 頁</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f8f9fa; color: #263238; }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 28px 16px; }}
    .info {{ margin: 16px 0; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 4px 16px rgba(0,0,0,.08); }}
    th {{ background: #0f766e; color: #fff; text-align: left; }}
    th, td {{ padding: 12px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
    a {{ color: #075985; font-weight: 700; }}
  </style>
</head>
<body><main>
  <h1>電子書資源（第 {page_number}/{page_count} 頁）</h1>
  <div class="info"><a href="ebooks_index.html">電子書首頁</a>　{nav_links}</div>
  <table>
    <thead><tr><th>序號</th><th>書名</th><th>作者</th><th>出版社</th><th>適讀年段</th><th>命中館藏</th><th>網路資源</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</main></body></html>
"""


def _ebook_index_html(records, page_files):
    embedded = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    links = "\n".join(
        f'<a class="page-link" href="{filename}">第 {index} 頁</a>'
        for index, filename in enumerate(page_files, 1)
    )
    return f"""<!doctype html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>電子書資源清單</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1040px; margin: 0 auto; padding: 32px 20px; background: #f8f9fa; color: #263238; }}
    main {{ display: grid; gap: 20px; }}
    .panel {{ background: #fff; padding: 24px; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.08); }}
    input {{ width: 100%; min-height: 46px; padding: 0 14px; border: 1px solid #ccd6dd; border-radius: 8px; font: inherit; }}
    .list {{ display: grid; gap: 10px; }}
    .book {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; background: #fff; }}
    .book-title {{ font-weight: 800; color: #075985; text-decoration: none; }}
    .meta {{ color: #64748b; font-size: .92rem; margin-top: 4px; }}
    .resource {{ display: inline-block; margin: 6px 6px 0 0; padding: 4px 9px; border-radius: 999px; background: #ccfbf1; color: #115e59; font-weight: 700; text-decoration: none; }}
    .pages {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }}
    .page-link {{ display: block; padding: 16px; text-align: center; color: #fff; background: #0f766e; border-radius: 8px; text-decoration: none; font-weight: 700; }}
  </style>
</head>
<body><main>
  <section class="panel">
    <h1>電子書資源清單</h1>
    <p>共找到 {len(records)} 本有網路資源的書籍，分為 {len(page_files)} 頁。</p>
    <input id="search-input" type="search" placeholder="輸入書名、作者、出版社或網路資源">
    <p id="result-count">載入中...</p>
  </section>
  <section class="panel">
    <h2>搜尋結果</h2>
    <div class="list" id="results-list"></div>
  </section>
  <section class="panel">
    <h2>分頁</h2>
    <nav class="pages">{links}</nav>
  </section>
</main>
<script id="ebooks-data" type="application/json">{embedded}</script>
<script>
const searchInput = document.getElementById("search-input");
const resultCount = document.getElementById("result-count");
const resultsList = document.getElementById("results-list");
const books = JSON.parse(document.getElementById("ebooks-data").textContent || "[]");

function escapeHtml(value) {{
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}}

function normalize(value) {{
  return String(value || "").normalize("NFKC").toLowerCase();
}}

function matches(book, query) {{
  if (!query) return true;
  return [
    book.title,
    book.matched_title,
    book.author,
    book.publisher,
    book.range,
    ...(book.online_resources || [])
  ].some(value => normalize(value).includes(query));
}}

function bookHtml(book) {{
  const resources = (book.online_resources || [])
    .map(url => `<a class="resource" href="${{escapeHtml(url)}}" target="_blank" rel="noopener">網路資源</a>`)
    .join("");
  return `<article class="book">
    <a class="book-title" href="${{escapeHtml(book.anchor)}}">${{escapeHtml(book.title)}}</a>
    <div class="meta">${{escapeHtml(book.author || "-")}} / ${{escapeHtml(book.publisher || "-")}} / ${{escapeHtml(book.range || "-")}}</div>
    <div class="meta">命中館藏：${{book.matched_url ? `<a href="${{escapeHtml(book.matched_url)}}" target="_blank" rel="noopener">${{escapeHtml(book.matched_title || "-")}}</a>` : escapeHtml(book.matched_title || "-")}}</div>
    <div>${{resources}}</div>
  </article>`;
}}

function render() {{
  const query = normalize(searchInput.value);
  const filtered = books.filter(book => matches(book, query));
  resultCount.textContent = `共 ${{filtered.length}} 本有網路資源書籍`;
  resultsList.innerHTML = filtered.length
    ? filtered.map(bookHtml).join("")
    : '<p>沒有符合條件的電子書資源。</p>';
}}

searchInput.addEventListener("input", render);
render();
</script>
</body></html>
"""


def write_ebook_pages(records, output_dir):
    page_count = (
        (len(records) + EBOOK_OUTPUT_PAGE_SIZE - 1) // EBOOK_OUTPUT_PAGE_SIZE
        if records
        else 0
    )
    page_files = []
    output_records = []
    for page_number in range(1, page_count + 1):
        start = (page_number - 1) * EBOOK_OUTPUT_PAGE_SIZE
        chunk = []
        for offset, record in enumerate(records[start:start + EBOOK_OUTPUT_PAGE_SIZE], 1):
            output_number = start + offset
            output_record = deepcopy(record)
            output_record["output_number"] = output_number
            output_record["page"] = f"ebooks_page_{page_number}.html"
            output_record["anchor"] = f"ebooks_page_{page_number}.html#ebook-{output_number}"
            chunk.append(output_record)
            output_records.append(output_record)

        filename = f"ebooks_page_{page_number}.html"
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(_ebook_page_html(chunk, page_number, page_count))
        page_files.append(filename)
        print(f"已產生電子書第 {page_number} 頁（{len(chunk)} 本）：{filename}")

    with open(os.path.join(output_dir, "ebooks_data.json"), "w", encoding="utf-8") as f:
        json.dump(output_records, f, ensure_ascii=False, indent=2)
    with open(os.path.join(output_dir, "ebooks_index.html"), "w", encoding="utf-8") as f:
        f.write(_ebook_index_html(output_records, page_files))
    print(f"電子書首頁已生成：{os.path.join(output_dir, 'ebooks_index.html')}")
    print(f"電子書資料已生成：{os.path.join(output_dir, 'ebooks_data.json')}")
    return page_files


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pattern = os.path.join(script_dir, "books_page_*.html")
    html_files = [
        path
        for path in glob.glob(pattern)
        if not path.endswith("_with_library.html")
    ]
    if not html_files:
        print("找不到 books_page_*.html 來源檔案")
        return

    ebooks = collect_ebooks(html_files)
    write_ebook_pages(ebooks, script_dir)


if __name__ == "__main__":
    main()
