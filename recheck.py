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
    _lookup_candidate,
    search_library_status,
)
from library_output import (
    LibraryStructureError,
    _append_holding_cells,
    _prepare_output_template,
    write_result_pages,
)


MAX_RETRIES = max(0, int(os.getenv("RECHECK_MAX_RETRIES", "2")))
RETRY_SECONDS = max(0.0, float(os.getenv("RECHECK_RETRY_SECONDS", "1")))
TAIPEI_TZ = timezone(timedelta(hours=8), name="Asia/Taipei")


def _page_number(path):
    match = re.search(r"books_with_library_page_(\d+)\.html$", os.path.basename(path))
    return int(match.group(1)) if match else sys.maxsize


def discover_input_files(script_dir):
    index_paths = [
        os.path.join(script_dir, "index.html"),
        os.path.join(script_dir, "books_with_library_index.html"),
    ]
    for index_path in index_paths:
        if not os.path.exists(index_path):
            continue
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


def _prepare_template(source_soup):
    return _prepare_output_template(source_soup)


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
    holding_rows = []
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
            stats["unconfirmed"] += 1
        else:
            stats["failed"] += 1

        if outcome == "holding":
            holding_rows.append(row)
        if BOOK_SLEEP_SECONDS > 0:
            time.sleep(BOOK_SLEEP_SECONDS)

    return holding_rows, stats


def write_rechecked_pages(template, rows, stats, output_dir, checked_at):
    _remove_existing_result_pages(output_dir)
    page_files = write_result_pages(
        template,
        rows,
        output_dir,
        incomplete=stats["failed"] > 0,
    )
    print(
        "重新確認完成："
        f"{stats['holding']} 本仍有館藏，"
        f"{stats['unconfirmed']} 本已移除，"
        f"{stats['failed']} 本查詢失敗未保留。"
    )
    return page_files


def _remove_existing_result_pages(output_dir):
    for path in glob.glob(os.path.join(output_dir, "books_with_library_page_*.html")):
        os.remove(path)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="重新確認既有 books_with_library 館藏結果"
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="結果目錄或 index.html；預設為程式所在目錄",
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
