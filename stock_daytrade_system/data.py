from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from stock_daytrade_system.resilience import record_source_health, retry_sync


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataError(RuntimeError):
    pass


class YahooChartClient:
    """Small stdlib client for Yahoo Finance chart data."""

    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, timeout: int = 20, pause_seconds: float = 0.2, retries: int = 3) -> None:
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self.retries = retries

    def fetch_daily(self, symbol: str, range_: str = "6mo") -> List[Bar]:
        return self._fetch_chart(symbol, range_=range_, interval="1d", include_prepost=False)

    def fetch_intraday(
        self,
        symbol: str,
        range_: str = "1d",
        interval: str = "5m",
        include_prepost: bool = False,
    ) -> List[Bar]:
        return self._fetch_chart(symbol, range_=range_, interval=interval, include_prepost=include_prepost)

    def fetch_chart_with_meta(
        self,
        symbol: str,
        range_: str,
        interval: str,
        include_prepost: bool = False,
    ) -> Tuple[List[Bar], Dict]:
        result = self._fetch_chart_result(
            symbol,
            range_=range_,
            interval=interval,
            include_prepost=include_prepost,
        )
        time.sleep(self.pause_seconds)
        return self._parse_result(result), result.get("meta", {})

    def _fetch_chart(
        self,
        symbol: str,
        range_: str,
        interval: str,
        include_prepost: bool,
    ) -> List[Bar]:
        result = self._fetch_chart_result(
            symbol,
            range_=range_,
            interval=interval,
            include_prepost=include_prepost,
        )
        bars = self._parse_result(result)
        time.sleep(self.pause_seconds)
        return bars

    def _fetch_chart_result(
        self,
        symbol: str,
        range_: str,
        interval: str,
        include_prepost: bool,
    ) -> Dict:
        encoded = urllib.parse.quote(symbol, safe="")
        query = urllib.parse.urlencode(
            {
                "range": range_,
                "interval": interval,
                "includePrePost": "true" if include_prepost else "false",
                "events": "div,splits",
            }
        )
        url = f"{self.base_url}/{encoded}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 AI-stock-research/0.1",
                "Accept": "application/json",
            },
        )
        def operation() -> Dict:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                raise MarketDataError(f"failed to fetch {symbol}: {exc}") from exc

        payload = retry_sync(
            operation,
            source="yahoo_chart",
            operation_name=f"Yahoo chart {symbol} {interval}",
            retry_delays=(1.0, 2.0, 4.0)[: self.retries],
            should_retry=lambda exc: not _is_non_retryable(exc),
        )

        result = payload.get("chart", {}).get("result")
        if not result:
            error = payload.get("chart", {}).get("error")
            raise MarketDataError(f"no chart data for {symbol}: {error}")

        return result[0]

    def fetch_many_daily(self, symbols: Iterable[str], range_: str = "6mo") -> Dict[str, List[Bar]]:
        data, _errors = self.fetch_many_daily_with_errors(symbols, range_=range_)
        return data

    def fetch_many_daily_with_errors(
        self, symbols: Iterable[str], range_: str = "6mo"
    ) -> Tuple[Dict[str, List[Bar]], Dict[str, str]]:
        return self._fetch_many_with_errors(symbols, range_=range_, interval="1d")

    def fetch_many_intraday_with_errors(
        self,
        symbols: Iterable[str],
        range_: str = "1d",
        interval: str = "5m",
        include_prepost: bool = False,
    ) -> Tuple[Dict[str, List[Bar]], Dict[str, str]]:
        return self._fetch_many_with_errors(
            symbols,
            range_=range_,
            interval=interval,
            include_prepost=include_prepost,
        )

    def _fetch_many_with_errors(
        self,
        symbols: Iterable[str],
        range_: str,
        interval: str,
        include_prepost: bool = False,
    ) -> Tuple[Dict[str, List[Bar]], Dict[str, str]]:
        data: Dict[str, List[Bar]] = {}
        errors: Dict[str, str] = {}
        for symbol in symbols:
            try:
                data[symbol] = self._fetch_chart(
                    symbol,
                    range_=range_,
                    interval=interval,
                    include_prepost=include_prepost,
                )
            except MarketDataError as exc:
                data[symbol] = []
                errors[symbol] = str(exc)
        success_count = sum(1 for bars in data.values() if bars)
        record_source_health(
            "yahoo_chart",
            "PARTIAL" if errors and success_count else "ERROR" if errors else "OK",
            success_count=success_count,
            failure_count=len(errors),
            failed_symbols=errors.keys(),
            error="; ".join(f"{symbol}: {error}" for symbol, error in sorted(errors.items())[:5]),
            message="Yahoo 部分股票資料擷取失敗，已以空資料降級處理。" if errors else "Yahoo 資料擷取成功。",
        )
        return data, errors

    def _parse_result(self, result: Dict) -> List[Bar]:
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: List[Bar] = []
        for index, stamp in enumerate(timestamps):
            values = [
                _get(opens, index),
                _get(highs, index),
                _get(lows, index),
                _get(closes, index),
                _get(volumes, index),
            ]
            if any(value is None for value in values):
                continue
            bars.append(
                Bar(
                    timestamp=datetime.fromtimestamp(stamp),
                    open=float(values[0]),
                    high=float(values[1]),
                    low=float(values[2]),
                    close=float(values[3]),
                    volume=float(values[4]),
                )
            )
        return bars


def _get(values: List[Optional[float]], index: int) -> Optional[float]:
    if index >= len(values):
        return None
    return values[index]


def _is_non_retryable(exc: MarketDataError) -> bool:
    text = str(exc).lower()
    return "nodename nor servname" in text or "name or service not known" in text
