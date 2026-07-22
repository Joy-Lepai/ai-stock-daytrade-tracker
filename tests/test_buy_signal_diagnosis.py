import unittest

from stock_daytrade_system.buy_signal_diagnosis import build_buy_signal_diagnosis


class BuySignalDiagnosisTests(unittest.TestCase):
    def test_post_close_with_stale_layers_explains_review_only(self):
        payload = {
            "market_mode": "post_close_review",
            "market_mode_label": "盤後復盤模式",
            "allow_intraday_signal": False,
            "can_show_any_strong_long": False,
            "required_stale_layers": ["full_market", "post_close_validation"],
            "stale_layers": ["full_market", "post_close_validation"],
            "price_status_summary": {"status": "資料不足"},
            "front_category_summary": {"counts": {}, "total": 0},
            "fugle_priority_pool": {"selected_symbols": ["6919.TW"]},
        }

        diagnosis = build_buy_signal_diagnosis(payload)

        self.assertEqual(diagnosis["state"], "review_only")
        self.assertFalse(diagnosis["can_answer_intraday_buy"])
        self.assertIn("不能判斷盤中可以做多", diagnosis["headline"])
        self.assertIn("全市場掃描", diagnosis["primary_reason"])
        self.assertIn("6919.TW", diagnosis["what_to_watch_now"][0])
        self.assertIn("/refresh_full_market", diagnosis["next_steps"][0])

    def test_intraday_no_signal_explains_waiting_conditions(self):
        payload = {
            "market_mode": "intraday",
            "market_mode_label": "盤中",
            "allow_intraday_signal": True,
            "can_show_any_strong_long": False,
            "required_stale_layers": [],
            "stale_layers": [],
            "price_status_summary": {"status": "正常", "live_count": 30},
            "front_category_summary": {
                "counts": {"強烈買多": 0, "買多": 0, "觀察": 8, "看空": 2},
                "strong_buy_count": 0,
                "buy_count": 0,
                "watch_count": 8,
                "bearish_count": 2,
                "total": 10,
                "no_signal_reason": "量比與突破尚未確認。",
            },
        }

        diagnosis = build_buy_signal_diagnosis(payload)

        self.assertEqual(diagnosis["state"], "wait_signal")
        self.assertTrue(diagnosis["can_answer_intraday_buy"])
        self.assertFalse(diagnosis["can_show_strong_buy"])
        self.assertIn("現在沒有可做多", diagnosis["headline"])
        self.assertIn("量比", diagnosis["primary_reason"])
        self.assertEqual(diagnosis["counts"]["watch"], 8)

    def test_intraday_strong_buy_keeps_risk_language(self):
        payload = {
            "market_mode": "intraday",
            "market_mode_label": "盤中",
            "allow_intraday_signal": True,
            "can_show_any_strong_long": True,
            "required_stale_layers": [],
            "stale_layers": [],
            "price_status_summary": {"status": "正常", "live_count": 30},
            "front_category_summary": {
                "counts": {"強烈買多": 2, "買多": 1, "觀察": 8, "看空": 2},
                "strong_buy_count": 2,
                "buy_count": 1,
                "watch_count": 8,
                "bearish_count": 2,
                "total": 13,
            },
        }

        diagnosis = build_buy_signal_diagnosis(payload)

        self.assertEqual(diagnosis["state"], "has_strong_buy")
        self.assertTrue(diagnosis["can_show_strong_buy"])
        self.assertIn("不是無腦買", diagnosis["plain_answer"])
        self.assertIn("進場雷達", " ".join(diagnosis["next_steps"]))


if __name__ == "__main__":
    unittest.main()
