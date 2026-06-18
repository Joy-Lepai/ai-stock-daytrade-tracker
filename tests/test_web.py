import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from stock_daytrade_system.web import (
    _current_commit_hash,
    _extract_body,
    _extract_style,
    _scheduled_tracker_interval,
    _tracker_html_needs_refresh,
    latest_tracker_file,
    render_paper_dashboard_page,
    render_tw_advisor_page,
)


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

    def test_tracker_html_needs_refresh_when_static_file_is_old(self):
        html = """
        <html><body>
          app version / commit：4acbeb16719c
          scoring model：long_model_v2_volume_vwap_2026-06-12
        </body></html>
        """

        self.assertTrue(_tracker_html_needs_refresh(html))

    def test_tracker_html_with_current_markers_does_not_need_refresh(self):
        commit = _current_commit_hash()
        html = f"""
        <html><body>
          app version / commit：{commit}
          scoring model：long_model_v2_b_plus_practice_2026-06-13
          明日續強候選股
          明日買多觀察池
          AI 今日決策中心
          訊號中心
          B+ 觸發條件追蹤
          B+可練習觀察數量
        </body></html>
        """

        self.assertFalse(_tracker_html_needs_refresh(html))

    def test_paper_dashboard_page_has_required_sections(self):
        html = render_paper_dashboard_page()

        self.assertIn("虛擬交易 Paper Trading", html)
        self.assertIn("/api/paper/dashboard", html)
        self.assertIn("/api/paper/manual-trade", html)
        self.assertIn("/api/paper/close-trade", html)
        self.assertIn("手動虛擬交易", html)
        self.assertIn("paper-decision-summary", html)
        self.assertIn("AI 虛擬交易摘要", html)
        self.assertIn("建立虛擬買進", html)
        self.assertIn("不串接券商", html)
        self.assertIn("目前持倉", html)
        self.assertIn("尚無持倉", html)
        self.assertIn("虛擬交易 API 暫時無法更新", html)
        self.assertNotIn("虛擬交易資料暫時無法更新", html)
        self.assertNotIn("!response.ok || payload.error", html)

    def test_tw_advisor_page_has_stock_input_and_scan_api(self):
        html = render_tw_advisor_page()

        self.assertIn("個股當沖建議", html)
        self.assertIn("6770.TW", html)
        self.assertIn("/api/tw/scan/symbol", html)
        self.assertIn("買多", html)
        self.assertIn("賣空", html)
        self.assertIn("觀察", html)
        self.assertIn("最新成交價", html)
        self.assertIn("模型參考價", html)
        self.assertIn("價格來源", html)
        self.assertIn("VWAP", html)
        self.assertIn("量比", html)
        self.assertIn("不構成投資建議", html)

    def test_scheduled_tracker_interval_uses_taiwan_market_windows(self):
        tw = ZoneInfo("Asia/Taipei")

        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 17, 7, 5, tzinfo=tw)),
            (1800, "開盤前觀察池"),
        )
        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 17, 9, 30, tzinfo=tw)),
            (300, "台股盤中"),
        )
        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 17, 13, 45, tzinfo=tw)),
            (900, "收盤後回測"),
        )
        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 20, 9, 30, tzinfo=tw)),
            (None, "週末休市"),
        )


if __name__ == "__main__":
    unittest.main()
