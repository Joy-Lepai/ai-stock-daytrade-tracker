from datetime import datetime
import unittest
from unittest.mock import patch

from stock_daytrade_system.data import Bar, MarketDataError, YahooChartClient
from stock_daytrade_system.resilience import GLOBAL_HEALTH, health_status_snapshot, retry_sync


class ResilienceTests(unittest.TestCase):
    def tearDown(self):
        GLOBAL_HEALTH.reset()

    def test_retry_sync_uses_exponential_backoff_and_recovers(self):
        calls = {"count": 0}

        def flaky():
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("temporary outage")
            return "ok"

        with patch("stock_daytrade_system.resilience.time.sleep") as sleep:
            result = retry_sync(flaky, source="twse", operation_name="test fetch")

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_retry_sync_records_error_after_all_retries_fail(self):
        def broken():
            raise RuntimeError("down")

        with patch("stock_daytrade_system.resilience.time.sleep"):
            with self.assertRaises(RuntimeError):
                retry_sync(broken, source="c_money", operation_name="test fetch")

        health = health_status_snapshot()["c_money"]
        self.assertEqual(health["status"], "ERROR")
        self.assertIn("down", health["last_error"])
        self.assertEqual(health["retry_count"], 3)

    def test_yahoo_batch_degrades_single_symbol_failure(self):
        class FakeClient(YahooChartClient):
            def _fetch_chart(self, symbol, range_, interval, include_prepost):
                if symbol == "3260.TW":
                    raise MarketDataError("3260.TW failed")
                return [Bar(datetime(2026, 1, 1), 1, 2, 1, 2, 1000)]

        data, errors = FakeClient().fetch_many_intraday_with_errors(["2330.TW", "3260.TW"], interval="5m")

        self.assertEqual(len(data["2330.TW"]), 1)
        self.assertEqual(data["3260.TW"], [])
        self.assertIn("3260.TW", errors)
        self.assertEqual(health_status_snapshot()["yahoo_chart"]["status"], "PARTIAL")


if __name__ == "__main__":
    unittest.main()
