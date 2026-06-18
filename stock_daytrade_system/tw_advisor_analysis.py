from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


TW_ADVISOR_ANALYSIS_VERSION = "tw_advisor_analysis_v1_technical_volume_risk_2026-06-18"


@dataclass(frozen=True)
class TwAdvisorAnalysis:
    version: str
    technical_score: float
    volume_score: float
    chase_risk_score: float
    technical_status: str
    volume_status: str
    chase_risk_status: str
    action_label: str
    action_summary: str
    technical_summary: str
    volume_summary: str
    risk_summary: str
    next_step: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_tw_advisor_analysis(
    *,
    scan: dict,
    candidate: Optional[dict],
    display: dict,
    market_status: str = "",
) -> TwAdvisorAnalysis:
    candidate = candidate or {}
    current_price = _number(display.get("current_price"), scan.get("latest_price"), candidate.get("last_price"))
    vwap = _number(scan.get("vwap"), candidate.get("vwap"))
    volume_ratio = _number(scan.get("volume_ratio"), candidate.get("volume_ratio"), default=0.0) or 0.0
    change_pct = _number(display.get("change_pct"), scan.get("change_pct"), candidate.get("change_pct"), default=0.0) or 0.0
    bullish_score = _number(candidate.get("bullish_score"), default=0.0) or 0.0
    risk_score = _number(candidate.get("risk_score"), scan.get("risk_score"), default=0.0) or 0.0
    confidence_score = _number(candidate.get("confidence_score"), scan.get("confidence_score"), default=0.0) or 0.0
    upper_shadow_pct = _number(candidate.get("upper_shadow_pct"), scan.get("upper_shadow_pct"), default=0.0) or 0.0
    above_vwap = bool(scan.get("above_vwap", candidate.get("above_vwap", False)))
    break_prev_high = bool(scan.get("break_prev_high", candidate.get("break_prev_high", False)))
    break_5d_high = bool(scan.get("break_5d_high", candidate.get("break_5d_high", False)))
    opening_range_high = _number(candidate.get("opening_range_high"))
    opening_range_low = _number(candidate.get("opening_range_low"))
    break_orb = bool(current_price and opening_range_high and current_price >= opening_range_high)
    below_opening_low = bool(current_price and opening_range_low and current_price < opening_range_low)
    vwap_distance = _distance_pct(current_price, vwap)

    technical_score = _technical_score(
        above_vwap=above_vwap,
        break_prev_high=break_prev_high,
        break_5d_high=break_5d_high,
        break_orb=break_orb,
        below_opening_low=below_opening_low,
        bullish_score=bullish_score,
        market_status=market_status,
    )
    volume_score = _volume_score(volume_ratio=volume_ratio, change_pct=change_pct, turnover=_number(scan.get("turnover")))
    chase_risk_score = _chase_risk_score(
        risk_score=risk_score,
        vwap_distance=vwap_distance,
        change_pct=change_pct,
        upper_shadow_pct=upper_shadow_pct,
        volume_ratio=volume_ratio,
        above_vwap=above_vwap,
    )

    action_label = _action_label(
        candidate_label=str(candidate.get("trade_bias_label") or scan.get("trade_bias_label") or "觀察"),
        technical_score=technical_score,
        volume_score=volume_score,
        chase_risk_score=chase_risk_score,
        confidence_score=confidence_score,
        above_vwap=above_vwap,
        break_prev_high=break_prev_high or break_orb,
        below_opening_low=below_opening_low,
        change_pct=change_pct,
        volume_ratio=volume_ratio,
    )
    technical_summary = _technical_summary(above_vwap, break_prev_high, break_5d_high, break_orb, below_opening_low)
    volume_summary = _volume_summary(volume_ratio, change_pct)
    risk_summary = _risk_summary(chase_risk_score, vwap_distance, change_pct, upper_shadow_pct)
    action_summary = _action_summary(action_label, technical_score, volume_score, chase_risk_score, confidence_score)
    next_step = _next_step(action_label, above_vwap, volume_ratio, break_prev_high or break_orb, chase_risk_score)

    return TwAdvisorAnalysis(
        version=TW_ADVISOR_ANALYSIS_VERSION,
        technical_score=round(technical_score, 2),
        volume_score=round(volume_score, 2),
        chase_risk_score=round(chase_risk_score, 2),
        technical_status=_score_status(technical_score, high=75, medium=55, labels=("技術偏強", "技術待確認", "技術偏弱")),
        volume_status=_score_status(volume_score, high=75, medium=55, labels=("量能確認", "量能普通", "量能不足")),
        chase_risk_status=_risk_status(chase_risk_score),
        action_label=action_label,
        action_summary=action_summary,
        technical_summary=technical_summary,
        volume_summary=volume_summary,
        risk_summary=risk_summary,
        next_step=next_step,
    )


