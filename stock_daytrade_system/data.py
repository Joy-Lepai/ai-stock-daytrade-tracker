from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple


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

    def __init__(self, timeout: int = 20, pause_seconds: float = 0.2) -> None:
        self.timeout = timeout
        self.pause_seconds = pause_seconds

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
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise MarketDataError(f"failed to fetch {symbol}: {exc}") from exc

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
