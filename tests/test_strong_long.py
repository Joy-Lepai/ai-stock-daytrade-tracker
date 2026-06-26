import unittest

from stock_daytrade_system.strong_long import build_strong_long_funnel, evaluate_strong_long_candidate


def candidate(**overrides):
    data = {
        "symbol": "2330.TW",
        "entry_status": "wait_breakout",
        "grade": "B+",
        "last_price": 99.7,
        "vwap": 99.0,
        "volume_ratio": 1.2,
        "above_vwap": True,
        "previous_high": 100.0,
        "trigger_price": 100.0,
        "bullish_score": 78,
        "risk_score": 45,
        "confidence_score": 65,
        "stop_loss": 97.5,
        "upper_shadow_pct": 1.0,
        "break_prev_high": False,
        "conflicts": [],
    }
    data.update(overrides)
    return data


class StrongLongTests(unittest.TestCase):
    def test_wait_breakout_can_be_strong_long_candidate_but_not_executable(self):
        result = evaluate_strong_long_candidate(candidate())

        self.assertTrue(result.is_candidate)
        self.assertFalse(result.is_executable)
        self.assertEqual(result.subtitle, "等待突破")

    def test_executable_is_candidate_and_executable(self):
        result = evaluate_strong_long_candidate(candidate(entry_status="executable", break_prev_high=True))

        self.assertTrue(result.is_candidate)
        self.assertTrue(result.is_executable)

    def test_non_intraday_blocks_realtime_strong_long(self):
        result = evaluate_strong_long_candidate(candidate(), intraday=False, market_mode="closed_review")

        self.assertFalse(result.is_candidate)
        self.assertIn("非盤中模式", result.blockers)

    def test_cached_price_blocks_strong_long(self):
        result = evaluate_strong_long_candidate(candidate(), price_status_label="cached", uses_last_known=True)

        self.assertFalse(result.is_candidate)
        self.assertIn("使用上一筆", result.blockers)

    def test_funnel_counts_stages_and_blockers(self):
        payload = build_strong_long_funnel(
            [
                candidate(),
                candidate(symbol="2317.TW", entry_status="high_risk", risk_score=70),
                candidate(symbol="2303.TW", volume_ratio=0.6, entry_status="wait_volume"),
            ],
            total_market_count=1125,
            momentum_candidate_count=40,
            live_count=120,
        )

        self.assertEqual(payload["total_market_count"], 1125)
        self.assertEqual(payload["momentum_candidate_count"], 40)
        self.assertEqual(payload["strong_long_candidate_count"], 1)
        self.assertEqual(payload["executable_count"], 0)
        self.assertEqual(payload["blocked_high_risk_count"], 1)
        self.assertEqual(payload["blocked_wait_volume_count"], 1)
        self.assertTrue(payload["top_blockers"])
        self.assertTrue(payload["action_plan"])
        self.assertTrue(payload["primary_action"])
        self.assertTrue(payload["primary_wait_condition"])

    def test_funnel_action_plan_translates_blockers_to_operator_steps(self):
        payload = build_strong_long_funnel(
            [
                candidate(symbol="2317.TW", entry_status="high_risk", risk_score=70),
                candidate(symbol="2303.TW", volume_ratio=0.6, entry_status="wait_volume"),
            ],
            total_market_count=1125,
            momentum_candidate_count=40,
            live_count=120,
        )

        actions = " ".join(str(item.get("action", "")) for item in payload["action_plan"])
        avoids = " ".join(str(item.get("avoid", "")) for item in payload["action_plan"])

        self.assertIn("先降追價風險", actions)
        self.assertIn("不要追高", avoids)
        self.assertIn("high_risk 只能觀察", avoids)


if __name__ == "__main__":
    unittest.main()
