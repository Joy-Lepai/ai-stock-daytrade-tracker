import unittest

from stock_daytrade_system.tw_advisor_analysis import build_tw_advisor_analysis


class TWAdvisorAnalysisTests(unittest.TestCase):
    def test_strong_vwap_volume_breakout_can_be_long(self):
        analysis = build_tw_advisor_analysis(
            scan={
                "latest_price": 105,
                "vwap": 103,
                "above_vwap": True,
                "break_prev_high": True,
                "break_5d_high": False,
                "volume_ratio": 1.3,
                "change_pct": 3.2,
                "turnover": 1_500_000_000,
            },
            candidate={
                "bullish_score": 76,
                "risk_score": 35,
                "confidence_score": 65,
                "trade_bias_label": "觀察",
                "opening_range_high": 104,
                "upper_shadow_pct": 0.4,
            },
            display={"current_price": 105, "change_pct": 3.2},
            market_status="偏多",
        )

        self.assertEqual(analysis.action_label, "買多")
        self.assertGreaterEqual(analysis.technical_score, 75)
        self.assertGreaterEqual(analysis.volume_score, 65)
        self.assertLessEqual(analysis.chase_risk_score, 55)
        self.assertIn("可列入買多觀察", analysis.action_summary)

    def test_overextended_low_volume_stock_stays_watch(self):
        analysis = build_tw_advisor_analysis(
            scan={
                "latest_price": 110,
                "vwap": 104,
                "above_vwap": True,
                "break_prev_high": True,
                "volume_ratio": 0.7,
                "change_pct": 8.5,
            },
            candidate={
                "bullish_score": 70,
                "risk_score": 60,
                "confidence_score": 50,
                "trade_bias_label": "觀察",
                "upper_shadow_pct": 1.8,
            },
            display={"current_price": 110, "change_pct": 8.5},
            market_status="偏多",
        )

        self.assertEqual(analysis.action_label, "觀察")
        self.assertGreaterEqual(analysis.chase_risk_score, 70)
        self.assertIn("追價風險", analysis.risk_summary)

    def test_below_vwap_with_volume_can_be_short(self):
        analysis = build_tw_advisor_analysis(
            scan={
                "latest_price": 98,
                "vwap": 100,
                "above_vwap": False,
                "break_prev_high": False,
                "volume_ratio": 1.1,
                "change_pct": -1.4,
            },
            candidate={
                "bullish_score": 30,
                "risk_score": 45,
                "confidence_score": 60,
                "trade_bias_label": "觀察",
                "opening_range_low": 99,
            },
            display={"current_price": 98, "change_pct": -1.4},
            market_status="偏空",
        )

        self.assertEqual(analysis.action_label, "賣空")
        self.assertIn("賣空", analysis.next_step)


if __name__ == "__main__":
    unittest.main()
