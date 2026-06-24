import unittest

from stock_daytrade_system.trade_bias import evaluate_trade_bias


class TradeBiasTests(unittest.TestCase):
    def test_executable_above_vwap_is_long(self):
        result = evaluate_trade_bias(
            entry_status="executable",
            grade="A",
            bullish_score=85,
            risk_score=30,
            confidence_score=80,
            above_vwap=True,
            last_price=105,
            vwap=103,
            change_pct=2.5,
            volume_ratio=1.3,
            market_status="偏多",
            break_prev_high=True,
        )

        self.assertEqual(result.bias, "long")
        self.assertEqual(result.label, "進場雷達通過")

    def test_practice_long_is_counted_as_long_even_when_volume_is_practice_threshold(self):
        result = evaluate_trade_bias(
            entry_status="practice_long",
            grade="B+",
            bullish_score=72,
            risk_score=35,
            confidence_score=80,
            above_vwap=True,
            last_price=32.5,
            vwap=32.35,
            change_pct=1.2,
            volume_ratio=0.86,
            market_status="偏多",
            break_prev_high=True,
        )

        self.assertEqual(result.bias, "long")
        self.assertEqual(result.label, "練習買多")
        self.assertIn("練習買多", result.reason)

    def test_breakdown_below_vwap_with_volume_is_short(self):
        result = evaluate_trade_bias(
            entry_status="avoid",
            grade="D",
            bullish_score=20,
            risk_score=40,
            confidence_score=60,
            above_vwap=False,
            last_price=98,
            vwap=100,
            change_pct=-1.5,
            volume_ratio=1.2,
            market_status="中性",
            risk_reasons=["跌破 VWAP"],
        )

        self.assertEqual(result.bias, "short")
        self.assertEqual(result.label, "賣空")

    def test_weak_volume_below_vwap_is_watch_not_short(self):
        result = evaluate_trade_bias(
            entry_status="avoid",
            grade="D",
            bullish_score=20,
            risk_score=40,
            confidence_score=50,
            above_vwap=False,
            last_price=98,
            vwap=100,
            change_pct=-1.5,
            volume_ratio=0.5,
            market_status="中性",
        )

        self.assertEqual(result.bias, "watch")
        self.assertEqual(result.label, "觀察")


if __name__ == "__main__":
    unittest.main()
