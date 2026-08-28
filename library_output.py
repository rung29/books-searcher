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

    holding_count = len(records)
    message = (
        f"共找到 {holding_count} 本有館藏書籍。"
        if page_files
        else "沒有找到有館藏的書籍。"
    )
    warning = (
        '<div class="warning-banner"><span class="warning-icon">⚠️</span><span>部分來源未完成查詢，目前只顯示已確認有館藏的結果。</span></div>'
        if incomplete
        else ""
    )
    embedded_records = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="zh-TW" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>伸港圖書館認證書籍清單</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Noto+Sans+TC:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --font-sans: 'Outfit', 'Noto Sans TC', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --bg-primary: #fffaf5;
      --bg-card: rgba(255, 255, 255, 0.92);
      --bg-card-hover: #ffffff;
      --bg-inner: rgba(254, 243, 235, 0.65);
      --text-primary: #292524;
      --text-secondary: #78716c;
      --text-muted: #a8a29e;
      --accent: #ea580c;
      --accent-hover: #c2410c;
      --accent-light: #ffedd5;
      --accent-gradient: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
      --border: rgba(254, 215, 170, 0.8);
      --border-subtle: rgba(231, 229, 228, 0.85);
      --border-focus: #f97316;
      --success-bg: rgba(16, 185, 129, 0.12);
      --success-text: #059669;
      --success-border: rgba(16, 185, 129, 0.25);
      --call-bg: #fff7ed;
      --call-text: #c2410c;
      --call-border: #fed7aa;
      --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
      --shadow-md: 0 4px 16px -2px rgba(234, 88, 12, 0.08), 0 2px 6px -1px rgba(0, 0, 0, 0.04);
      --shadow-lg: 0 10px 25px -3px rgba(234, 88, 12, 0.12), 0 4px 10px -2px rgba(0, 0, 0, 0.04);
      --glass-blur: blur(14px);
    }}

    [data-theme="dark"] {{
      --bg-primary: #14110e;
      --bg-card: rgba(28, 25, 23, 0.92);
      --bg-card-hover: rgba(38, 34, 31, 0.98);
      --bg-inner: rgba(41, 37, 36, 0.65);
      --text-primary: #fafaf9;
      --text-secondary: #a8a29e;
      --text-muted: #78716c;
      --accent: #fb923c;
      --accent-hover: #f97316;
      --accent-light: rgba(251, 146, 60, 0.18);
      --accent-gradient: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
      --border: rgba(120, 113, 108, 0.4);
      --border-subtle: rgba(68, 64, 60, 0.6);
      --border-focus: #fb923c;
      --success-bg: rgba(16, 185, 129, 0.2);
      --success-text: #34d399;
      --success-border: rgba(16, 185, 129, 0.35);
      --call-bg: rgba(251, 146, 60, 0.15);
      --call-text: #fdba74;
      --call-border: rgba(251, 146, 60, 0.3);
      --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.3);
      --shadow-md: 0 4px 16px 0 rgba(0, 0, 0, 0.4);
      --shadow-lg: 0 10px 30px 0 rgba(0, 0, 0, 0.5);
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      transition: background-color 0.25s ease, border-color 0.25s ease, color 0.25s ease, box-shadow 0.25s ease;
    }}

    body {{
      font-family: var(--font-sans);
      background-color: var(--bg-primary);
      background-image:
        radial-gradient(circle at 10% 10%, rgba(249, 115, 22, 0.08), transparent 30%),
        radial-gradient(circle at 90% 90%, rgba(234, 88, 12, 0.06), transparent 35%);
      background-attachment: fixed;
      color: var(--text-primary);
      min-height: 100vh;
      padding: 24px 16px 48px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}

    .container {{
      width: 100%;
      max-width: 960px;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }}

    .app-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      padding: 20px 24px;
      background: var(--bg-card);
      backdrop-filter: var(--glass-blur);
      -webkit-backdrop-filter: var(--glass-blur);
      border: 1.5px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow-md);
    }}

    .header-brand {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}

    .brand-icon {{
      width: 44px;
      height: 44px;
      border-radius: 12px;
      background: var(--accent-gradient);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.4rem;
      color: #fff;
      box-shadow: 0 4px 12px rgba(234, 88, 12, 0.3);
      flex-shrink: 0;
    }}

    .header-title-group h1 {{
      font-size: 1.35rem;
      font-weight: 800;
      color: var(--text-primary);
      letter-spacing: -0.01em;
    }}

    .header-title-group p {{
      font-size: 0.85rem;
      color: var(--text-secondary);
      margin-top: 2px;
    }}

    .header-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .btn-nav {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 18px;
      border-radius: 12px;
      font-size: 0.92rem;
      font-weight: 700;
      color: #ffffff;
      background: var(--accent-gradient);
      text-decoration: none;
      border: none;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(234, 88, 12, 0.28);
      transition: transform 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
    }}

    .btn-nav:hover {{
      transform: translateY(-1.5px);
      box-shadow: 0 6px 18px rgba(234, 88, 12, 0.38);
      opacity: 0.95;
    }}

    .btn-nav:active {{
      transform: translateY(0);
    }}

    .btn-icon {{
      width: 40px;
      height: 40px;
      border-radius: 12px;
      background: var(--bg-inner);
      border: 1px solid var(--border);
      color: var(--text-primary);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.1rem;
      transition: transform 0.2s ease, background-color 0.2s ease;
    }}

    .btn-icon:hover {{
      transform: scale(1.05);
      border-color: var(--accent);
    }}

    .panel {{
      background: var(--bg-card);
      backdrop-filter: var(--glass-blur);
      -webkit-backdrop-filter: var(--glass-blur);
      border: 1.5px solid var(--border);
      border-radius: 18px;
      padding: 22px;
      box-shadow: var(--shadow-sm);
    }}

    .panel-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }}

    .panel-title {{
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .search-wrapper {{
      position: relative;
      display: flex;
      align-items: center;
    }}

    .search-icon {{
      position: absolute;
      left: 16px;
      font-size: 1.1rem;
      color: var(--text-muted);
      pointer-events: none;
    }}

    .search-input {{
      width: 100%;
      min-height: 50px;
      padding: 0 46px 0 46px;
      border: 1.5px solid var(--border);
      border-radius: 14px;
      background: var(--bg-inner);
      color: var(--text-primary);
      font-family: inherit;
      font-size: 0.95rem;
      outline: none;
      box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.02);
      transition: border-color 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease;
    }}

    .search-input:focus {{
      border-color: var(--border-focus);
      background: var(--bg-card-hover);
      box-shadow: 0 0 0 4px rgba(249, 115, 22, 0.18);
    }}

    .search-clear-btn {{
      position: absolute;
      right: 12px;
      width: 28px;
      height: 28px;
      border-radius: 50%;
      border: none;
      background: transparent;
      color: var(--text-muted);
      font-size: 1.2rem;
      cursor: pointer;
      display: none;
      align-items: center;
      justify-content: center;
    }}

    .search-clear-btn:hover {{
      color: var(--accent);
      background: var(--accent-light);
    }}

    .search-meta-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 12px;
      font-size: 0.88rem;
      color: var(--text-secondary);
    }}

    .count-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 12px;
      background: var(--accent-light);
      color: var(--accent);
      border-radius: 999px;
      font-weight: 700;
      font-size: 0.85rem;
    }}

    .warning-banner {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 16px;
      background: rgba(245, 158, 11, 0.1);
      border: 1px solid rgba(245, 158, 11, 0.3);
      border-radius: 12px;
      color: #b45309;
      font-size: 0.88rem;
      margin-bottom: 14px;
    }}

    [data-theme="dark"] .warning-banner {{
      color: #fbbf24;
    }}

    .list {{
      display: grid;
      gap: 12px;
    }}

    .book-card {{
      background: var(--bg-card);
      border: 1.5px solid var(--border);
      border-radius: 14px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      box-shadow: var(--shadow-sm);
      transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
    }}

    .book-card:hover {{
      transform: translateY(-2px);
      box-shadow: var(--shadow-md);
      border-color: var(--accent);
    }}

    .book-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }}

    .book-title {{
      font-size: 1.08rem;
      font-weight: 700;
      color: var(--text-primary);
      text-decoration: none;
      line-height: 1.4;
    }}

    .book-title:hover {{
      color: var(--accent);
      text-decoration: underline;
    }}

    .book-meta {{
      font-size: 0.88rem;
      color: var(--text-secondary);
      display: flex;
      flex-wrap: wrap;
      gap: 6px 12px;
      align-items: center;
    }}

    .book-meta-item {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }}

    .book-matched {{
      font-size: 0.88rem;
      color: var(--text-secondary);
      padding: 8px 12px;
      background: var(--bg-inner);
      border-radius: 10px;
      border: 1px solid var(--border-subtle);
    }}

    .book-matched a {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
    }}

    .book-matched a:hover {{
      text-decoration: underline;
    }}

    .book-tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 2px;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      padding: 3px 10px;
      border-radius: 8px;
      font-size: 0.82rem;
      font-weight: 600;
      line-height: 1.4;
    }}

    .pill-call {{
      background: var(--call-bg);
      color: var(--call-text);
      border: 1px solid var(--call-border);
    }}

    .pill-status {{
      background: var(--success-bg);
      color: var(--success-text);
      border: 1px solid var(--success-border);
    }}

    .pill-none {{
      background: var(--bg-inner);
      color: var(--text-muted);
      border: 1px solid var(--border-subtle);
    }}

    .group-card {{
      background: var(--bg-card);
      border: 1.5px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: var(--shadow-sm);
    }}

    .group-toggle-btn {{
      width: 100%;
      padding: 14px 18px;
      background: transparent;
      border: none;
      color: var(--text-primary);
      font-family: inherit;
      font-size: 0.98rem;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      text-align: left;
      transition: background-color 0.2s ease;
    }}

    .group-toggle-btn:hover {{
      background: var(--bg-inner);
      color: var(--accent);
    }}

    .group-count-tag {{
      display: inline-flex;
      align-items: center;
      padding: 2px 10px;
      border-radius: 999px;
      background: var(--accent-light);
      color: var(--accent);
      font-size: 0.82rem;
      font-weight: 700;
      margin-left: 8px;
    }}

    .group-arrow {{
      font-size: 0.85rem;
      transition: transform 0.2s ease;
      color: var(--text-muted);
    }}

    .group-toggle-btn[aria-expanded="true"] .group-arrow {{
      transform: rotate(180deg);
      color: var(--accent);
    }}

    .group-items {{
      display: grid;
      gap: 10px;
      padding: 14px 16px;
      background: var(--bg-inner);
      border-top: 1px solid var(--border);
    }}

    .muted-text {{
      color: var(--text-muted);
      text-align: center;
      padding: 24px;
      font-size: 0.95rem;
    }}

    .hidden {{
      display: none !important;
    }}

    @media (max-width: 640px) {{
      body {{
        padding: 16px 10px 32px;
      }}
      .app-header {{
        padding: 16px;
      }}
      .header-actions {{
        width: 100%;
        justify-content: space-between;
      }}
      .btn-nav {{
        flex: 1;
        justify-content: center;
      }}
      .panel {{
        padding: 16px;
      }}
    }}
  </style>
