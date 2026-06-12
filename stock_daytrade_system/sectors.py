from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.indicators import latest_return
from stock_daytrade_system.intraday import OpeningSignal


@dataclass(frozen=True)
class SectorStrength:
    sector: str
    member_count: int
    avg_one_day_return: float
    avg_five_day_return: float
    avg_relative_strength: float
    bullish_count: int
    bearish_count: int
    direction: str
    score: float


@dataclass(frozen=True)
class SectorOpeningStrength:
    sector: str
    member_count: int
    confirmed_long_count: int
    confirmed_short_count: int
    watch_count: int
    avg_volume_ratio: float
    direction: str
    score: float


def rank_sector_strength(
    symbols: Iterable[WatchSymbol],
    data: Dict[str, List[Bar]],
    benchmark_bars: List[Bar],
) -> List[SectorStrength]:
    benchmark_five_day = latest_return(benchmark_bars, 5) if benchmark_bars else 0.0
    grouped: Dict[str, List[WatchSymbol]] = {}
    for item in symbols:
        grouped.setdefault(item.sector, []).append(item)

    strengths: List[SectorStrength] = []
    for sector, members in grouped.items():
        one_day_returns: List[float] = []
        five_day_returns: List[float] = []
        bullish_count = 0
        bearish_count = 0

        for member in members:
            bars = data.get(member.symbol, [])
            if len(bars) < 6:
                continue
            one_day = latest_return(bars, 1)
            five_day = latest_return(bars, 5)
            one_day_returns.append(one_day)
            five_day_returns.append(five_day)
            if one_day > 0:
                bullish_count += 1
            elif one_day < 0:
                bearish_count += 1

        if not one_day_returns:
            continue

        avg_one_day = sum(one_day_returns) / len(one_day_returns)
        avg_five_day = sum(five_day_returns) / len(five_day_returns)
        relative_strength = avg_five_day - benchmark_five_day
        score = avg_one_day * 0.7 + relative_strength * 0.5 + (bullish_count - bearish_count) * 0.4
        direction = "強勢" if score >= 1 else "弱勢" if score <= -1 else "中性"
        strengths.append(
            SectorStrength(
                sector=sector,
                member_count=len(one_day_returns),
                avg_one_day_return=round(avg_one_day, 2),
                avg_five_day_return=round(avg_five_day, 2),
                avg_relative_strength=round(relative_strength, 2),
                bullish_count=bullish_count,
                bearish_count=bearish_count,
                direction=direction,
                score=round(score, 2),
            )
        )

    strengths.sort(key=lambda item: item.score, reverse=True)
    return strengths


def rank_opening_sector_strength(signals: Iterable[OpeningSignal]) -> List[SectorOpeningStrength]:
    grouped: Dict[str, List[OpeningSignal]] = {}
    for signal in signals:
        grouped.setdefault(signal.sector, []).append(signal)

    strengths: List[SectorOpeningStrength] = []
    for sector, items in grouped.items():
        confirmed_long = sum(1 for item in items if item.direction == "做多確認")
        confirmed_short = sum(1 for item in items if item.direction == "做空確認")
        watch_count = sum(1 for item in items if item.direction == "觀望")
        avg_volume_ratio = sum(item.volume_ratio for item in items) / len(items)
        score = (confirmed_long - confirmed_short) * 2 + avg_volume_ratio
        direction = "偏多" if confirmed_long > confirmed_short else "偏空" if confirmed_short > confirmed_long else "觀望"
        strengths.append(
            SectorOpeningStrength(
                sector=sector,
                member_count=len(items),
                confirmed_long_count=confirmed_long,
                confirmed_short_count=confirmed_short,
                watch_count=watch_count,
                avg_volume_ratio=round(avg_volume_ratio, 2),
                direction=direction,
                score=round(score, 2),
            )
        )

    strengths.sort(key=lambda item: item.score, reverse=True)
    return strengths
