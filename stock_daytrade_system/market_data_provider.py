from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Iterable, Protocol

from stock_daytrade_system.data import Bar, YahooChartClient
from stock_daytrade_system.resilience import record_source_health


PROVIDER_VERSION = "market_data_provider_v1_skeleton_2026-06-19"


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    enabled: bool
    configured: bool
    role: str
    mode: str
    websocket_status: str
    health_key: str
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class MarketDataProvider(Protocol):
    name: str
    health_key: str

    def status(self, *, role: str) -> ProviderStatus:
        ...

    def fetch_many_daily_with_errors(self, symbols: Iterable[str], range_: str = "6mo") -> tuple[dict[str, list[Bar]], dict[str, str]]:
        ...

    def fetch_many_intraday_with_errors(
        self,
        symbols: Iterable[str],
        range_: str = "1d",
        interval: str = "5m",
        include_prepost: bool = False,
    ) -> tuple[dict[str, list[Bar]], dict[str, str]]:
        ...


class YahooMarketDataProvider:
    name = "yahoo"
    health_key = "yahoo_chart"

    def __init__(self, client: YahooChartClient | None = None) -> None:
        self.client = client or YahooChartClient()

    def status(self, *, role: str) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            enabled=True,
            configured=True,
            role=role,
            mode="rest_polling",
            websocket_status="not_supported",
            health_key=self.health_key,
            message="Yahoo chart endpoint 作為目前穩定 fallback；可能延遲，不是逐筆 Tick。",
        )

    def fetch_many_daily_with_errors(self, symbols: Iterable[str], range_: str = "6mo") -> tuple[dict[str, list[Bar]], dict[str, str]]:
        return self.client.fetch_many_daily_with_errors(symbols, range_=range_)

    def fetch_many_intraday_with_errors(
        self,
        symbols: Iterable[str],
        range_: str = "1d",
        interval: str = "5m",
        include_prepost: bool = False,
    ) -> tuple[dict[str, list[Bar]], dict[str, str]]:
        return self.client.fetch_many_intraday_with_errors(
            symbols,
            range_=range_,
            interval=interval,
            include_prepost=include_prepost,
        )


class FugleMarketDataProvider:
    name = "fugle"
    health_key = "fugle"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("FUGLE_API_KEY", "")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def status(self, *, role: str) -> ProviderStatus:
        configured = self.configured
        return ProviderStatus(
            name=self.name,
            enabled=False,
            configured=configured,
            role=role,
            mode="websocket_skeleton",
            websocket_status="not_connected",
            health_key=self.health_key,
            message=(
                "Fugle API key 已設定，但 WebSocket adapter 尚未啟用；目前會自動 fallback。"
                if configured
                else "Fugle API key 未設定；目前未啟用。"
            ),
        )

    def fetch_many_daily_with_errors(self, symbols: Iterable[str], range_: str = "6mo") -> tuple[dict[str, list[Bar]], dict[str, str]]:
        raise ProviderUnavailable("Fugle provider skeleton 尚未實作 REST/日線 adapter")

    def fetch_many_intraday_with_errors(
        self,
        symbols: Iterable[str],
        range_: str = "1d",
        interval: str = "5m",
        include_prepost: bool = False,
    ) -> tuple[dict[str, list[Bar]], dict[str, str]]:
        raise ProviderUnavailable("Fugle provider skeleton 尚未連線 WebSocket/Tick adapter")


