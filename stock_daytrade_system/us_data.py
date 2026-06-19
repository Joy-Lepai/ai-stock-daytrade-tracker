from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.data import Bar, MarketDataError, YahooChartClient
from stock_daytrade_system.indicators import average_volume, pct_change
from stock_daytrade_system.resilience import record_source_health
from stock_daytrade_system.us_symbols import USSymbolInfo, us_symbol_map, us_watchlist


US_DATA_VERSION = "us_data_yahoo_chart_v1"
US_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class USMarketSnapshot:
    symbol: str
    name_en: str
    name_zh: str
    short_name_zh: str
    sector_en: str
    sector_zh: str
    industry_en: str
    industry_zh: str
    description_zh: str
    is_etf: bool
    latest_price: float
    previous_close: float
    open: float
    high: float
    low: float
    volume: float
    change_pct: float
    vwap: float
    above_vwap: bool
    volume_ratio: float
    premarket_price: Optional[float]
    afterhours_price: Optional[float]
    premarket_high: Optional[float]
    break_premarket_high: bool
    break_previous_high: bool
    break_opening_range_high: bool
    opening_range_high: Optional[float]
    previous_high: Optional[float]
    average_volume: float
    market_cap: Optional[float]
    data_missing: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class USDataStatus:
    source: str
    ok: bool
    success_count: int
    failed_symbols: List[str]
    errors: Dict[str, str]
    last_success_at: Optional[str]
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class USMarketDataBundle:
    snapshots: List[USMarketSnapshot]
    status: USDataStatus


def fetch_us_watchlist_data(
    client: Optional[YahooChartClient] = None,
    symbols: Optional[Iterable[USSymbolInfo]] = None,
    now: Optional[datetime] = None,
) -> USMarketDataBundle:
    client = client or YahooChartClient(timeout=12, pause_seconds=0.05)
    watchlist = list(symbols or us_watchlist())
    updated_at = (now or datetime.now(US_TZ)).astimezone(US_TZ).isoformat(timespec="seconds")
    snapshots: List[USMarketSnapshot] = []
    errors: Dict[str, str] = {}
    for item in watchlist:
        try:
            snapshots.append(_fetch_symbol_snapshot(client, item, now=now))
        except Exception as exc:
            errors[item.symbol] = str(exc)
    status = USDataStatus(
        source="Yahoo Finance chart endpoint",
        ok=not errors,
        success_count=len(snapshots),
        failed_symbols=sorted(errors),
        errors=errors,
        last_success_at=updated_at if snapshots else None,
        updated_at=updated_at,
    )
    record_source_health(
        "us_yahoo_chart",
        "PARTIAL" if errors and snapshots else "ERROR" if errors else "OK",
        success_count=len(snapshots),
        failure_count=len(errors),
        failed_symbols=errors.keys(),
        error="; ".join(f"{symbol}: {error}" for symbol, error in sorted(errors.items())[:5]),
        message="美股 Yahoo 部分標的擷取失敗，已降級顯示。" if errors else "美股 Yahoo watchlist 擷取成功。",
    )
    return USMarketDataBundle(snapshots=snapshots, status=status)


