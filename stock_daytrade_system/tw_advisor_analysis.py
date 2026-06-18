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
    action_plan: dict
    key_levels: list[dict]

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
    previous_high = _number(candidate.get("previous_high"))
    high_5d = _number(candidate.get("high_5d"))
    high_10d = _number(candidate.get("high_10d"))
    trigger_price = _number(candidate.get("trigger_price"))
    stop_loss = _number(candidate.get("stop_loss"))
    target_price = _number(candidate.get("target_price"))
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
    action_plan = _action_plan(
        action_label=action_label,
        current_price=current_price,
        vwap=vwap,
        volume_ratio=volume_ratio,
        break_prev_high=break_prev_high,
        break_orb=break_orb,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        previous_high=previous_high,
        trigger_price=trigger_price,
        stop_loss=stop_loss,
        target_price=target_price,
        chase_risk_score=chase_risk_score,
    )
    key_levels = _key_levels(
        vwap=vwap,
        previous_high=previous_high,
        high_5d=high_5d,
        high_10d=high_10d,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        trigger_price=trigger_price,
        stop_loss=action_plan.get("stop_loss"),
        target_price=action_plan.get("target_price"),
    )

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
        action_plan=action_plan,
        key_levels=key_levels,
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


def _action_plan(
    *,
    action_label: str,
    current_price: Optional[float],
    vwap: Optional[float],
    volume_ratio: float,
    break_prev_high: bool,
    break_orb: bool,
    opening_range_high: Optional[float],
    opening_range_low: Optional[float],
    previous_high: Optional[float],
    trigger_price: Optional[float],
    stop_loss: Optional[float],
    target_price: Optional[float],
    chase_risk_score: float,
) -> dict:
    long_trigger = _max_price(trigger_price, previous_high, opening_range_high, current_price if break_prev_high or break_orb else None)
    short_trigger = _min_price(opening_range_low, vwap)
    entry_reference = current_price
    if action_label == "買多":
        plan_stop = stop_loss or _pct(current_price, -1.0) or _pct(vwap, -0.3)
        plan_target = _valid_long_target(target_price, entry_reference) or _pct(current_price, 2.0)
        return _plan_dict(
            action_label=action_label,
            trigger_condition=f"站穩 { _fmt(long_trigger) }，且量比維持 1.0x 以上。",
            entry_reference=entry_reference,
            stop_loss=plan_stop,
            target_price=plan_target,
            wait_condition="若拉回 VWAP 不破，可用虛擬交易練習分批觀察。",
            invalidation_condition="跌破 VWAP 或量能快速萎縮，取消買多觀察。",
            no_chase_reason="若距離 VWAP 超過 3% 或出現長上影，不直接追價。",
        )
    if action_label == "賣空":
        plan_stop = _max_price(vwap, _pct(current_price, 1.0))
        plan_target = _pct(current_price, -2.0)
        return _plan_dict(
            action_label=action_label,
            trigger_condition=f"跌破 { _fmt(short_trigger) } 後未快速站回。",
            entry_reference=entry_reference,
            stop_loss=plan_stop,
            target_price=plan_target,
            wait_condition="等待跌破 VWAP 或開盤區間低點後，確認反彈無力。",
            invalidation_condition="重新站回 VWAP 且量能放大，取消賣空觀察。",
            no_chase_reason="若已急跌過深，不在低檔追空。",
        )
    if chase_risk_score >= 65:
        wait_condition = f"等待拉回 VWAP {_fmt(vwap)} 附近且不跌破，或量比重新放大。"
        no_chase_reason = "追價風險偏高，現價不適合直接追。"
        entry_reference = vwap or current_price
    elif volume_ratio < 1.0:
        wait_condition = "等待量比放大到 1.0x 以上。"
        no_chase_reason = "量能尚未確認，避免只看漲幅追價。"
    elif not (break_prev_high or break_orb):
        wait_condition = "等待突破昨日高點或開盤區間高點。"
        no_chase_reason = "尚未完成突破，不急著進場。"
    else:
        wait_condition = "等待下一根 K 棒延續，或回測 VWAP 不破。"
        no_chase_reason = "目前訊號尚未達可執行標準。"
    observation_stop = _pct(entry_reference, -1.0) if chase_risk_score >= 65 else _valid_long_stop(stop_loss, entry_reference) or _pct(entry_reference, -1.0)
    observation_target = (
        _max_price(current_price, _pct(entry_reference, 2.0))
        if chase_risk_score >= 65
        else _valid_long_target(target_price, entry_reference) or _max_price(current_price, _pct(entry_reference, 2.0))
    )
    return _plan_dict(
        action_label=action_label,
        trigger_condition="尚未達成可執行觸發條件。",
        entry_reference=entry_reference,
        stop_loss=observation_stop,
        target_price=observation_target,
        wait_condition=wait_condition,
        invalidation_condition="跌破 VWAP、風險分數續升或出現假突破，維持觀察不進場。",
        no_chase_reason=no_chase_reason,
    )


