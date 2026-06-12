import unittest

from stock_daytrade_system.confidence_model import evaluate_signal


class ConfidenceModelTests(unittest.TestCase):
    def test_complete_clean_signal_has_high_confidence(self):
        result = evaluate_signal(
            latest_price=101,
            volume=1_000_000,
            vwap=100,
            above_vwap=True,
            volume_ratio=1.6,
            break_prev_high=True,
            break_orb=True,
            higher_high=True,
            higher_low=True,
            risk_score=20,
            bullish_score=85,
            entry_status="executable",
            market_status="bullish",
            sector_strength=3,
        )

        self.assertEqual(result.confidence_level, "high")
        self.assertEqual(result.adjusted_entry_status, "executable")
        self.assertEqual(result.conflicts_count, 0)
        self.assertIn("信心", result.confidence_summary)

    def test_missing_data_reduces_confidence_and_lists_conflict(self):
        result = evaluate_signal(
            latest_price=None,
            volume=1_000_000,
            vwap=None,
            above_vwap=True,
            volume_ratio=1.2,
            bullish_score=70,
            entry_status="wait_breakout",
            data_status="partial",
        )

        self.assertLess(result.confidence_score, 60)
        self.assertTrue(any(item["code"] == "missing_data_signal" for item in result.conflicts))
        self.assertIn("核心資料缺漏", result.conflict_summary)

    def test_below_vwap_cannot_remain_executable(self):
        result = evaluate_signal(
            latest_price=98,
            volume=1_000_000,
            vwap=100,
            above_vwap=False,
            volume_ratio=1.3,
            break_prev_high=True,
            bullish_score=80,
            risk_score=25,
            entry_status="executable",
        )

        self.assertNotEqual(result.adjusted_entry_status, "executable")
        self.assertIn(result.adjusted_entry_status, {"wait_vwap", "avoid"})

    def test_high_risk_lowers_confidence_and_creates_conflict(self):
        result = evaluate_signal(
            latest_price=108,
            volume=2_000_000,
            vwap=100,
            above_vwap=True,
            volume_ratio=1.8,
            break_prev_high=True,
            distance_to_vwap_pct=8,
            bullish_score=85,
            risk_score=75,
            entry_status="executable",
            long_upper_shadow=True,
        )

        self.assertLess(result.confidence_score, 80)
        self.assertTrue(any(item["code"] == "high_risk_executable" for item in result.conflicts))
        self.assertIn(result.adjusted_entry_status, {"high_risk", "avoid"})

    def test_score_below_sixty_blocks_executable(self):
        result = evaluate_signal(
            latest_price=100,
            volume=500_000,
            vwap=99,
            above_vwap=True,
            volume_ratio=0.5,
            bullish_score=70,
            risk_score=20,
            entry_status="executable",
            data_status="partial",
            data_errors=["quote_partial"],
        )

        self.assertLess(result.confidence_score, 60)
        self.assertNotEqual(result.adjusted_entry_status, "executable")


if __name__ == "__main__":
    unittest.main()
