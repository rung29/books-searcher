import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ebook_finder


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def search_html(with_resource=True):
    resource = (
        '<div><span>網路資源：</span>'
        '<a href="https://ebook.nlpi.edu.tw/bookdetail/22896">電子書</a></div>'
        if with_resource
        else ""
    )
    return f"""
    <html><body>
      <div class="book">
        <a href="content.cfm?mid=12" title="神犬奇兵">神犬奇兵</a>
        <p>作者：廖炳焜</p>
        {resource}
      </div>
    </body></html>
    """


def source_page_html():
    return """
    <html><body>
      <table>
        <thead><tr><th>序號</th><th>書名</th><th>作者</th><th>出版社</th>
          <th>適讀年段</th><th>認證狀態</th></tr></thead>
        <tbody>
          <tr>
            <td data-label="序號">1</td>
            <td data-label="書名"><a href="https://example.test/book">神犬奇兵</a></td>
            <td data-label="作者">廖炳焜</td>
            <td data-label="出版社">小兵</td>
            <td data-label="適讀年段">國小中年級</td>
            <td data-label="認證狀態">可認證</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """


class EbookFinderTests(unittest.TestCase):
    def setUp(self):
        self.old_sleep = ebook_finder.BOOK_SLEEP_SECONDS
        ebook_finder.BOOK_SLEEP_SECONDS = 0

    def tearDown(self):
        ebook_finder.BOOK_SLEEP_SECONDS = self.old_sleep

    @patch("ebook_finder.requests.get")
    def test_search_status_keeps_candidate_with_online_resource(self, mock_get):
        mock_get.return_value = FakeResponse(search_html(with_resource=True))

        result = ebook_finder.search_ebook_status("神犬奇兵", "廖炳焜")

        self.assertTrue(result["has_online_resource"])
        self.assertEqual(
            result["online_resources"],
            ["https://ebook.nlpi.edu.tw/bookdetail/22896"],
        )

    @patch("ebook_finder.requests.get")
    def test_search_status_ignores_candidate_without_online_resource(self, mock_get):
        mock_get.return_value = FakeResponse(search_html(with_resource=False))

        result = ebook_finder.search_ebook_status("神犬奇兵", "廖炳焜")

        self.assertFalse(result["has_online_resource"])
        self.assertEqual(result["online_resources"], [])

    @patch("ebook_finder.search_ebook_status")
    def test_collects_and_writes_ebook_pages(self, mock_search):
        mock_search.return_value = {
            "has_online_resource": True,
            "title": "神犬奇兵",
            "author": "廖炳焜",
            "detail_url": "https://library.test/content.cfm?mid=12",
            "match_type": "完整書名",
            "online_resources": ["https://ebook.nlpi.edu.tw/bookdetail/22896"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "books_page_1.html"
            source.write_text(source_page_html(), encoding="utf-8")

            records = ebook_finder.collect_ebooks([str(source)])
            files = ebook_finder.write_ebook_pages(records, tmp)

            self.assertEqual(files, ["ebooks_page_1.html"])
            self.assertTrue((Path(tmp) / "ebooks_index.html").exists())
            self.assertTrue((Path(tmp) / "ebooks_data.json").exists())
            self.assertIn(
                "https://ebook.nlpi.edu.tw/bookdetail/22896",
                (Path(tmp) / "ebooks_index.html").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
