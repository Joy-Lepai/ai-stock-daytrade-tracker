import unittest
from datetime import datetime, timedelta

from stock_daytrade_system.data import Bar
from stock_daytrade_system.timeframe_diagnostics import (
    build_timeframe_gap_report,
    build_timeframe_windows,
    classify_trend_continuation,
    trend_continuation_validation,
)


def daily_bar(index, close, high=None, low=None, volume=1_000_000):
    return Bar(
        timestamp=datetime(2026, 6, 1) + timedelta(days=index),
        open=close - 0.5,
        high=high if high is not None else close + 1,
        low=low if low is not None else close - 1,
        close=close,
        volume=volume,
    )


def intraday_bar(index, open_price, close, high=None, low=None, volume=100_000):
    return Bar(
        timestamp=datetime(2026, 6, 18, 9, 0) + timedelta(minutes=index * 5),
        open=open_price,
        high=high if high is not None else max(open_price, close) + 0.1,
        low=low if low is not None else min(open_price, close) - 0.1,
        close=close,
        volume=volume,
    )


class TimeframeDiagnosticsTests(unittest.TestCase):
    def test_gap_report_describes_three_windows(self):
        report = build_timeframe_gap_report()

        self.assertIn("intraday", report["current_inputs"])
        self.assertIn("short_term", report["current_inputs"])
        self.assertIn("context", report["current_inputs"])
        self.assertIn("單一時間點", "；".join(report["known_gaps"]))

    def test_trend_continuation_watch_when_curve_is_healthy(self):
        daily = [daily_bar(index, 90 + index * 0.2) for index in range(65)]
        intraday = [
            intraday_bar(0, 100.0, 100.4, volume=120_000),
            intraday_bar(1, 100.4, 100.8, volume=130_000),
            intraday_bar(2, 100.7, 101.1, volume=140_000),
            intraday_bar(3, 101.0, 101.4, volume=150_000),
            intraday_bar(4, 101.3, 101.7, volume=150_000),
            intraday_bar(5, 101.6, 102.0, volume=145_000),
        ]
        windows = build_timeframe_windows(
            daily,
            intraday,
            last_price=102,
            vwap=100.8,
            previous_high=101,
            high_5d=101.5,
            high_10d=101.6,
            volume_ratio=1.2,
        )

        diagnosis = classify_trend_continuation(
            grade="B+",
            entry_status="practice_long",
            last_price=102,
            vwap=100.8,
            volume_ratio=1.2,
            risk_score=42,
            bullish_score=78,
            change_pct=3.2,
            stop_loss=99.8,
            target_price=105,
            upper_shadow_pct=0.2,
            break_prev_high=True,
            break_5d_high=True,
            market_state="偏多",
            timeframe_diagnostics=windows,
        )

        self.assertEqual(diagnosis["status"], "trend_continuation_watch")
        self.assertEqual(diagnosis["label"], "做多｜趨勢延續觀察")
        self.assertTrue(windows["intraday_window"]["higher_high"])
        self.assertTrue(windows["intraday_window"]["higher_low"])
        self.assertTrue(windows["intraday_window"]["vwap_stay_ok"])

    def test_high_risk_chase_when_upper_shadow_and_volume_decay(self):
        daily = [daily_bar(index, 90 + index * 0.2) for index in range(65)]
        intraday = [
            intraday_bar(0, 100, 101, volume=300_000),
            intraday_bar(1, 101, 102, volume=280_000),
            intraday_bar(2, 102, 103, volume=260_000),
            intraday_bar(3, 103, 103.2, high=105.5, low=102.8, volume=30_000),
            intraday_bar(4, 103.2, 103.1, high=105.2, low=102.7, volume=25_000),
            intraday_bar(5, 103.1, 103.0, high=105.0, low=102.6, volume=20_000),
        ]
        windows = build_timeframe_windows(
            daily,
            intraday,
            last_price=103,
            vwap=100.5,
            previous_high=101,
            high_5d=101.5,
            high_10d=102,
            volume_ratio=3.5,
        )

        diagnosis = classify_trend_continuation(
            grade="C",
            entry_status="high_risk",
            last_price=103,
            vwap=100.5,
            volume_ratio=3.5,
            risk_score=70,
            bullish_score=85,
            change_pct=8,
            stop_loss=96,
            target_price=106,
            upper_shadow_pct=2,
            break_prev_high=True,
            break_5d_high=True,
            market_state="偏多",
            timeframe_diagnostics=windows,
        )

        self.assertEqual(diagnosis["status"], "high_risk_chase")
        self.assertIn("追價風險", diagnosis["label"])
        self.assertTrue(windows["intraday_window"]["volume_decay"])

    def test_validation_reports_insufficient_sample(self):
        payload = trend_continuation_validation([])

        self.assertEqual(payload["sample_size"], 0)
        self.assertFalse(payload["is_statistically_meaningful"])
        self.assertIn("樣本不足", payload["message"])


if __name__ == "__main__":
    unittest.main()
