import unittest

from stock_daytrade_system.limit_up_phase import build_limit_up_market_phase


class LimitUpPhaseTests(unittest.TestCase):
    def test_broad_limit_wave_chase_risk_has_operator_priority(self):
        phase = build_limit_up_market_phase(
            total=12,
            chase_risk=8,
            wait_confirm=2,
            data_missing=0,
            entered=1,
            subject="接近漲停 / 漲停",
        )

        self.assertEqual(phase["market_phase"], "broad_limit_wave_chase_risk")
        self.assertEqual(phase["market_phase_label"], "漲停潮但追價風險主導")
        self.assertIn("盤面很熱", phase["market_phase_summary"])
        self.assertIn("拉回 VWAP", phase["operator_priority"])

    def test_missed_limit_up_wave_prioritizes_data_pool_repair(self):
        phase = build_limit_up_market_phase(
            total=5,
            missed=1,
            chase_risk=4,
            subject="接近漲停 / 漲停",
        )

        self.assertEqual(phase["market_phase"], "limit_wave_data_gap")
        self.assertIn("真漏抓", phase["market_phase_summary"])
        self.assertIn("不要手動把漏抓股升級成買多", phase["operator_priority"])

    def test_zero_limit_up_wave_uses_custom_empty_target(self):
        phase = build_limit_up_market_phase(
            total=0,
            subject="接近漲停 / 急拉",
            empty_target="快照",
        )

        self.assertEqual(phase["market_phase"], "no_limit_wave")
        self.assertIn("快照", phase["market_phase_summary"])
        self.assertIn("強烈買多漏斗", phase["market_phase_summary"])


if __name__ == "__main__":
    unittest.main()
