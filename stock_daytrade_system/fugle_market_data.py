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


FUGLE_MARKET_DATA_VERSION = "fugle_market_data_v2_quote_trades_candles_2026-06-21"
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class FugleMarketDataConfig:
    enabled: bool
    api_key: str = ""
    base_url: str = "https://api.fugle.tw/marketdata/v1.0/stock"
    timeout_seconds: float = 4.0
    trades_limit: int = 30
    candles_timeframe: str = "1"
    large_trade_threshold: float = 200.0

    @classmethod
    def from_env(cls) -> "FugleMarketDataConfig":
        enabled = str(os.environ.get("FUGLE_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
        timeout = _float(os.environ.get("FUGLE_TIMEOUT_SECONDS"), default=4.0) or 4.0
        limit = int(_float(os.environ.get("FUGLE_TRADES_LIMIT"), default=30) or 30)
        timeframe = str(os.environ.get("FUGLE_CANDLES_TIMEFRAME", "1")).strip() or "1"
        threshold = _float(os.environ.get("FUGLE_LARGE_TRADE_THRESHOLD"), default=200.0) or 200.0
        return cls(
            enabled=enabled,
            api_key=str(os.environ.get("FUGLE_API_KEY", "")).strip(),
            base_url=str(os.environ.get("FUGLE_BASE_URL", "")).strip().rstrip("/")
            or "https://api.fugle.tw/marketdata/v1.0/stock",
            timeout_seconds=timeout,
            trades_limit=max(1, min(limit, 100)),
            candles_timeframe=timeframe,
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


@dataclass(frozen=True)
class FugleQuoteSignal:
    symbol: str
    status: str
    status_label: str
    enabled: bool
    configured: bool
    source: str = "Fugle REST Quote"
    version: str = FUGLE_MARKET_DATA_VERSION
    fetched_at: str = ""
    date: str = ""
    name: str = ""
    exchange: str = ""
    market: str = ""
    price: Optional[float] = None
    previous_close: Optional[float] = None
    change_pct: Optional[float] = None
    quote_time: str = ""
    last_updated: str = ""
    open_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    avg_price: Optional[float] = None
    last_size: Optional[float] = None
    bid_levels: list[dict] = field(default_factory=list)
    ask_levels: list[dict] = field(default_factory=list)
    bid_total_volume: Optional[float] = None
    ask_total_volume: Optional[float] = None
    bid_price: Optional[float] = None
    bid_volume: Optional[float] = None
    ask_price: Optional[float] = None
    ask_volume: Optional[float] = None
    orderbook_imbalance: Optional[float] = None
    five_level_status: str = "missing"
    five_level_status_label: str = "五檔資料不足"
    total_trade_value: Optional[float] = None
    total_trade_volume: Optional[float] = None
    total_trade_volume_at_bid: Optional[float] = None
    total_trade_volume_at_ask: Optional[float] = None
    total_transaction: Optional[float] = None
    intraday_flow_ratio: Optional[float] = None
    last_trade_price: Optional[float] = None
    last_trade_size: Optional[float] = None
    last_trade_bid: Optional[float] = None
    last_trade_ask: Optional[float] = None
    last_trade_time: str = ""
    last_trade_side: str = "unknown"
    last_trade_summary: str = "最新成交方向不足。"
    is_limit_up_price: bool = False
    is_limit_down_price: bool = False
    is_limit_up_bid: bool = False
    is_limit_down_bid: bool = False
    is_limit_up_ask: bool = False
    is_limit_down_ask: bool = False
    is_trial: bool = False
    is_continuous: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "partial"} and self.price is not None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FugleCandlesSignal:
    symbol: str
    status: str
    status_label: str
    enabled: bool
    configured: bool
    source: str = "Fugle REST Candles"
    version: str = FUGLE_MARKET_DATA_VERSION
    fetched_at: str = ""
    date: str = ""
    timeframe: str = "1"
    candles_count: int = 0
    candles: list[dict] = field(default_factory=list)
    latest_close: Optional[float] = None
    latest_average: Optional[float] = None
    latest_time: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "partial"} and self.candles_count > 0

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

    def fetch_quote(self, symbol: str) -> FugleQuoteSignal:
        normalized = _normalize_symbol(symbol)
        now_text = datetime.now(TAIPEI).isoformat(timespec="seconds")
        if not self.config.enabled:
            record_source_health("fugle_quote", "PARTIAL", message="Fugle Quote 未啟用。")
            return FugleQuoteSignal(
                symbol=normalized,
                status="disabled",
                status_label="尚未啟用",
                enabled=False,
                configured=self.config.configured,
                fetched_at=now_text,
                error="FUGLE_ENABLED 未啟用",
            )
        if not self.config.configured:
            record_source_health("fugle_quote", "PARTIAL", message="Fugle Quote 已啟用但缺少 API Key。")
            return FugleQuoteSignal(
                symbol=normalized,
                status="not_configured",
                status_label="尚未設定 API Key",
                enabled=True,
                configured=False,
                fetched_at=now_text,
                error="缺少 FUGLE_API_KEY",
            )
        try:
            payload = self._fetch_rest_quote(normalized)
            signal = parse_fugle_quote(normalized, payload)
            record_source_health("fugle_quote", "OK", success_count=1, message="Fugle Quote 擷取成功。")
            return signal
        except Exception as exc:  # pragma: no cover - network best effort
            record_source_health(
                "fugle_quote",
                "ERROR",
                failure_count=1,
                failed_symbols=[normalized],
                error=str(exc),
                message="Fugle Quote 擷取失敗，已回退 TWSE MIS / Yahoo。",
            )
            return FugleQuoteSignal(
                symbol=normalized,
                status="failed",
                status_label="擷取失敗",
                enabled=True,
                configured=True,
                fetched_at=now_text,
                error=str(exc),
            )

    def fetch_candles(self, symbol: str, timeframe: Optional[str] = None) -> FugleCandlesSignal:
        normalized = _normalize_symbol(symbol)
        now_text = datetime.now(TAIPEI).isoformat(timespec="seconds")
        timeframe = str(timeframe or self.config.candles_timeframe or "1")
        if not self.config.enabled:
            record_source_health("fugle_candles", "PARTIAL", message="Fugle Candles 未啟用。")
            return FugleCandlesSignal(
                symbol=normalized,
                status="disabled",
                status_label="尚未啟用",
                enabled=False,
                configured=self.config.configured,
                fetched_at=now_text,
                timeframe=timeframe,
                error="FUGLE_ENABLED 未啟用",
            )
        if not self.config.configured:
            record_source_health("fugle_candles", "PARTIAL", message="Fugle Candles 已啟用但缺少 API Key。")
            return FugleCandlesSignal(
                symbol=normalized,
                status="not_configured",
                status_label="尚未設定 API Key",
                enabled=True,
                configured=False,
                fetched_at=now_text,
                timeframe=timeframe,
                error="缺少 FUGLE_API_KEY",
            )
        try:
            payload = self._fetch_rest_candles(normalized, timeframe=timeframe)
            signal = parse_fugle_candles(normalized, payload, timeframe=timeframe)
            record_source_health("fugle_candles", "OK", success_count=1, message="Fugle Candles 擷取成功。")
            return signal
        except Exception as exc:  # pragma: no cover - network best effort
            record_source_health(
                "fugle_candles",
                "ERROR",
                failure_count=1,
                failed_symbols=[normalized],
                error=str(exc),
                message="Fugle Candles 擷取失敗，已回退 Yahoo 分 K。",
            )
            return FugleCandlesSignal(
                symbol=normalized,
                status="failed",
                status_label="擷取失敗",
                enabled=True,
                configured=True,
                fetched_at=now_text,
                timeframe=timeframe,
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

    def _fetch_rest_quote(self, symbol: str) -> dict:
        code = _symbol_code(symbol)
        url = f"{self.config.base_url}/intraday/quote/{code}"
        return self._fetch_json(url, operation_name=f"Fugle quote {symbol}", source="fugle_quote")

    def _fetch_rest_candles(self, symbol: str, *, timeframe: str) -> dict:
        code = _symbol_code(symbol)
        query = urllib.parse.urlencode({"timeframe": timeframe, "sort": "asc"})
        url = f"{self.config.base_url}/intraday/candles/{code}?{query}"
        return self._fetch_json(url, operation_name=f"Fugle candles {symbol}", source="fugle_candles")

    def _fetch_json(self, url: str, *, operation_name: str, source: str) -> dict:
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

        return retry_sync(operation, source=source, operation_name=operation_name)


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


def parse_fugle_quote(symbol: str, payload: Any, *, source: str = "Fugle REST Quote") -> FugleQuoteSignal:
    data = _object_to_dict(payload)
    normalized = _normalize_symbol(symbol or str(data.get("symbol") or ""))
    fetched_at = datetime.now(TAIPEI).isoformat(timespec="seconds")
    bids = _fugle_levels(data.get("bids"))
    asks = _fugle_levels(data.get("asks"))
    bid_total = _sum_level_size(bids)
    ask_total = _sum_level_size(asks)
    imbalance = _orderbook_imbalance(bid_total, ask_total)
    total = _object_to_dict(data.get("total"))
    last_trade = _object_to_dict(data.get("lastTrade"))
    price = _pick_float(data, "lastPrice", "closePrice")
    previous_close = _pick_float(data, "previousClose", "referencePrice")
    change_pct = _pick_float(data, "changePercent")
    last_trade_price = _pick_float(last_trade, "price")
    last_trade_bid = _pick_float(last_trade, "bid")
    last_trade_ask = _pick_float(last_trade, "ask")
    last_trade_size = _pick_float(last_trade, "size")
    last_trade_side, last_trade_summary = _last_trade_side(
        price=last_trade_price,
        bid=last_trade_bid,
        ask=last_trade_ask,
        size=last_trade_size,
    )
    flow_ratio = _flow_ratio(
        _pick_float(total, "tradeVolumeAtAsk"),
        _pick_float(total, "tradeVolumeAtBid"),
    )
    five_status, five_label = _five_level_status(
        bids,
        asks,
        price=price,
        is_limit_up=bool(data.get("isLimitUpPrice") or data.get("isLimitUpBid")),
        is_limit_down=bool(data.get("isLimitDownPrice") or data.get("isLimitDownAsk")),
    )
    last_updated = _format_epoch_time(data.get("lastUpdated"))
    quote_time = _format_epoch_time(
        data.get("closeTime")
        or data.get("lastUpdated")
        or last_trade.get("time")
        or total.get("time")
    )
    return FugleQuoteSignal(
        symbol=normalized,
        status="ok" if price is not None else "partial",
        status_label="已接入 Fugle Quote" if price is not None else "Fugle Quote 價格不足",
        enabled=True,
        configured=True,
        source=source,
        fetched_at=fetched_at,
        date=str(data.get("date") or ""),
        name=str(data.get("name") or ""),
        exchange=str(data.get("exchange") or ""),
        market=str(data.get("market") or ""),
        price=price,
        previous_close=previous_close,
        change_pct=change_pct,
        quote_time=quote_time,
        last_updated=last_updated,
        open_price=_pick_float(data, "openPrice"),
        high_price=_pick_float(data, "highPrice"),
        low_price=_pick_float(data, "lowPrice"),
        avg_price=_pick_float(data, "avgPrice"),
        last_size=_pick_float(data, "lastSize"),
        bid_levels=bids,
        ask_levels=asks,
        bid_total_volume=bid_total,
        ask_total_volume=ask_total,
        bid_price=bids[0]["price"] if bids else None,
        bid_volume=bids[0]["volume"] if bids else None,
        ask_price=asks[0]["price"] if asks else None,
        ask_volume=asks[0]["volume"] if asks else None,
        orderbook_imbalance=imbalance,
        five_level_status=five_status,
        five_level_status_label=five_label,
        total_trade_value=_pick_float(total, "tradeValue"),
        total_trade_volume=_pick_float(total, "tradeVolume"),
        total_trade_volume_at_bid=_pick_float(total, "tradeVolumeAtBid"),
        total_trade_volume_at_ask=_pick_float(total, "tradeVolumeAtAsk"),
        total_transaction=_pick_float(total, "transaction"),
        intraday_flow_ratio=flow_ratio,
        last_trade_price=last_trade_price,
        last_trade_size=last_trade_size,
        last_trade_bid=last_trade_bid,
        last_trade_ask=last_trade_ask,
        last_trade_time=_format_epoch_time(last_trade.get("time")),
        last_trade_side=last_trade_side,
        last_trade_summary=last_trade_summary,
        is_limit_up_price=bool(data.get("isLimitUpPrice")),
        is_limit_down_price=bool(data.get("isLimitDownPrice")),
        is_limit_up_bid=bool(data.get("isLimitUpBid")),
        is_limit_down_bid=bool(data.get("isLimitDownBid")),
        is_limit_up_ask=bool(data.get("isLimitUpAsk")),
        is_limit_down_ask=bool(data.get("isLimitDownAsk")),
        is_trial=bool(data.get("isTrial")),
        is_continuous=bool(data.get("isContinuous")),
        warnings=["Fugle Quote 用於重點標的進場確認；不作全市場掃描。"],
    )


def parse_fugle_candles(
    symbol: str,
    payload: Any,
    *,
    timeframe: str = "1",
    source: str = "Fugle REST Candles",
) -> FugleCandlesSignal:
    data = _object_to_dict(payload)
    rows = data.get("data") if isinstance(data.get("data"), list) else []
    normalized = _normalize_symbol(symbol or str(data.get("symbol") or ""))
    fetched_at = datetime.now(TAIPEI).isoformat(timespec="seconds")
    candles = []
    for row in rows:
        item = _object_to_dict(row)
        candles.append(
            {
                "timestamp": str(item.get("date") or ""),
                "open": _pick_float(item, "open"),
                "high": _pick_float(item, "high"),
                "low": _pick_float(item, "low"),
                "close": _pick_float(item, "close"),
                "volume": _pick_float(item, "volume"),
                "average": _pick_float(item, "average"),
            }
        )
    latest = candles[-1] if candles else {}
    return FugleCandlesSignal(
        symbol=normalized,
        status="ok" if candles else "partial",
        status_label="已接入 Fugle 1分K" if candles else "Fugle K 線不足",
        enabled=True,
        configured=True,
        source=source,
        fetched_at=fetched_at,
        date=str(data.get("date") or ""),
        timeframe=str(data.get("timeframe") or timeframe),
        candles_count=len(candles),
        candles=candles,
        latest_close=latest.get("close"),
        latest_average=latest.get("average"),
        latest_time=str(latest.get("timestamp") or ""),
        warnings=["Fugle Candles 用於重點標的 VWAP / 最新價墊高 / 量能延續確認。"],
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
    return _format_epoch_time(value)


def _format_epoch_time(value) -> str:
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


def _fugle_levels(rows) -> list[dict]:
    if not isinstance(rows, list):
        return []
    levels = []
    for index, row in enumerate(rows[:5]):
        item = _object_to_dict(row)
        price = _pick_float(item, "price")
        size = _pick_float(item, "size", "volume")
        if price is None:
            continue
        levels.append({"level": index + 1, "price": price, "volume": size})
    return levels


def _sum_level_size(levels: list[dict]) -> Optional[float]:
    if not levels:
        return None
    return round(sum(float(item.get("volume") or 0) for item in levels), 2)


def _orderbook_imbalance(bid_total: Optional[float], ask_total: Optional[float]) -> Optional[float]:
    bid = float(bid_total or 0)
    ask = float(ask_total or 0)
    total = bid + ask
    if total <= 0:
        return None
    return round((bid - ask) / total * 100, 2)


def _five_level_status(
    bids: list[dict],
    asks: list[dict],
    *,
    price: Optional[float],
    is_limit_up: bool,
    is_limit_down: bool,
) -> tuple[str, str]:
    if bids and asks:
        return "available", "Fugle 五檔可用"
    if bids and is_limit_up:
        return "limit_up_bid_only", "漲停鎖住，僅見買盤"
    if asks and is_limit_down:
        return "limit_down_ask_only", "跌停鎖住，僅見賣盤"
    if bids:
        return "bid_only", "僅有委買盤"
    if asks:
        return "ask_only", "僅有委賣盤"
    return "missing", "五檔資料不足"


def _flow_ratio(at_ask: Optional[float], at_bid: Optional[float]) -> Optional[float]:
    ask = float(at_ask or 0)
    bid = float(at_bid or 0)
    total = ask + bid
    if total <= 0:
        return None
    return round((ask - bid) / total * 100, 2)


def _last_trade_side(
    *,
    price: Optional[float],
    bid: Optional[float],
    ask: Optional[float],
    size: Optional[float],
) -> tuple[str, str]:
    amount = "-" if size is None else f"{size:.0f}"
    if price is not None and ask is not None and price >= ask:
        return "buy_sweep", f"最後一筆偏主動買進：{amount} 股，成交價 {price:g} 接近/打到委賣價 {ask:g}。"
    if price is not None and bid is not None and price <= bid:
        return "sell_sweep", f"最後一筆偏主動賣出：{amount} 股，成交價 {price:g} 接近/打到委買價 {bid:g}。"
    return "unknown", "最後一筆成交方向不足。"
