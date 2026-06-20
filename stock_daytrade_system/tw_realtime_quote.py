from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict, field
from typing import Optional

from stock_daytrade_system.resilience import record_source_health, retry_sync


@dataclass(frozen=True)
class TwRealtimeQuote:
    symbol: str
    name: str
    price: Optional[float]
    previous_close: Optional[float]
    change_pct: Optional[float]
    quote_time: str
    source: str = "TWSE MIS"
    status: str = "ok"
    error: str = ""
    bid_levels: list[dict] = field(default_factory=list)
    ask_levels: list[dict] = field(default_factory=list)
    bid_total_volume: Optional[float] = None
    ask_total_volume: Optional[float] = None
    bid_price: Optional[float] = None
    bid_volume: Optional[float] = None
    ask_price: Optional[float] = None
    ask_volume: Optional[float] = None
    five_level_status: str = "missing"
    five_level_status_label: str = "五檔資料不足"
    orderbook_imbalance: Optional[float] = None
    limit_up: Optional[float] = None
    limit_down: Optional[float] = None
    is_limit_up_locked: bool = False
    is_limit_down_locked: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class TwRealtimeQuoteClient:
    """Best-effort TWSE/TPEx current quote client.

    The official MIS endpoint can occasionally return "-" during auction,
    lunch/close transitions, or for unavailable symbols. Callers should always
    keep a fallback quote source.
    """

    base_url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

    def __init__(self, timeout: int = 8) -> None:
        self.timeout = timeout

    def fetch(self, symbol: str) -> TwRealtimeQuote:
        normalized = _normalize_symbol(symbol)
        errors = []
        for market in _market_candidates(normalized):
            try:
                quote = self._fetch_market(normalized, market)
                if quote.price is not None:
                    record_source_health("twse_mis", "OK", success_count=1, message="TWSE MIS 即時價擷取成功。")
                    return quote
                errors.append(quote.error or "price unavailable")
            except Exception as exc:  # pragma: no cover - network best effort
                errors.append(str(exc))
        record_source_health(
            "twse_mis",
            "ERROR",
            failure_count=1,
            failed_symbols=[normalized],
            error="; ".join(error for error in errors if error),
            message="TWSE MIS 即時價擷取失敗，已交由呼叫端回退資料。",
        )
        return TwRealtimeQuote(
            symbol=normalized,
            name=normalized,
            price=None,
            previous_close=None,
            change_pct=None,
            quote_time="",
            status="failed",
            error="; ".join(error for error in errors if error) or "quote unavailable",
        )

    def _fetch_market(self, symbol: str, market: str) -> TwRealtimeQuote:
        code = symbol.split(".")[0]
        ex_ch = f"{market}_{code}.tw"
        query = urllib.parse.urlencode(
            {
                "ex_ch": ex_ch,
                "json": "1",
                "delay": "0",
                "_": str(int(time.time() * 1000)),
            }
        )
        request = urllib.request.Request(
            f"{self.base_url}?{query}",
            headers={
                "User-Agent": "Mozilla/5.0 AI-stock-research/0.1",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://mis.twse.com.tw/stock/index.jsp",
            },
        )
        def operation() -> dict:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (ssl.SSLError, urllib.error.URLError) as exc:
                if "CERTIFICATE_VERIFY_FAILED" not in str(exc) and not isinstance(exc, ssl.SSLError):
                    raise
                context = ssl._create_unverified_context()
                with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                    return json.loads(response.read().decode("utf-8"))

        payload = retry_sync(
            operation,
            source="twse_mis",
            operation_name=f"TWSE MIS quote {symbol}",
        )
        rows = payload.get("msgArray") or []
        if not rows:
            return TwRealtimeQuote(
                symbol=symbol,
                name=symbol,
                price=None,
                previous_close=None,
                change_pct=None,
                quote_time="",
                status="failed",
                error=f"{market} quote missing",
            )
        return parse_twse_quote_row(symbol, rows[0])


