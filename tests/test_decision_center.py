import unittest

from stock_daytrade_system.decision_center import build_decision_center, build_paper_decision_summary


def candidate(symbol="2330.TW", grade="B+", entry_status="wait_vwap", **overrides):
    data = {
        "market": "TW",
        "symbol": symbol,
        "name": "台積電",
        "grade": grade,
        "entry_status": entry_status,
        "lifecycle_status": "observed",
        "last_price": 100,
        "vwap": 100.5,
        "volume_ratio": 0.9,
        "stop_loss": 98,
        "target_price": 104,
        "confidence_level": "medium",
        "confidence_level_label": "中等信心",
        "confidence_summary": "等待 VWAP 確認。",
        "risk_score": 20,
        "above_vwap": False,
        "conflicts_count": 1,
        "conflict_summary": "突破但未站上 VWAP",
    }
    data.update(overrides)
    return data


class DecisionCenterTests(unittest.TestCase):
    def test_executable_zero_generates_conservative_no_trade_reason(self):
        payload = build_decision_center(
            market="TW",
            market_session="regular",
            market_status="偏多",
            candidates=[candidate()],
            checklist={"executable": 0, "triggered": 0, "grade_b_plus": 0, "wait_vwap": 1},
        )

        self.assertIn(payload["operation_tendency"], {"保守觀望", "等待確認"})
        self.assertIn("今日沒有可執行做多標的", payload["no_trade_reason"])

    def test_b_plus_generates_waiting_confirmation(self):
        payload = build_decision_center(
            market="TW",
            market_session="regular",
            market_status="偏多",
            candidates=[candidate()],
            checklist={"executable": 0, "triggered": 0, "grade_b_plus": 1, "wait_vwap": 1},
        )

        self.assertEqual(payload["operation_tendency"], "等待確認")
        self.assertIn("B+ 練習觀察", payload["executable_summary"])
        self.assertIn("不代表正式做多建議", payload["no_trade_reason"])

    def test_many_high_risk_generates_avoid_chasing(self):
        payload = build_decision_center(
            market="US",
            market_session="regular",
            market_status="bullish",
            candidates=[
                candidate("NVDA", grade="C", entry_status="high_risk", market="US", name_zh="輝達"),
                candidate("TSLA", grade="C", entry_status="high_risk", market="US", name_zh="特斯拉"),
                candidate("AMD", grade="C", entry_status="high_risk", market="US", name_zh="超微半導體"),
            ],
            checklist={"high_risk": 3, "executable": 0, "grade_b_plus": 0},
        )

        self.assertEqual(payload["operation_tendency"], "避免追價")
        self.assertIn("風險分數偏高", payload["major_risks"])

    def test_bearish_market_generates_defensive_view(self):
        payload = build_decision_center(
            market="TW",
            market_session="regular",
            market_status="偏空",
            candidates=[candidate()],
            checklist={"executable": 0, "grade_b_plus": 1},
        )

        self.assertEqual(payload["operation_tendency"], "不適合交易")
        self.assertIn("等大盤轉強", payload["main_waiting_conditions"])

    def test_failed_data_source_generates_limited_judgment(self):
        payload = build_decision_center(
            market="US",
            market_session="regular",
            market_status="neutral",
            candidates=[],
            data_source_status={"ok": False, "failed_symbols": ["NVDA"]},
        )

        self.assertEqual(payload["operation_tendency"], "保守觀望")
        self.assertIn("目前資料不足", payload["action_suggestion"])
        self.assertEqual(payload["data_state"]["status"], "failed")

    def test_signal_center_classifies_buckets(self):
        payload = build_decision_center(
            market="TW",
            candidates=[
                candidate("2330.TW", grade="A", entry_status="executable", above_vwap=True),
                candidate("3711.TW", grade="B", entry_status="practice_long", above_vwap=True),
                candidate("2884.TW", grade="B+", entry_status="wait_vwap"),
                candidate("5880.TW", grade="B", entry_status="wait_volume"),
                candidate("1216.TW", grade="D", entry_status="avoid"),
            ],
        )

        center = payload["signal_center"]
        self.assertEqual(len(center["executable"]), 1)
        self.assertEqual(len(center["practice_long"]), 1)
        self.assertEqual(len(center["b_plus"]), 1)
        self.assertEqual(len(center["waiting"]), 1)
        self.assertEqual(len(center["risk"]), 1)

    def test_paper_decision_summary_handles_empty_and_b_plus_waiting(self):
        payload = build_paper_decision_summary(
            {
                "generated_at": "2026-06-13T10:00:00",
                "positions": [],
                "trades": [],
                "performance": {"manual_trades": 1, "system_trades": 0},
                "b_plus_triggers": [{"lifecycle_status": "observed"}],
            }
        )

        self.assertEqual(payload["manual_trades"], 1)
        self.assertEqual(payload["b_plus_waiting"], 1)
        self.assertTrue(payload["practice_available"])
        self.assertIn("目前無持倉", payload["summary_text"])


if __name__ == "__main__":
    unittest.main()