class ShioajiMarketDataProvider:
    name = "shioaji"
    health_key = "shioaji"

    def __init__(self, api_key: str | None = None, secret_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SHIOAJI_API_KEY", "")
        self.secret_key = secret_key or os.getenv("SHIOAJI_SECRET_KEY", "")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def status(self, *, role: str) -> ProviderStatus:
        configured = self.configured
        return ProviderStatus(
            name=self.name,
            enabled=False,
            configured=configured,
            role=role,
            mode="websocket_skeleton",
            websocket_status="not_connected",
            health_key=self.health_key,
            message=(
                "Shioaji API key/secret 已設定，但行情 adapter 尚未啟用；目前會自動 fallback。"
                if configured
                else "Shioaji API key/secret 未設定；目前未啟用。"
            ),
        )

    def fetch_many_daily_with_errors(self, symbols: Iterable[str], range_: str = "6mo") -> tuple[dict[str, list[Bar]], dict[str, str]]:
        raise ProviderUnavailable("Shioaji provider skeleton 尚未實作日線 adapter")

    def fetch_many_intraday_with_errors(
        self,
        symbols: Iterable[str],
        range_: str = "1d",
        interval: str = "5m",
        include_prepost: bool = False,
    ) -> tuple[dict[str, list[Bar]], dict[str, str]]:
        raise ProviderUnavailable("Shioaji provider skeleton 尚未連線行情 stream adapter")


class MarketDataProviderManager:
    def __init__(self, primary: str | None = None, fallback: str = "yahoo") -> None:
        self.primary_name = (primary or os.getenv("STOCK_MARKET_DATA_PROVIDER") or "yahoo").strip().lower()
        self.fallback_name = (os.getenv("STOCK_MARKET_DATA_FALLBACK_PROVIDER") or fallback).strip().lower()
        self.providers: dict[str, MarketDataProvider] = {
            "yahoo": YahooMarketDataProvider(),
            "fugle": FugleMarketDataProvider(),
            "shioaji": ShioajiMarketDataProvider(),
        }

    def status_payload(self) -> dict:
        primary = self._provider(self.primary_name)
        fallback = self._fallback_provider()
        active = primary if self._provider_ready(primary) else fallback
        return {
            "version": PROVIDER_VERSION,
            "primary_provider": self.primary_name,
            "fallback_provider": fallback.name,
            "active_provider": active.name,
            "providers": [
                provider.status(role="primary" if name == self.primary_name else "fallback" if name == fallback.name else "available").to_dict()
                for name, provider in sorted(self.providers.items())
            ],
        }

    def fetch_many_daily_with_errors(self, symbols: Iterable[str], range_: str = "6mo") -> tuple[dict[str, list[Bar]], dict[str, str]]:
        return self._with_fallback(
            lambda provider: provider.fetch_many_daily_with_errors(symbols, range_=range_),
            operation="daily",
        )

    def fetch_many_intraday_with_errors(
        self,
        symbols: Iterable[str],
        range_: str = "1d",
        interval: str = "5m",
        include_prepost: bool = False,
    ) -> tuple[dict[str, list[Bar]], dict[str, str]]:
        return self._with_fallback(
            lambda provider: provider.fetch_many_intraday_with_errors(
                symbols,
                range_=range_,
                interval=interval,
                include_prepost=include_prepost,
            ),
            operation=f"intraday_{interval}",
        )

    def _with_fallback(self, call, *, operation: str) -> tuple[dict[str, list[Bar]], dict[str, str]]:
        primary = self._provider(self.primary_name)
        fallback = self._fallback_provider()
        if self._provider_ready(primary):
            try:
                return call(primary)
            except ProviderUnavailable as exc:
                record_source_health(
                    primary.health_key,
                    "PARTIAL",
                    failure_count=1,
                    error=str(exc),
                    message=f"{primary.name} {operation} 尚未啟用，已 fallback 至 {fallback.name}。",
                )
            except Exception as exc:
                record_source_health(
                    primary.health_key,
                    "ERROR",
                    failure_count=1,
                    error=str(exc),
                    message=f"{primary.name} {operation} 失敗，已 fallback 至 {fallback.name}。",
                )
        else:
            record_source_health(
                primary.health_key,
                "PARTIAL",
                error="provider_not_configured",
                message=f"{primary.name} 未設定或未啟用，已 fallback 至 {fallback.name}。",
            )
        return call(fallback)

    def _provider(self, name: str) -> MarketDataProvider:
        return self.providers.get(name) or self.providers["yahoo"]

    def _fallback_provider(self) -> MarketDataProvider:
        fallback = self._provider(self.fallback_name)
        if self._provider_ready(fallback):
            return fallback
        return self.providers["yahoo"]

    def _provider_ready(self, provider: MarketDataProvider) -> bool:
        status = provider.status(role="primary")
        return bool(status.enabled and status.configured)


def get_market_data_provider_manager(primary: str | None = None) -> MarketDataProviderManager:
    return MarketDataProviderManager(primary=primary)