def _technical_score(
    *,
    above_vwap: bool,
    break_prev_high: bool,
    break_5d_high: bool,
    break_orb: bool,
    below_opening_low: bool,
    bullish_score: float,
    market_status: str,
) -> float:
    score = 35.0
    if above_vwap:
        score += 20
    else:
        score -= 20
    if break_prev_high:
        score += 15
    if break_5d_high:
        score += 10
    if break_orb:
        score += 15
    if below_opening_low:
        score -= 25
    if bullish_score >= 80:
        score += 12
    elif bullish_score >= 65:
        score += 8
    elif bullish_score < 45:
        score -= 8
    if "偏多" in market_status or "bullish" in market_status.lower():
        score += 5
    if "偏空" in market_status or "bearish" in market_status.lower():
        score -= 15
    return _clamp(score)


def _volume_score(*, volume_ratio: float, change_pct: float, turnover: Optional[float]) -> float:
    score = 35.0
    if volume_ratio >= 1.5:
        score += 35
    elif volume_ratio >= 1.0:
        score += 25
    elif volume_ratio >= 0.8:
        score += 12
    else:
        score -= 15
    if change_pct > 0 and volume_ratio >= 1.0:
        score += 10
    if change_pct > 3 and volume_ratio < 0.8:
        score -= 15
    if turnover and turnover >= 1_000_000_000:
        score += 10
    return _clamp(score)


def _chase_risk_score(
    *,
    risk_score: float,
    vwap_distance: Optional[float],
    change_pct: float,
    upper_shadow_pct: float,
    volume_ratio: float,
    above_vwap: bool,
) -> float:
    score = min(max(risk_score, 0), 80)
    if vwap_distance is not None:
        if vwap_distance > 3:
            score += 20
        elif vwap_distance > 1:
            score += 10
        elif vwap_distance < -0.5:
            score += 12
    if change_pct > 8:
        score += 25
    elif change_pct > 5:
        score += 15
    if upper_shadow_pct >= 1.5:
        score += 15
    if change_pct > 3 and volume_ratio < 0.8:
        score += 12
    if not above_vwap:
        score += 18
    return _clamp(score)


def _action_label(
    *,
    candidate_label: str,
    technical_score: float,
    volume_score: float,
    chase_risk_score: float,
    confidence_score: float,
    above_vwap: bool,
    break_prev_high: bool,
    below_opening_low: bool,
    change_pct: float,
    volume_ratio: float,
) -> str:
    if below_opening_low and not above_vwap and volume_ratio >= 0.8:
        return "賣空"
    if candidate_label in {"買多", "賣空"}:
        return candidate_label
    if (
        technical_score >= 75
        and volume_score >= 65
        and chase_risk_score <= 55
        and confidence_score >= 55
        and above_vwap
        and break_prev_high
    ):
        return "買多"
    if not above_vwap and change_pct <= -1 and volume_ratio >= 1.0:
        return "賣空"
    return "觀察"


