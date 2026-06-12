from datetime import datetime, timedelta
import unittest

from stock_daytrade_system.config import RiskConfig, WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.scoring import MarketBias, score_symbol


def make_bars(start: float, step: float, count: int = 70, volume: float = 2_000_000):
    bars = []
    date = datetime(2026, 1, 1)
    price = start
    for index in range(count):
        price += step
        bars.append(
            Bar(
                timestamp=date + timedelta(days=index),
                open=price - 0.3,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=volume,
            )
        )
    return bars


class ScoringTests(unittest.TestCase):
    def test_scores_long_candidate_when_trend_and_relative_strength_are_positive(self):
        stock = make_bars(100, 1.0)
        benchmark = make_bars(100, 0.2)
        result = score_symbol(
            WatchSymbol("2330.TW", "台積電", "semiconductor"),
            stock,
            benchmark,
            MarketBias(3.0, "偏多", []),
            RiskConfig(
                min_price=20,
                min_avg_volume=1_000_000,
                max_loss_per_trade=3_000,
                round_lot_size=1_000,
                max_candidates_per_side=10,
            ),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.direction, "做多觀察")
        self.assertGreaterEqual(result.score, 3)
        self.assertGreater(result.trigger_price, result.stop_loss)
        self.assertGreater(result.target_price, result.trigger_price)
        self.assertGreaterEqual(result.suggested_shares, 0)

    def test_filters_low_volume_symbols(self):
        stock = make_bars(100, 1.0, volume=100_000)
        benchmark = make_bars(100, 0.2)
        result = score_symbol(
            WatchSymbol("0000.TW", "低量股"),
            stock,
            benchmark,
            MarketBias(3.0, "偏多", []),
            RiskConfig(
                min_price=20,
                min_avg_volume=1_000_000,
                max_loss_per_trade=3_000,
                round_lot_size=1_000,
                max_candidates_per_side=10,
            ),
        )

        self.assertIsNone(result)

    def test_intraday_style_strength_can_override_weak_midterm_trend(self):
        stock = make_bars(130, -0.6, volume=2_000_000)
        previous = stock[-2]
        stock[-1] = Bar(
            timestamp=stock[-1].timestamp,
            open=previous.close + 0.5,
            high=previous.high + 5.0,
            low=previous.close,
            close=previous.high + 4.0,
            volume=2_500_000,
        )
        benchmark = make_bars(100, 0.2)

        result = score_symbol(
            WatchSymbol("6919.TW", "康霈生技", "biotech"),
            stock,
            benchmark,
            MarketBias(3.0, "偏多", []),
            RiskConfig(
                min_price=20,
                min_avg_volume=1_000_000,
                max_loss_per_trade=3_000,
                round_lot_size=1_000,
                max_candidates_per_side=20,
            ),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.direction, "做多觀察")
        self.assertIn("當日強勢上漲", " ".join(result.reasons))


if __name__ == "__main__":
    unittest.main()
