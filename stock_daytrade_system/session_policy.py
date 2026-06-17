from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


SESSION_POLICY_VERSION = "session_policy_v1_time_gated_entry_2026-06-18"


@dataclass(frozen=True)
class EntryTimingPolicyResult:
    entry_status: str
    reason: str
    time_bucket: str


def time_bucket_for_market(moment: Optional[datetime], market: str = "TW") -> str:
    if moment is None:
        return "unknown"
    local_time = moment.replace(tzinfo=None)
    hm = (local_time.hour, local_time.minute)
    if str(market).upper() == "US":
        if hm < (9, 30):
            return "premarket"
        if hm < (10, 0):
            return "us_opening"
        if hm < (15, 0):
            return "us_main"
        if hm < (16, 0):
            return "us_late"
        if hm < (20, 0):
            return "afterhours"
        return "closed"
    if hm < (9, 0):
        return "pre_open"
    if hm < (9, 20):
        return "opening_observation"
    if hm < (10, 30):
        return "main_entry"
    if hm < (11, 30):
        return "pullback_only"
    if hm < (13, 30):
        return "late_avoid"
    return "after_close"


def apply_tw_entry_timing_policy(
    *,
    captured_at: Optional[datetime],
    grade: str,
    entry_status: str,
    above_vwap: bool,
    volume_ratio: float,
    vwap_distance_pct: Optional[float],
    bullish_score: float,
    risk_score: float,
) -> EntryTimingPolicyResult:
    bucket = time_bucket_for_market(captured_at, "TW")
    original = entry_status

    if bucket in {"unknown", "main_entry"}:
        return EntryTimingPolicyResult(entry_status, "", bucket)

    if bucket in {"pre_open", "after_close"}:
        if entry_status in {"executable", "practice_long"}:
            next_status = _waiting_status(above_vwap, volume_ratio, bullish_score)
            return EntryTimingPolicyResult(
                next_status,
                "時段策略：非盤中不直接列為可執行，明日開盤需重新確認 VWAP、量比與突破。",
                bucket,
            )
        return EntryTimingPolicyResult(entry_status, "", bucket)

    if bucket == "opening_observation":
        if entry_status == "executable" and grade == "A" and volume_ratio >= 1.2 and risk_score <= 35:
            return EntryTimingPolicyResult(entry_status, "時段策略：開盤初段僅允許高品質 A 級訊號可執行。", bucket)
        if entry_status in {"executable", "practice_long"}:
            return EntryTimingPolicyResult(
                "wait_breakout" if above_vwap else "wait_vwap",
                "時段策略：開盤前 20 分鐘先觀察，等待突破與量能延續，避免第一波追價。",
                bucket,
            )
        return EntryTimingPolicyResult(entry_status, "", bucket)

    if bucket == "pullback_only":
        if entry_status in {"executable", "practice_long"} and _too_far_from_vwap(vwap_distance_pct, 1.2):
            return EntryTimingPolicyResult(
                "wait_pullback",
                "時段策略：10:30 後不追伸，改等回測 VWAP 不破。",
                bucket,
            )
        return EntryTimingPolicyResult(entry_status, "", bucket)

    if bucket == "late_avoid":
        if entry_status in {"executable", "practice_long"}:
            return EntryTimingPolicyResult(
                "wait_pullback",
                "時段策略：11:30 後避免新追價，只保留回測觀察。",
                bucket,
            )
        return EntryTimingPolicyResult(entry_status, "", bucket)

    if original != entry_status:
        return EntryTimingPolicyResult(entry_status, "時段策略：依盤中時間調整進場狀態。", bucket)
    return EntryTimingPolicyResult(entry_status, "", bucket)


def _waiting_status(above_vwap: bool, volume_ratio: float, bullish_score: float) -> str:
    if not above_vwap and bullish_score >= 55:
        return "wait_vwap"
    if volume_ratio < 1.0 and bullish_score >= 55:
        return "wait_volume"
    return "wait_breakout"


def _too_far_from_vwap(vwap_distance_pct: Optional[float], threshold: float) -> bool:
    return vwap_distance_pct is None or vwap_distance_pct > threshold
