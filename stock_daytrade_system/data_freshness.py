from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


DATA_FRESHNESS_VERSION = "data_freshness_v1_last_known_price_2026-06-19"
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class DataFreshness:
    state: str
    label: str
    badge: str
    message: str
    age_minutes: Optional[float]
    is_live: bool
    is_delayed: bool
    is_stale: bool
    uses_last_known: bool
    can_use_for_daytrade: bool

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["version"] = DATA_FRESHNESS_VERSION
        return payload


def evaluate_data_freshness(
    *,
    now: datetime,
    latest_at: Optional[datetime],
    source_failed: bool = False,
    partial: bool = False,
    is_market_open: bool = False,
    live_after_seconds: int = 90,
    delayed_after_seconds: int = 5 * 60,
    stale_after_seconds: int = 15 * 60,
) -> DataFreshness:
    local_now = _localize(now)
    local_latest = _localize(latest_at) if latest_at else None
    age_seconds: Optional[float] = None
    if local_latest:
        age_seconds = max((local_now - local_latest).total_seconds(), 0.0)
    age_minutes = round(age_seconds / 60, 1) if age_seconds is not None else None

    if source_failed and local_latest is None:
        return DataFreshness(
            state="missing",
            label="缺資料",
            badge="資料缺漏",
            message="目前沒有可用價格，不能產生有效當沖判斷。",
            age_minutes=None,
            is_live=False,
            is_delayed=False,
            is_stale=True,
            uses_last_known=False,
            can_use_for_daytrade=False,
        )
    if local_latest is None:
        return DataFreshness(
            state="missing",
            label="缺資料",
            badge="資料缺漏",
            message="尚未取得最新資料，僅供觀察。",
            age_minutes=None,
            is_live=False,
            is_delayed=False,
            is_stale=True,
            uses_last_known=False,
            can_use_for_daytrade=False,
        )
    if age_seconds is not None and age_seconds <= live_after_seconds and not source_failed:
        label = "即時"
        return DataFreshness(
            state="live",
            label=label,
            badge="Live",
            message="資料更新正常，可作為盤中判斷來源之一。",
            age_minutes=age_minutes,
            is_live=True,
            is_delayed=False,
            is_stale=False,
            uses_last_known=False,
            can_use_for_daytrade=True,
        )
    if age_seconds is not None and age_seconds <= delayed_after_seconds and not source_failed:
        return DataFreshness(
            state="delayed",
            label="短暫延遲",
            badge="延遲",
            message="資料短暫延遲，請搭配券商報價確認。",
            age_minutes=age_minutes,
            is_live=False,
            is_delayed=True,
            is_stale=False,
            uses_last_known=True,
            can_use_for_daytrade=not partial,
        )
    if age_seconds is not None and age_seconds <= stale_after_seconds:
        return DataFreshness(
            state="last_known",
            label="使用上一筆有效價格",
            badge="快取",
            message="即時來源暫時不穩，已使用上一筆有效價格；不建議直接依此追價。",
            age_minutes=age_minutes,
            is_live=False,
            is_delayed=True,
            is_stale=False,
            uses_last_known=True,
            can_use_for_daytrade=False if is_market_open else not source_failed,
        )
    return DataFreshness(
        state="stale",
        label="過期",
        badge="過期",
        message="資料已超過允許時間，暫停產生盤中可執行判斷。",
        age_minutes=age_minutes,
        is_live=False,
        is_delayed=False,
        is_stale=True,
        uses_last_known=True,
        can_use_for_daytrade=False,
    )


def _localize(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=TAIPEI)
    return value.astimezone(TAIPEI)
