import json
import glob
import os
import sys
from copy import deepcopy

from bs4 import BeautifulSoup


OUTPUT_PAGE_SIZE = max(1, int(os.getenv("INTEGRATE_OUTPUT_PAGE_SIZE", "25")))


class LibraryStructureError(RuntimeError):
    """Raised when a library page is neither a recognized result nor no-result page."""


def _prepare_output_template(source_soup):
    template = deepcopy(source_soup)
    thead = template.find("thead")
    tbody = template.find("tbody")
    if not thead or not tbody or not thead.find("tr"):
        raise LibraryStructureError("來源檔案缺少預期的書目表格")

    header_row = thead.find("tr")
    for th in header_row.find_all("th"):
        if th.get_text(strip=True) in [
            "伸港館藏狀態",
            "館藏情形",
            "命中館藏",
            "索書號",
            "館藏狀態",
        ]:
            th.decompose()
    for header in ["館藏情形", "命中館藏", "索書號", "館藏狀態"]:
        th = template.new_tag("th")
        th.string = header
        header_row.append(th)
    tbody.clear()
    return template


def _append_holding_cells(soup, row, result):
    for label in ["伸港館藏狀態", "館藏情形", "命中館藏", "索書號", "館藏狀態"]:
        for old_td in row.find_all("td", {"data-label": label}):
            old_td.decompose()

    td_has = soup.new_tag("td")
    td_has["data-label"] = "館藏情形"
    span_has = soup.new_tag("span")
    span_has["class"] = "status status-yes"
    span_has.string = "有館藏"
    td_has.append(span_has)
    row.append(td_has)

    td_match = soup.new_tag("td")
    td_match["data-label"] = "命中館藏"
    if result.get("matched_title"):
        link = soup.new_tag("a", href=result.get("detail_url") or "#")
        link["target"] = "_blank"
        link["rel"] = "noopener"
        link.string = result["matched_title"]
        td_match.append(link)
        if result.get("match_type") and result["match_type"] != "完整書名":
            note = soup.new_tag("small")
            note.string = f"（{result['match_type']}匹配）"
            td_match.append(note)
    else:
        td_match.string = "-"
    row.append(td_match)

    td_call = soup.new_tag("td")
    td_call["data-label"] = "索書號"
    for item in result.get("items") or []:
        div = soup.new_tag("div")
        div["style"] = "margin-bottom: 4px;"
        div.string = item.get("call_number") or "-"
        td_call.append(div)
    if not td_call.contents:
        td_call.string = "-"
    row.append(td_call)

    td_status = soup.new_tag("td")
    td_status["data-label"] = "館藏狀態"
    for item in result.get("items") or []:
        div = soup.new_tag("div")
        div["style"] = "margin-bottom: 4px;"
        span = soup.new_tag("span")
        status = item.get("status") or "-"
        span["class"] = "status status-yes" if "在架" in status else "status status-no"
        span.string = status
        div.append(span)
        td_status.append(div)
    if not td_status.contents:
        td_status.string = "-"
    row.append(td_status)


def _add_page_navigation(soup, current_page, page_count):
    table = soup.find("table")
    if not table:
        return
    nav = soup.new_tag("div")
    nav["class"] = "info"
    home_link = soup.new_tag("a", href="index.html")
    home_link.string = "結果首頁"
    nav.append(home_link)
    nav.append("　")
    for page_number in range(1, page_count + 1):
        if page_number == current_page:
            current = soup.new_tag("strong")
            current.string = f"第 {page_number} 頁"
            nav.append(current)
        else:
            link = soup.new_tag(
                "a", href=f"books_with_library_page_{page_number}.html"
            )
            link.string = f"第 {page_number} 頁"
            nav.append(link)
        if page_number < page_count:
            nav.append("　")
    table.insert_before(nav)


def _cell_text(row, label):
    cell = row.find("td", {"data-label": label})
    return cell.get_text(" ", strip=True) if cell else ""


def _cell_values(row, label):
    cell = row.find("td", {"data-label": label})
    if not cell:
        return []
    values = [
        element.get_text(" ", strip=True)
        for element in cell.find_all(["div", "span"], recursive=True)
        if element.get_text(" ", strip=True)
    ]
    if not values:
        text = cell.get_text(" ", strip=True)
        return [text] if text and text != "-" else []
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _first_link(row, label):
    cell = row.find("td", {"data-label": label})
    link = cell.find("a", href=True) if cell else None
    return link["href"] if link else ""


