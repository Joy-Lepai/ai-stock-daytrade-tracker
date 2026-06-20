from datetime import datetime, timedelta
import unittest

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.intraday import OpeningSignal
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


def opening_signal(last_price, vwap, volume_ratio=1.2):
    return OpeningSignal(
        symbol="2330.TW",
        name="台積電",
        sector="semiconductor",
        direction="偏多確認",
        score=4,
        last_price=last_price,
        opening_range_high=last_price - 1,
        opening_range_low=last_price - 2,
        vwap=vwap,
        cumulative_volume=900_000,
        volume_ratio=volume_ratio,
        reasons=[],
    )


class LongModelTests(unittest.TestCase):
    def test_builds_a_grade_candidate_for_breakout_above_vwap(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
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
        self.assertEqual(candidates[0].entry_status, "executable")
        self.assertEqual(candidates[0].trade_bias, "long")
        self.assertEqual(candidates[0].trade_bias_label, "可執行")
        self.assertGreaterEqual(candidates[0].bullish_score, 60)
        self.assertTrue(candidates[0].above_vwap)
        self.assertIn("intraday_window", candidates[0].timeframe_diagnostics)
        self.assertTrue(candidates[0].trend_diagnosis)
        self.assertIn("institutional_label", candidates[0].institutional_context)
        self.assertIn("sector_status_label", candidates[0].sector_context)

    def test_pre_open_time_policy_does_not_mark_candidate_executable(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("2330.TW", "台積電", "semiconductor")],
            {"2330.TW": bars},
            {"2330.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
            captured_at=datetime(2026, 1, 31, 8, 30),
        )

        self.assertEqual(candidates[0].grade, "A")
        self.assertNotEqual(candidates[0].entry_status, "executable")
        self.assertIn("非盤中不直接列為可執行", "；".join(candidates[0].reasons))

    def test_main_entry_time_policy_keeps_high_quality_executable(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("2330.TW", "台積電", "semiconductor")],
            {"2330.TW": bars},
            {"2330.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
            captured_at=datetime(2026, 1, 31, 9, 30),
        )

        self.assertEqual(candidates[0].grade, "A")
        self.assertEqual(candidates[0].entry_status, "executable")

    def test_late_session_time_policy_avoids_new_chase_entries(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("2330.TW", "台積電", "semiconductor")],
            {"2330.TW": bars},
            {"2330.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
            captured_at=datetime(2026, 1, 31, 11, 45),
        )

        self.assertEqual(candidates[0].grade, "A")
        self.assertEqual(candidates[0].entry_status, "wait_pullback")
        self.assertIn("11:30 後避免新追價", "；".join(candidates[0].reasons))

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

    def test_partial_breakout_stays_b_level_for_pullback_tracking(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars[-3] = daily_bar(27, 100.8, high=107, low=99, volume=1_000_000)
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("6278.TW", "台表科", "electronics")],
            {"6278.TW": bars},
            {"6278.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].grade, "B")
        self.assertEqual(candidates[0].entry_status, "practice_long")
        self.assertTrue(candidates[0].break_prev_high)
        self.assertFalse(candidates[0].break_5d_high)

    def test_low_volume_ratio_cannot_be_a_or_b(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=70_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("1216.TW", "統一", "consumer")],
            {"1216.TW": bars},
            {"1216.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertLess(candidates[0].volume_ratio, 0.8)
        self.assertEqual(candidates[0].grade, "D")
        self.assertEqual(candidates[0].entry_status, "wait_volume")

    def test_b_plus_with_sub_one_volume_becomes_practice_long(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=112_500) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("2892.TW", "第一金", "finance")],
            {"2892.TW": bars},
            {"2892.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertGreaterEqual(candidates[0].volume_ratio, 0.8)
        self.assertLess(candidates[0].volume_ratio, 1.0)
        self.assertEqual(candidates[0].grade, "B+")
        self.assertEqual(candidates[0].entry_status, "practice_long")
        self.assertIn("B+練習觀察", "；".join(candidates[0].reasons))

    def test_near_vwap_but_not_above_waits_for_vwap(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))

        candidates = build_long_candidates(
            [WatchSymbol("2330.TW", "台積電", "semiconductor")],
            {"2330.TW": bars},
            {"2330.TW": []},
            [opening_signal(last_price=105, vwap=105.2, volume_ratio=1.2)],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].grade, "B+")
        self.assertFalse(candidates[0].above_vwap)
        self.assertEqual(candidates[0].entry_status, "wait_vwap")

    def test_bearish_market_blocks_candidate_to_d_grade(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 104 + index * 0.2, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("2303.TW", "聯電", "semiconductor")],
            {"2303.TW": bars},
            {"2303.TW": intraday},
            [],
            [],
            MarketBias(score=-3, direction="偏空", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].grade, "D")

    def test_below_vwap_is_d_grade_even_with_breakout_context(self):
        bars = [daily_bar(index, 90 + index * 0.4) for index in range(30)]
        bars.append(daily_bar(30, 105, high=106, low=101, volume=2_000_000))
        intraday = [intraday_bar(index, 107 - index * 0.4, volume=180_000) for index in range(8)]

        candidates = build_long_candidates(
            [WatchSymbol("6239.TW", "力成", "semiconductor")],
            {"6239.TW": bars},
            {"6239.TW": intraday},
            [],
            [],
            MarketBias(score=3, direction="偏多", notes=[]),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].grade, "D")
        self.assertFalse(candidates[0].above_vwap)
        self.assertIn(candidates[0].trade_bias, {"short", "watch"})


if __name__ == "__main__":
    unittest.main()