</head>
<body>
<div class="container">
  <header class="app-header">
    <div class="header-brand">
      <div class="brand-icon">📚</div>
      <div class="header-title-group">
        <h1>伸港圖書館認證書籍清單</h1>
      </div>
    </div>
    <div class="header-actions">
      <a href="ebooks_index.html" class="btn-nav" id="switch-to-ebooks">
        <span>📖</span>
        <span>切換至電子書資源</span>
      </a>
      <button type="button" class="btn-icon" id="theme-toggle" aria-label="切換深淺色模式" title="切換深淺色模式">🌙</button>
    </div>
  </header>

  <section class="panel">
    {warning}
    <div class="search-wrapper">
      <span class="search-icon">🔍</span>
      <input id="search-input" class="search-input" type="search" placeholder="輸入書名、命中館藏、作者、出版社或索書號，例如：屁屁偵探、859.61、低年級 & 859* 1" autocomplete="off">
      <button type="button" class="search-clear-btn" id="search-clear" aria-label="清除搜尋">&times;</button>
    </div>
    <div class="search-meta-row">
      <span>搜尋條件支援關鍵字與 <code>*</code> 通配符號</span>
      <span class="count-badge" id="result-count">資料讀取中...</span>
    </div>
  </section>

  <section class="panel" id="groups-section">
    <div class="panel-header">
      <h2 class="panel-title"><span>📂</span> 相似書名群組</h2>
    </div>
    <div class="list" id="groups-list"></div>
  </section>

  <section class="panel">
    <div class="panel-header">
      <h2 class="panel-title" id="results-title"><span>📋</span> 全部館藏</h2>
    </div>
    <div class="list" id="results-list"></div>
  </section>
