from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from stock_daytrade_system.labels import sector_label


SECTOR_CONTEXT_VERSION = "sector_context_v1_background_2026-06-20"


@dataclass(frozen=True)
class SectorContext:
    industry: str
    industry_label: str
    theme_tags: list[str]
    sector_strength_score: Optional[float]
    sector_rank: Optional[int]
    sector_advancers_count: Optional[int]
    sector_decliners_count: Optional[int]
    sector_volume_ratio_avg: Optional[float]
    sector_top_symbols: list[str]
    is_sector_leader: bool
    is_sector_lagging: bool
    sector_status: str
    sector_status_label: str
    sector_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_sector_context_map(sector_strengths: Iterable[object], candidates: Iterable[object] = ()) -> dict[str, SectorContext]:
    strengths = list(sector_strengths or [])
    ranked = {item.sector: index + 1 for index, item in enumerate(sorted(strengths, key=lambda row: row.score, reverse=True))}
    candidates_by_sector: dict[str, list[object]] = {}
    for item in candidates or []:
        candidates_by_sector.setdefault(str(getattr(item, "sector", "")), []).append(item)
    result: dict[str, SectorContext] = {}
    for item in strengths:
        sector = str(getattr(item, "sector", ""))
        peers = sorted(
            candidates_by_sector.get(sector, []),
            key=lambda row: (
                -float(getattr(row, "bullish_score", 0) or 0),
                float(getattr(row, "risk_score", 0) or 0),
            ),
        )
        result[sector] = evaluate_sector_context(item, rank=ranked.get(sector), peers=peers)
    return result


def evaluate_sector_context(strength: Optional[object], *, rank: Optional[int] = None, peers: Iterable[object] = ()) -> SectorContext:
    if strength is None:
        return SectorContext(
            industry="",
            industry_label="暫無族群資料",
            theme_tags=[],
            sector_strength_score=None,
            sector_rank=None,
            sector_advancers_count=None,
            sector_decliners_count=None,
            sector_volume_ratio_avg=None,
            sector_top_symbols=[],
            is_sector_leader=False,
            is_sector_lagging=False,
            sector_status="unknown",
            sector_status_label="暫無族群資料",
            sector_reason="目前沒有足夠同族群資料；不可因族群未知而提高做多層級。",
        )
    score = float(getattr(strength, "score", 0) or 0)
    direction = str(getattr(strength, "direction", "") or "")
    sector = str(getattr(strength, "sector", "") or "")
    if direction == "強勢" or score >= 1:
        status = "strong"
        label = "族群偏強"
        reason = "同族群相對強勢，若個股同時符合 VWAP、量比、突破與風控，可作背景支持。"
    elif direction == "弱勢" or score <= -1:
        status = "weak"
        label = "族群偏弱"
        reason = "同族群偏弱，個股若出現做多訊號仍需保守，不可直接追價。"
    else:
        status = "neutral"
        label = "族群中性"
        reason = "同族群沒有明顯同步轉強，仍以個股 VWAP、量比與突破為主。"
    peers = list(peers or [])
    top_symbols = [f"{getattr(item, 'symbol', '')}｜{getattr(item, 'name', '')}" for item in peers[:5]]
    avg_volume_ratio = None
    volumes = [float(getattr(item, "volume_ratio", 0) or 0) for item in peers if getattr(item, "volume_ratio", None) is not None]
    if volumes:
        avg_volume_ratio = round(sum(volumes) / len(volumes), 2)
    return SectorContext(
        industry=sector,
        industry_label=sector_label(sector),
        theme_tags=[sector_label(sector)] if sector else [],
        sector_strength_score=round(score, 2),
        sector_rank=rank,
        sector_advancers_count=int(getattr(strength, "bullish_count", 0) or 0),
        sector_decliners_count=int(getattr(strength, "bearish_count", 0) or 0),
        sector_volume_ratio_avg=avg_volume_ratio,
        sector_top_symbols=top_symbols,
        is_sector_leader=bool(rank is not None and rank <= 3 and status == "strong"),
        is_sector_lagging=status == "weak",
        sector_status=status,
        sector_status_label=label,
        sector_reason=reason,
    )
