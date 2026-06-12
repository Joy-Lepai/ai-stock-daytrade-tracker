from datetime import datetime, timedelta
import unittest

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.sectors import rank_sector_strength


def make_bars(start: float, step: float, count: int = 10):
    bars = []
    price = start
    for index in range(count):
        price += step
        bars.append(
            Bar(
                timestamp=datetime(2026, 1, 1) + timedelta(days=index),
                open=price - 1,
                high=price + 1,
                low=price - 2,
                close=price,
                volume=1_000_000,
            )
        )
    return bars


class SectorStrengthTests(unittest.TestCase):
    def test_ranks_stronger_sector_first(self):
        symbols = [
            WatchSymbol("A.TW", "A", "semiconductor"),
            WatchSymbol("B.TW", "B", "semiconductor"),
            WatchSymbol("C.TW", "C", "shipping"),
        ]
        data = {
            "A.TW": make_bars(100, 2),
            "B.TW": make_bars(90, 1.5),
            "C.TW": make_bars(100, -1),
        }

        result = rank_sector_strength(symbols, data, make_bars(100, 0.1))

        self.assertEqual(result[0].sector, "semiconductor")
        self.assertEqual(result[-1].sector, "shipping")
        self.assertEqual(result[0].direction, "強勢")


if __name__ == "__main__":
    unittest.main()