</div>

<script id="books-data" type="application/json">{embedded_records}</script>
<script>
const DATA_URL = "{data_filename}";
const searchInput = document.getElementById("search-input");
const searchClearBtn = document.getElementById("search-clear");
const resultCount = document.getElementById("result-count");
const groupsSection = document.getElementById("groups-section");
const groupsList = document.getElementById("groups-list");
const resultsTitle = document.getElementById("results-title");
const resultsList = document.getElementById("results-list");
const themeToggleBtn = document.getElementById("theme-toggle");
let books = [];

function initTheme() {{
  const savedTheme = localStorage.getItem("books_theme") ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", savedTheme);
  themeToggleBtn.textContent = savedTheme === "dark" ? "☀️" : "🌙";
}}

themeToggleBtn.addEventListener("click", () => {{
  const current = document.documentElement.getAttribute("data-theme") || "light";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("books_theme", next);
  themeToggleBtn.textContent = next === "dark" ? "☀️" : "🌙";
}});

initTheme();

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
  const calls = (book.call_numbers || []).map(call => `<span class="pill pill-call">索書號: ${{escapeHtml(call)}}</span>`).join("");
  const statuses = (book.library_statuses || []).map(status => `<span class="pill pill-status">${{escapeHtml(status)}}</span>`).join("");
  const titleHref = book.url || book.matched_url || "#";
  const metaItems = [
    book.author ? `<span class="book-meta-item">✍️ ${{escapeHtml(book.author)}}</span>` : "",
    book.publisher ? `<span class="book-meta-item">🏢 ${{escapeHtml(book.publisher)}}</span>` : "",
    book.range ? `<span class="book-meta-item">🎓 ${{escapeHtml(book.range)}}</span>` : "",
    book.certification ? `<span class="book-meta-item">⭐ ${{escapeHtml(book.certification)}}</span>` : ""
  ].filter(Boolean).join(" · ");

  return `<article class="book-card">
    <div class="book-header">
      <a class="book-title" href="${{escapeHtml(titleHref)}}" target="_blank" rel="noopener">${{escapeHtml(book.title)}}</a>
    </div>
    <div class="book-meta">${{metaItems || "-"}}</div>
    <div class="book-matched">
      <strong>命中館藏：</strong>${{book.matched_url ? `<a href="${{escapeHtml(book.matched_url)}}" target="_blank" rel="noopener">${{escapeHtml(book.matched_title || "-")}}</a>` : escapeHtml(book.matched_title || "-")}}
    </div>
    <div class="book-tags">${{calls || '<span class="pill pill-none">無索書號</span>'}} ${{statuses}}</div>
  </article>`;
}}

