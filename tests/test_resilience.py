from datetime import datetime
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

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

    def test_retry_sync_records_non_retryable_as_partial(self):
        def invalid_symbol():
            raise RuntimeError("symbol_not_found")

        with patch("stock_daytrade_system.resilience.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                retry_sync(
                    invalid_symbol,
                    source="yahoo_chart",
                    operation_name="Yahoo chart 6485.TW 5m",
                    should_retry=lambda exc: False,
                )

        health = health_status_snapshot()["yahoo_chart"]
        self.assertEqual(health["status"], "PARTIAL")
        self.assertEqual(health["retry_count"], 0)
        self.assertEqual(sleep.call_count, 0)

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

    def test_yahoo_404_is_symbol_not_found_without_retry(self):
        client = YahooChartClient(retries=3)

        def raise_404(*args, **kwargs):
            raise HTTPError("https://example.test", 404, "Not Found", hdrs=None, fp=None)

        with patch("urllib.request.urlopen", side_effect=raise_404) as urlopen:
            with patch("stock_daytrade_system.resilience.time.sleep") as sleep:
                data, errors = client.fetch_many_intraday_with_errors(["6485.TW"], interval="5m")

        self.assertEqual(data["6485.TW"], [])
        self.assertTrue(errors["6485.TW"].startswith("symbol_not_found"))
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(sleep.call_count, 0)

    def test_yahoo_futures_proxy_404_is_classified_without_retry(self):
        client = YahooChartClient(retries=3)

        def raise_404(*args, **kwargs):
            raise HTTPError("https://example.test", 404, "Not Found", hdrs=None, fp=None)

        with patch("urllib.request.urlopen", side_effect=raise_404) as urlopen:
            with patch("stock_daytrade_system.resilience.time.sleep") as sleep:
                data, errors = client.fetch_many_daily_with_errors(["TX=F"])

        self.assertEqual(data["TX=F"], [])
        self.assertTrue(errors["TX=F"].startswith("yahoo_proxy_unavailable"))
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(sleep.call_count, 0)


if __name__ == "__main__":
    unittest.main()
