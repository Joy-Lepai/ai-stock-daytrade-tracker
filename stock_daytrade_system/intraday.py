from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.indicators import average_volume


@dataclass(frozen=True)
class OpeningSignal:
    symbol: str
    name: str
    sector: str
    direction: str
    score: float
    last_price: float
    opening_range_high: float
    opening_range_low: float
    vwap: float
    cumulative_volume: float
    volume_ratio: float
    reasons: List[str]


def analyze_opening_confirmation(
    item: WatchSymbol,
    intraday_bars: List[Bar],
    daily_bars: List[Bar],
    opening_bars: int = 3,
    min_volume_ratio: float = 1.2,
) -> Optional[OpeningSignal]:
    if len(intraday_bars) < opening_bars + 1 or len(daily_bars) < 20:
        return None

    opening = intraday_bars[:opening_bars]
    latest = intraday_bars[-1]
    opening_range_high = max(bar.high for bar in opening)
    opening_range_low = min(bar.low for bar in opening)
    opening_price = opening[0].open
    cumulative_volume = sum(bar.volume for bar in intraday_bars)
    vwap = _vwap(intraday_bars)
    avg_daily_volume = average_volume(daily_bars, 20)
    expected_volume = avg_daily_volume * min(len(intraday_bars) * 5 / 270, 1)
    volume_ratio = cumulative_volume / expected_volume if expected_volume else 0.0

    long_score = 0.0
    short_score = 0.0
    long_reasons: List[str] = []
    short_reasons: List[str] = []
    has_volume_confirmation = volume_ratio >= min_volume_ratio

    if latest.close > opening_range_high:
        long_score += 2.0
        long_reasons.append("突破開盤區間高點")
    if latest.close > opening_price:
        long_score += 1.0
        long_reasons.append("現價高於開盤價")
    if has_volume_confirmation:
        long_score += 1.0
        short_score += 1.0
        long_reasons.append(f"量比 {volume_ratio:.2f}x")
        short_reasons.append(f"量比 {volume_ratio:.2f}x")

    if latest.close < opening_range_low:
        short_score += 2.0
        short_reasons.append("跌破開盤區間低點")
    if latest.close < opening_price:
        short_score += 1.0
        short_reasons.append("現價低於開盤價")

    if has_volume_confirmation and long_score >= 3 and long_score >= short_score:
        direction = "做多確認"
        score = long_score
        reasons = long_reasons
    elif has_volume_confirmation and short_score >= 3:
        direction = "做空確認"
        score = short_score
        reasons = short_reasons
    else:
        direction = "觀望"
        score = max(long_score, short_score)
        reasons = ["尚未突破開盤區間或量能不足"]

    return OpeningSignal(
        symbol=item.symbol,
        name=item.name,
        sector=item.sector,
        direction=direction,
        score=round(score, 2),
        last_price=round(latest.close, 2),
        opening_range_high=round(opening_range_high, 2),
        opening_range_low=round(opening_range_low, 2),
        vwap=round(vwap, 2),
        cumulative_volume=round(cumulative_volume, 0),
        volume_ratio=round(volume_ratio, 2),
        reasons=reasons,
    )


def _vwap(bars: List[Bar]) -> float:
    weighted_value = 0.0
    total_volume = 0.0
    fallback_prices: List[float] = []
    for bar in bars:
        typical_price = (bar.high + bar.low + bar.close) / 3
        fallback_prices.append(typical_price)
        if bar.volume <= 0:
            continue
        weighted_value += typical_price * bar.volume
        total_volume += bar.volume
    if total_volume > 0:
        return weighted_value / total_volume
    return sum(fallback_prices) / len(fallback_prices) if fallback_prices else 0.0
