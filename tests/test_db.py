from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from stock_daytrade_system.data import Bar
from stock_daytrade_system.db import backtest_summary, connect, latest_candidates, save_long_candidates, update_backtests
from stock_daytrade_system.long_model import LongCandidate


def candidate(volume_ratio=1.5, grade="A"):
    return LongCandidate(
        symbol="2330.TW",
        name="台積電",
        sector="semiconductor",
        last_price=100,
        change_pct=2,
        volume=2_000_000,
        turnover=200_000_000,
        avg_volume_20=1_000_000,
        daily_volume_ratio=2,
        intraday_volume=200_000,
        volume_ratio=volume_ratio,
        vwap=99,
        above_vwap=True,
        previous_high=98,
        high_5d=99,
        high_10d=99,
        break_prev_high=True,
        break_5d_high=True,
        break_10d_high=True,
        upper_shadow_pct=0,
        institutional_buy_million=None,
        margin_balance=None,
        short_balance=None,
        daytrade_ratio=None,
        sector_strength=3,
        news_topics=[],
        market_state="偏多",
        bullish_score=85,
        risk_score=20,
        grade=grade,
        entry_status="強勢做多觀察",
        trigger_price=99,
        stop_loss=97,
        target_price=102,
        opening_range_high=None,
        opening_range_low=None,
        reasons=["站上VWAP"],
        risk_reasons=[],
    )


def bar(index, high, low, close):
    return Bar(
        timestamp=datetime(2026, 1, 1, 9, 5) + timedelta(minutes=index * 5),
        open=100,
        high=high,
        low=low,
        close=close,
        volume=10_000,
    )


class DatabaseTests(unittest.TestCase):
    def test_saves_candidates_and_updates_backtest(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 5), [candidate()])
                update_backtests(
                    conn,
                    datetime(2026, 1, 1, 9, 20),
                    {"2330.TW": [bar(0, 101, 99, 100), bar(1, 103, 100, 102)]},
                )

                rows = latest_candidates(conn)
                summary = backtest_summary(conn)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grade"], "A")
        self.assertEqual(summary["target"], 1)

    def test_latest_candidates_downgrades_stale_a_grade_without_volume_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 5), [candidate(volume_ratio=0.6)])
                rows = latest_candidates(conn)

        self.assertEqual(rows[0]["grade"], "B")


if __name__ == "__main__":
    unittest.main()
