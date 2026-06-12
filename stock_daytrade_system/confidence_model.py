from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from stock_daytrade_system.confidence_config import DEFAULT_CONFIDENCE_CONFIG, ConfidenceConfig


CONFIDENCE_MODEL_VERSION = "confidence_model_v1_quality_conflicts"


@dataclass(frozen=True)
class IndicatorConflict:
    code: str
    message: str


@dataclass(frozen=True)
class ConfidenceResult:
    confidence_score: float
    confidence_level: str
    confidence_level_label: str
    conflicts: list[dict]
    conflicts_count: int
    conflict_summary: str
    confidence_summary: str
    original_entry_status: str
    adjusted_entry_status: str
    confidence_adjustment_reason: str


def evaluate_signal(
    *,
    latest_price: Optional[float],
    volume: Optional[float],
    vwap: Optional[float],
    above_vwap: bool,
    volume_ratio: Optional[float],
    break_prev_high: bool = False,
    break_orb: bool = False,
    break_5d_high: bool = False,
    higher_high: bool = False,
    higher_low: bool = False,
    distance_to_vwap_pct: Optional[float] = None,
    risk_score: float = 0.0,
    bullish_score: float = 0.0,
    entry_status: str = "",
    market_status: str = "neutral",
    sector_strength: Optional[float | str] = None,
    long_upper_shadow: bool = False,
    failed_breakout: bool = False,
    data_status: str = "ok",
    data_errors: Optional[Iterable[str]] = None,
    is_stale: bool = False,
    config: ConfidenceConfig = DEFAULT_CONFIDENCE_CONFIG,
) -> ConfidenceResult:
    score = 0.0
    missing: list[str] = []
    errors = list(data_errors or [])

    if _present(latest_price):
        score += 10
    else:
        missing.append("最新價")
    if _present(volume):
        score += 10
    else:
        missing.append("成交量")
    if _present(vwap):
        score += 10
    else:
        missing.append("VWAP")
    if break_prev_high is not None or break_5d_high is not None:
        score += 10
    else:
        missing.append("前高")
    if data_status == "ok" and not errors:
        score += 10
    if not missing and not errors:
        score += 10
    if missing:
        score -= config.missing_data_penalty
    if is_stale:
        score -= config.stale_data_penalty
    if data_status == "partial":
        score -= 10

    vol_ratio = float(volume_ratio or 0)
    if above_vwap:
        score += 10
    else:
        score -= config.below_vwap_penalty
    if vol_ratio >= 1.5:
        score += 15
    elif vol_ratio >= 1.0:
        score += 10
    if break_prev_high or break_orb:
        score += 10
    if higher_high and higher_low:
        score += 10
    elif higher_high or higher_low:
        score += 5
    if _market_bullish_or_neutral(market_status):
        score += 10
    else:
        score -= 20
    if _sector_strong(sector_strength):
        score += 10

    conflicts = _indicator_conflicts(
        bullish_score=bullish_score,
        risk_score=risk_score,
        above_vwap=above_vwap,
        volume_ratio=vol_ratio,
        break_prev_high=break_prev_high,
        break_orb=break_orb,
        distance_to_vwap_pct=distance_to_vwap_pct,
        entry_status=entry_status,
        market_status=market_status,
        long_upper_shadow=long_upper_shadow,
        failed_breakout=failed_breakout,
        missing=missing,
    )
    score -= min(len(conflicts) * config.conflict_penalty, 30)
    if distance_to_vwap_pct is not None and distance_to_vwap_pct > 3:
        score -= 15
    if risk_score > 60:
        score -= 20
    if entry_status == "high_risk":
        score -= config.high_risk_penalty
    if failed_breakout:
        score -= 20
    if long_upper_shadow:
        score -= 15

    if entry_status == "high_risk" or risk_score >= 60:
        score = min(score, 65)
    if not above_vwap or entry_status == "avoid":
        score = min(score, 60)
    if missing:
        score = min(score, 55)

    score = round(max(0, min(100, score)), 2)
    level = confidence_level(score)
    adjusted, reason = adjusted_entry_status(entry_status, score, conflicts, above_vwap, config)
    conflict_dicts = [asdict(item) for item in conflicts]
    return ConfidenceResult(
        confidence_score=score,
        confidence_level=level,
        confidence_level_label=confidence_level_label(level),
        conflicts=conflict_dicts,
        conflicts_count=len(conflicts),
        conflict_summary=_conflict_summary(conflicts),
        confidence_summary=_confidence_summary(score, level, conflicts, adjusted, above_vwap, vol_ratio),
        original_entry_status=entry_status,
        adjusted_entry_status=adjusted,
        confidence_adjustment_reason=reason,
    )


