from datetime import datetime, timedelta
from pathlib import Path
import json
import tempfile
import unittest

from stock_daytrade_system.data import Bar
from stock_daytrade_system.paper_trading import update_paper_trades
from stock_daytrade_system.tracker import TrackedSymbol


def tracked_symbol(entry_status="可進場"):
    return TrackedSymbol(
        source="auto",
        symbol="1605.TW",
        name="華新",
        sector="institutional_buy",
        status="等待確認",
        priority=2,
        bullish_label="強烈看漲",
        bullish_score=8.5,
        bullish_reasons=["盤前做多"],
        entry_status=entry_status,
        cancel_conditions=["跌破VWAP 37.10"],
        last_price=37.7,
        day_change_pct=9.9,
        candidate_direction="做多觀察",
        candidate_score=7,
        opening_direction="觀望",
        opening_score=2,
        sector_state="強勢",
        trigger_price=34.6,
        stop_loss=36.5,
        target_price=38.2,
        risk_per_share=1.2,
        suggested_shares=1000,
        volume_ratio=1.4,
        vwap=37.1,
        vwap_state="站上VWAP",
        institutional_rank=1,
        institutional_buy_million=100,
        notes=[],
    )


def bar(index, high, low, close):
    return Bar(
        timestamp=datetime(2026, 1, 5, 9, 10) + timedelta(minutes=index * 5),
        open=37.7,
        high=high,
        low=low,
        close=close,
        volume=10000,
    )


class PaperTradingTests(unittest.TestCase):
    def test_opens_and_closes_target_hit_during_market_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            summary = update_paper_trades(
                datetime(2026, 1, 5, 9, 10),
                [tracked_symbol()],
                {"1605.TW": [bar(0, 38.3, 37.5, 38.2)]},
                output_dir,
            )

            self.assertEqual(summary.closed_count, 1)
            self.assertEqual(summary.win_count, 1)
            self.assertGreater(summary.realized_pnl, 0)
            trades = json.loads((output_dir / "paper-trades.json").read_text(encoding="utf-8"))
            self.assertEqual(trades[0]["exit_reason"], "達標")
            self.assertTrue((output_dir / "paper-trades.csv").exists())

    def test_does_not_open_new_trade_after_market_entry_window(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = update_paper_trades(
                datetime(2026, 1, 5, 18, 0),
                [tracked_symbol()],
                {"1605.TW": [bar(0, 38.3, 37.5, 38.2)]},
                Path(directory),
            )

            self.assertEqual(summary.open_count, 0)
            self.assertEqual(summary.closed_count, 0)


if __name__ == "__main__":
    unittest.main()
