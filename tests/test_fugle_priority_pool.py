import unittest
from types import SimpleNamespace

from stock_daytrade_system.fugle_priority_pool import build_fugle_priority_pool


def item(symbol, entry_status, grade="B", bullish=70, risk=35, confidence=65, volume_ratio=1.0):
    return SimpleNamespace(
        symbol=symbol,
        name=symbol,
        grade=grade,
        entry_status=entry_status,
        trade_bias="long" if entry_status in {"executable", "practice_long"} else "watch",
        last_price=100,
        vwap=99.8,
        volume_ratio=volume_ratio,
        trigger_price=100.3,
        stop_loss=98.8,
        risk_score=risk,
        bullish_score=bullish,
        confidence_score=confidence,
    )


class FuglePriorityPoolTests(unittest.TestCase):
    def test_selects_top_five_without_changing_model(self):
        payload = build_fugle_priority_pool(
            [
                item("2330.TW", "wait_volume"),
                item("2317.TW", "executable", grade="A", bullish=85, risk=25),
                item("2884.TW", "practice_long", grade="B+"),
                item("3711.TW", "wait_breakout"),
                item("6919.TW", "high_risk", grade="C", risk=72, volume_ratio=5),
                item("1101.TW", "wait_vwap"),
                item("9999.TW", "avoid", grade="D"),
            ],
            b_plus_triggers=[{"symbol": "2884.TW", "trigger_readiness": "near"}],
            pinned_symbols=["6919.tw"],
            max_symbols=5,
            enabled=True,
            configured=True,
        )

        selected = payload["selected"]
        symbols = [row["symbol"] for row in selected]
        self.assertEqual(payload["mode"], "basic_user_5_symbols")
        self.assertEqual(payload["max_symbols"], 5)
        self.assertEqual(payload["considered_count"], 6)
        self.assertEqual(len(selected), 5)
        self.assertEqual(len(payload["standby"]), 1)
        self.assertEqual(payload["standby"][0]["symbol"], "2330.TW")
        self.assertIn("名額 5 檔已滿", payload["standby"][0]["not_selected_reason"])
        self.assertEqual(symbols[0], "6919.TW")
        self.assertIn("使用者指定即時追蹤", selected[0]["priority_reason"])
        self.assertFalse(selected[0]["can_use_for_entry_confirmation"])
        self.assertIn("2884.TW", symbols)
        self.assertNotIn("9999.TW", symbols)
        self.assertTrue(next(row for row in selected if row["symbol"] == "2317.TW")["can_use_for_entry_confirmation"])
        self.assertIn("不會改 A / B+ / B 條件", " ".join(payload["selection_policy"]))
        self.assertEqual(payload["pinned_symbols"], ["6919.TW"])

    def test_empty_pool_is_safe(self):
        payload = build_fugle_priority_pool([], max_symbols=5, enabled=False, configured=False)

        self.assertEqual(payload["selected"], [])
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["configured"])
        self.assertIn("沒有需要", payload["message"])


if __name__ == "__main__":
    unittest.main()
