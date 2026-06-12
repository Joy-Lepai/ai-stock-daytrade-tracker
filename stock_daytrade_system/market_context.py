from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from stock_daytrade_system.data import Bar
from stock_daytrade_system.indicators import latest_return
from stock_daytrade_system.taifex import TaifexFutureQuote


@dataclass(frozen=True)
class MarketIndicator:
    group: str
    name: str
    symbol: str
    value: str
    change: str
    status: str
    note: str


MARKET_LABELS = {
    "^GSPC": ("前一日美股 / US previous close", "S&P 500"),
    "^IXIC": ("前一日美股 / US previous close", "Nasdaq"),
    "^DJI": ("前一日美股 / US previous close", "Dow Jones"),
    "^SOX": ("前一日美股 / US previous close", "費半 SOX"),
    "ES=F": ("開盤前美股期貨 / US futures", "S&P 500 期貨 ES"),
    "NQ=F": ("開盤前美股期貨 / US futures", "Nasdaq 期貨 NQ"),
    "^TWII": ("台股背景 / Taiwan market", "加權指數"),
}


def build_market_indicators(
    market_data: Dict[str, List[Bar]],
    taifex_quote: Optional[TaifexFutureQuote],
) -> List[MarketIndicator]:
    indicators: List[MarketIndicator] = []
    for symbol in ["^GSPC", "^IXIC", "^DJI", "^SOX", "ES=F", "NQ=F", "^TWII"]:
        bars = market_data.get(symbol, [])
        if len(bars) < 2:
            continue
        group, name = MARKET_LABELS[symbol]
        one_day = latest_return(bars, 1)
        indicators.append(
            MarketIndicator(
                group=group,
                name=name,
                symbol=symbol,
                value=f"{bars[-1].close:.2f}",
                change=f"{one_day:+.2f}%",
                status=_direction(one_day),
                note=_bar_date_note(bars[-1]),
            )
        )

    if taifex_quote is not None:
        indicators.append(
            MarketIndicator(
                group="開盤前台指期 / TAIFEX TX",
                name=f"臺股期貨 {taifex_quote.contract_month}",
                symbol=taifex_quote.product,
                value=_fmt_number(taifex_quote.last),
                change=_fmt_pct(taifex_quote.change_pct),
                status=_direction(taifex_quote.change_pct or 0),
                note=f"{taifex_quote.trade_date} {taifex_quote.session}，量 {_fmt_number(taifex_quote.volume)}，買/賣 {_fmt_number(taifex_quote.bid)}/{_fmt_number(taifex_quote.ask)}",
            )
        )
    return indicators


def _direction(change_pct: float) -> str:
    if change_pct > 0:
        return "偏多"
    if change_pct < 0:
        return "偏空"
    return "中性"


def _fmt_number(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def _bar_date_note(bar: Bar) -> str:
    return f"資料日 {bar.timestamp.strftime('%Y-%m-%d')}"
