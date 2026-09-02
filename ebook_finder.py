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

    colon_parts = re.split(r"\s*[:：]\s*", normalized, maxsplit=1)
    search_base = normalized
    if len(colon_parts) == 2 and colon_parts[1].strip():
        search_base = colon_parts[1].strip()

    if search_base:
        variants.append((search_base, "完整書名"))

    punctuation_spaced = re.sub(
        r"[^\w\u4e00-\u9fff]+", " ", search_base
    )
    punctuation_spaced = re.sub(r"\s+", " ", punctuation_spaced).strip()
    if punctuation_spaced and punctuation_spaced != search_base:
        variants.append((punctuation_spaced, "標準化書名"))

    main = re.split(r"\s*[,，]\s*", search_base, maxsplit=1)[0].strip()
    if main and main != search_base:
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
            try:
                candidates = _search_ebook_candidates(query_title)
            except LibraryStructureError as exc:
                print(
                    f"ebook lookup structure changed for {book_title} "
                    f"(variant: {query_title}): {exc}",
                    file=sys.stderr,
                )
                continue
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
  <link rel="icon" href="icon.svg" type="image/svg+xml">
  <link rel="shortcut icon" href="icon.svg" type="image/svg+xml">
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


def _ebook_index_html():
    return """<!doctype html>
<html lang="zh-TW" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>電子書資源清單</title>
  <link rel="icon" href="icon.svg" type="image/svg+xml">
  <link rel="shortcut icon" href="icon.svg" type="image/svg+xml">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Noto+Sans+TC:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-sans: 'Outfit', 'Noto Sans TC', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --bg-primary: #fffaf5;
      --bg-card: rgba(255, 255, 255, 0.92);
      --bg-card-hover: #ffffff;
      --bg-inner: rgba(254, 243, 235, 0.65);
      --text-primary: #292524;
      --text-secondary: #78716c;
      --text-muted: #a8a29e;
      --accent: #ea580c;
      --accent-light: #ffedd5;
      --accent-gradient: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
      --border: rgba(254, 215, 170, 0.8);
      --border-subtle: rgba(231, 229, 228, 0.85);
      --border-focus: #f97316;
      --resource-bg: #fff7ed;
      --resource-text: #c2410c;
      --resource-border: #fed7aa;
      --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
      --shadow-md: 0 4px 16px -2px rgba(234, 88, 12, 0.08), 0 2px 6px -1px rgba(0, 0, 0, 0.04);
      --glass-blur: blur(14px);
    }
    [data-theme="dark"] {
      --bg-primary: #14110e;
      --bg-card: rgba(28, 25, 23, 0.92);
      --bg-card-hover: rgba(38, 34, 31, 0.98);
      --bg-inner: rgba(41, 37, 36, 0.65);
      --text-primary: #fafaf9;
      --text-secondary: #a8a29e;
      --text-muted: #78716c;
      --accent: #fb923c;
      --accent-light: rgba(251, 146, 60, 0.18);
      --accent-gradient: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
      --border: rgba(120, 113, 108, 0.4);
      --border-subtle: rgba(68, 64, 60, 0.6);
      --border-focus: #fb923c;
      --resource-bg: rgba(251, 146, 60, 0.15);
      --resource-text: #fdba74;
      --resource-border: rgba(251, 146, 60, 0.3);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; transition: background-color .25s ease, border-color .25s ease, color .25s ease, box-shadow .25s ease; }
    body { font-family: var(--font-sans); background-color: var(--bg-primary); background-image: radial-gradient(circle at 10% 10%, rgba(249, 115, 22, 0.08), transparent 30%), radial-gradient(circle at 90% 90%, rgba(234, 88, 12, 0.06), transparent 35%); background-attachment: fixed; color: var(--text-primary); min-height: 100vh; padding: 24px 16px 48px; display: flex; flex-direction: column; align-items: center; }
    .container { width: 100%; max-width: 960px; display: flex; flex-direction: column; gap: 20px; }
    .app-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; padding: 20px 24px; background: var(--bg-card); backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); border: 1.5px solid var(--border); border-radius: 18px; box-shadow: var(--shadow-md); }
    .header-brand { display: flex; align-items: center; gap: 14px; }
    .brand-icon { width: 44px; height: 44px; border-radius: 12px; background: var(--accent-gradient); display: flex; align-items: center; justify-content: center; font-size: 1.4rem; color: #fff; box-shadow: 0 4px 12px rgba(234, 88, 12, 0.3); flex-shrink: 0; }
    .header-title-group h1 { font-size: 1.35rem; font-weight: 800; letter-spacing: -0.01em; }
    .header-title-group p { font-size: 0.85rem; color: var(--text-secondary); margin-top: 2px; }
    .header-actions { display: flex; align-items: center; gap: 10px; }
    .btn-nav { display: inline-flex; align-items: center; gap: 8px; padding: 10px 18px; border-radius: 12px; font-size: 0.92rem; font-weight: 700; color: #fff; background: var(--accent-gradient); text-decoration: none; border: none; cursor: pointer; box-shadow: 0 4px 14px rgba(234, 88, 12, 0.28); }
    .btn-icon { width: 40px; height: 40px; border-radius: 12px; background: var(--bg-inner); border: 1px solid var(--border); color: var(--text-primary); cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; }
    .panel { background: var(--bg-card); backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); border: 1.5px solid var(--border); border-radius: 18px; padding: 22px; box-shadow: var(--shadow-sm); }
    .panel-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
    .panel-title { font-size: 1.15rem; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .search-wrapper { position: relative; display: flex; align-items: center; }
    .search-icon { position: absolute; left: 16px; font-size: 1.1rem; color: var(--text-muted); pointer-events: none; }
    .search-input { width: 100%; min-height: 50px; padding: 0 88px 0 46px; border: 1.5px solid var(--border); border-radius: 14px; background: var(--bg-inner); color: var(--text-primary); font-family: inherit; font-size: 0.95rem; outline: none; }
    .search-input:focus { border-color: var(--border-focus); background: var(--bg-card-hover); box-shadow: 0 0 0 4px rgba(249, 115, 22, 0.18); }
    .search-clear-btn { position: absolute; right: 12px; width: 28px; height: 28px; border-radius: 50%; border: none; background: transparent; color: var(--text-muted); font-size: 1.2rem; cursor: pointer; display: none; align-items: center; justify-content: center; }
    .voice-search-btn { position: absolute; right: 46px; width: 30px; height: 30px; border-radius: 50%; border: 1px solid var(--border); background: var(--bg-card); color: var(--accent); font-size: 1rem; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; box-shadow: var(--shadow-sm); }
    .voice-search-btn.recording { color: #fff; background: #ef4444; border-color: #ef4444; animation: voice-pulse 1.35s ease-in-out infinite; }
    .voice-search-btn[hidden] { display: none; }
    @keyframes voice-pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.35); } 50% { box-shadow: 0 0 0 8px rgba(239, 68, 68, 0); } }
    .search-meta-row { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; font-size: 0.88rem; color: var(--text-secondary); }
    .count-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; background: var(--accent-light); color: var(--accent); border-radius: 999px; font-weight: 700; font-size: 0.85rem; }
    .results-toolbar { display: flex; align-items: center; gap: 10px; color: var(--text-secondary); font-size: 0.88rem; }
    .results-toolbar-group { display: flex; align-items: center; justify-content: flex-end; gap: 12px; flex-wrap: wrap; }
    .page-size-select { min-height: 38px; padding: 0 34px 0 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-inner); color: var(--text-primary); font-family: inherit; font-weight: 700; cursor: pointer; outline: none; }
    .pagination { display: flex; justify-content: center; align-items: center; gap: 12px; margin-top: 16px; }
    .pagination-top { margin-top: 0; }
    .page-btn { min-width: 40px; height: 38px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-inner); color: var(--text-primary); cursor: pointer; font-size: 1rem; font-weight: 800; }
    .page-btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .page-info { color: var(--text-secondary); font-size: 0.9rem; font-weight: 700; min-width: 96px; text-align: center; }
    .hidden { display: none !important; }
    .list { display: grid; gap: 12px; }
    .book-card { background: var(--bg-card); border: 1.5px solid var(--border); border-radius: 14px; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; box-shadow: var(--shadow-sm); }
    .book-title { font-size: 1.08rem; font-weight: 700; color: var(--text-primary); text-decoration: none; line-height: 1.4; }
    .book-meta { font-size: 0.88rem; color: var(--text-secondary); display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; }
    .book-meta-item { display: inline-flex; align-items: center; gap: 4px; }
    .book-matched { font-size: 0.88rem; color: var(--text-secondary); padding: 8px 12px; background: var(--bg-inner); border-radius: 10px; border: 1px solid var(--border-subtle); }
    .book-matched a { color: var(--accent); font-weight: 600; text-decoration: none; }
    .resource-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
    .resource-pill { display: inline-flex; align-items: center; gap: 5px; padding: 6px 14px; border-radius: 999px; background: var(--resource-bg); color: var(--resource-text); border: 1px solid var(--resource-border); font-weight: 700; font-size: 0.85rem; text-decoration: none; }
    .muted-text { color: var(--text-muted); text-align: center; padding: 24px; font-size: 0.95rem; }
    @media (max-width: 640px) {
      body { padding: 16px 10px 32px; }
      .app-header { padding: 16px; }
      .header-actions { width: 100%; justify-content: space-between; }
      .btn-nav { flex: 1; justify-content: center; }
      .panel { padding: 16px; }
      .results-toolbar-group { width: 100%; justify-content: space-between; }
      .pagination { gap: 8px; }
    }
  </style>
</head>
<body>
<div class="container">
  <header class="app-header">
    <div class="header-brand">
      <div class="brand-icon">🌐</div>
      <div class="header-title-group">
        <h1>電子書資源清單</h1>
        <p>依 ebooks_data.json 載入可線上閱讀的書籍清單。</p>
      </div>
    </div>
    <div class="header-actions">
      <a href="index.html" class="btn-nav"><span>←</span><span>返回館藏查詢</span></a>
      <button type="button" class="btn-icon" id="theme-toggle" aria-label="切換深淺色模式" title="切換深淺色模式">🌙</button>
    </div>
  </header>
  <section class="panel">
    <div class="search-wrapper">
      <span class="search-icon">🔍</span>
      <input id="search-input" class="search-input" type="search" placeholder="輸入書名、作者、出版社或網路資源" autocomplete="off">
      <button type="button" class="voice-search-btn" id="voice-search" aria-label="語音輸入搜尋" title="語音輸入搜尋">🎤</button>
      <button type="button" class="search-clear-btn" id="search-clear" aria-label="清除搜尋">&times;</button>
    </div>
    <div class="search-meta-row"><span>即時搜尋線上電子書資源</span><span class="count-badge" id="result-count">載入中...</span></div>
  </section>
  <section class="panel">
    <div class="panel-header">
      <h2 class="panel-title"><span>📋</span> 搜尋結果</h2>
      <div class="results-toolbar-group">
        <div class="pagination pagination-top hidden" id="pagination-top">
          <button type="button" class="page-btn" id="prev-page-top" aria-label="上一頁">‹</button>
          <span class="page-info" id="page-info-top">第 1 / 1 頁</span>
          <button type="button" class="page-btn" id="next-page-top" aria-label="下一頁">›</button>
        </div>
        <label class="results-toolbar" for="page-size"><span>每頁顯示</span><select class="page-size-select" id="page-size"><option value="10">10</option><option value="20" selected>20</option><option value="50">50</option><option value="100">100</option></select></label>
      </div>
    </div>
    <div class="list" id="results-list"></div>
    <div class="pagination hidden" id="pagination">
      <button type="button" class="page-btn" id="prev-page" aria-label="上一頁">‹</button>
      <span class="page-info" id="page-info">第 1 / 1 頁</span>
      <button type="button" class="page-btn" id="next-page" aria-label="下一頁">›</button>
    </div>
  </section>
</div>
<script>
const DATA_URL = "ebooks_data.json";
const searchInput = document.getElementById("search-input");
const voiceSearchBtn = document.getElementById("voice-search");
const searchClearBtn = document.getElementById("search-clear");
const resultCount = document.getElementById("result-count");
const resultsList = document.getElementById("results-list");
const themeToggleBtn = document.getElementById("theme-toggle");
const pageSizeSelect = document.getElementById("page-size");
const pagination = document.getElementById("pagination");
const paginationTop = document.getElementById("pagination-top");
const prevPageBtn = document.getElementById("prev-page");
const prevPageTopBtn = document.getElementById("prev-page-top");
const nextPageBtn = document.getElementById("next-page");
const nextPageTopBtn = document.getElementById("next-page-top");
const pageInfo = document.getElementById("page-info");
const pageInfoTop = document.getElementById("page-info-top");
let books = [];
let filteredBooks = [];
let currentPage = 1;
let pageSize = 20;
function initTheme() {
  const savedTheme = localStorage.getItem("books_theme") || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", savedTheme);
  themeToggleBtn.textContent = savedTheme === "dark" ? "☀️" : "🌙";
}
themeToggleBtn.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("books_theme", next);
  themeToggleBtn.textContent = next === "dark" ? "☀️" : "🌙";
});
initTheme();
function escapeHtml(value) {
  return String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function normalize(value) {
  return String(value || "").normalize("NFKC").toLowerCase();
}
function matches(book, query) {
  if (!query) return true;
  return [book.title, book.matched_title, book.author, book.publisher, book.range, book.certification, ...(book.online_resources || [])].some(value => normalize(value).includes(query));
}
function bookHtml(book) {
  const resources = (book.online_resources || []).map(url => `<a class="resource-pill" href="${escapeHtml(url)}" target="_blank" rel="noopener"><span>🌐</span> 網路資源</a>`).join("");
  const metaItems = [
    book.author ? `<span class="book-meta-item">✍️ ${escapeHtml(book.author)}</span>` : "",
    book.publisher ? `<span class="book-meta-item">🏢 ${escapeHtml(book.publisher)}</span>` : "",
    book.range ? `<span class="book-meta-item">🎓 ${escapeHtml(book.range)}</span>` : ""
  ].filter(Boolean).join(" · ");
  return `<article class="book-card"><a class="book-title" href="${escapeHtml(book.url || book.matched_url || '#')}" target="_blank" rel="noopener">${escapeHtml(book.title)}</a><div class="book-meta">${metaItems || "-"}</div><div class="book-matched"><strong>命中館藏：</strong>${book.matched_url ? `<a href="${escapeHtml(book.matched_url)}" target="_blank" rel="noopener">${escapeHtml(book.matched_title || "-")}</a>` : escapeHtml(book.matched_title || "-")}</div><div class="resource-list">${resources}</div></article>`;
}
function syncPagination(totalPages) {
  const shouldShow = filteredBooks.length > pageSize;
  pagination.classList.toggle("hidden", !shouldShow);
  paginationTop.classList.toggle("hidden", !shouldShow);
  pageInfo.textContent = `第 ${currentPage} / ${totalPages} 頁`;
  pageInfoTop.textContent = pageInfo.textContent;
  prevPageBtn.disabled = currentPage === 1;
  prevPageTopBtn.disabled = currentPage === 1;
  nextPageBtn.disabled = currentPage === totalPages;
  nextPageTopBtn.disabled = currentPage === totalPages;
}
function renderResults() {
  const totalPages = Math.max(1, Math.ceil(filteredBooks.length / pageSize));
  if (currentPage > totalPages) currentPage = totalPages;
  const start = (currentPage - 1) * pageSize;
  const currentBooks = filteredBooks.slice(start, start + pageSize);
  syncPagination(totalPages);
  resultCount.textContent = `共 ${filteredBooks.length} 本有網路資源書籍`;
  resultsList.innerHTML = currentBooks.length ? currentBooks.map(bookHtml).join("") : '<p class="muted-text">沒有符合條件的電子書資源。</p>';
}
function applySearch() {
  const rawValue = searchInput.value;
  searchClearBtn.style.display = rawValue ? "flex" : "none";
  currentPage = 1;
  filteredBooks = books.filter(book => matches(book, normalize(rawValue)));
  renderResults();
}
function initializeBooks(data) {
  books = Array.isArray(data) ? data : [];
  filteredBooks = books.slice();
  applySearch();
}
function goToPreviousPage() {
  if (currentPage > 1) {
    currentPage -= 1;
    renderResults();
    resultsList.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
function goToNextPage() {
  const totalPages = Math.max(1, Math.ceil(filteredBooks.length / pageSize));
  if (currentPage < totalPages) {
    currentPage += 1;
    renderResults();
    resultsList.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
searchClearBtn.addEventListener("click", () => {
  searchInput.value = "";
  searchInput.focus();
  applySearch();
});
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SpeechRecognition && voiceSearchBtn) {
  const recognition = new SpeechRecognition();
  recognition.lang = "zh-TW";
  recognition.continuous = false;
  recognition.interimResults = false;
  let isRecording = false;
  function stopVoiceSearch() {
    isRecording = false;
    voiceSearchBtn.classList.remove("recording");
    voiceSearchBtn.textContent = "🎤";
    try { recognition.stop(); } catch {}
  }
  voiceSearchBtn.addEventListener("click", () => {
    if (isRecording) {
      stopVoiceSearch();
      return;
    }
    try {
      recognition.start();
      isRecording = true;
      voiceSearchBtn.classList.add("recording");
      voiceSearchBtn.textContent = "■";
    } catch {
      stopVoiceSearch();
    }
  });
  recognition.addEventListener("result", event => {
    const transcript = event.results[0][0].transcript.trim();
    if (transcript) {
      searchInput.value = transcript;
      applySearch();
    }
  });
  recognition.addEventListener("end", stopVoiceSearch);
  recognition.addEventListener("error", stopVoiceSearch);
} else if (voiceSearchBtn) {
  voiceSearchBtn.hidden = true;
}
pageSizeSelect.addEventListener("change", () => {
  pageSize = Number(pageSizeSelect.value) || 20;
  currentPage = 1;
  renderResults();
});
prevPageBtn.addEventListener("click", goToPreviousPage);
prevPageTopBtn.addEventListener("click", goToPreviousPage);
nextPageBtn.addEventListener("click", goToNextPage);
nextPageTopBtn.addEventListener("click", goToNextPage);
searchInput.addEventListener("input", applySearch);
fetch(DATA_URL)
  .then(response => response.json())
  .then(initializeBooks)
  .catch(() => {
    resultCount.textContent = "無法讀取 ebooks_data.json。請確認檔案與首頁放在同一個目錄。";
    resultsList.innerHTML = '<p class="muted-text">電子書資料載入失敗。</p>';
  });
</script>
</body>
</html>
"""

def write_ebook_pages(records, output_dir):
    output_records = []
    for index, record in enumerate(records, 1):
        output_record = deepcopy(record)
        output_record["output_number"] = index
        output_records.append(output_record)

    with open(os.path.join(output_dir, "ebooks_data.json"), "w", encoding="utf-8") as f:
        json.dump(output_records, f, ensure_ascii=False, indent=2)
    with open(os.path.join(output_dir, "ebooks_index.html"), "w", encoding="utf-8") as f:
        f.write(_ebook_index_html())
    print(f"電子書首頁已生成：{os.path.join(output_dir, 'ebooks_index.html')}")
    print(f"電子書資料已生成：{os.path.join(output_dir, 'ebooks_data.json')}")
    return []

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
