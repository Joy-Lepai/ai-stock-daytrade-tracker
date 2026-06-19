from datetime import datetime, timedelta
import unittest

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.intraday import analyze_opening_confirmation


def bar(index, open_, high, low, close, volume):
    return Bar(
        timestamp=datetime(2026, 1, 1, 9, 0) + timedelta(minutes=index * 5),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def daily_bars():
    return [
        Bar(
            timestamp=datetime(2026, 1, 1) + timedelta(days=index),
            open=100,
            high=105,
            low=95,
            close=102,
            volume=1_000_000,
        )
        for index in range(20)
    ]


class IntradaySignalTests(unittest.TestCase):
    def test_builds_opening_signal_with_three_early_bars(self):
        signal = analyze_opening_confirmation(
            WatchSymbol("2330.TW", "台積電"),
            [
                bar(0, 100, 101, 99, 100.5, 80_000),
                bar(1, 100.5, 101.5, 100, 101, 80_000),
                bar(2, 101, 102, 100.5, 101.8, 80_000),
            ],
            daily_bars(),
            opening_bars=3,
        )

        self.assertIsNotNone(signal)
        self.assertGreater(signal.volume_ratio, 1)
        self.assertGreater(signal.vwap, 0)

    def test_confirms_long_when_price_breaks_opening_range_with_volume(self):
        signal = analyze_opening_confirmation(
            WatchSymbol("2330.TW", "台積電"),
            [
                bar(0, 100, 101, 99, 100.5, 30_000),
                bar(1, 100.5, 101.5, 100, 101, 30_000),
                bar(2, 101, 102, 100.5, 101.8, 30_000),
                bar(3, 101.8, 104, 101.8, 103.5, 40_000),
            ],
            daily_bars(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "偏多確認")
        self.assertGreater(signal.vwap, 0)

    def test_returns_watch_when_breakout_has_insufficient_volume(self):
        signal = analyze_opening_confirmation(
            WatchSymbol("2330.TW", "台積電"),
            [
                bar(0, 100, 101, 99, 100.5, 1_000),
                bar(1, 100.5, 101.5, 100, 101, 1_000),
                bar(2, 101, 102, 100.5, 101.8, 1_000),
                bar(3, 101.8, 104, 101.8, 103.5, 1_000),
            ],
            daily_bars(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "觀望")


if __name__ == "__main__":
    unittest.main()