def _plan_dict(
    *,
    action_label: str,
    trigger_condition: str,
    entry_reference: Optional[float],
    stop_loss: Optional[float],
    target_price: Optional[float],
    wait_condition: str,
    invalidation_condition: str,
    no_chase_reason: str,
) -> dict:
    risk_reward = _risk_reward(entry_reference, stop_loss, target_price)
    return {
        "state": action_label,
        "trigger_condition": trigger_condition,
        "entry_reference": _round(entry_reference),
        "stop_loss": _round(stop_loss),
        "target_price": _round(target_price),
        "risk_reward_ratio": _round(risk_reward),
        "wait_condition": wait_condition,
        "invalidation_condition": invalidation_condition,
        "no_chase_reason": no_chase_reason,
        "plan_summary": f"狀態為「{action_label}」。先看觸發條件，再確認停損與風險報酬比。",
    }


def _key_levels(
    *,
    vwap: Optional[float],
    previous_high: Optional[float],
    high_5d: Optional[float],
    high_10d: Optional[float],
    opening_range_high: Optional[float],
    opening_range_low: Optional[float],
    trigger_price: Optional[float],
    stop_loss: Optional[float],
    target_price: Optional[float],
) -> list[dict]:
    rows = [
        ("VWAP", vwap, "盤中均價線"),
        ("昨日高點", previous_high, "突破強弱分界"),
        ("5 日高點", high_5d, "短線壓力"),
        ("10 日高點", high_10d, "較大壓力"),
        ("開盤區間高點", opening_range_high, "ORB 多方觸發"),
        ("開盤區間低點", opening_range_low, "跌破轉弱警戒"),
        ("模型觸發價", trigger_price, "原模型參考"),
        ("停損價", stop_loss, "風控價"),
        ("停利價", target_price, "目標價"),
    ]
    return [{"label": label, "value": _round(value), "note": note} for label, value, note in rows if value is not None]


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


def _risk_reward(entry: Optional[float], stop_loss: Optional[float], target_price: Optional[float]) -> Optional[float]:
    if entry is None or stop_loss is None or target_price is None:
        return None
    risk = abs(entry - stop_loss)
    reward = abs(target_price - entry)
    if risk <= 0:
        return None
    return reward / risk


def _valid_long_stop(value: Optional[float], entry: Optional[float]) -> Optional[float]:
    if value is None or entry is None:
        return None
    return value if value < entry else None


def _valid_long_target(value: Optional[float], entry: Optional[float]) -> Optional[float]:
    if value is None or entry is None:
        return None
    return value if value > entry else None


def _pct(value: Optional[float], pct: float) -> Optional[float]:
    if value is None:
        return None
    return value * (1 + pct / 100)


def _max_price(*values: Optional[float]) -> Optional[float]:
    rows = [value for value in values if value is not None]
    return max(rows) if rows else None


def _min_price(*values: Optional[float]) -> Optional[float]:
    rows = [value for value in values if value is not None]
    return min(rows) if rows else None


def _round(value: Optional[float]) -> Optional[float]:
    return round(value, 2) if value is not None else None


def _fmt(value: Optional[float]) -> str:
    return f"{value:.2f}" if value is not None else "關鍵價"


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
