from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


INSTITUTIONAL_CONTEXT_VERSION = "institutional_context_v1_cmoney_background_2026-06-20"


@dataclass(frozen=True)
class InstitutionalContext:
    foreign_buy_sell: Optional[float]
    investment_trust_buy_sell: Optional[float]
    dealer_buy_sell: Optional[float]
    institutional_total_buy_sell: Optional[float]
    foreign_3d_sum: Optional[float]
    foreign_5d_sum: Optional[float]
    investment_trust_3d_sum: Optional[float]
    investment_trust_5d_sum: Optional[float]
    dealer_3d_sum: Optional[float]
    dealer_5d_sum: Optional[float]
    institutional_trend: str
    institutional_label: str
    institutional_reason: str
    institutional_data_date: str
    institutional_data_status: str
    source: str
    can_upgrade_signal: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_institutional_context(ranking) -> InstitutionalContext:
    if ranking is None:
        return InstitutionalContext(
            foreign_buy_sell=None,
            investment_trust_buy_sell=None,
            dealer_buy_sell=None,
            institutional_total_buy_sell=None,
            foreign_3d_sum=None,
            foreign_5d_sum=None,
            investment_trust_3d_sum=None,
            investment_trust_5d_sum=None,
            dealer_3d_sum=None,
            dealer_5d_sum=None,
            institutional_trend="unknown",
            institutional_label="籌碼資料不足",
            institutional_reason="目前沒有對應的法人排行資料；籌碼背景僅供參考，不影響強烈買多條件。",
            institutional_data_date="",
            institutional_data_status="missing",
            source="CMoney institutional leaderboard",
        )
    foreign = float(getattr(ranking, "foreign_buy_million", 0) or 0)
    trust = float(getattr(ranking, "investment_buy_million", 0) or 0)
    dealer = float(getattr(ranking, "dealers_buy_million", 0) or 0)
    total = float(getattr(ranking, "total_buy_million", 0) or 0)
    positives = sum(1 for value in (foreign, trust, dealer) if value > 0)
    negatives = sum(1 for value in (foreign, trust, dealer) if value < 0)
    if total > 0 and positives >= 2 and negatives == 0:
        trend = "bullish"
        label = "籌碼偏多"
        reason = "前一交易日三大法人合計買超，且主要法人方向偏一致；只能作為背景支持。"
    elif total < 0 and negatives >= 2:
        trend = "bearish"
        label = "籌碼偏弱"
        reason = "前一交易日三大法人合計賣超或主要法人偏賣，需保守看待。"
    elif positives and negatives:
        trend = "mixed"
        label = "籌碼分歧"
        reason = "外資、投信或自營商方向不一致，籌碼僅作背景參考。"
    else:
        trend = "neutral"
        label = "籌碼中性"
        reason = "法人買賣方向沒有明顯一致性，不能作為進場依據。"
    return InstitutionalContext(
        foreign_buy_sell=round(foreign, 2),
        investment_trust_buy_sell=round(trust, 2),
        dealer_buy_sell=round(dealer, 2),
        institutional_total_buy_sell=round(total, 2),
        foreign_3d_sum=None,
        foreign_5d_sum=None,
        investment_trust_3d_sum=None,
        investment_trust_5d_sum=None,
        dealer_3d_sum=None,
        dealer_5d_sum=None,
        institutional_trend=trend,
        institutional_label=label,
        institutional_reason=reason + " 目前資料源只提供近一日排行，3日 / 5日趨勢暫列資料不足。",
        institutional_data_date=str(getattr(ranking, "date", "") or ""),
        institutional_data_status="partial",
        source="CMoney institutional leaderboard",
    )