def _technical_summary(
    above_vwap: bool,
    break_prev_high: bool,
    break_5d_high: bool,
    break_orb: bool,
    below_opening_low: bool,
) -> str:
    if below_opening_low:
        return "價格跌破開盤區間低點，短線結構轉弱。"
    parts = []
    parts.append("股價站上 VWAP" if above_vwap else "股價尚未站上 VWAP")
    if break_orb:
        parts.append("突破開盤區間高點")
    if break_prev_high:
        parts.append("突破昨日高點")
    if break_5d_high:
        parts.append("突破 5 日高點")
    if len(parts) == 1:
        parts.append("突破條件尚未完整")
    return "，".join(parts) + "。"


def _volume_summary(volume_ratio: float, change_pct: float) -> str:
    if volume_ratio >= 1.5:
        return f"量比 {volume_ratio:.2f}x，短線資金明顯放大。"
    if volume_ratio >= 1.0:
        return f"量比 {volume_ratio:.2f}x，量能基本確認。"
    if change_pct > 3:
        return f"漲幅已放大，但量比 {volume_ratio:.2f}x，需小心價漲量不足。"
    return f"量比 {volume_ratio:.2f}x，量能仍待確認。"


def _risk_summary(chase_risk_score: float, vwap_distance: Optional[float], change_pct: float, upper_shadow_pct: float) -> str:
    reasons = []
    if vwap_distance is not None:
        reasons.append(f"距離 VWAP {vwap_distance:.2f}%")
    if change_pct > 5:
        reasons.append(f"漲幅 {change_pct:.2f}%")
    if upper_shadow_pct >= 1.5:
        reasons.append("上影線偏長")
    if chase_risk_score >= 70:
        return "追價風險偏高，" + ("、".join(reasons) if reasons else "不適合直接追價") + "。"
    if chase_risk_score >= 45:
        return "追價風險中等，" + ("、".join(reasons) if reasons else "建議等待回測確認") + "。"
    return "追價風險相對可控，但仍需設定停損。"


def _action_summary(action_label: str, technical_score: float, volume_score: float, chase_risk_score: float, confidence_score: float) -> str:
    if action_label == "買多":
        return "技術結構、量能與風險條件相對配合，可列入買多觀察。"
    if action_label == "賣空":
        return "價格結構偏弱且量能配合，可列入賣空觀察。"
    if technical_score >= 65 and volume_score < 55:
        return "技術線不差，但量能尚未確認，暫時觀察。"
    if chase_risk_score >= 65:
        return "股價雖有動能，但追價風險偏高，暫時觀察。"
    if confidence_score < 50:
        return "資料或結構信心不足，不列為可執行。"
    return "目前買多與賣空條件都不完整，維持觀察。"


def _next_step(action_label: str, above_vwap: bool, volume_ratio: float, has_breakout: bool, chase_risk_score: float) -> str:
    if action_label == "買多":
        return "先確認停損距離、VWAP 是否守住，再用虛擬交易練習。"
    if action_label == "賣空":
        return "賣空觀察需確認跌破 VWAP 後未快速站回，並設定停損。"
    if not above_vwap:
        return "等待站回 VWAP，或跌破後形成賣空確認。"
    if volume_ratio < 1.0:
        return "等待量比放大到 1.0x 以上。"
    if not has_breakout:
        return "等待突破昨日高點或開盤區間高點。"
    if chase_risk_score >= 65:
        return "等待拉回 VWAP 附近且不跌破。"
    return "持續觀察下一根 K 棒是否延續。"


def _score_status(score: float, *, high: float, medium: float, labels: tuple[str, str, str]) -> str:
    if score >= high:
        return labels[0]
    if score >= medium:
        return labels[1]
    return labels[2]


def _risk_status(score: float) -> str:
    if score >= 70:
        return "追價風險高"
    if score >= 45:
        return "追價風險中等"
    return "追價風險可控"


def _number(*values, default: Optional[float] = None) -> Optional[float]:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _distance_pct(price: Optional[float], base: Optional[float]) -> Optional[float]:
    if price is None or base is None or base <= 0:
        return None
    return (price - base) / base * 100


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
