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

    def test_yahoo_404_diagnostics_are_separated_from_hard_failures(self):
        now = datetime(2026, 6, 18, 9, 22, tzinfo=ZoneInfo("Asia/Taipei"))
        fresh = now - timedelta(seconds=30)
        symbols = [
            WatchSymbol("2330.TW", "台積電", "semiconductor"),
            WatchSymbol("6485.TW", "點序", "semiconductor"),
            WatchSymbol("TX=F", "台指期代理", "index"),
        ]

        payload = build_tw_diagnostics(
            DiagnosticInputs(
                now=now,
                all_symbols=symbols,
                intraday_symbols=[item.symbol for item in symbols],
                daily_data={},
                intraday_data={"2330.TW": [Bar(fresh, 100, 101, 99, 100, 1000)]},
                daily_errors={},
                intraday_errors={
                    "6485.TW": "symbol_not_found:6485.TW: HTTP 404 Not Found",
                    "TX=F": "yahoo_proxy_unavailable:TX=F: HTTP 404 Not Found",
                },
                taifex_errors={},
                cmoney_errors={},
                market_session="regular",
                market_status="偏多",
                momentum_scan={"items": []},
                candidates=[],
            )
        )

        health = payload["data_health"]
        self.assertEqual(health["symbol_not_found_count"], 1)
        self.assertEqual(health["yahoo_proxy_unavailable_count"], 1)
        self.assertIn("6485.TW", health["symbol_not_found_symbols"])
        self.assertIn("TX=F", health["yahoo_proxy_unavailable_symbols"])
        self.assertIn("不影響官方資料源", health["unavailable_symbols_message"])

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

    def test_limit_up_strength_analysis_separates_seen_high_risk_from_missed(self):
        now = datetime(2026, 6, 30, 9, 35, tzinfo=ZoneInfo("Asia/Taipei"))
        payload = build_tw_diagnostics(
            DiagnosticInputs(
                now=now,
                all_symbols=[WatchSymbol("8150.TW", "南茂", "semiconductor")],
                intraday_symbols=["8150.TW"],
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
                            "symbol": "8150.TW",
                            "name": "南茂",
                            "change_pct": 9.8,
                            "latest_price": 58.5,
                            "volume_ratio": 4.2,
                            "above_vwap": True,
                            "break_prev_high": True,
                            "ai_grade": "C",
                            "entry_status": "high_risk",
                            "not_selected_reason": "強勢但追價風險高，不列入今日做多。",
                        }
                    ]
                },
                candidates=[],
            )
        )

        limit_up = payload["limit_up_strength_analysis"]
        self.assertEqual(limit_up["near_limit_up_count"], 1)
        self.assertEqual(limit_up["seen_count"], 1)
        self.assertEqual(limit_up["high_risk_count"], 1)
        self.assertEqual(limit_up["missed_by_pool_count"], 0)
        self.assertEqual(limit_up["chase_risk_count"], 1)
        self.assertIn("追價風險高", limit_up["action_summary"])
        self.assertEqual(limit_up["rows"][0]["limit_up_decision"], "有看到，但追價風險高")
        self.assertIn("不列入今日做多", limit_up["rows"][0]["limit_up_explanation"])
        self.assertEqual(limit_up["rows"][0]["limit_up_action_type"], "chase_risk")
        self.assertIn("不直接追漲停", limit_up["rows"][0]["limit_up_now_action"])
        self.assertIn("拉回 VWAP", limit_up["rows"][0]["limit_up_wait_for"])


if __name__ == "__main__":
    unittest.main()
