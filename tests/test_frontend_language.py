import unittest

from stock_daytrade_system.frontend_language import front_decision_card, front_trade_view


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

    def test_decision_card_has_user_facing_core_fields(self):
        card = front_decision_card(
            {
                "grade": "B",
                "entry_status": "wait_volume",
                "last_price": 101,
                "vwap": 100,
                "above_vwap": True,
                "volume_ratio": 0.72,
                "stop_loss": 99,
                "bullish_score": 72,
                "risk_score": 42,
                "confidence_score": 64,
            },
            entry_radar={
                "entry_state": "等待確認",
                "blocker_code": "low_volume_ratio",
                "blocker_summary": "量比不足",
                "next_trigger": "量比放大到 1.0x 以上",
            },
        )

        self.assertIn(card.final_decision, {"做多", "觀察", "強烈做多", "做空"})
        self.assertEqual(card.top_reason, "量比不足")
        self.assertIn("1.0x", card.next_trigger)
        self.assertTrue(card.invalid_condition)
        self.assertGreaterEqual(card.precision_score, 0)

    def test_previous_limit_volume_stock_becomes_continuation_observation_not_strong_long(self):
        card = front_decision_card(
            {
                "grade": "C",
                "entry_status": "high_risk",
                "last_price": 109,
                "change_pct": 9.9,
                "turnover": 1_200_000_000,
                "daily_volume_ratio": 5.4,
                "volume_ratio": 5.4,
                "vwap": 106,
                "above_vwap": True,
                "stop_loss": 104,
                "risk_score": 76,
                "confidence_score": 60,
                "upper_shadow_pct": 1.2,
            },
            entry_radar={
                "entry_state": "暫不進場",
                "blocker_code": "high_risk",
                "blocker_summary": "追價風險高",
                "next_trigger": "等待拉回 VWAP",
            },
        )

        self.assertEqual(card.final_decision, "觀察")
        self.assertEqual(card.observation_type, "續強觀察")
        self.assertIn("前日爆量漲停", card.top_reason)
        self.assertIn("站上 VWAP", card.next_trigger)


if __name__ == "__main__":
    unittest.main()
