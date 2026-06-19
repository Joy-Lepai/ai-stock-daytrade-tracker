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


if __name__ == "__main__":
    unittest.main()
