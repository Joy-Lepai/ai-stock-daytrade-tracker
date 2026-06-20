from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.resilience import record_source_health, retry_sync


SHIOAJI_QUOTE_VERSION = "shioaji_quote_mvp_snapshot_2026-06-20"
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class ShioajiQuoteConfig:
    enabled: bool
    api_key: str = ""
    secret_key: str = ""
    http_base_url: str = ""
    timeout_seconds: float = 3.0

    @classmethod
    def from_env(cls) -> "ShioajiQuoteConfig":
        enabled = str(os.environ.get("SHIOAJI_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
        timeout = _float(os.environ.get("SHIOAJI_TIMEOUT_SECONDS"), default=3.0) or 3.0
        return cls(
            enabled=enabled,
            api_key=str(os.environ.get("SHIOAJI_API_KEY", "")).strip(),
            secret_key=str(os.environ.get("SHIOAJI_SECRET_KEY", "")).strip(),
            http_base_url=str(os.environ.get("SHIOAJI_HTTP_BASE_URL", "")).strip().rstrip("/"),
            timeout_seconds=timeout,
        )

    @property
    def configured(self) -> bool:
        return bool(self.http_base_url or (self.api_key and self.secret_key))


@dataclass(frozen=True)
class ShioajiQuote:
    symbol: str
    status: str
    status_label: str
    enabled: bool
    configured: bool
    source: str = "Shioaji"
    version: str = SHIOAJI_QUOTE_VERSION
    price: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    total_volume: Optional[float] = None
    change_pct: Optional[float] = None
    average_price: Optional[float] = None
    bid_price: Optional[float] = None
    bid_volume: Optional[float] = None
    ask_price: Optional[float] = None
    ask_volume: Optional[float] = None
    tick_type: str = ""
    quote_time: str = ""
    fetched_at: str = ""
    tick_status: str = "missing"
    bidask_status: str = "missing"
    five_level_status: str = "not_streaming"
    orderbook_imbalance: Optional[float] = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.price is not None

    def to_dict(self) -> dict:
        return asdict(self)


class ShioajiQuoteClient:
    """Best-effort Shioaji market data adapter.

    This MVP only reads quotes. It never places orders and never requires
    trading permission. If Shioaji is not installed or credentials are missing,
    callers receive a disabled / not_configured payload instead of an exception.
    """

    def __init__(self, config: Optional[ShioajiQuoteConfig] = None) -> None:
        self.config = config or ShioajiQuoteConfig.from_env()

    def fetch_snapshot(self, symbol: str) -> ShioajiQuote:
        normalized = _normalize_symbol(symbol)
        now_text = datetime.now(TAIPEI).isoformat(timespec="seconds")
        if not self.config.enabled:
            record_source_health("shioaji", "PARTIAL", message="Shioaji 報價未啟用。")
            return ShioajiQuote(
                symbol=normalized,
                status="disabled",
                status_label="尚未啟用",
                enabled=False,
                configured=self.config.configured,
                fetched_at=now_text,
                error="SHIOAJI_ENABLED 未啟用",
            )
        if not self.config.configured:
            record_source_health("shioaji", "PARTIAL", message="Shioaji 已啟用但缺少 API Key / Secret 或 HTTP bridge。")
            return ShioajiQuote(
                symbol=normalized,
                status="not_configured",
                status_label="尚未設定憑證",
                enabled=True,
                configured=False,
                fetched_at=now_text,
                error="缺少 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY",
            )
        try:
            quote = (
                self._fetch_http_snapshot(normalized)
                if self.config.http_base_url
                else self._fetch_python_snapshot(normalized)
            )
            record_source_health("shioaji", "OK", success_count=1, message="Shioaji 報價擷取成功。")
            return quote
        except Exception as exc:  # pragma: no cover - network / optional dependency
            record_source_health(
                "shioaji",
                "ERROR",
                failure_count=1,
                failed_symbols=[normalized],
                error=str(exc),
                message="Shioaji 報價擷取失敗，已回退既有資料源。",
            )
            return ShioajiQuote(
                symbol=normalized,
                status="failed",
                status_label="擷取失敗",
                enabled=True,
                configured=True,
                fetched_at=now_text,
                error=str(exc),
            )

    def _fetch_http_snapshot(self, symbol: str) -> ShioajiQuote:
        code = _symbol_code(symbol)
        url = f"{self.config.http_base_url}/api/shioaji/snapshot?{urllib.parse.urlencode({'symbol': code})}"

        def operation() -> dict:
            with urllib.request.urlopen(url, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))

        payload = retry_sync(operation, source="shioaji", operation_name=f"Shioaji HTTP snapshot {symbol}")
        return parse_shioaji_snapshot(symbol, payload, source="Shioaji HTTP bridge")

    def _fetch_python_snapshot(self, symbol: str) -> ShioajiQuote:
        try:
            import shioaji as sj  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Render / 本機尚未安裝 shioaji 套件；請先安裝或設定 SHIOAJI_HTTP_BASE_URL。") from exc

        def operation() -> Any:
            api = sj.Shioaji()
            api.login(api_key=self.config.api_key, secret_key=self.config.secret_key, fetch_contract=True)
            code = _symbol_code(symbol)
            contract = getattr(getattr(api.Contracts, "Stocks"), code, None)
            if contract is None:
                raise RuntimeError(f"Shioaji 找不到股票合約 {code}")
            snapshots = api.snapshots([contract])
            try:
                api.logout()
            except Exception:
                pass
            if not snapshots:
                raise RuntimeError(f"Shioaji snapshot empty for {symbol}")
            return snapshots[0]

        snapshot = retry_sync(operation, source="shioaji", operation_name=f"Shioaji Python snapshot {symbol}")
        return parse_shioaji_snapshot(symbol, snapshot, source="Shioaji Python snapshot")


def parse_shioaji_snapshot(symbol: str, payload: Any, *, source: str = "Shioaji") -> ShioajiQuote:
    data = _object_to_dict(payload)
    price = _pick_float(data, "close", "price", "last_price")
    open_price = _pick_float(data, "open")
    high = _pick_float(data, "high")
    low = _pick_float(data, "low")
    close = _pick_float(data, "close", "price", "last_price")
    volume = _pick_float(data, "volume")
    total_volume = _pick_float(data, "total_volume", "totalVolume", "total_vol")
    average_price = _pick_float(data, "average_price", "avg_price")
    previous_close = _pick_float(data, "reference_price", "yesterday_close", "prev_close")
    change_pct = None
    if price is not None and previous_close:
        change_pct = round((price - previous_close) / previous_close * 100, 2)
    bid_price = _pick_float(data, "bid_price", "buy_price")
    bid_volume = _pick_float(data, "bid_volume", "buy_volume")
    ask_price = _pick_float(data, "ask_price", "sell_price")
    ask_volume = _pick_float(data, "ask_volume", "sell_volume")
    imbalance = _orderbook_imbalance(bid_volume, ask_volume)
    ts = _pick_time(data)
    warnings = ["Shioaji MVP 目前使用 snapshot / top-of-book；完整五檔委買委賣需 streaming worker。"]
    return ShioajiQuote(
        symbol=_normalize_symbol(symbol),
        status="ok" if price is not None else "partial",
        status_label="已接入" if price is not None else "部分資料",
        enabled=True,
        configured=True,
        source=source,
        price=price,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        total_volume=total_volume,
        change_pct=change_pct,
        average_price=average_price,
        bid_price=bid_price,
        bid_volume=bid_volume,
        ask_price=ask_price,
        ask_volume=ask_volume,
        tick_type=str(data.get("tick_type") or data.get("tickType") or ""),
        quote_time=ts,
        fetched_at=datetime.now(TAIPEI).isoformat(timespec="seconds"),
        tick_status="snapshot" if price is not None else "missing",
        bidask_status="top_of_book" if bid_price is not None or ask_price is not None else "missing",
        five_level_status="not_streaming",
        orderbook_imbalance=imbalance,
        warnings=warnings,
    )


def _normalize_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw.endswith(".TW") or raw.endswith(".TWO"):
        return raw
    if raw.isdigit():
        return f"{raw}.TW"
    return raw


def _symbol_code(symbol: str) -> str:
    return _normalize_symbol(symbol).split(".")[0]


def _object_to_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _pick_float(data: dict, *keys: str) -> Optional[float]:
    for key in keys:
        value = _float(data.get(key))
        if value is not None:
            return value
    return None


def _float(value, default: Optional[float] = None) -> Optional[float]:
    try:
        text = str(value).replace(",", "").strip()
        if not text or text.lower() in {"none", "nan", "null", "-", "--"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _pick_time(data: dict) -> str:
    raw = data.get("ts") or data.get("datetime") or data.get("time") or data.get("date")
    if isinstance(raw, datetime):
        return raw.astimezone(TAIPEI).isoformat(timespec="seconds")
    text = str(raw or "").strip()
    return text


def _orderbook_imbalance(bid_volume: Optional[float], ask_volume: Optional[float]) -> Optional[float]:
    bid = bid_volume or 0.0
    ask = ask_volume or 0.0
    total = bid + ask
    if total <= 0:
        return None
    return round((bid - ask) / total * 100, 2)