def _record_from_row(row, book_id, page_file):
    return {
        "id": book_id,
        "page": page_file,
        "anchor": f"{page_file}#{book_id}",
        "number": _cell_text(row, "序號"),
        "title": _cell_text(row, "書名"),
        "url": _first_link(row, "書名"),
        "author": _cell_text(row, "作者"),
        "publisher": _cell_text(row, "出版社"),
        "range": _cell_text(row, "適讀年段"),
        "certification": _cell_text(row, "認證狀態"),
        "matched_title": _cell_text(row, "命中館藏"),
        "matched_url": _first_link(row, "命中館藏"),
        "call_numbers": _cell_values(row, "索書號"),
        "library_statuses": _cell_values(row, "館藏狀態"),
    }


def write_result_pages(template, holding_rows, output_dir, incomplete=False):
    page_count = (
        (len(holding_rows) + OUTPUT_PAGE_SIZE - 1) // OUTPUT_PAGE_SIZE
        if holding_rows
        else 0
    )
    page_files = []
    records = []

    for page_number in range(1, page_count + 1):
        page_soup = deepcopy(template)
        if page_soup.title:
            page_soup.title.string = f"伸港館藏查詢結果 - 第 {page_number} 頁"
        heading = page_soup.find("h1")
        if heading:
            heading.string = f"伸港圖書館館藏結果（第 {page_number}/{page_count} 頁）"
        tbody = page_soup.find("tbody")
        start = (page_number - 1) * OUTPUT_PAGE_SIZE
        chunk = holding_rows[start:start + OUTPUT_PAGE_SIZE]
        for row_number, row in enumerate(chunk, 1):
            output_row = deepcopy(row)
            global_number = start + row_number
            book_id = f"book-{global_number}"
            output_row["id"] = book_id
            number_td = output_row.find("td", {"data-label": "序號"})
            if number_td:
                number_td.string = str(global_number)
            records.append(
                _record_from_row(
                    output_row,
                    book_id,
                    f"books_with_library_page_{page_number}.html",
                )
            )
            tbody.append(output_row)
        _add_page_navigation(page_soup, page_number, page_count)
        if incomplete:
            warning = page_soup.new_tag("div")
            warning["class"] = "info"
            warning.string = "部分來源未完成查詢；本頁只保留已確認有館藏的結果。"
            page_soup.find("table").insert_before(warning)

        filename = f"books_with_library_page_{page_number}.html"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(str(page_soup))
        page_files.append(filename)
        print(f"已產生第 {page_number} 頁（{len(chunk)} 本）：{filepath}")

    write_results_index(output_dir, page_files, records, incomplete)
    return page_files


