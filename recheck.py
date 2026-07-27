import argparse
import glob
import os
import re
import sys
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from integrate import (
    BOOK_SLEEP_SECONDS,
    LibraryStructureError,
    OUTPUT_PAGE_SIZE,
    _append_holding_cells,
    _lookup_candidate,
    search_library_status,
)


MAX_RETRIES = max(0, int(os.getenv("RECHECK_MAX_RETRIES", "2")))
RETRY_SECONDS = max(0.0, float(os.getenv("RECHECK_RETRY_SECONDS", "1")))
TAIPEI_TZ = timezone(timedelta(hours=8), name="Asia/Taipei")


def _page_number(path):
    match = re.search(r"books_with_library_page_(\d+)\.html$", os.path.basename(path))
    return int(match.group(1)) if match else sys.maxsize


def discover_input_files(script_dir):
    index_path = os.path.join(script_dir, "books_with_library_index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index_soup = BeautifulSoup(f.read(), "html.parser")
        linked_files = []
        seen = set()
        for link in index_soup.find_all("a", href=True):
            filename = os.path.basename(link["href"])
            if not re.fullmatch(r"books_with_library_page_\d+\.html", filename):
                continue
            path = os.path.join(script_dir, filename)
            if os.path.exists(path) and path not in seen:
                seen.add(path)
                linked_files.append(path)
        if linked_files:
            return sorted(linked_files, key=_page_number)

    return sorted(
        glob.glob(os.path.join(script_dir, "books_with_library_page_*.html")),
        key=_page_number,
    )


def _extract_mid(row):
    match_cell = row.find("td", {"data-label": "命中館藏"})
    link = match_cell.find("a", href=True) if match_cell else None
    if not link:
        return None
    match = re.search(r"[?&]mid=(\d+)", link["href"])
    return match.group(1) if match else None


def _cell_text(row, label):
    cell = row.find("td", {"data-label": label})
    return cell.get_text(" ", strip=True) if cell else ""


def _status_values(row):
    cell = row.find("td", {"data-label": "館藏狀態"})
    if not cell:
        return []
    values = [
        element.get_text(" ", strip=True)
        for element in cell.find_all(["span", "div"], recursive=True)
        if element.get_text(" ", strip=True)
    ]
    if not values:
        text = cell.get_text(" ", strip=True)
        return [text] if text and text != "-" else []
    # A div containing a span duplicates the same status; retain unique values in order.
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _result_status_values(result):
    return [
        item.get("status") or "-"
        for item in result.get("items") or []
    ]


def _status_change(old_values, new_values, outcome):
    if outcome == "unconfirmed":
        return "本次未確認"
    if outcome == "failed":
        return "查詢失敗"
    old_text = "、".join(old_values) if old_values else "-"
    new_text = "、".join(new_values) if new_values else "-"
    if old_values == new_values:
        return "無變化"
    return f"{old_text} → {new_text}"


def _fallback_search(title, author):
    result = search_library_status(title, author)
    if result.get("error"):
        raise requests.exceptions.RequestException(result["error"])
    return result if result.get("has_holding") else None


def _lookup_once(mid, matched_title, title, author):
    if not mid:
        return _fallback_search(title, author)
    try:
        return _lookup_candidate(
            {"mid": mid, "title": matched_title or title, "author": author}
        )
    except LibraryStructureError:
        # The saved catalog record may have disappeared or changed shape.
        return _fallback_search(title, author)


def recheck_book(mid, matched_title, title, author):
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = _lookup_once(mid, matched_title, title, author)
            if result is None:
                return {"outcome": "unconfirmed", "has_holding": False, "items": []}
            result["outcome"] = "holding"
            return result
        except (requests.exceptions.RequestException, LibraryStructureError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES and RETRY_SECONDS > 0:
                time.sleep(RETRY_SECONDS)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES and RETRY_SECONDS > 0:
                time.sleep(RETRY_SECONDS)

    return {
        "outcome": "failed",
        "has_holding": False,
        "items": [],
        "error": str(last_error) if last_error else "查詢失敗",
    }


def _replace_nonholding_cells(soup, row, label):
    for field in ["館藏情形", "索書號", "館藏狀態"]:
        for cell in row.find_all("td", {"data-label": field}):
            cell.decompose()

    holding_cell = soup.new_tag("td")
    holding_cell["data-label"] = "館藏情形"
    pill = soup.new_tag("span")
    pill["class"] = "status status-no"
    pill.string = label
    holding_cell.append(pill)
    row.append(holding_cell)

    call_cell = soup.new_tag("td")
    call_cell["data-label"] = "索書號"
    call_cell.string = "-"
    row.append(call_cell)

    status_cell = soup.new_tag("td")
    status_cell["data-label"] = "館藏狀態"
    status_cell.string = label
    row.append(status_cell)


def _append_change_cell(soup, row, text):
    for cell in row.find_all("td", {"data-label": "狀態變化"}):
        cell.decompose()
    change_cell = soup.new_tag("td")
    change_cell["data-label"] = "狀態變化"
    change_cell.string = text
    row.append(change_cell)


def _prepare_template(source_soup):
    template = deepcopy(source_soup)
    tbody = template.find("tbody")
    header_row = template.find("thead").find("tr") if template.find("thead") else None
    if not tbody or not header_row:
        raise LibraryStructureError("重新查詢來源缺少預期表格")
    for th in header_row.find_all("th"):
        if th.get_text(strip=True) == "狀態變化":
            th.decompose()
    th = template.new_tag("th")
    th.string = "狀態變化"
    header_row.append(th)
    tbody.clear()
    return template


def load_previous_rows(input_files):
    rows = []
    template_source = None
    for input_file in sorted(input_files, key=_page_number):
        with open(input_file, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        tbody = soup.find("tbody")
        if not tbody:
            raise LibraryStructureError(f"{input_file} 缺少館藏結果表格")
        if template_source is None:
            template_source = soup
        rows.extend(deepcopy(row) for row in tbody.find_all("tr"))
    if template_source is None:
        raise LibraryStructureError("沒有可使用的上次館藏結果")
    return _prepare_template(template_source), rows


def recheck_rows(rows):
    updated_rows = []
    stats = {
        "total": len(rows),
        "holding": 0,
        "available": 0,
        "other": 0,
        "unconfirmed": 0,
        "failed": 0,
    }

    for index, row in enumerate(rows, 1):
        title = _cell_text(row, "書名")
        author = _cell_text(row, "作者")
        matched_title = _cell_text(row, "命中館藏")
        mid = _extract_mid(row)
        old_values = _status_values(row)
        print(f"[{index}/{len(rows)}] 重新確認: {title}...")

        result = recheck_book(mid, matched_title, title, author)
        outcome = result["outcome"]
        if outcome == "holding":
            _append_holding_cells(BeautifulSoup("", "html.parser"), row, result)
            new_values = _result_status_values(result)
            stats["holding"] += 1
            if any("在架" in status for status in new_values):
                stats["available"] += 1
            else:
                stats["other"] += 1
        elif outcome == "unconfirmed":
            _replace_nonholding_cells(
                BeautifulSoup("", "html.parser"),
                row,
                "本次未確認到館藏",
            )
            new_values = []
            stats["unconfirmed"] += 1
        else:
            _replace_nonholding_cells(
                BeautifulSoup("", "html.parser"),
                row,
                "查詢失敗",
            )
            new_values = []
            stats["failed"] += 1

        _append_change_cell(
            BeautifulSoup("", "html.parser"),
            row,
            _status_change(old_values, new_values, outcome),
        )
        updated_rows.append(row)
        if BOOK_SLEEP_SECONDS > 0:
            time.sleep(BOOK_SLEEP_SECONDS)

    return updated_rows, stats


def _add_navigation(soup, current_page, page_count):
    table = soup.find("table")
    if not table:
        return
    nav = soup.new_tag("div")
    nav["class"] = "info"
    home = soup.new_tag("a", href="books_rechecked_index.html")
    home.string = "重新確認首頁"
    nav.append(home)
    nav.append("　")
    for page_number in range(1, page_count + 1):
        if page_number == current_page:
            current = soup.new_tag("strong")
            current.string = f"第 {page_number} 頁"
            nav.append(current)
        else:
            link = soup.new_tag("a", href=f"books_rechecked_page_{page_number}.html")
            link.string = f"第 {page_number} 頁"
            nav.append(link)
        if page_number < page_count:
            nav.append("　")
    table.insert_before(nav)


def write_rechecked_pages(template, rows, stats, output_dir, checked_at):
    page_count = (len(rows) + OUTPUT_PAGE_SIZE - 1) // OUTPUT_PAGE_SIZE if rows else 0
    page_files = []
    for page_number in range(1, page_count + 1):
        soup = deepcopy(template)
        if soup.title:
            soup.title.string = f"館藏重新確認 - 第 {page_number} 頁"
        heading = soup.find("h1")
        if heading:
            heading.string = f"館藏重新確認（第 {page_number}/{page_count} 頁）"
        tbody = soup.find("tbody")
        start = (page_number - 1) * OUTPUT_PAGE_SIZE
        for offset, row in enumerate(rows[start:start + OUTPUT_PAGE_SIZE], 1):
            output_row = deepcopy(row)
            number_cell = output_row.find("td", {"data-label": "序號"})
            if number_cell:
                number_cell.string = str(start + offset)
            tbody.append(output_row)
        _add_navigation(soup, page_number, page_count)

        filename = f"books_rechecked_page_{page_number}.html"
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(str(soup))
        page_files.append(filename)
        print(f"已產生重新確認第 {page_number} 頁：{filename}")

    write_rechecked_index(output_dir, page_files, stats, checked_at)
    return page_files


def write_rechecked_index(output_dir, page_files, stats, checked_at):
    links = "\n".join(
        f'<a class="page-link" href="{filename}">第 {index} 頁</a>'
        for index, filename in enumerate(page_files, 1)
    )
    html = f"""<!doctype html>
<html lang="zh-TW"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>館藏重新確認結果</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 900px; margin: 0 auto; padding: 36px 20px; background: #f4f6f8; color: #263238; }}
    main {{ background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 16px #0002; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(140px,1fr)); gap: 12px; }}
    .stat {{ padding: 16px; border-radius: 8px; background: #eef4ff; }}
    .stat strong {{ display: block; font-size: 1.7rem; }}
    .pages {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }}
    .page-link {{ padding: 12px 18px; color: white; background: #007bff;
      border-radius: 8px; text-decoration: none; font-weight: 700; }}
  </style>
</head><body><main>
  <h1>館藏重新確認結果</h1>
  <p>最後重新確認時間：{checked_at}</p>
  <section class="stats">
    <div class="stat"><strong>{stats['holding']}</strong>仍有館藏</div>
    <div class="stat"><strong>{stats['available']}</strong>目前有在架館藏</div>
    <div class="stat"><strong>{stats['other']}</strong>全部借出或其他狀態</div>
    <div class="stat"><strong>{stats['unconfirmed']}</strong>本次未確認到館藏</div>
    <div class="stat"><strong>{stats['failed']}</strong>查詢失敗</div>
  </section>
  <nav class="pages" aria-label="重新確認結果頁面">{links}</nav>
</main></body></html>
"""
    index_path = os.path.join(output_dir, "books_rechecked_index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"重新確認首頁已生成：{index_path}")


def _parse_args():
    parser = argparse.ArgumentParser(
        description="重新確認既有 books_with_library 館藏結果"
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="結果目錄或 books_with_library_index.html；預設為程式所在目錄",
    )
    return parser.parse_args()


def _source_directory(source, script_dir):
    if not source:
        return script_dir
    resolved = os.path.abspath(source)
    return os.path.dirname(resolved) if os.path.isfile(resolved) else resolved


def main():
    args = _parse_args()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    source_dir = _source_directory(args.source, script_dir)
    input_files = discover_input_files(source_dir)
    if not input_files:
        print(f"在 {source_dir} 找不到館藏結果，請先執行 integrate.py。")
        return

    try:
        template, rows = load_previous_rows(input_files)
        print(f"本次將重新確認 {len(rows)} 本：{source_dir}")
        updated_rows, stats = recheck_rows(rows)
        checked_at = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        write_rechecked_pages(template, updated_rows, stats, source_dir, checked_at)
    except (OSError, LibraryStructureError) as exc:
        print(f"重新確認失敗：{exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
