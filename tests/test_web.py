import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from stock_daytrade_system.web import (
    _current_commit_hash,
    _extract_body,
    _extract_style,
    _notification_signals_payload,
    _scheduled_tracker_interval,
    _tracker_html_needs_refresh,
    latest_tracker_file,
    render_accuracy_page,
    render_paper_dashboard_page,
    render_shell,
    render_tw_advisor_page,
)
from stock_daytrade_system.db import connect


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
          台股做多當沖追蹤器 v1
          今日決策摘要
          最接近強烈買多 5 檔
          買多觀察池 10 檔
          最大原因 / 最大卡關
          下一步
          失效條件
          精準分數
          我的持倉作戰區
          上一交易日復盤
          下個交易日觀察清單
          模型檢討
          精準資料缺口總覽
          資料健康度
          台股全市場異動掃描池
          漏抓股票診斷
          模型條件診斷
          B+ 觸發條件追蹤
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
        self.assertIn("虛擬平倉覆盤", html)
        self.assertIn("本次交易檢討標籤", html)
        self.assertIn("FOMO（衝動追高）", html)
        self.assertIn("review_tags", html)
        self.assertIn("不串接券商", html)
        self.assertIn("目前持倉", html)
        self.assertIn("尚無持倉", html)
        self.assertIn("虛擬交易 API 暫時無法更新", html)
        self.assertNotIn("虛擬交易資料暫時無法更新", html)
        self.assertNotIn("!response.ok || payload.error", html)

    def test_accuracy_page_has_review_tag_pie_chart(self):
        html = render_accuracy_page()

        self.assertIn("心魔分佈（錯誤原因統計）", html)
        self.assertIn("accuracy-review-chart", html)
        self.assertIn("review_tag_loss_distribution", html)
        self.assertIn("虧損交易覆盤標籤", html)
        self.assertIn("樣本品質", html)
        self.assertIn("sampleQualityLabel", html)
        self.assertIn("資料完整度", html)
        self.assertIn("accuracy-data-completeness", html)
        self.assertIn("可否調參", html)
        self.assertIn("進場雷達成績單", html)
        self.assertIn("accuracy-entry-radar", html)
        self.assertIn("renderEntryRadarScorecard", html)
        self.assertIn("最大卡關", html)

    def test_tw_advisor_page_has_stock_input_and_scan_api(self):
        html = render_tw_advisor_page()

        self.assertIn("個股當沖作戰卡", html)
        self.assertIn("1301、1301.TW、6603.TWO、台塑", html)
        self.assertIn("data-symbol=\"2330\"", html)
        self.assertIn("data-symbol=\"1301\"", html)
        self.assertIn("new URLSearchParams(window.location.search).get(\"symbol\")", html)
        self.assertIn("/api/tw/scan/symbol", html)
        self.assertIn("/tw/advisor?symbol=", html)
        self.assertIn("買多", html)
        self.assertIn("看空", html)
        self.assertIn("觀察", html)
        self.assertIn("作戰速讀", html)
        self.assertIn("renderAdvisorQuickReadCard", html)
        self.assertIn("資料狀態", html)
        self.assertIn("建議動作", html)
        self.assertIn("結論卡", html)
        self.assertIn("進場雷達", html)
        self.assertIn("最大卡關原因", html)
        self.assertIn("下一個觸發條件", html)
        self.assertIn("目前結論", html)
        self.assertIn("decision_card", html)
        self.assertIn("精準分數", html)
        self.assertIn("失效條件", html)
        self.assertIn("entry_status", html)
        self.assertIn("強烈買多 / 進場確認", html)
        self.assertIn("強烈買多候選：值得盯盤", html)
        self.assertIn("通過進場確認才代表已觸發進場條件", html)
        self.assertIn("最新成交價", html)
        self.assertIn("資料可信度", html)
        self.assertIn("Yahoo 5分K", html)
        self.assertIn("可用於當沖判斷", html)
        self.assertIn("價格來源", html)
        self.assertIn("關鍵指標", html)
        self.assertIn("預估賺賠比", html)
        self.assertIn("趨勢延續診斷", html)
        self.assertIn("籌碼背景", html)
        self.assertIn("族群狀態", html)
        self.assertIn("Fugle 逐筆成交 / 大單偵測", html)
        self.assertIn("Fugle 即時行情 / 五檔力道", html)
        self.assertIn("Fugle 1分K / 均價確認", html)
        self.assertIn("內外盤力道", html)
        self.assertIn("Fugle 逐筆成交只作進場確認背景", html)
        self.assertIn("法人買超不能取代", html)
        self.assertIn("族群強只是背景", html)
        self.assertIn("精準當沖資料檢查", html)
        self.assertIn("進場前檢查表", html)
        self.assertNotIn("Shioaji 即時報價 / 盤口確認", html)
        self.assertIn("TWSE MIS 公開五檔委買委賣", html)
        self.assertIn("公開五檔", html)
        self.assertIn("進場確認雷達", html)
        self.assertIn("Fugle 逐筆成交", html)
        self.assertIn("不會直接產生強烈買多，也不會自動下單", html)
        self.assertIn("停損距離合理", html)
        self.assertIn("缺 Tick / 五檔時不作高精準進場", html)
        self.assertIn("逐筆成交 Tick", html)
        self.assertIn("五檔委買委賣", html)
        self.assertIn("Fugle 逐筆成交與 TWSE MIS 公開五檔已作為 MVP", html)
        self.assertIn("分時走勢圖", html)
        self.assertIn("價格 / VWAP / 關鍵價位 / 停損停利", html)
        self.assertIn("做多理由", html)
        self.assertIn("風險理由", html)
        self.assertIn("目前不執行原因", html)
        self.assertIn("下一步條件與失效條件", html)
        self.assertIn("B+ 變 A", html)
        self.assertIn("停損價", html)
        self.assertIn("停利價", html)
        self.assertIn("這檔過去表現", html)
        self.assertIn("來源與全市場排名", html)
        self.assertIn("開發者資訊", html)
        self.assertIn("VWAP", html)
        self.assertIn("量比", html)
        self.assertIn("資料不足，不能產生有效當沖建議。", html)
        self.assertIn("這檔歷史樣本不足", html)
        self.assertIn("不構成投資建議", html)

    def test_scheduled_tracker_interval_uses_taiwan_market_windows(self):
        tw = ZoneInfo("Asia/Taipei")

        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 17, 7, 5, tzinfo=tw)),
            (1800, "開盤前觀察池"),
        )
        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 17, 9, 30, tzinfo=tw)),
            (900, "台股盤中"),
        )
        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 17, 13, 45, tzinfo=tw)),
            (900, "收盤後回測"),
        )
        self.assertEqual(
            _scheduled_tracker_interval(datetime(2026, 6, 20, 9, 30, tzinfo=tw)),
            (None, "週末休市"),
        )

    def test_dashboard_shell_polls_refresh_status_without_auto_refresh(self):
        html = render_shell("<main>ok</main>", active_file="today.html")

        self.assertIn("/api/refresh/status", html)
        self.assertIn("/api/system/version", html)
        self.assertIn("手動更新", html)
        self.assertIn("系統狀態與資料來源", html)
        self.assertIn("manual-refresh-menu", html)
        self.assertIn("版本驗收", html)
        self.assertIn("資料新鮮度", html)
        self.assertIn("/api/notification/signals", html)
        self.assertIn("notify-sound-toggle", html)
        self.assertIn("notify-desktop-toggle", html)
        self.assertIn("AudioContext", html)
        self.assertIn("Notification.requestPermission", html)
        self.assertIn("position-risk-per-trade", html)
        self.assertIn("stockPositionTradeRisk", html)
        self.assertIn("建議點火", html)
        self.assertIn("風暴比不佳，建議放棄", html)
        self.assertIn("window.StockHotkeys", html)
        self.assertIn("全部觀察股", html)
        self.assertIn("已觸發 / 多頭動能股", html)
        self.assertIn("等待站回 VWAP 股", html)
        self.assertIn("高風險觀望股", html)
        self.assertIn("data-stock-search", html)
        self.assertIn("document.addEventListener(\"keydown\"", html)
        self.assertIn("/refresh_full_market", html)
        self.assertIn("/refresh_watchlist", html)
        self.assertIn("/refresh_positions", html)
        self.assertIn("/refresh_post_close_validation", html)
        self.assertIn("盤後驗證", html)
        self.assertIn("market_mode_label", html)
        self.assertIn("provider_status", html)
        self.assertIn("行情 provider", html)
        self.assertIn("refresh_guidance", html)
        self.assertIn("建議動作", html)
        self.assertIn("WebSocket", html)
        self.assertIn("deployment_status", html)
        self.assertIn("signal_guard", html)
        self.assertNotIn('fetch("/refresh"', html)

    def test_notification_signals_payload_returns_triggered_status(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "daytrade.db"
            with connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO symbols (symbol, name, sector, market) VALUES (?, ?, ?, ?)",
                    ("2330.TW", "台積電", "半導體", "TW"),
                )
                conn.execute(
                    """
                    INSERT INTO recommendations (
                      market, date, symbol, first_seen_at, latest_seen_at, grade,
                      bullish_score, risk_score, entry_status, lifecycle_status,
                      observed_at, trigger_time, trigger_price, trigger_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "TW",
                        "2026-06-19",
                        "2330.TW",
                        "2026-06-19T09:05:00",
                        "2026-06-19T09:10:00",
                        "B+",
                        72,
                        35,
                        "wait_vwap",
                        "triggered",
                        "2026-06-19T09:05:00",
                        "2026-06-19T09:10:00",
                        100,
                        "站回 VWAP 後觸發",
                    ),
                )

                payload = _notification_signals_payload(conn)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["date"], "2026-06-19")
        self.assertEqual(payload["signals"][0]["symbol"], "2330.TW")
        self.assertEqual(payload["signals"][0]["name_zh"], "台積電")
        self.assertEqual(payload["signals"][0]["status"], "triggered")


if __name__ == "__main__":
    unittest.main()
