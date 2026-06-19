import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.tw_diagnostics import DiagnosticInputs, build_tw_diagnostics


class TWDiagnosticsTests(unittest.TestCase):
    def test_data_health_marks_stale_intraday_data(self):
        now = datetime(2026, 6, 18, 9, 22, tzinfo=ZoneInfo("Asia/Taipei"))
        old = now - timedelta(minutes=20)

        payload = build_tw_diagnostics(
            DiagnosticInputs(
                now=now,
                all_symbols=[WatchSymbol("2330.TW", "台積電", "semiconductor")],
                intraday_symbols=["2330.TW"],
                daily_data={},
                intraday_data={"2330.TW": [Bar(old, 100, 101, 99, 100, 1000)]},
                daily_errors={},
                intraday_errors={},
                taifex_errors={},
                cmoney_errors={},
                market_session="regular",
                market_status="偏多",
                momentum_scan={"items": []},
                candidates=[],
            )
        )

        self.assertEqual(payload["data_health"]["status"], "過期")
        self.assertIn("暫停產生當沖建議", payload["data_health"]["recommendation_state"])
        self.assertEqual(payload["data_health"]["missing_count"], 0)
        self.assertEqual(payload["data_health"]["delayed_count"], 1)
        self.assertIn("timeframe_gap_report", payload)
        self.assertIn("trend_continuation_report", payload)

    def test_single_missing_symbol_does_not_mark_whole_dashboard_abnormal(self):
        now = datetime(2026, 6, 18, 9, 22, tzinfo=ZoneInfo("Asia/Taipei"))
        fresh = now - timedelta(seconds=30)
        symbols = [
            WatchSymbol("2330.TW", "台積電", "semiconductor"),
            WatchSymbol("2317.TW", "鴻海", "electronics"),
            WatchSymbol("1301.TW", "台塑", "plastics"),
        ]

        payload = build_tw_diagnostics(
            DiagnosticInputs(
                now=now,
                all_symbols=symbols,
                intraday_symbols=[item.symbol for item in symbols],
                daily_data={},
                intraday_data={
                    "2330.TW": [Bar(fresh, 100, 101, 99, 100, 1000)],
                    "2317.TW": [Bar(fresh, 100, 101, 99, 100, 1000)],
                },
                daily_errors={},
                intraday_errors={"1301.TW": "api failed"},
                taifex_errors={},
                cmoney_errors={},
                market_session="regular",
                market_status="偏多",
                momentum_scan={"items": []},
                candidates=[],
            )
        )

        health = payload["data_health"]
        self.assertEqual(health["status"], "部分缺漏")
        self.assertEqual(health["live_count"], 2)
        self.assertEqual(health["missing_count"], 1)
        self.assertLess(health["missing_ratio"], 35)

    def test_missed_stock_analysis_has_reason_code(self):
        now = datetime(2026, 6, 18, 9, 22, tzinfo=ZoneInfo("Asia/Taipei"))
        payload = build_tw_diagnostics(
            DiagnosticInputs(
                now=now,
                all_symbols=[WatchSymbol("6770.TW", "力積電", "semiconductor")],
                intraday_symbols=["6770.TW"],
                daily_data={},
                intraday_data={},
                daily_errors={},
                intraday_errors={},
                taifex_errors={},
                cmoney_errors={},
                market_session="regular",
                market_status="偏多",
                momentum_scan={
                    "items": [
                        {
                            "symbol": "6770.TW",
                            "name": "力積電",
                            "change_pct": 4.5,
                            "latest_price": 18.5,
                            "volume_ratio": 1.2,
                            "above_vwap": False,
                            "break_prev_high": True,
                            "break_5d_high": True,
                            "ai_grade": "D",
                            "entry_status": "wait_vwap",
                            "not_selected_reason": "未站上 VWAP",
                        }
                    ]
                },
                candidates=[],
            )
        )

        missed = payload["missed_stock_analysis"]
        self.assertEqual(missed["missed_count"], 0)
        self.assertEqual(missed["seen_but_filtered_count"], 1)
        self.assertEqual(missed["seen_but_filtered"]["by_status"]["wait_vwap"], 1)
        self.assertEqual(missed["rows"][0]["reason_code"], "below_vwap")
        self.assertEqual(missed["rows"][0]["diagnostic_bucket"], "seen_but_filtered")


if __name__ == "__main__":
    unittest.main()
