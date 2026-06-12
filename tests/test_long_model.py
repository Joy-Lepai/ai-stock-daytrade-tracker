from datetime import datetime, timedelta
import unittest

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.long_model import build_long_candidates
from stock_daytrade_system.scoring import MarketBias


def daily_bar(index, close, high=None, low=None, volume=1_000_000):
    high = high if high is not None else close + 1
    low = low if low is not None else close - 1
    return Bar(
        timestamp=datetime(2026, 1, 1) + timedelta(days=index),
        open=close - 0.5,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def intraday_bar(index, close, volume=50_000):
    return Bar(
        timestamp=datetime(2026, 1, 31, 9, 0) + timedelta(minutes=index * 5),
        open=close - 0.1,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        volume=volume,
    )


class LongModelTests(unittest.TestCase):
    def test_builds_a_grade_candidate_for_breakout_above_vwap(self):
        bars = [daily_bar(index, 90 + index * 0.2) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("2330.TW", "台積電", "semiconductor")],
            {"2330.TW": bars},
            {"2330.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].grade, "A")
        self.assertGreaterEqual(candidates[0].bullish_score, 60)
        self.assertTrue(candidates[0].above_vwap)

    def test_high_gain_with_long_upper_shadow_is_not_a_grade(self):
        bars = [daily_bar(index, 90 + index * 0.2) for index in range(30)]
        bars.append(daily_bar(30, 105, high=112, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("6919.TW", "康霈生技", "biotech")],
            {"6919.TW": bars},
            {"6919.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertNotEqual(candidates[0].grade, "A")
        self.assertIn("長上影，追價風險高", candidates[0].risk_reasons)

    def test_breakout_without_volume_expansion_is_not_a_grade(self):
        bars = [daily_bar(index, 90 + index * 0.2) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=60_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("6278.TW", "台表科", "electronics")],
            {"6278.TW": bars},
            {"6278.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertNotEqual(candidates[0].grade, "A")
        self.assertLess(candidates[0].volume_ratio, 1)


if __name__ == "__main__":
    unittest.main()
