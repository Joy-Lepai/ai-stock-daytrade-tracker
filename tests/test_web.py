import unittest

from stock_daytrade_system.web import _extract_body, _extract_style, latest_tracker_file, render_paper_dashboard_page


class WebTests(unittest.TestCase):
    def test_extracts_body_from_html(self):
        self.assertEqual(_extract_body("<html><body><main>ok</main></body></html>"), "<main>ok</main>")

    def test_extracts_style_from_html(self):
        self.assertEqual(_extract_style("<head><style>.x{}</style></head>"), ".x{}")

    def test_latest_tracker_file_returns_none_for_empty_directory(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(latest_tracker_file(Path(directory)))

    def test_paper_dashboard_page_has_required_sections(self):
        html = render_paper_dashboard_page()

        self.assertIn("虛擬交易 Paper Trading", html)
        self.assertIn("/api/paper/dashboard", html)
        self.assertIn("不串接券商", html)
        self.assertIn("目前持倉", html)
        self.assertIn("尚無持倉", html)
        self.assertNotIn("!response.ok || payload.error", html)


if __name__ == "__main__":
    unittest.main()
