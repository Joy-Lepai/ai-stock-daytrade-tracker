from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.resilience import record_source_health, retry_sync


FUGLE_MARKET_DATA_VERSION = "fugle_market_data_v1_rest_trades_2026-06-21"
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class FugleMarketDataConfig:
    enabled: bool
    api_key: str = ""
    base_url: str = "https://api.fugle.tw/marketdata/v1.0/stock"
    timeout_seconds: float = 4.0
    trades_limit: int = 30
    large_trade_threshold: float = 200.0

    @classmethod
    def from_env(cls) -> "FugleMarketDataConfig":
        enabled = str(os.environ.get("FUGLE_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
        timeout = _float(os.environ.get("FUGLE_TIMEOUT_SECONDS"), default=4.0) or 4.0
        limit = int(_float(os.environ.get("FUGLE_TRADES_LIMIT"), default=30) or 30)
        threshold = _float(os.environ.get("FUGLE_LARGE_TRADE_THRESHOLD"), default=200.0) or 200.0
        return cls(
            enabled=enabled,
            api_key=str(os.environ.get("FUGLE_API_KEY", "")).strip(),
            base_url=str(os.environ.get("FUGLE_BASE_URL", "")).strip().rstrip("/")
            or "https://api.fugle.tw/marketdata/v1.0/stock",
            timeout_seconds=timeout,
            trades_limit=max(1, min(limit, 100)),
            large_trade_threshold=threshold,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class FugleTradeSignal:
    symbol: str
    status: str
    status_label: str
    enabled: bool
    configured: bool
    source: str = "Fugle REST Trades"
    version: str = FUGLE_MARKET_DATA_VERSION
    fetched_at: str = ""
    date: str = ""
    trades_count: int = 0
    latest_price: Optional[float] = None
    latest_size: Optional[float] = None
    latest_time: str = ""
    latest_bid: Optional[float] = None
    latest_ask: Optional[float] = None
    large_trade_status: str = "missing"
    large_trade_summary: str = "目前缺逐筆成交資料，無法判斷大單敲進 / 敲出。"
    large_trade_threshold: Optional[float] = None
    large_trade_price: Optional[float] = None
    large_trade_size: Optional[float] = None
    large_trade_time: str = ""
    large_buy_count: int = 0
    large_sell_count: int = 0
    large_unknown_count: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "partial"} and self.trades_count > 0

    def to_dict(self) -> dict:
        return asdict(self)


class FugleMarketDataClient:
    """Read-only Fugle market data adapter.

    This client only fetches market data. It never places orders. If the API key
    is absent or Fugle is disabled, callers receive a safe payload instead of an
    exception so the dashboard can keep rendering.
    """

    def __init__(self, config: Optional[FugleMarketDataConfig] = None) -> None:
        self.config = config or FugleMarketDataConfig.from_env()

    def fetch_trades(self, symbol: str) -> FugleTradeSignal:
        normalized = _normalize_symbol(symbol)
        now_text = datetime.now(TAIPEI).isoformat(timespec="seconds")
        if not self.config.enabled:
            record_source_health("fugle", "PARTIAL", message="Fugle 行情未啟用。")
            return FugleTradeSignal(
                symbol=normalized,
                status="disabled",
                status_label="尚未啟用",
                enabled=False,
                configured=self.config.configured,
                fetched_at=now_text,
                large_trade_threshold=self.config.large_trade_threshold,
                error="FUGLE_ENABLED 未啟用",
            )
        if not self.config.configured:
            record_source_health("fugle", "PARTIAL", message="Fugle 已啟用但缺少 API Key。")
            return FugleTradeSignal(
                symbol=normalized,
                status="not_configured",
                status_label="尚未設定 API Key",
                enabled=True,
                configured=False,
                fetched_at=now_text,
                large_trade_threshold=self.config.large_trade_threshold,
                error="缺少 FUGLE_API_KEY",
            )
        try:
            payload = self._fetch_rest_trades(normalized)
            signal = parse_fugle_trades(
                normalized,
                payload,
                threshold=self.config.large_trade_threshold,
                source="Fugle REST Trades",
            )
            record_source_health("fugle", "OK", success_count=1, message="Fugle 逐筆成交擷取成功。")
            return signal
        except Exception as exc:  # pragma: no cover - network best effort
            record_source_health(
                "fugle",
                "ERROR",
                failure_count=1,
                failed_symbols=[normalized],
                error=str(exc),
                message="Fugle 逐筆成交擷取失敗，已回退既有行情來源。",
            )
            return FugleTradeSignal(
                symbol=normalized,
                status="failed",
                status_label="擷取失敗",
                enabled=True,
                configured=True,
                fetched_at=now_text,
                large_trade_threshold=self.config.large_trade_threshold,
                error=str(exc),
            )

    def _fetch_rest_trades(self, symbol: str) -> dict:
        code = _symbol_code(symbol)
        query = urllib.parse.urlencode({"limit": self.config.trades_limit, "sort": "desc"})
        url = f"{self.config.base_url}/intraday/trades/{code}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "X-API-KEY": self.config.api_key,
                "Accept": "application/json",
                "User-Agent": "AI-stock-daytrade-tracker/0.1",
            },
        )

        def operation() -> dict:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))

        return retry_sync(operation, source="fugle", operation_name=f"Fugle trades {symbol}")