def parse_twse_quote_row(symbol: str, row: dict) -> TwRealtimeQuote:
    price = _float(row.get("z"))
    previous_close = _float(row.get("y"))
    change_pct = ((price - previous_close) / previous_close * 100) if price and previous_close else None
    date = str(row.get("d") or "")
    time_text = str(row.get("t") or "")
    quote_time = _quote_time(date, time_text)
    bid_levels = _levels(row.get("b"), row.get("g"))
    ask_levels = _levels(row.get("a"), row.get("f"))
    bid_total = _sum_volume(bid_levels)
    ask_total = _sum_volume(ask_levels)
    imbalance = _orderbook_imbalance(bid_total, ask_total)
    limit_up = _float(row.get("u"))
    limit_down = _float(row.get("w"))
    status, status_label = _five_level_status(bid_levels, ask_levels, price, limit_up, limit_down)
    return TwRealtimeQuote(
        symbol=_normalize_symbol(symbol),
        name=str(row.get("n") or symbol),
        price=round(price, 2) if price is not None else None,
        previous_close=round(previous_close, 2) if previous_close is not None else None,
        change_pct=round(change_pct, 2) if change_pct is not None else None,
        quote_time=quote_time,
        status="ok" if price is not None else "partial",
        error="" if price is not None else "current price unavailable",
        bid_levels=bid_levels,
        ask_levels=ask_levels,
        bid_total_volume=bid_total,
        ask_total_volume=ask_total,
        bid_price=bid_levels[0]["price"] if bid_levels else None,
        bid_volume=bid_levels[0]["volume"] if bid_levels else None,
        ask_price=ask_levels[0]["price"] if ask_levels else None,
        ask_volume=ask_levels[0]["volume"] if ask_levels else None,
        five_level_status=status,
        five_level_status_label=status_label,
        orderbook_imbalance=imbalance,
        limit_up=round(limit_up, 2) if limit_up is not None else None,
        limit_down=round(limit_down, 2) if limit_down is not None else None,
        is_limit_up_locked=bool(price is not None and limit_up is not None and price >= limit_up and not ask_levels and bid_levels),
        is_limit_down_locked=bool(price is not None and limit_down is not None and price <= limit_down and not bid_levels and ask_levels),
    )


def _normalize_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw.endswith(".TW") or raw.endswith(".TWO"):
        return raw
    if raw.isdigit():
        return f"{raw}.TW"
    return raw


def _market_candidates(symbol: str) -> list[str]:
    if symbol.endswith(".TWO"):
        return ["otc", "tse"]
    return ["tse", "otc"]


def _float(value) -> Optional[float]:
    try:
        text = str(value).replace(",", "").strip()
        if not text or text in {"-", "--", "null"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _levels(price_text, volume_text) -> list[dict]:
    prices = [_float(item) for item in str(price_text or "").split("_")]
    volumes = [_float(item) for item in str(volume_text or "").split("_")]
    rows = []
    for index, price in enumerate(prices):
        if price is None:
            continue
        volume = volumes[index] if index < len(volumes) else None
        rows.append({"level": index + 1, "price": round(price, 2), "volume": volume})
    return rows[:5]


def _sum_volume(levels: list[dict]) -> Optional[float]:
    if not levels:
        return None
    total = sum(float(item.get("volume") or 0) for item in levels)
    return round(total, 2)


def _orderbook_imbalance(bid_total: Optional[float], ask_total: Optional[float]) -> Optional[float]:
    bid = float(bid_total or 0)
    ask = float(ask_total or 0)
    total = bid + ask
    if total <= 0:
        return None
    return round((bid - ask) / total * 100, 2)


def _five_level_status(
    bid_levels: list[dict],
    ask_levels: list[dict],
    price: Optional[float],
    limit_up: Optional[float],
    limit_down: Optional[float],
) -> tuple[str, str]:
    if bid_levels and ask_levels:
        return "available", "公開五檔可用"
    if bid_levels and price is not None and limit_up is not None and price >= limit_up:
        return "limit_up_bid_only", "漲停鎖住，僅見買盤"
    if ask_levels and price is not None and limit_down is not None and price <= limit_down:
        return "limit_down_ask_only", "跌停鎖住，僅見賣盤"
    if bid_levels:
        return "bid_only", "僅有委買盤"
    if ask_levels:
        return "ask_only", "僅有委賣盤"
    return "missing", "五檔資料不足"


def _quote_time(date: str, time_text: str) -> str:
    if not date or not time_text:
        return ""
    date = date.strip()
    time_text = time_text.strip()
    time_digits = "".join(char for char in time_text if char.isdigit())
    if len(date) == 8 and len(time_digits) >= 6:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]} {time_digits[:2]}:{time_digits[2:4]}:{time_digits[4:6]}"
    return f"{date} {time_text}".strip()
