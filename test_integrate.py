import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

import crawler
import integrate
import library_output
import web_app


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


def search_html(candidates=None, no_results=False):
    if no_results:
        return "<html><body><p>查無符合資料，共 0 筆</p></body></html>"
    rows = []
    for mid, title, author in candidates or []:
        rows.append(
            f'<li><a href="content.cfm?mid={mid}">{title}</a>'
            f"<span>作者：{author} 出版者：測試社</span></li>"
        )
    return f"<html><body>{''.join(rows)}</body></html>"


def content_html(location, title="館藏版本"):
    return f"""
    <html><body>
      <h1>{title}</h1>
      <select><option>全部</option><option>{location}</option></select>
      <table>
        <tr><th>條碼號</th><th>館藏地</th><th>館藏流通類別</th>
            <th>索書號</th><th>館藏狀態</th><th>資料類型</th></tr>
        <tr><td>ABC001</td><td>{location}</td><td>可借28天</td>
            <td>J 123</td><td>在架</td><td>兒童書</td></tr>
      </table>
    </body></html>
    """


class LibraryLookupTests(unittest.TestCase):
    def setUp(self):
        self.old_pages = integrate.MAX_CONTENT_PAGES
        self.old_sleep = integrate.PAGE_SLEEP_SECONDS
        integrate.MAX_CONTENT_PAGES = 1
        integrate.PAGE_SLEEP_SECONDS = 0

    def tearDown(self):
        integrate.MAX_CONTENT_PAGES = self.old_pages
        integrate.PAGE_SLEEP_SECONDS = self.old_sleep

    @patch("integrate.requests.get")
    def test_checks_later_candidate_until_holding_is_found(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(search_html([
                ("1", "同名書（舊版）", "甲"),
                ("2", "同名書（新版）", "乙"),
            ])),
            FakeResponse(content_html("彰化縣立圖書館")),
            FakeResponse(content_html("伸港兒童專區")),
        ]

        result = integrate.search_library_status("同名書")

        self.assertTrue(result["has_holding"])
        self.assertEqual(result["matched_title"], "同名書（新版）")
        self.assertIn("mid=2", result["detail_url"])

    def test_extracts_title_and_author_from_actual_book_card_shape(self):
        soup = integrate.BeautifulSoup(
            """
            <div class="book">
              <div class="cover">
                <a href="content.cfm?mid=12" title="實際書名"><img alt="實際書名"></a>
              </div>
              <div class="book-text">
                <a href="content.cfm?mid=12"><h3><span>1</span>實際書名</h3></a>
                <div class="resultinfo"><p>作者：王小明</p><p>出版者：測試社</p></div>
              </div>
            </div>
            """,
            "html.parser",
        )

        candidates = integrate._extract_search_candidates(soup)

        self.assertEqual(candidates[0]["title"], "實際書名")
        self.assertEqual(candidates[0]["author"], "王小明")

    def test_normalizes_catalog_labels_and_rearranged_punctuation(self):
        cases = [
            (
                "好品格童話2：孔雀先生的祕密",
                "好品格童話.2,孔雀先生的祕密 [中年級認證書] /",
            ),
            (
                "屁屁偵探讀本9：幸運貓落到誰手上",
                "屁屁偵探讀本: [中年級認證書] 9,幸運貓落到誰手上! /",
            ),
        ]

        for source_title, catalog_title in cases:
            with self.subTest(source_title=source_title):
                self.assertEqual(
                    integrate._compact_text(source_title),
                    integrate._compact_text(catalog_title),
                )
                self.assertEqual(
                    integrate._title_similarity(source_title, catalog_title),
                    1.0,
                )

    def test_normalizes_author_role_markers(self):
        self.assertTrue(
            integrate._author_matches(
                "文 / 李光福  圖 / 吳若嫻",
                "李光福,吳若嫻",
            )
        )

    @patch("integrate.requests.get")
    def test_finds_book_cataloged_only_by_subtitle(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(search_html(no_results=True)),
            FakeResponse(search_html(no_results=True)),
            FakeResponse(search_html([("504644", "長髮小善人", "李光福,吳若嫻")])),
            FakeResponse(content_html("伸港兒童專區")),
        ]

        result = integrate.search_library_status(
            "美德新幹線9：長髮小善人",
            "文 / 李光福  圖 / 吳若嫻",
        )

        self.assertTrue(result["has_holding"])
        self.assertEqual(result["matched_title"], "長髮小善人")
        self.assertEqual(result["match_type"], "副標題")

    @patch("integrate.requests.get")
    def test_finds_catalog_title_through_series_volume_fallback(self, mock_get):
        catalog_title = "好品格童話.2,孔雀先生的祕密 [中年級認證書] /"
        mock_get.side_effect = [
            FakeResponse(search_html(no_results=True)),
            FakeResponse(search_html(no_results=True)),
            FakeResponse(search_html([("22", catalog_title, "賴曉珍")])),
            FakeResponse(content_html("伸港兒童專區")),
        ]

        result = integrate.search_library_status(
            "好品格童話2：孔雀先生的祕密", "賴曉珍"
        )

        self.assertTrue(result["has_holding"])
        self.assertEqual(result["matched_title"], catalog_title)
        self.assertEqual(result["match_type"], "副標題")

    @patch("integrate.requests.get")
    def test_uses_main_title_when_full_title_has_no_candidates(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(search_html(no_results=True)),
            FakeResponse(search_html([("7", "森林冒險", "王小明")])),
            FakeResponse(content_html("伸港兒童專區")),
        ]

        result = integrate.search_library_status("森林冒險：勇氣之旅", "王小明")

        self.assertTrue(result["has_holding"])
        self.assertEqual(result["match_type"], "主書名")

    @patch("integrate.requests.get")
    def test_short_main_title_requires_author_match(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(search_html(no_results=True)),
            FakeResponse(search_html([("9", "秘密", "不同作者")])),
        ]

        result = integrate.search_library_status("秘密：新版", "指定作者")

        self.assertFalse(result["has_holding"])
        self.assertEqual(mock_get.call_count, 2)

    @patch("integrate.requests.get")
    def test_unrecognized_search_page_reports_structure_error(self, mock_get):
        mock_get.return_value = FakeResponse("<html><body>網站維護中</body></html>")

        result = integrate.search_library_status("任意書名", "作者")

        self.assertEqual(result["error_type"], "structure")