def confidence_level(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "low"
    return "unreliable"


def confidence_level_label(level: str) -> str:
    return {
        "high": "高信心",
        "medium": "中等信心",
        "low": "低信心",
        "unreliable": "資料不足 / 不可信",
    }.get(level, "資料不足 / 不可信")


def adjusted_entry_status(
    entry_status: str,
    score: float,
    conflicts: list[IndicatorConflict],
    above_vwap: bool,
    config: ConfidenceConfig = DEFAULT_CONFIDENCE_CONFIG,
) -> tuple[str, str]:
    if score < config.unreliable_below:
        return "avoid", "信心分數低於不可信門檻，暫不列為可執行。"
    severe_codes = {item.code for item in conflicts} & {
        "high_risk_executable",
        "breakout_below_vwap",
        "missing_data_signal",
    }
    if entry_status == "executable":
        if not above_vwap:
            return "wait_vwap", "原始訊號可執行，但尚未站上 VWAP。"
        if score < config.block_executable_below:
            return "wait_breakout", "條件看似偏多，但資料或結構信心不足，不列為可執行。"
        if severe_codes:
            return "high_risk", "訊號存在重大衝突，先降為高風險觀察。"
        if score < config.executable_min_confidence:
            return entry_status, "信心分數未達高門檻，暫保留原始可執行但需謹慎。"
    return entry_status, ""


def _indicator_conflicts(
    *,
    bullish_score: float,
    risk_score: float,
    above_vwap: bool,
    volume_ratio: float,
    break_prev_high: bool,
    break_orb: bool,
    distance_to_vwap_pct: Optional[float],
    entry_status: str,
    market_status: str,
    long_upper_shadow: bool,
    failed_breakout: bool,
    missing: list[str],
) -> list[IndicatorConflict]:
    conflicts: list[IndicatorConflict] = []
    if (break_prev_high or break_orb) and not above_vwap:
        conflicts.append(IndicatorConflict("breakout_below_vwap", "價格有突破訊號，但尚未站上 VWAP，結構信心不足。"))
    if bullish_score >= 65 and volume_ratio < 1.0:
        conflicts.append(IndicatorConflict("bullish_low_volume", "多方分數偏高，但量比不足，短線資金尚未確認。"))
    if above_vwap and risk_score > 60:
        conflicts.append(IndicatorConflict("above_vwap_high_risk", "股價站上 VWAP，但風險分數偏高。"))
    if volume_ratio >= 1.5 and distance_to_vwap_pct is not None and distance_to_vwap_pct > 3:
        conflicts.append(IndicatorConflict("volume_extended_vwap", "量能放大，但距離 VWAP 過遠，追價風險提高。"))
    if not _market_bullish_or_neutral(market_status) and bullish_score >= 55:
        conflicts.append(IndicatorConflict("bullish_weak_market", "個股偏多，但大盤偏弱，訊號可信度降低。"))
    if missing:
        conflicts.append(IndicatorConflict("missing_data_signal", f"分數偏多，但核心資料缺漏：{'、'.join(missing)}。"))
    if entry_status == "executable" and (risk_score > 60 or long_upper_shadow or failed_breakout):
        conflicts.append(IndicatorConflict("high_risk_executable", "high_risk 條件卻接近可執行，需先降級觀察。"))
    if failed_breakout:
        conflicts.append(IndicatorConflict("failed_breakout", "突破後未能站穩，可能是假突破。"))
    if long_upper_shadow:
        conflicts.append(IndicatorConflict("long_upper_shadow", "出現長上影線，追價風險提高。"))
    return conflicts


def _confidence_summary(
    score: float,
    level: str,
    conflicts: list[IndicatorConflict],
    adjusted_entry_status_value: str,
    above_vwap: bool,
    volume_ratio: float,
) -> str:
    label = confidence_level_label(level)
    if conflicts:
        return f"此訊號為{label}，存在指標衝突：{conflicts[0].message} 建議先以 {adjusted_entry_status_value} 追蹤。"
    if level == "high":
        return "此訊號信心偏高。股價站上 VWAP，量能與結構條件相對完整，可列為重點觀察。"
    if level == "medium":
        if not above_vwap:
            return "此訊號為中等信心，但尚未站上 VWAP，需等待均價線轉強。"
        if volume_ratio < 1.0:
            return "此訊號為中等信心，結構尚可但量能仍需確認。"
        return "此訊號為中等信心，條件尚可但仍需等待更多盤中確認。"
    if level == "low":
        return "此訊號信心不足。條件看似偏多，但資料或結構尚未完整，不列為優先可執行。"
    return "此訊號資料不足 / 不可信。核心資料或結構條件不足，暫不列為可執行。"


def _conflict_summary(conflicts: list[IndicatorConflict]) -> str:
    if not conflicts:
        return "無明顯衝突"
    return conflicts[0].message


def _present(value: Optional[float]) -> bool:
    return value is not None and value > 0


def _market_bullish_or_neutral(value: str) -> bool:
    return value in {"bullish", "neutral", "偏多", "中性", "mixed"}


def _sector_strong(value: Optional[float | str]) -> bool:
    if isinstance(value, str):
        return value in {"strong", "強勢", "偏強"}
    return bool(value is not None and float(value) >= 2)