def write_results_index(output_dir, page_files, records, incomplete=False):
    data_filename = "books_with_library_data.json"
    data_path = os.path.join(output_dir, data_filename)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    page_links = "\n".join(
        f'<a class="page-link" href="{filename}">第 {index} 頁</a>'
        for index, filename in enumerate(page_files, 1)
    )
    holding_count = len(records)
    message = (
        f"共找到 {holding_count} 本有館藏書籍，分為 {len(page_files)} 頁。"
        if page_files
        else "沒有找到有館藏的書籍。"
    )
    warning = (
        '<p class="warning">部分來源未完成查詢，目前只顯示已確認有館藏的結果。</p>'
        if incomplete
        else ""
    )
    embedded_records = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>伸港圖書館館藏結果</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 1040px; margin: 0 auto; padding: 32px 20px; background: #f8f9fa; color: #263238; }}
    main {{ display: grid; gap: 20px; }}
    .panel {{ background: #fff; padding: 24px; border-radius: 12px;
      box-shadow: 0 4px 16px rgba(0,0,0,.08); }}
    h1, h2, p {{ margin-top: 0; }}
    h2 {{ font-size: 1.15rem; }}
    input {{ width: 100%; min-height: 46px; padding: 0 14px; border: 1px solid #ccd6dd;
      border-radius: 8px; font: inherit; }}
    .muted {{ color: #64748b; }}
    .pages {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 12px; margin-top: 16px; }}
    .page-link {{ display: block; padding: 16px; text-align: center; color: #fff;
      background: #007bff; border-radius: 8px; text-decoration: none; font-weight: 700; }}
    .page-link:hover {{ background: #0056b3; }}
    .summary {{ margin-bottom: 16px; }}
    .toolbar {{ display: grid; gap: 10px; }}
    .list {{ display: grid; gap: 10px; }}
    .group, .book {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; background: #fff; }}
    .group button {{ width: 100%; border: 0; background: transparent; padding: 0;
      color: #0056b3; cursor: pointer; text-align: left; font: inherit; font-weight: 800; }}
    .group-items {{ display: grid; gap: 8px; margin-top: 12px; }}
    .book-title {{ font-weight: 800; color: #0056b3; text-decoration: none; }}
    .book-title:hover {{ text-decoration: underline; }}
    .meta {{ color: #64748b; font-size: .92rem; margin-top: 4px; }}
    .calls {{ margin-top: 6px; }}
    .pill {{ display: inline-block; margin: 2px 4px 2px 0; padding: 3px 8px;
      border-radius: 999px; background: #e6f4f1; color: #115e59; font-size: .85rem; font-weight: 700; }}
    .hidden {{ display: none; }}
    .warning {{ padding: 12px; background: #fff3cd; border-left: 4px solid #ffc107; }}
  </style>
</head>
<body><main>
  <section class="panel">
  <h1>伸港圖書館館藏結果</h1>
  <p class="summary">{message}</p>
  {warning}
  <div class="toolbar">
    <input id="search-input" type="search" placeholder="輸入書名、命中館藏、作者、出版社或索書號，例如：屁屁偵探、859.61、低年級 & 859* 1">
    <p class="muted" id="result-count">資料讀取中...</p>
  </div>
  </section>
  <section class="panel" id="groups-section">
    <h2>相似書名群組</h2>
    <div class="list" id="groups-list"></div>
  </section>
  <section class="panel">
    <h2 id="results-title">搜尋結果</h2>
    <div class="list" id="results-list"></div>
  </section>
  <section class="panel">
  <h2>原始分頁</h2>
  <nav class="pages" aria-label="結果頁面">{page_links}</nav>
  </section>
</main></body>
<script id="books-data" type="application/json">{embedded_records}</script>
<script>
const DATA_URL = "{data_filename}";
const searchInput = document.getElementById("search-input");
const resultCount = document.getElementById("result-count");
const groupsSection = document.getElementById("groups-section");
const groupsList = document.getElementById("groups-list");
const resultsTitle = document.getElementById("results-title");
const resultsList = document.getElementById("results-list");
let books = [];

function normalize(value) {{
  return String(value || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\\p{{L}}\\p{{N}}*]+/gu, "");
}}

function searchableFields(book, query) {{
  const callNumberLike = /^[0-9*]+$/.test(query) && /[0-9]/.test(query);
  const values = callNumberLike
    ? (book.call_numbers || [])
    : [
        book.title,
        book.matched_title,
        book.author,
        book.publisher,
        book.range,
        book.certification,
        ...(book.call_numbers || []),
        ...(book.library_statuses || [])
      ];
  return values.map(normalize).filter(Boolean);
}}

function normalizeCallPart(value) {{
  return String(value || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\\p{{L}}\\p{{N}}*]+/gu, "");
}}

function callNumberTokens(callNumber) {{
  return String(callNumber || "")
    .normalize("NFKC")
    .toLowerCase()
    .split(/\\s+/)
    .map(normalizeCallPart)
    .filter(part => /\\d/.test(part));
}}

function wildcardRegex(query) {{
  const escaped = query
    .split("*")
    .map(part => part.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&"))
    .join(".*");
  return new RegExp(escaped);
}}

function wildcardPrefixRegex(query) {{
  const escaped = query
    .split("*")
    .map(part => part.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&"))
    .join(".*");
  return new RegExp(`^${{escaped}}`);
}}

function callQueryParts(rawQuery, normalizedQuery) {{
  const spacedParts = String(rawQuery || "")
    .normalize("NFKC")
    .trim()
    .split(/\\s+/)
    .map(normalizeCallPart)
    .filter(Boolean);
  if (spacedParts.length > 1) {{
    return spacedParts;
  }}
  const compactMatch = normalizedQuery.match(/^(\\d+)\\*(\\d+)$/);
  if (compactMatch) {{
    return [`${{compactMatch[1]}}*`, compactMatch[2]];
  }}
  return [];
}}

function matchesCallNumber(book, rawQuery, normalizedQuery) {{
  const parts = callQueryParts(rawQuery, normalizedQuery);
  if (parts.length > 0) {{
    return (book.call_numbers || []).some(callNumber => {{
      const tokens = callNumberTokens(callNumber);
      if (tokens.length < parts.length) {{
        return false;
      }}
      const firstMatcher = wildcardPrefixRegex(parts[0]);
      for (let start = 0; start <= tokens.length - parts.length; start += 1) {{
        if (!firstMatcher.test(tokens[start])) {{
          continue;
        }}
        const restMatches = parts.slice(1).every((part, offset) =>
          wildcardPrefixRegex(part.includes("*") ? part : `${{part}}*`).test(tokens[start + offset + 1])
        );
        if (restMatches) {{
          return true;
        }}
      }}
      return false;
    }});
  }}

  const fields = (book.call_numbers || []).map(normalize).filter(Boolean);
  if (normalizedQuery.includes("*")) {{
    const matcher = wildcardRegex(normalizedQuery);
    return fields.some(field => matcher.test(field));
  }}
  return fields.some(field => field.includes(normalizedQuery));
}}

function matchesSearch(book, rawQuery) {{
  const query = normalize(rawQuery);
  if (!query) {{
    return true;
  }}
  if (/^[0-9*]+$/.test(query) && /[0-9]/.test(query)) {{
    return matchesCallNumber(book, rawQuery, query);
  }}
  const fields = searchableFields(book, query);
  if (query.includes("*")) {{
    const matcher = wildcardRegex(query);
    return fields.some(field => matcher.test(field));
  }}
  return fields.some(field => field.includes(query));
}}

function searchConditions(value) {{
  return String(value || "")
    .split("&")
    .map(part => part.trim())
    .filter(Boolean);
}}

function seriesKey(title) {{
  let value = String(title || "").normalize("NFKC");
  value = value.replace(/[《》「」『』【】\\[\\]()（）]/g, " ");
  value = value.split(/[：:]/)[0];
  value = value.replace(/\\s*(第\\s*)?[0-9０-９一二三四五六七八九十]+\\s*(集|冊|卷|本)?\\s*$/u, "");
  value = value.replace(/\\s+/g, " ").trim();
  return value || title;
}}

function escapeHtml(value) {{
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}}

function bookHtml(book) {{
  const calls = (book.call_numbers || []).map(call => `<span class="pill">${{escapeHtml(call)}}</span>`).join("");
  const statuses = (book.library_statuses || []).map(status => `<span class="pill">${{escapeHtml(status)}}</span>`).join("");
  return `<article class="book">
    <a class="book-title" href="${{escapeHtml(book.anchor)}}">${{escapeHtml(book.title)}}</a>
    <div class="meta">${{escapeHtml(book.author || "-")}} / ${{escapeHtml(book.publisher || "-")}} / ${{escapeHtml(book.range || "-")}}</div>
    <div class="meta">命中館藏：${{book.matched_url ? `<a href="${{escapeHtml(book.matched_url)}}" target="_blank" rel="noopener">${{escapeHtml(book.matched_title || "-")}}</a>` : escapeHtml(book.matched_title || "-")}}</div>
    <div class="calls">${{calls || '<span class="pill">無索書號</span>'}} ${{statuses}}</div>
  </article>`;
}}

function renderResults(items, title = "搜尋結果") {{
  resultsTitle.textContent = title;
  resultCount.textContent = `共 ${{items.length}} 本有館藏書籍`;
  resultsList.innerHTML = items.length
    ? items.map(bookHtml).join("")
    : '<p class="muted">沒有符合條件的館藏書籍。</p>';
}}

function renderGroups() {{
  const groups = new Map();
  books.forEach(book => {{
    const key = seriesKey(book.title);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(book);
  }});
  const grouped = Array.from(groups.entries())
    .filter(([, items]) => items.length > 1)
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], "zh-Hant"));

  groupsSection.classList.toggle("hidden", grouped.length === 0);
  groupsList.innerHTML = grouped.map(([key, items], index) => `
    <article class="group">
      <button type="button" data-group-index="${{index}}">${{escapeHtml(key)}}（${{items.length}} 本）</button>
      <div class="group-items hidden" id="group-${{index}}">
        ${{items.map(bookHtml).join("")}}
      </div>
    </article>
  `).join("");
  groupsList.querySelectorAll("button[data-group-index]").forEach(button => {{
    button.addEventListener("click", () => {{
      document.getElementById(`group-${{button.dataset.groupIndex}}`).classList.toggle("hidden");
    }});
  }});
}}

function applySearch() {{
  const conditions = searchConditions(searchInput.value);
  if (conditions.length === 0) {{
    renderResults(books, "全部館藏");
    groupsSection.classList.remove("hidden");
    return;
  }}
  groupsSection.classList.add("hidden");
  renderResults(
    books.filter(book => conditions.every(condition => matchesSearch(book, condition))),
    "搜尋結果"
  );
}}

function initializeBooks(data) {{
  books = Array.isArray(data) ? data : [];
  renderGroups();
  applySearch();
}}

function embeddedBooks() {{
  const element = document.getElementById("books-data");
  if (!element) {{
    return null;
  }}
  try {{
    const data = JSON.parse(element.textContent || "[]");
    return Array.isArray(data) ? data : null;
  }} catch {{
    return null;
  }}
}}

const initialBooks = embeddedBooks();
if (initialBooks) {{
  initializeBooks(initialBooks);
}} else {{
  fetch(DATA_URL)
    .then(response => response.json())
    .then(initializeBooks)
    .catch(() => {{
      resultCount.textContent = "無法讀取 books_with_library_data.json。請確認檔案與首頁放在同一個目錄。";
    }});
}}

searchInput.addEventListener("input", applySearch);
</script>
</html>
"""
    legacy_index_path = os.path.join(output_dir, "books_with_library_index.html")
    if os.path.exists(legacy_index_path):
        os.remove(legacy_index_path)

    index_path = os.path.join(output_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"結果首頁已生成：{index_path}")
    print(f"結果資料已生成：{data_path}")


def _page_number(path):
    filename = os.path.basename(path)
    stem = filename.removeprefix("books_with_library_page_").removesuffix(".html")
    return int(stem) if stem.isdigit() else sys.maxsize


def _row_has_holding(row):
    status_cell = row.find("td", {"data-label": "館藏情形"})
    return status_cell and "有館藏" in status_cell.get_text(" ", strip=True)


def load_existing_holding_rows(input_dir):
    page_paths = sorted(
        glob.glob(os.path.join(input_dir, "books_with_library_page_*.html")),
        key=_page_number,
    )
    if not page_paths:
        raise LibraryStructureError("找不到 books_with_library_page_*.html")

    template = None
    rows = []
    for page_path in page_paths:
        with open(page_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        tbody = soup.find("tbody")
        if not soup.find("thead") or not tbody:
            raise LibraryStructureError(f"{page_path} 缺少預期的書目表格")
        if template is None:
            template = _prepare_output_template(soup)
        rows.extend(deepcopy(row) for row in tbody.find_all("tr") if _row_has_holding(row))

    return template, rows


def _remove_existing_result_pages(output_dir):
    for path in glob.glob(os.path.join(output_dir, "books_with_library_page_*.html")):
        os.remove(path)


def rebuild_from_existing_pages(target_dir):
    template, holding_rows = load_existing_holding_rows(target_dir)
    _remove_existing_result_pages(target_dir)
    page_files = write_result_pages(template, holding_rows, target_dir)
    print(f"已從既有館藏頁面重建 {len(page_files)} 個分頁、index 與 JSON。")
    return page_files


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    target = argv[0] if argv else script_dir
    target_dir = os.path.dirname(os.path.abspath(target)) if os.path.isfile(target) else os.path.abspath(target)

    try:
        rebuild_from_existing_pages(target_dir)
    except (OSError, LibraryStructureError) as exc:
        print(f"輸出整理失敗：{exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()


