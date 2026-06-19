from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from typing import Dict, List, Optional

from stock_daytrade_system.resilience import record_source_health, retry_sync


@dataclass(frozen=True)
class TaifexFutureQuote:
    product: str
    contract_month: str
    trade_date: str
    session: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    last: Optional[float]
    change: Optional[float]
    change_pct: Optional[float]
    volume: Optional[float]
    settlement: Optional[float]
    open_interest: Optional[float]
    bid: Optional[float]
    ask: Optional[float]

    def summary(self) -> str:
        change_pct = "n/a" if self.change_pct is None else f"{self.change_pct:+.2f}%"
        last = "n/a" if self.last is None else f"{self.last:.0f}"
        volume = "n/a" if self.volume is None else f"{self.volume:.0f}"
        bid = "n/a" if self.bid is None else f"{self.bid:.0f}"
        ask = "n/a" if self.ask is None else f"{self.ask:.0f}"
        return (
            f"TAIFEX {self.product} {self.contract_month} "
            f"{self.session}: 最後成交 {last}, 漲跌 {change_pct}, "
            f"成交量 {volume}, 買/賣 {bid}/{ask}"
        )


class TaifexDataError(RuntimeError):
    pass


class TaifexClient:
    base_url = "https://www.taifex.com.tw/cht/3/futDailyMarketReport"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def fetch_latest_future_quote(self, commodity_id: str = "TX") -> TaifexFutureQuote:
        html = self._request_page()
        quotes = parse_future_quotes(html)
        matching = [quote for quote in quotes if quote.product == commodity_id]
        if not matching:
            raise TaifexDataError(f"no TAIFEX quote found for {commodity_id}")
        matching.sort(key=lambda quote: (quote.volume or 0), reverse=True)
        return matching[0]

    def fetch_future_quote(
        self,
        query_date: str,
        commodity_id: str = "TX",
        market_code: str = "0",
    ) -> TaifexFutureQuote:
        html = self._request_page(
            {
                "queryType": "2",
                "marketCode": market_code,
                "dateaddcnt": "",
                "commodity_id": commodity_id,
                "commodity_id2": "",
                "queryDate": query_date,
                "MarketCode": market_code,
                "commodity_idt": commodity_id,
                "commodity_id2t": "",
                "commodity_id2t2": "",
            }
        )
        quotes = parse_future_quotes(html)
        matching = [quote for quote in quotes if quote.product == commodity_id]
        if not matching:
            raise TaifexDataError(f"no TAIFEX quote found for {commodity_id} on {query_date}")
        matching.sort(key=lambda quote: (quote.volume or 0), reverse=True)
        return matching[0]

    def _request_page(self, form: Optional[Dict[str, str]] = None) -> str:
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 AI-stock-research/0.1",
                "Accept": "text/html",
            },
            method="POST" if data is not None else "GET",
        )
        def operation() -> bytes:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except Exception as exc:
                raise TaifexDataError(f"failed to fetch TAIFEX data: {exc}") from exc

        raw = retry_sync(
            operation,
            source="taifex",
            operation_name="TAIFEX futures quote",
        )
        record_source_health("taifex", "OK", success_count=1, message="TAIFEX 台指期資料擷取成功。")
        return raw.decode("utf-8", errors="replace")


def parse_future_quotes(html: str) -> List[TaifexFutureQuote]:
    trade_date = _match_text(r"日期：\s*([0-9]{4}/[0-9]{2}/[0-9]{2})", html) or ""
    session = _detect_session(html)
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
    quotes: List[TaifexFutureQuote] = []

    for row in rows:
        cells = [_clean_cell(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)]
        if len(cells) < 15:
            continue
        product = cells[0].strip()
        contract_month = cells[1].strip()
        if not product or "/" in contract_month or contract_month == "到期月份(週別)":
            continue
        if not re.fullmatch(r"[A-Z0-9]+", product):
            continue

        quotes.append(
            TaifexFutureQuote(
                product=product,
                contract_month=contract_month,
                trade_date=trade_date,
                session=session,
                open=_to_float(cells[2]),
                high=_to_float(cells[3]),
                low=_to_float(cells[4]),
                last=_to_float(cells[5]),
                change=_to_float(cells[6]),
                change_pct=_to_float(cells[7].replace("%", "")),
                volume=_to_float(cells[8]),
                settlement=_to_float(cells[9]),
                open_interest=_to_float(cells[10]),
                bid=_to_float(cells[11]),
                ask=_to_float(cells[12]),
            )
        )
    return quotes


def _clean_cell(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    value = value.replace("\xa0", " ")
    value = value.replace("▲", "+").replace("▼", "-")
    return re.sub(r"\s+", " ", value).strip()


def _to_float(value: str) -> Optional[float]:
    cleaned = value.replace(",", "").replace("%", "").strip()
    if cleaned in {"", "-", "--"}:
        return None
    cleaned = cleaned.replace("+", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _match_text(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text)
    if match is None:
        return None
    return match.group(1)


def _detect_session(html: str) -> str:
    if "盤後交易時段行情表" in html:
        return "盤後"
    if "一般交易時段行情表" in html:
        return "日盤"
    return "未知"
