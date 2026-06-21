import unittest
from datetime import datetime, timedelta

from stock_daytrade_system.breakout_trap_diagnosis import build_breakout_trap_diagnosis
from stock_daytrade_system.data import Bar


def bar(index, close, high=None, low=None):
    return Bar(
        timestamp=datetime(2026, 6, 18, 9, 30) + timedelta(minutes=index),
        open=close - 0.2,
        high=high if high is not None else close + 0.3,
        low=low if low is not None else close - 0.3,
        close=close,
        volume=1000,
    )


class BreakoutTrapDiagnosisTests(unittest.TestCase):
    def test_true_breakout_requires_vwap_volume_and_no_sell_pressure(self):
        payload = build_breakout_trap_diagnosis(
            candidate={
                "last_price": 105,
                "vwap": 103,
                "above_vwap": True,
                "previous_high": 104,
                "break_prev_high": True,
                "volume_ratio": 1.2,
                "risk_score": 35,
            },
            entry_confirmation={
                "orderbook_status": "supportive",
                "large_trade_status": "buy_sweep",
                "price_tick_trend": "rising",
            },
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            market_mode="intraday",
            intraday=True,
        ).to_dict()

        self.assertEqual(payload["status"], "true_breakout")
        self.assertEqual(payload["status_label"], "真突破")
        self.assertIn("股價站上 VWAP", payload["evidence"])

    def test_false_breakout_when_price_falls_back_below_breakout_level(self):
        payload = build_breakout_trap_diagnosis(
            candidate={
                "last_price": 103.5,
                "vwap": 102,
                "above_vwap": True,
                "previous_high": 104,
                "trigger_price": 104,
                "volume_ratio": 1.3,
                "risk_score": 45,
            },
            intraday_bars=[bar(0, 103), bar(1, 104.5, high=104.8), bar(2, 103.5)],
            entry_confirmation={"price_tick_trend": "weak"},
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            market_mode="intraday",
            intraday=True,
        ).to_dict()

        self.assertEqual(payload["status"], "false_breakout_risk")
        self.assertEqual(payload["status_label"], "假突破風險")
        self.assertIn("突破後跌回關鍵價下方", payload["warnings"])

    def test_fake_breakdown_reclaim_when_price_recovers_vwap(self):
        payload = build_breakout_trap_diagnosis(
            candidate={
                "last_price": 101,
                "vwap": 100,
                "above_vwap": True,
                "volume_ratio": 1.0,
                "risk_score": 40,
            },
            intraday_bars=[bar(0, 99.5, low=99.2), bar(1, 100.2, low=99.8), bar(2, 101, low=100.3)],
            entry_confirmation={"price_tick_trend": "stable", "orderbook_status": "neutral"},
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            market_mode="intraday",
            intraday=True,
        ).to_dict()

        self.assertEqual(payload["status"], "fake_breakdown_reclaim")
        self.assertEqual(payload["status_label"], "假跌破後站回")

    def test_breakdown_weak_when_below_vwap_with_sell_pressure(self):
        payload = build_breakout_trap_diagnosis(
            candidate={"last_price": 98, "vwap": 100, "above_vwap": False, "volume_ratio": 1.1, "risk_score": 50},
            entry_confirmation={"orderbook_status": "sell_pressure", "large_trade_status": "sell_sweep", "price_tick_trend": "weak"},
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            market_mode="intraday",
            intraday=True,
        ).to_dict()

        self.assertEqual(payload["status"], "breakdown_weak")
        self.assertEqual(payload["status_label"], "跌破轉弱")

    def test_high_risk_breakout_is_bull_trap_risk_not_true_breakout(self):
        payload = build_breakout_trap_diagnosis(
            candidate={
                "last_price": 110,
                "vwap": 104,
                "above_vwap": True,
                "previous_high": 106,
                "break_prev_high": True,
                "volume_ratio": 3.0,
                "risk_score": 75,
                "entry_status": "high_risk",
                "upper_shadow_pct": 2.5,
            },
            entry_confirmation={"orderbook_status": "supportive", "price_tick_trend": "stable"},
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            market_mode="intraday",
            intraday=True,
        ).to_dict()

        self.assertEqual(payload["status"], "bull_trap_risk")
        self.assertEqual(payload["status_label"], "誘多風險")
        self.assertIn("追價風險高", payload["summary"])

    def test_non_intraday_is_review_only(self):
        payload = build_breakout_trap_diagnosis(
            candidate={"last_price": 105, "vwap": 103, "above_vwap": True},
            data_health={"is_live": False},
            market_mode="closed_review",
            intraday=False,
        ).to_dict()

        self.assertEqual(payload["status"], "review_only")
        self.assertEqual(payload["status_label"], "復盤觀察")


if __name__ == "__main__":
    unittest.main()
