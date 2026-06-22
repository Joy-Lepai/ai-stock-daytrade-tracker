from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


MARKET_MODE_VERSION = "market_mode_v3_tw_pre_open_prepare_2026-06-22"
TW_TZ = ZoneInfo("Asia/Taipei")
TW_MARKET_HOLIDAYS = {
    date(2026, 1, 1),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 2, 27),
    date(2026, 4, 3),
    date(2026, 4, 6),
    date(2026, 5, 1),
    date(2026, 6, 19),
    date(2026, 9, 25),
    date(2026, 9, 28),
    date(2026, 10, 9),
}


@dataclass(frozen=True)
class MarketMode:
    mode: str
    label: str
    message: str
    last_trading_date: str
    data_date: str
    is_trading_day: bool
    is_holiday: bool
    is_market_open: bool
    is_post_close: bool
    is_weekend: bool
    is_data_current_for_mode: bool
    allow_intraday_signal: bool
    allow_strong_long: bool
    review_mode_message: str
    reason_code: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["version"] = MARKET_MODE_VERSION
        return payload


def evaluate_tw_market_mode(
    *,
    now: Optional[datetime] = None,
    data_date: Optional[str | date] = None,
    latest_data_at: Optional[str | datetime] = None,
    data_stale: bool = False,
    severe_missing: bool = False,
    watchlist_fresh: bool = True,
    positions_fresh: bool = True,
) -> MarketMode:
    local_now = _localize(now)
    today = local_now.date()
    is_weekend = local_now.weekday() >= 5
    is_holiday = today in TW_MARKET_HOLIDAYS
    is_trading_day = _is_tw_trading_day(today)
    current = local_now.time()
    is_pre_open = is_trading_day and current < time(9, 0)
    is_market_open = is_trading_day and time(9, 0) <= current < time(13, 30)
    is_post_close = is_trading_day and current >= time(13, 30)
    last_trading = _last_trading_date(local_now)
    raw_data_date = _parse_date(data_date)
    parsed_data_date = raw_data_date or last_trading
    if not is_trading_day and parsed_data_date == today:
        parsed_data_date = last_trading
    latest_dt = _parse_datetime(latest_data_at)

    stale_by_age = False
    if latest_dt and is_market_open:
        stale_by_age = (local_now - latest_dt.astimezone(TW_TZ)).total_seconds() > 15 * 60
    elif latest_dt and latest_dt.date() != parsed_data_date:
        stale_by_age = True
    stale = bool(data_stale or stale_by_age)

    if is_market_open:
        is_current = parsed_data_date == today and not stale and not severe_missing
        if is_current:
            mode = "intraday"
            label = "盤中模式"
            message = "目前為盤中模式，系統正在追蹤台股做多當沖條件。"
            reason = "intraday_current"
        else:
            mode = "stale_data"
            label = "資料異常模式"
            message = "資料已過期或缺漏嚴重，僅供參考，不建議依此交易。"
            reason = "stale_or_missing_intraday"
    elif is_post_close:
        is_current = parsed_data_date == today and not severe_missing
        mode = "post_close_review" if is_current else "stale_data"
        label = "盤後復盤模式" if is_current else "資料異常模式"
        message = (
            "目前為盤後復盤模式。以下為今日盤中資料與盤後驗證，僅供復盤，不提供即時進場判斷。"
            if is_current
            else "資料已過期或缺漏嚴重，僅供參考，不建議依此交易。"
        )
        reason = "post_close_review" if is_current else "post_close_data_missing"
    elif is_pre_open:
        expected = last_trading
        is_current = parsed_data_date == expected and not severe_missing
        mode = "pre_open_prepare" if is_current else "stale_data"
        label = "開盤前準備模式" if is_current else "資料異常模式"
        message = (
            "目前為開盤前準備模式。以下使用上一交易日資料整理今日觀察清單；尚未有今日 VWAP、量比與盤中突破確認，不提供即時買多判斷。"
            if is_current
            else "開盤前資料不足或缺漏嚴重，僅供觀察，不建議依此交易。"
        )
        reason = "pre_open_prepare" if is_current else "pre_open_data_missing"
    else:
        expected = last_trading
        is_current = parsed_data_date <= today and parsed_data_date >= expected - timedelta(days=3) and not severe_missing
        mode = "closed_review" if is_current else "stale_data"
        label = "休市復盤模式" if is_current else "資料異常模式"
        message = (
            "目前為休市復盤模式。以下顯示上一交易日資料，僅供復盤與下個交易日觀察，不提供即時做多判斷。"
            if is_current
            else "資料已過期或缺漏嚴重，僅供參考，不建議依此交易。"
        )
        reason = "closed_review" if is_current else "closed_stale_data"

    allow_intraday = mode == "intraday"
    allow_strong = allow_intraday and watchlist_fresh and positions_fresh
    if allow_intraday and not allow_strong:
        reason = "refresh_layer_stale"

    return MarketMode(
        mode=mode,
        label=label,
        message=message,
        last_trading_date=last_trading.isoformat(),
        data_date=parsed_data_date.isoformat(),
        is_trading_day=is_trading_day,
        is_holiday=is_holiday,
        is_market_open=is_market_open,
        is_post_close=is_post_close,
        is_weekend=is_weekend,
        is_data_current_for_mode=mode != "stale_data",
        allow_intraday_signal=allow_intraday,
        allow_strong_long=allow_strong,
        review_mode_message=message,
        reason_code=reason,
    )


def _last_trading_date(now: datetime) -> date:
    day = now.date()
    if _is_tw_trading_day(day) and now.time() >= time(13, 30):
        return day
    if _is_tw_trading_day(day) and now.time() >= time(9, 0):
        return day
    day -= timedelta(days=1)
    while not _is_tw_trading_day(day):
        day -= timedelta(days=1)
    return day


def _is_tw_trading_day(value: date) -> bool:
    return value.weekday() < 5 and value not in TW_MARKET_HOLIDAYS


def _localize(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(TW_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=TW_TZ)
    return value.astimezone(TW_TZ)


def _parse_date(value: Optional[str | date]) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _parse_datetime(value: Optional[str | datetime]) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _localize(value)
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _localize(parsed)
