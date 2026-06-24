import unittest

from stock_daytrade_system.entry_radar_summary import build_entry_radar_summary


def base_candidate(**overrides):
    payload = {
        "symbol": "2330.TW",
        "name": "台積電",
        "last_price": 101,
        "vwap": 100,
        "above_vwap": True,
        "volume_ratio": 1.1,
        "turnover": 100_000_000,
        "trigger_price": 102,
        "previous_high": 101.5,
        "break_prev_high": True,
        "stop_loss": 99,
        "risk_score": 35,
        "entry_status": "executable",
        "risk_reasons": [],
    }
    payload.update(overrides)
    return payload


class EntryRadarSummaryTests(unittest.TestCase):
    def test_data_problem_has_top_priority(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(above_vwap=False),
            data_health={"is_live": False, "uses_last_known": True, "price_status": "cached"},
            market_mode="intraday",
            intraday=True,
        ).to_dict()

        self.assertEqual(payload["entry_state"], "暫不進場")
        self.assertEqual(payload["blocker_code"], "cached_price")
        self.assertIn("使用上一筆", payload["blocker_summary"])

    def test_below_vwap_is_main_blocker_when_data_live(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(above_vwap=False),
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
        ).to_dict()

        self.assertEqual(payload["blocker_code"], "below_vwap")
        self.assertIn("VWAP", payload["blocker_summary"])
        self.assertIn("站上 VWAP", payload["next_trigger"])

    def test_low_volume_ratio_is_main_blocker(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(volume_ratio=0.72, entry_status="wait_volume"),
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
        ).to_dict()

        self.assertEqual(payload["entry_state"], "等待確認")
        self.assertEqual(payload["blocker_code"], "low_volume_ratio")
        self.assertIn("量比", payload["blocker_summary"])
        self.assertIn("1.0x", payload["next_trigger"])

    def test_high_risk_reports_chase_risk(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(entry_status="high_risk", risk_score=72, stop_loss=92),
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
        ).to_dict()

        self.assertEqual(payload["entry_state"], "暫不進場")
        self.assertEqual(payload["blocker_code"], "high_risk")
        self.assertIn("追價風險", payload["blocker_summary"])

    def test_missing_tick_is_confirmation_note_not_downgrade(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(entry_status="executable"),
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            entry_confirmation={"large_trade_status": "missing", "orderbook_status": "supportive"},
        ).to_dict()

        self.assertEqual(payload["entry_state"], "可進場觀察")
        self.assertEqual(payload["blocker_code"], "missing_tick")
        self.assertIn("缺逐筆成交", payload["blocker_summary"])

    def test_missing_orderbook_is_confirmation_note_not_downgrade(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(entry_status="executable"),
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            entry_confirmation={"large_trade_status": "buy_sweep", "orderbook_status": "missing"},
        ).to_dict()

        self.assertEqual(payload["entry_state"], "可進場觀察")
        self.assertEqual(payload["blocker_code"], "missing_orderbook")
        self.assertIn("缺五檔", payload["blocker_summary"])

    def test_multiple_confirmation_gaps_are_summarized_without_downgrade(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(entry_status="executable"),
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            entry_confirmation={"large_trade_status": "missing", "orderbook_status": "missing"},
        ).to_dict()

        self.assertEqual(payload["entry_state"], "可進場觀察")
        self.assertEqual(payload["blocker_code"], "missing_tick")
        self.assertIn("缺逐筆成交", payload["blocker_summary"])
        self.assertIn("缺五檔委買委賣", payload["blocker_summary"])
        self.assertIn("缺逐筆成交", payload["confirmation_note"])
        self.assertIn("缺五檔委買委賣", payload["confirmation_note"])

    def test_closed_review_does_not_show_intraday_entry(self):
        payload = build_entry_radar_summary(
            candidate=base_candidate(entry_status="executable"),
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            market_mode="closed_review",
            intraday=False,
        ).to_dict()

        self.assertEqual(payload["entry_state"], "暫不進場")
        self.assertEqual(payload["blocker_code"], "not_intraday")


if __name__ == "__main__":
    unittest.main()