def parse_fugle_trades(
    symbol: str,
    payload: Any,
    *,
    threshold: float = 200.0,
    source: str = "Fugle REST Trades",
) -> FugleTradeSignal:
    data = _object_to_dict(payload)
    rows = data.get("data") if isinstance(data.get("data"), list) else []
    normalized = _normalize_symbol(symbol or str(data.get("symbol") or ""))
    fetched_at = datetime.now(TAIPEI).isoformat(timespec="seconds")
    latest = rows[0] if rows else {}
    latest_price = _pick_float(latest, "price")
    latest_size = _pick_float(latest, "size")
    latest_bid = _pick_float(latest, "bid")
    latest_ask = _pick_float(latest, "ask")
    latest_time = _format_trade_time(latest.get("time"))
    large_rows = []
    for row in rows:
        size = _pick_float(row, "size")
        if size is not None and size >= threshold:
            large_rows.append(row)
    classified = [_classify_trade(row, threshold=threshold) for row in large_rows]
    large_buy_count = sum(1 for item in classified if item["status"] == "buy_sweep")
    large_sell_count = sum(1 for item in classified if item["status"] == "sell_sweep")
    large_unknown_count = sum(1 for item in classified if item["status"] == "unknown")
    chosen = classified[0] if classified else None
    if chosen:
        large_status = chosen["status"]
        large_summary = chosen["summary"]
        large_price = chosen["price"]
        large_size = chosen["size"]
        large_time = chosen["time"]
    elif rows:
        large_status = "neutral"
        large_summary = f"最近 {len(rows)} 筆成交未達大單門檻 {threshold:.0f} 股。"
        large_price = None
        large_size = None
        large_time = ""
    else:
        large_status = "missing"
        large_summary = "目前缺逐筆成交資料，無法判斷大單敲進 / 敲出。"
        large_price = None
        large_size = None
        large_time = ""
    return FugleTradeSignal(
        symbol=normalized,
        status="ok" if rows else "partial",
        status_label="已接入逐筆成交" if rows else "逐筆成交不足",
        enabled=True,
        configured=True,
        source=source,
        fetched_at=fetched_at,
        date=str(data.get("date") or ""),
        trades_count=len(rows),
        latest_price=latest_price,
        latest_size=latest_size,
        latest_time=latest_time,
        latest_bid=latest_bid,
        latest_ask=latest_ask,
        large_trade_status=large_status,
        large_trade_summary=large_summary,
        large_trade_threshold=threshold,
        large_trade_price=large_price,
        large_trade_size=large_size,
        large_trade_time=large_time,
        large_buy_count=large_buy_count,
        large_sell_count=large_sell_count,
        large_unknown_count=large_unknown_count,
        warnings=["Fugle REST Trades 為輪詢式逐筆成交；若要秒級推播，下一階段改接 WebSocket Trades。"],
    )


def _classify_trade(row: dict, *, threshold: float) -> dict:
    price = _pick_float(row, "price")
    bid = _pick_float(row, "bid")
    ask = _pick_float(row, "ask")
    size = _pick_float(row, "size") or 0.0
    time_text = _format_trade_time(row.get("time"))
    if price is not None and ask is not None and price >= ask:
        return {
            "status": "buy_sweep",
            "summary": f"疑似大單敲進：{size:.0f} 股，成交價 {price:g} 接近/打到委賣價 {ask:g}。",
            "price": price,
            "size": size,
            "time": time_text,
        }
    if price is not None and bid is not None and price <= bid:
        return {
            "status": "sell_sweep",
            "summary": f"疑似大單敲出：{size:.0f} 股，成交價 {price:g} 接近/打到委買價 {bid:g}。",
            "price": price,
            "size": size,
            "time": time_text,
        }
    return {
        "status": "unknown",
        "summary": f"大額成交 {size:.0f} 股，但缺 bid/ask 或方向不明。",
        "price": price,
        "size": size,
        "time": time_text,
    }


def _normalize_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw.endswith(".TW") or raw.endswith(".TWO"):
        return raw
    if raw.isdigit():
        return f"{raw}.TW"
    return raw


def _symbol_code(symbol: str) -> str:
    return _normalize_symbol(symbol).split(".")[0]


def _object_to_dict(payload: Any) -> dict:
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "to_dict"):
        return dict(payload.to_dict())
    if hasattr(payload, "__dict__"):
        return dict(vars(payload))
    return {}


def _pick_float(data: dict, *keys: str) -> Optional[float]:
    for key in keys:
        value = data.get(key)
        parsed = _float(value)
        if parsed is not None:
            return parsed
    return None


def _float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _format_trade_time(value) -> str:
    if value is None or value == "":
        return ""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    # Fugle uses epoch microseconds in examples; accept milliseconds as well.
    seconds = number / 1_000_000 if number > 10_000_000_000_000 else number / 1_000
    try:
        return datetime.fromtimestamp(seconds, TAIPEI).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return str(value)