def _fetch_symbol_snapshot(
    client: YahooChartClient,
    info: USSymbolInfo,
    now: Optional[datetime] = None,
) -> USMarketSnapshot:
    intraday_bars, meta = client.fetch_chart_with_meta(
        info.symbol,
        range_="1d",
        interval="5m",
        include_prepost=True,
    )
    daily_bars = client.fetch_daily(info.symbol, range_="3mo")
    if not intraday_bars:
        raise MarketDataError(f"{info.symbol} 無盤中資料")
    if len(daily_bars) < 2:
        raise MarketDataError(f"{info.symbol} 日線資料不足")

    latest = intraday_bars[-1]
    previous = daily_bars[-2]
    latest_price = float(latest.close)
    previous_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or previous.close)
    regular_bars = _regular_bars(intraday_bars)
    active_bars = regular_bars or intraday_bars
    premarket_bars = _premarket_bars(intraday_bars)
    afterhours_bars = _afterhours_bars(intraday_bars)
    opening_bars = regular_bars[:6]
    opening_range_high = max((bar.high for bar in opening_bars), default=None)
    premarket_high = max((bar.high for bar in premarket_bars), default=None)
    vwap = _vwap(active_bars)
    avg_volume = average_volume(daily_bars[:-1], 20)
    cumulative_volume = sum(bar.volume for bar in active_bars)
    expected_volume = avg_volume * min(max(len(active_bars), 1) * 5 / 390, 1)
    volume_ratio = cumulative_volume / expected_volume if expected_volume else 0.0
    market_cap = _optional_float(meta.get("marketCap"))
    data_missing = []
    if market_cap is None:
        data_missing.append("market_cap")
    if premarket_high is None:
        data_missing.append("premarket_high")

    return USMarketSnapshot(
        symbol=info.symbol,
        name_en=info.name_en,
        name_zh=info.name_zh,
        short_name_zh=info.short_name_zh,
        sector_en=info.sector_en,
        sector_zh=info.sector_zh,
        industry_en=info.industry_en,
        industry_zh=info.industry_zh,
        description_zh=info.description_zh,
        is_etf=info.is_etf,
        latest_price=round(latest_price, 2),
        previous_close=round(previous_close, 2),
        open=round(active_bars[0].open, 2),
        high=round(max(bar.high for bar in active_bars), 2),
        low=round(min(bar.low for bar in active_bars), 2),
        volume=round(cumulative_volume, 0),
        change_pct=round(pct_change(latest_price, previous_close), 2),
        vwap=round(vwap, 2),
        above_vwap=latest_price >= vwap if vwap else False,
        volume_ratio=round(volume_ratio, 2),
        premarket_price=round(premarket_bars[-1].close, 2) if premarket_bars else None,
        afterhours_price=round(afterhours_bars[-1].close, 2) if afterhours_bars else None,
        premarket_high=round(premarket_high, 2) if premarket_high is not None else None,
        break_premarket_high=bool(premarket_high is not None and latest_price > premarket_high),
        break_previous_high=latest_price > previous.high,
        break_opening_range_high=bool(opening_range_high is not None and latest_price > opening_range_high),
        opening_range_high=round(opening_range_high, 2) if opening_range_high is not None else None,
        previous_high=round(previous.high, 2),
        average_volume=round(avg_volume, 0),
        market_cap=market_cap,
        data_missing=data_missing,
    )


def index_environment(snapshots: Iterable[USMarketSnapshot]) -> dict:
    by_symbol = {item.symbol: item for item in snapshots}
    qqq = by_symbol.get("QQQ")
    spy = by_symbol.get("SPY")
    qqq_change = qqq.change_pct if qqq else 0.0
    spy_change = spy.change_pct if spy else 0.0
    if qqq_change <= -1.0 and spy_change <= -0.8:
        status = "bearish"
        text = "QQQ / SPY 同步偏弱"
    elif qqq_change >= 0.5 and spy_change >= 0.3:
        status = "bullish"
        text = "QQQ / SPY 同步偏多"
    else:
        status = "neutral"
        text = "QQQ / SPY 中性震盪"
    return {
        "market_status": status,
        "status_text": text,
        "qqq_change_pct": qqq_change,
        "spy_change_pct": spy_change,
    }


def _regular_bars(bars: List[Bar]) -> List[Bar]:
    return [bar for bar in bars if time(9, 30) <= _ny_time(bar).time() < time(16, 0)]


def _premarket_bars(bars: List[Bar]) -> List[Bar]:
    return [bar for bar in bars if time(4, 0) <= _ny_time(bar).time() < time(9, 30)]


def _afterhours_bars(bars: List[Bar]) -> List[Bar]:
    return [bar for bar in bars if time(16, 0) <= _ny_time(bar).time() < time(20, 0)]


def _ny_time(bar: Bar) -> datetime:
    local_tz = datetime.now().astimezone().tzinfo
    source = bar.timestamp.replace(tzinfo=local_tz)
    return source.astimezone(US_TZ)


def _vwap(bars: List[Bar]) -> float:
    weighted_value = 0.0
    total_volume = 0.0
    fallback_prices = []
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3
        fallback_prices.append(typical)
        if bar.volume <= 0:
            continue
        weighted_value += typical * bar.volume
        total_volume += bar.volume
    if total_volume:
        return weighted_value / total_volume
    return sum(fallback_prices) / len(fallback_prices) if fallback_prices else 0.0


def _optional_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
