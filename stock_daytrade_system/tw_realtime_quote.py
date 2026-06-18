from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Optional


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
                    return quote
                errors.append(quote.error or "price unavailable")
            except Exception as exc:  # pragma: no cover - network best effort
                errors.append(str(exc))
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
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
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
    return TwRealtimeQuote(
        symbol=_normalize_symbol(symbol),
        name=str(row.get("n") or symbol),
        price=round(price, 2) if price is not None else None,
        previous_close=round(previous_close, 2) if previous_close is not None else None,
        change_pct=round(change_pct, 2) if change_pct is not None else None,
        quote_time=quote_time,
        status="ok" if price is not None else "partial",
        error="" if price is not None else "current price unavailable",
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


def _quote_time(date: str, time_text: str) -> str:
    if not date or not time_text:
        return ""
    date = date.strip()
    time_text = time_text.strip()
    time_digits = "".join(char for char in time_text if char.isdigit())
    if len(date) == 8 and len(time_digits) >= 6:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]} {time_digits[:2]}:{time_digits[2:4]}:{time_digits[4:6]}"
    return f"{date} {time_text}".strip()
