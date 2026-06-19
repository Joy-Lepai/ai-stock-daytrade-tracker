from datetime import datetime, timedelta
from pathlib import Path
import json
import tempfile
import unittest

from stock_daytrade_system.data import Bar
from stock_daytrade_system.performance import record_signal_performance
from stock_daytrade_system.tracker import TrackedSymbol


def tracked_symbol(entry_status="可進場", shares=1000):
    return TrackedSymbol(
        source="auto",
        symbol="2330.TW",
        name="台積電",
        sector="semiconductor",
        status="可執行",
        priority=0,
        bullish_label="方向偏多",
        bullish_score=8,
        bullish_reasons=["盤前做多"],
        entry_status=entry_status,
        cancel_conditions=["跌破VWAP 101.00"],
        last_price=102,
        day_change_pct=2,
        candidate_direction="做多觀察",
        candidate_score=7,
        opening_direction="偏多確認",
        opening_score=4,
        sector_state="強勢",
        trigger_price=101,
        stop_loss=99,
        target_price=105,
        risk_per_share=3,
        suggested_shares=shares,
        volume_ratio=1.4,
        vwap=101,
        vwap_state="站上VWAP",
        institutional_rank=None,
        institutional_buy_million=None,
        notes=[],
    )


def bar(index, high, low, close):
    return Bar(
        timestamp=datetime(2026, 1, 1, 9, 5) + timedelta(minutes=index * 5),
        open=102,
        high=high,
        low=low,
        close=close,
        volume=10_000,
    )


class SignalPerformanceTests(unittest.TestCase):
    def test_records_and_evaluates_target_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)

            summary = record_signal_performance(
                datetime(2026, 1, 1, 9, 5),
                [tracked_symbol()],
                {"2330.TW": [bar(0, 103, 101, 102.5), bar(1, 105.5, 102, 105)]},
                output_dir,
            )

            self.assertEqual(summary.total, 1)
            self.assertEqual(summary.target_count, 1)
            self.assertEqual(summary.stop_count, 0)
            records = json.loads((output_dir / "signal-performance.json").read_text(encoding="utf-8"))
            self.assertEqual(records[0]["outcome"], "達標")
            self.assertTrue((output_dir / "signal-performance.csv").exists())

    def test_skips_high_risk_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = record_signal_performance(
                datetime(2026, 1, 1, 9, 5),
                [tracked_symbol(entry_status="風險過高", shares=0)],
                {},
                Path(directory),
            )

            self.assertEqual(summary.total, 0)


if __name__ == "__main__":
    unittest.main()
