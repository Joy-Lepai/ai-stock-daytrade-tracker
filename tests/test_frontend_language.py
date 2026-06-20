import unittest

from stock_daytrade_system.frontend_language import front_trade_view


class FrontendLanguageTests(unittest.TestCase):
    def test_executable_with_complete_fresh_data_is_strong_long(self):
        view = front_trade_view(
            {
                "grade": "A",
                "entry_status": "executable",
                "vwap": 100,
                "volume_ratio": 1.2,
                "stop_loss": 99,
                "reasons": ["站上 VWAP 且量能確認"],
            }
        )

        self.assertEqual(view.category, "強烈做多")
        self.assertTrue(view.is_strong_long_allowed)

    def test_practice_long_is_long_but_not_strong_long(self):
        view = front_trade_view(
            {
                "grade": "B+",
                "entry_status": "practice_long",
                "vwap": 100,
                "volume_ratio": 0.85,
                "stop_loss": 98,
            }
        )

        self.assertEqual(view.category, "做多")
        self.assertIn("練習買多", view.subtitle)
        self.assertFalse(view.is_strong_long_allowed)

    def test_wait_breakout_can_be_strong_long_candidate_without_executable(self):
        view = front_trade_view(
            {
                "grade": "B+",
                "entry_status": "wait_breakout",
                "last_price": 99.7,
                "vwap": 99,
                "volume_ratio": 1.2,
                "above_vwap": True,
                "previous_high": 100,
                "trigger_price": 100,
                "bullish_score": 78,
                "risk_score": 45,
                "confidence_score": 65,
                "stop_loss": 97.5,
                "upper_shadow_pct": 1.0,
                "conflicts": [],
            }
        )

        self.assertEqual(view.category, "強烈做多")
        self.assertIn("等待突破", view.subtitle)
        self.assertTrue(view.is_strong_long_allowed)
        self.assertIn("not_executable_yet", view.reason_codes)

    def test_high_risk_is_observation_not_long(self):
        view = front_trade_view(
            {
                "grade": "C",
                "entry_status": "high_risk",
                "vwap": 100,
                "volume_ratio": 5,
                "stop_loss": 96,
                "risk_reasons": ["追價風險高"],
            }
        )

        self.assertEqual(view.category, "觀察")
        self.assertIn("追價風險高", view.subtitle)
        self.assertFalse(view.is_strong_long_allowed)

    def test_stale_or_missing_core_data_blocks_strong_long(self):
        view = front_trade_view(
            {"grade": "A", "entry_status": "executable", "volume_ratio": 1.5, "stop_loss": 99},
            stale=True,
        )

        self.assertEqual(view.category, "做多")
        self.assertIn("資料過期", view.subtitle)
        self.assertFalse(view.is_strong_long_allowed)

    def test_cached_price_status_blocks_strong_long_without_type_error(self):
        view = front_trade_view(
            {
                "grade": "A",
                "entry_status": "executable",
                "vwap": 100,
                "volume_ratio": 1.5,
                "stop_loss": 99,
            },
            price_status_label="cached",
            uses_last_known=True,
        )

        self.assertNotEqual(view.category, "強烈做多")
        self.assertFalse(view.is_strong_long_allowed)
        self.assertIn("refresh_layer_stale", view.reason_codes)

    def test_delayed_price_status_blocks_strong_long_without_type_error(self):
        view = front_trade_view(
            {
                "grade": "A",
                "entry_status": "executable",
                "vwap": 100,
                "volume_ratio": 1.5,
                "stop_loss": 99,
            },
            price_status_label="delayed",
            is_delayed=True,
        )

        self.assertNotEqual(view.category, "強烈做多")
        self.assertFalse(view.is_strong_long_allowed)
        self.assertIn("stale_data", view.reason_codes)


if __name__ == "__main__":
    unittest.main()
