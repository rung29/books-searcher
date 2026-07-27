import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import integrate
import recheck


def previous_results_html(count):
    rows = []
    for index in range(1, count + 1):
        rows.append(
            f"""
            <tr>
              <td data-label="序號">{index}</td>
              <td data-label="書名">書籍 {index}</td>
              <td data-label="作者">作者 {index}</td>
              <td data-label="出版社">出版社</td>
              <td data-label="適讀年段">國小</td>
              <td data-label="認證狀態">通過</td>
              <td data-label="館藏情形"><span>有館藏</span></td>
              <td data-label="命中館藏"><a href="https://example.test/content.cfm?mid={index}">館藏 {index}</a></td>
              <td data-label="索書號">J {index}</td>
              <td data-label="館藏狀態"><span>借出</span></td>
            </tr>
            """
        )
    return f"""
    <!doctype html><html><head><title>舊結果</title></head><body>
      <h1>舊結果</h1>
      <table>
        <thead><tr><th>序號</th><th>書名</th><th>作者</th><th>出版社</th>
          <th>適讀年段</th><th>認證狀態</th><th>館藏情形</th>
          <th>命中館藏</th><th>索書號</th><th>館藏狀態</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </body></html>
    """


class RecheckLookupTests(unittest.TestCase):
    @patch("recheck.time.sleep")
    @patch("recheck._lookup_once")
    def test_retries_twice_then_returns_holding(self, mock_lookup, mock_sleep):
        mock_lookup.side_effect = [
            requests.exceptions.Timeout("first"),
            requests.exceptions.Timeout("second"),
            {
                "has_holding": True,
                "items": [{"call_number": "J 1", "status": "在架"}],
            },
        ]

        result = recheck.recheck_book("1", "館藏書名", "來源書名", "作者")

        self.assertEqual(result["outcome"], "holding")
        self.assertEqual(mock_lookup.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("recheck._lookup_once", return_value=None)
    def test_valid_record_without_branch_holding_is_unconfirmed(self, mock_lookup):
        result = recheck.recheck_book("1", "館藏書名", "來源書名", "作者")

        self.assertEqual(result["outcome"], "unconfirmed")
        self.assertFalse(result["has_holding"])


class RecheckOutputTests(unittest.TestCase):
    def test_discovers_only_pages_linked_by_current_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for page_number in (1, 2, 3):
                (root / f"books_with_library_page_{page_number}.html").write_text(
                    previous_results_html(1),
                    encoding="utf-8",
                )
            (root / "books_with_library_index.html").write_text(
                """
                <a href="books_with_library_page_1.html">第 1 頁</a>
                <a href="books_with_library_page_2.html">第 2 頁</a>
                """,
                encoding="utf-8",
            )

            files = recheck.discover_input_files(tmp)

        self.assertEqual(
            [Path(path).name for path in files],
            ["books_with_library_page_1.html", "books_with_library_page_2.html"],
        )

    @patch("recheck.recheck_book")
    def test_retains_every_row_and_updates_status_classification(self, mock_recheck):
        mock_recheck.side_effect = [
            {
                "outcome": "holding",
                "has_holding": True,
                "items": [{"call_number": "J 1", "status": "在架"}],
                "matched_title": "館藏 1",
                "detail_url": "https://example.test/content.cfm?mid=1",
            },
            {"outcome": "unconfirmed", "has_holding": False, "items": []},
            {
                "outcome": "failed",
                "has_holding": False,
                "items": [],
                "error": "逾時",
            },
        ]
        soup = integrate.BeautifulSoup(previous_results_html(3), "html.parser")
        rows = soup.find("tbody").find_all("tr")
        old_sleep = recheck.BOOK_SLEEP_SECONDS
        recheck.BOOK_SLEEP_SECONDS = 0
        try:
            updated, stats = recheck.recheck_rows(rows)
        finally:
            recheck.BOOK_SLEEP_SECONDS = old_sleep

        self.assertEqual(len(updated), 3)
        self.assertEqual(stats["holding"], 1)
        self.assertEqual(stats["available"], 1)
        self.assertEqual(stats["unconfirmed"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(
            updated[0].find("td", {"data-label": "狀態變化"}).get_text(strip=True),
            "借出 → 在架",
        )
        self.assertIn(
            "本次未確認到館藏",
            updated[1].find("td", {"data-label": "館藏情形"}).get_text(strip=True),
        )

    def test_writes_rechecked_pages_and_statistics_index(self):
        source = integrate.BeautifulSoup(previous_results_html(26), "html.parser")
        template = recheck._prepare_template(source)
        rows = source.find("tbody").find_all("tr")
        stats = {
            "total": 26,
            "holding": 20,
            "available": 12,
            "other": 8,
            "unconfirmed": 4,
            "failed": 2,
        }
        old_size = recheck.OUTPUT_PAGE_SIZE
        recheck.OUTPUT_PAGE_SIZE = 25
        try:
            with tempfile.TemporaryDirectory() as tmp:
                files = recheck.write_rechecked_pages(
                    template, rows, stats, tmp, "2026-07-27 14:30"
                )
                index = (Path(tmp) / "books_rechecked_index.html").read_text(
                    encoding="utf-8"
                )
                second_page = integrate.BeautifulSoup(
                    (Path(tmp) / files[1]).read_text(encoding="utf-8"),
                    "html.parser",
                )
        finally:
            recheck.OUTPUT_PAGE_SIZE = old_size

        self.assertEqual(
            files,
            ["books_rechecked_page_1.html", "books_rechecked_page_2.html"],
        )
        self.assertIn("2026-07-27 14:30", index)
        self.assertIn(">12<", index)
        self.assertEqual(len(second_page.find("tbody").find_all("tr")), 1)


if __name__ == "__main__":
    unittest.main()