function renderResults(items, title = "搜尋結果") {{
  resultsTitle.innerHTML = `<span>📋</span> ${{escapeHtml(title)}}`;
  resultCount.textContent = `共 ${{items.length}} 本有館藏書籍`;
  resultsList.innerHTML = items.length
    ? items.map(bookHtml).join("")
    : '<p class="muted-text">沒有符合條件的館藏書籍。</p>';
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
    <article class="group-card">
      <button type="button" class="group-toggle-btn" data-group-index="${{index}}" aria-expanded="false">
        <span>${{escapeHtml(key)}} <span class="group-count-tag">${{items.length}} 本</span></span>
        <span class="group-arrow">▼</span>
      </button>
      <div class="group-items hidden" id="group-${{index}}">
        ${{items.map(bookHtml).join("")}}
      </div>
    </article>
  `).join("");
  groupsList.querySelectorAll("button[data-group-index]").forEach(button => {{
    button.addEventListener("click", () => {{
      const target = document.getElementById(`group-${{button.dataset.groupIndex}}`);
      const isHidden = target.classList.toggle("hidden");
      button.setAttribute("aria-expanded", (!isHidden).toString());
    }});
  }});
}}

function applySearch() {{
  const rawValue = searchInput.value;
  searchClearBtn.style.display = rawValue ? "flex" : "none";
  const conditions = searchConditions(rawValue);
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

searchClearBtn.addEventListener("click", () => {{
  searchInput.value = "";
  searchInput.focus();
  applySearch();
}});

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
</body>
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