class WebApiTests(unittest.TestCase):
    @patch("web_app.search_library_status")
    def test_library_api_forwards_author_and_match_details(self, mock_search):
        mock_search.return_value = {
            "has_holding": True,
            "items": [{"call_number": "J 123", "status": "在架"}],
            "matched_title": "實際館藏版本",
            "detail_url": "https://example.test/content.cfm?mid=2",
        }
        client = web_app.app.test_client()

        response = client.post(
            "/api/library-status",
            json={"title": "來源書名", "author": "來源作者"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["matched_title"], "實際館藏版本")
        mock_search.assert_called_once_with("來源書名", "來源作者")


class CrawlerStructureTests(unittest.TestCase):
    def test_unrecognized_results_page_raises_structure_error(self):
        session = FakeSession(FakeResponse("<html><body>網站維護中</body></html>"))

        with self.assertRaises(crawler.SourceStructureError):
            crawler.fetch_books_by_page(session, 1)


def source_page_html(book_count):
    rows = []
    for index in range(1, book_count + 1):
        rows.append(
            f"""
            <tr>
              <td data-label="序號">{index}</td>
              <td data-label="書名"><a href="#">書籍 {index}</a></td>
              <td data-label="作者">作者 {index}</td>
              <td data-label="出版社">出版社</td>
              <td data-label="適讀年段">國小</td>
              <td data-label="認證狀態">通過</td>
            </tr>
            """
        )
    return f"""
    <!doctype html><html><head><title>來源清單</title></head><body>
      <h1>來源清單</h1>
      <div class="info">查詢條件</div>
      <table>
        <thead><tr><th>序號</th><th>書名</th><th>作者</th><th>出版社</th>
          <th>適讀年段</th><th>認證狀態</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </body></html>
    """


class IntegratedOutputTests(unittest.TestCase):
    @patch("integrate.search_library_status")
    def test_collects_only_rows_with_holdings(self, mock_search):
        mock_search.side_effect = [
            {
                "has_holding": True,
                "items": [{"call_number": "J 1", "status": "在架"}],
                "matched_title": "書籍 1",
                "detail_url": "https://example.test/1",
            },
            {"has_holding": False, "items": []},
            {
                "has_holding": True,
                "items": [{"call_number": "J 3", "status": "借出"}],
                "matched_title": "書籍 3",
                "detail_url": "https://example.test/3",
            },
        ]
        old_sleep = integrate.BOOK_SLEEP_SECONDS
        integrate.BOOK_SLEEP_SECONDS = 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "books_page_1.html"
                source.write_text(source_page_html(3), encoding="utf-8")

                template, rows, incomplete = integrate.collect_holding_rows([str(source)])
        finally:
            integrate.BOOK_SLEEP_SECONDS = old_sleep

        self.assertEqual(len(rows), 2)
        self.assertFalse(incomplete)
        self.assertEqual(rows[0].find("td", {"data-label": "館藏情形"}).get_text(strip=True), "有館藏")
        self.assertIsNotNone(template.find("th", string="命中館藏"))

    def test_writes_three_pages_for_55_holdings_and_an_index(self):
        source_soup = integrate.BeautifulSoup(source_page_html(55), "html.parser")
        template = library_output._prepare_output_template(source_soup)
        rows = source_soup.find("tbody").find_all("tr")
        old_size = library_output.OUTPUT_PAGE_SIZE
        library_output.OUTPUT_PAGE_SIZE = 25
        try:
            with tempfile.TemporaryDirectory() as tmp:
                files = library_output.write_result_pages(template, rows, tmp)
                row_counts = []
                first_numbers = []
                for filename in files:
                    page = integrate.BeautifulSoup(
                        (Path(tmp) / filename).read_text(encoding="utf-8"),
                        "html.parser",
                    )
                    page_rows = page.find("tbody").find_all("tr")
                    row_counts.append(len(page_rows))
                    first_numbers.append(
                        page_rows[0].find("td", {"data-label": "序號"}).get_text(strip=True)
                    )
                index_html = (Path(tmp) / "index.html").read_text(
                    encoding="utf-8"
                )
                legacy_index_exists = (
                    Path(tmp) / "books_with_library_index.html"
                ).exists()
                data = json.loads(
                    (Path(tmp) / "books_with_library_data.json").read_text(
                        encoding="utf-8"
                    )
                )
        finally:
            library_output.OUTPUT_PAGE_SIZE = old_size

        self.assertEqual(files, [
            "books_with_library_page_1.html",
            "books_with_library_page_2.html",
            "books_with_library_page_3.html",
        ])
        self.assertEqual(row_counts, [25, 25, 5])
        self.assertEqual(first_numbers, ["1", "26", "51"])
        self.assertIn("books_with_library_page_3.html", index_html)
        self.assertFalse(legacy_index_exists)
        self.assertIn("books_with_library_data.json", index_html)
        self.assertEqual(len(data), 55)
        self.assertEqual(data[0]["anchor"], "books_with_library_page_1.html#book-1")
        self.assertEqual(data[25]["anchor"], "books_with_library_page_2.html#book-26")


if __name__ == "__main__":
    unittest.main()
