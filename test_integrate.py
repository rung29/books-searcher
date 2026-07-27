import unittest
from unittest.mock import patch

import crawler
import integrate
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


if __name__ == "__main__":
    unittest.main()
