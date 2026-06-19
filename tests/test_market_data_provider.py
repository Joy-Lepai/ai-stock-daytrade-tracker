import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from stock_daytrade_system.data import Bar
from stock_daytrade_system.market_data_provider import (
    MarketDataProviderManager,
    ProviderStatus,
    ProviderUnavailable,
)
from stock_daytrade_system.resilience import GLOBAL_HEALTH, health_status_compact


@dataclass
class FakeProvider:
    name: str = "fake"
    health_key: str = "fake"
    configured: bool = True
    error: Optional[Exception] = None

    def status(self, *, role: str) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            enabled=self.configured,
            configured=self.configured,
            role=role,
            mode="test",
            websocket_status="test",
            health_key=self.health_key,
            message="test provider",
        )

    def fetch_many_daily_with_errors(self, symbols, range_="6mo"):
        if self.error:
            raise self.error
        return ({symbol: [Bar(datetime(2026, 6, 19), 100, 101, 99, 100, 1000)] for symbol in symbols}, {})

    def fetch_many_intraday_with_errors(self, symbols, range_="1d", interval="5m", include_prepost=False):
        if self.error:
            raise self.error
        return ({symbol: [Bar(datetime(2026, 6, 19, 9, 0), 100, 101, 99, 100, 1000)] for symbol in symbols}, {})


class MarketDataProviderTests(unittest.TestCase):
    def tearDown(self):
        GLOBAL_HEALTH.reset()

    def test_default_status_uses_yahoo_fallback_provider(self):
        manager = MarketDataProviderManager()

        payload = manager.status_payload()

        self.assertEqual(payload["active_provider"], "yahoo")
        self.assertEqual(payload["fallback_provider"], "yahoo")
        self.assertIn("providers", payload)

    def test_unconfigured_primary_falls_back_without_breaking_fetch(self):
        manager = MarketDataProviderManager(primary="fugle")
        manager.providers["yahoo"] = FakeProvider(name="yahoo", health_key="yahoo_chart")

        data, errors = manager.fetch_many_daily_with_errors(["2330.TW"])

        self.assertIn("2330.TW", data)
        self.assertEqual(errors, {})
        self.assertEqual(health_status_compact().get("fugle"), "PARTIAL")

    def test_unavailable_primary_records_partial_and_uses_fallback(self):
        manager = MarketDataProviderManager(primary="fugle")
        manager.providers["fugle"] = FakeProvider(
            name="fugle",
            health_key="fugle",
            error=ProviderUnavailable("adapter pending"),
        )
        manager.providers["yahoo"] = FakeProvider(name="yahoo", health_key="yahoo_chart")

        data, errors = manager.fetch_many_intraday_with_errors(["2884.TW"])

        self.assertIn("2884.TW", data)
        self.assertEqual(errors, {})
        self.assertEqual(health_status_compact().get("fugle"), "PARTIAL")


if __name__ == "__main__":
    unittest.main()
