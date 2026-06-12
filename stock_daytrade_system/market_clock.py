from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


TW_TZ = ZoneInfo("Asia/Taipei")
US_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketSession:
    market: str
    session: str
    status_text: str
    refresh_interval_seconds: int
    now_local: datetime
    timezone: str


def taiwan_market_session(now: datetime | None = None) -> MarketSession:
    local_now = _localize(now, TW_TZ)
    is_weekday = local_now.weekday() < 5
    is_regular = is_weekday and time(9, 0) <= local_now.time() < time(13, 30)
    session = "regular" if is_regular else "closed"
    return MarketSession(
        market="TW",
        session=session,
        status_text="台股盤中" if is_regular else "台股非交易時間",
        refresh_interval_seconds=30 if is_regular else 300,
        now_local=local_now,
        timezone="Asia/Taipei",
    )


def us_market_session(now: datetime | None = None) -> MarketSession:
    local_now = _localize(now, US_TZ)
    is_weekday = local_now.weekday() < 5
    current = local_now.time()
    if is_weekday and time(4, 0) <= current < time(9, 30):
        session = "premarket"
        text = "美股盤前"
        interval = 60
    elif is_weekday and time(9, 30) <= current < time(16, 0):
        session = "regular"
        text = "美股盤中"
        interval = 30
    elif is_weekday and time(16, 0) <= current < time(20, 0):
        session = "afterhours"
        text = "美股盤後"
        interval = 60
    else:
        session = "closed"
        text = "美股休市"
        interval = 300
    return MarketSession(
        market="US",
        session=session,
        status_text=text,
        refresh_interval_seconds=interval,
        now_local=local_now,
        timezone="America/New_York",
    )


def _localize(now: datetime | None, timezone: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone)
    return now.astimezone(timezone)
