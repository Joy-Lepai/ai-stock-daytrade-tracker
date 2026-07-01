from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, Optional

from stock_daytrade_system.db import recent_tw_orderbook_snapshots, save_tw_orderbook_snapshot
from stock_daytrade_system.entry_confirmation import build_entry_confirmation
from stock_daytrade_system.fugle_market_data import FugleMarketDataClient
from stock_daytrade_system.tw_scan_service import (
    _bars_from_fugle_candles,
    _fugle_quote_as_realtime,
    _radar_quote_payload,
)


FUGLE_ENTRY_RADAR_VERSION = "fugle_entry_radar_v2_confirmation_quality_2026-06-26"
DEFAULT_FUGLE_PRIORITY_POOL_LIMIT = 5
DEFAULT_FUGLE_REFRESH_INTERVAL_SECONDS = 300
DEFAULT_FUGLE_BASIC_REST_CALLS_PER_MINUTE = 60


def enrich_fugle_priority_pool(
    pool: dict,
    *,
    client: FugleMarketDataClient,
    conn: sqlite3.Connection,
    captured_at: datetime,
) -> dict:
    """Best-effort Fugle entry radar for the selected priority pool only.

    This intentionally limits calls to the already-selected pool, so Fugle basic
    users stay within the 5-symbol tracking workflow. Failures are recorded per
    symbol and never block the full tracker.
    """

    payload = dict(pool or {})
    rows = [dict(item) for item in (payload.get("selected") or [])]
    if not rows:
        payload.update(
            {
                "entry_radar_version": FUGLE_ENTRY_RADAR_VERSION,
                "confirmation_updated_at": captured_at.isoformat(timespec="seconds"),
                "confirmation_success_count": 0,
                "confirmation_failed_count": 0,
                "planned_api_calls": 0,
                "actual_api_calls": 0,
                "api_budget_message": _api_budget_message(0, 0),
                "entry_radar_health": _entry_radar_health(
                    selected_count=0,
                    tracking_limit=_priority_pool_limit(),
                    success_count=0,
                    failed_count=0,
                    skipped_count=0,
                    actual_api_calls=0,
                    planned_api_calls=0,
                    status="empty",
                    message="目前沒有符合 Fugle 進場雷達追蹤池的股票。",
                ),
            }
        )
        return payload
    if not client.config.enabled or not client.config.configured:
        reason = "Fugle 尚未啟用" if not client.config.enabled else "Fugle API Key 尚未設定"
        payload.update(
            {
                "entry_radar_version": FUGLE_ENTRY_RADAR_VERSION,
                "confirmation_updated_at": captured_at.isoformat(timespec="seconds"),
                "confirmation_success_count": 0,
                "confirmation_failed_count": len(rows),
                "planned_api_calls": 0,
                "actual_api_calls": 0,
                "api_budget_message": _api_budget_message(0, 0),
                "entry_radar_status": "disabled" if not client.config.enabled else "not_configured",
                "entry_radar_message": f"{reason}，已保留 5 檔追蹤池，但不抓五檔 / 逐筆資料。",
                "selected": [_unavailable_item(item, reason=reason) for item in rows],
                "entry_radar_health": _entry_radar_health(
                    selected_count=len(rows),
                    tracking_limit=_priority_pool_limit(),
                    success_count=0,
                    failed_count=len(rows),
                    skipped_count=0,
                    actual_api_calls=0,
                    planned_api_calls=0,
                    status="disabled" if not client.config.enabled else "not_configured",
                    message=f"{reason}，保留追蹤池但不可做進場確認。",
                ),
            }
        )
        return payload

    tracking_limit = _priority_pool_limit()
    rows_to_fetch = rows[:tracking_limit]
    skipped_rows = rows[tracking_limit:]
    enriched: list[dict] = []
    success_count = 0
    failed_count = 0
    api_calls = 0
    for item in rows_to_fetch:
        try:
            enriched_item = _enrich_item(item, client=client, conn=conn, captured_at=captured_at)
            api_calls += int(enriched_item.get("fugle_api_calls") or 0)
            if enriched_item.get("fugle_confirmation_status") == "ok":
                success_count += 1
            else:
                failed_count += 1
            enriched.append(enriched_item)
        except Exception as exc:  # pragma: no cover - defensive guardrail
            failed_count += 1
            item["fugle_confirmation_status"] = "failed"
            item["fugle_confirmation_error"] = str(exc)
            item["entry_confirmation_summary"] = "Fugle 進場雷達暫時無法更新，僅保留原本模型觀察。"
            item["entry_confirmation_next_step"] = "等待下一次重點追蹤刷新。"
            enriched.append(item)
    skipped_items = [
        _unavailable_item(
            item,
            reason=f"超過 Fugle 進場雷達 {tracking_limit} 檔追蹤上限",
        )
        for item in skipped_rows
    ]

    radar_status = "ok" if success_count and not failed_count and not skipped_items else "partial"
    radar_message = (
        f"Fugle 進場雷達只追蹤前 {tracking_limit} 檔；"
        f"超過上限 {len(skipped_items)} 檔保留原模型觀察。"
        if skipped_items
        else "Fugle 進場雷達已更新。"
    )
    payload.update(
        {
            "entry_radar_version": FUGLE_ENTRY_RADAR_VERSION,
            "confirmation_updated_at": captured_at.isoformat(timespec="seconds"),
            "confirmation_success_count": success_count,
            "confirmation_failed_count": failed_count,
            "confirmation_skipped_count": len(skipped_items),
            "tracking_limit": tracking_limit,
            "planned_api_calls": len(rows_to_fetch) * 3,
            "actual_api_calls": api_calls,
            "api_budget_message": _api_budget_message(api_calls, len(rows_to_fetch) * 3),
            "entry_radar_status": radar_status,
            "entry_radar_message": radar_message,
            "selected": enriched + skipped_items,
            "entry_radar_health": _entry_radar_health(
                selected_count=len(rows),
                tracking_limit=tracking_limit,
                success_count=success_count,
                failed_count=failed_count,
                skipped_count=len(skipped_items),
                actual_api_calls=api_calls,
                planned_api_calls=len(rows_to_fetch) * 3,
                status=radar_status,
                message=radar_message,
            ),
        }
    )
    return payload


def _enrich_item(
    item: dict,
    *,
    client: FugleMarketDataClient,
    conn: sqlite3.Connection,
    captured_at: datetime,
) -> dict:
    symbol = str(item.get("symbol") or "")
    quote = _to_dict(client.fetch_quote(symbol))
    trades = _to_dict(client.fetch_trades(symbol))
    candles = _to_dict(client.fetch_candles(symbol, timeframe=client.config.candles_timeframe))
    fallback_quote = {
        "price": item.get("last_price"),
        "quote_time": captured_at.isoformat(timespec="seconds"),
        "source": "long_model_priority_pool",
    }
    realtime_quote = _radar_quote_payload(
        _fugle_quote_as_realtime(quote, fallback_quote),
        {},
        trades,
    )
    bars = _bars_from_fugle_candles(candles)
    data_health = _data_health_from_fugle(quote, trades, candles)
    save_tw_orderbook_snapshot(
        conn,
        market="TW",
        symbol=symbol,
        captured_at=captured_at,
        quote=realtime_quote,
    )
    history = recent_tw_orderbook_snapshots(conn, market="TW", symbol=symbol, limit=5)
    candidate = dict(item)
    if "above_vwap" not in candidate:
        price = _float(candidate.get("last_price"))
        vwap = _float(candidate.get("vwap"))
        candidate["above_vwap"] = bool(price is not None and vwap is not None and price >= vwap)
    confirmation = build_entry_confirmation(
        candidate=candidate,
        intraday_bars=bars,
        data_health=data_health,
        realtime_quote=realtime_quote,
        orderbook_history=history,
    ).to_dict()

    enriched = dict(item)
    enriched.update(
        {
            "fugle_confirmation_status": "ok" if quote.get("status") in {"ok", "partial"} else quote.get("status") or "missing",
            "fugle_quote_status": quote.get("status") or "missing",
            "fugle_quote_status_label": quote.get("status_label") or "Quote 不足",
            "fugle_trades_status": trades.get("status") or "missing",
            "fugle_trades_status_label": trades.get("status_label") or "逐筆不足",
            "fugle_candles_status": candles.get("status") or "missing",
            "fugle_candles_status_label": candles.get("status_label") or "1分K 不足",
            "fugle_quote_time": quote.get("quote_time") or quote.get("last_updated") or "",
            "bid_total_volume": confirmation.get("bid_total_volume"),
            "ask_total_volume": confirmation.get("ask_total_volume"),
            "orderbook_imbalance": confirmation.get("orderbook_imbalance"),
            "orderbook_status": confirmation.get("orderbook_status"),
            "bid_volume_trend": confirmation.get("bid_volume_trend"),
            "bid_volume_trend_summary": confirmation.get("bid_volume_trend_summary"),
            "ask_volume_trend": confirmation.get("ask_volume_trend"),
            "ask_volume_trend_summary": confirmation.get("ask_volume_trend_summary"),
            "large_trade_status": confirmation.get("large_trade_status"),
            "large_trade_summary": confirmation.get("large_trade_summary"),
            "price_tick_trend": confirmation.get("price_tick_trend"),
            "price_tick_summary": confirmation.get("price_tick_summary"),
            "entry_confirmation_status": confirmation.get("status"),
            "entry_confirmation_status_label": confirmation.get("status_label"),
            "entry_confirmation_score": confirmation.get("score"),
            "entry_confirmation_summary": confirmation.get("summary"),
            "entry_confirmation_next_step": confirmation.get("next_step"),
            "entry_confirmation_can_consider": confirmation.get("can_consider_entry"),
            "entry_confirmation_warnings": confirmation.get("warnings") or [],
            "confirmation_quality": confirmation.get("confirmation_quality"),
            "confirmation_quality_label": confirmation.get("confirmation_quality_label"),
            "confirmation_quality_reason": confirmation.get("confirmation_quality_reason"),
            "critical_data_ready": confirmation.get("critical_data_ready"),
            "orderbook_history_count": confirmation.get("orderbook_history_count") or 0,
            "fugle_api_calls": 3,
        }
    )
    return enriched


def _unavailable_item(item: dict, *, reason: str) -> dict:
    payload = dict(item)
    payload.update(
        {
            "fugle_confirmation_status": "disabled",
            "fugle_confirmation_error": reason,
            "fugle_quote_status": "disabled",
            "fugle_trades_status": "disabled",
            "fugle_candles_status": "disabled",
            "orderbook_status": "missing",
            "bid_volume_trend": "missing",
            "bid_volume_trend_summary": "Fugle 未啟用，尚無法判斷委買量變化。",
            "ask_volume_trend": "missing",
            "ask_volume_trend_summary": "Fugle 未啟用，尚無法判斷委賣量變化。",
            "large_trade_status": "missing",
            "large_trade_summary": "Fugle 未啟用，尚無法判斷大單敲進 / 敲出。",
            "price_tick_trend": "missing",
            "price_tick_summary": "Fugle 未啟用，尚無法判斷最新價是否墊高。",
            "entry_confirmation_status": "waiting",
            "entry_confirmation_status_label": "等待 Fugle 設定",
            "entry_confirmation_summary": f"{reason}，此檔僅保留在即時追蹤池，不作進場確認。",
            "entry_confirmation_next_step": "完成 Fugle 設定並重新刷新後，再觀察五檔、逐筆與價格墊高。",
            "entry_confirmation_can_consider": False,
            "entry_confirmation_warnings": [reason],
            "confirmation_quality": "blocked",
            "confirmation_quality_label": "暫不進場",
            "confirmation_quality_reason": f"{reason}，缺即時五檔與逐筆確認。",
            "critical_data_ready": False,
            "orderbook_history_count": 0,
            "fugle_api_calls": 0,
        }
    )
    return payload


def _data_health_from_fugle(quote: dict, trades: dict, candles: dict) -> dict:
    quote_ok = quote.get("status") == "ok" and quote.get("price") is not None
    return {
        "price_status": "live" if quote_ok else "missing",
        "is_live": bool(quote_ok),
        "can_use_for_intraday_signal": bool(quote_ok),
        "fugle_status": trades.get("status") or "missing",
        "fugle_status_label": trades.get("status_label") or "",
        "fugle_trades_count": trades.get("trades_count") or 0,
        "fugle_large_trade_status": trades.get("large_trade_status") or "missing",
        "fugle_quote_status": quote.get("status") or "missing",
        "fugle_quote_five_level_status": quote.get("five_level_status") or "missing",
        "fugle_candles_status": candles.get("status") or "missing",
        "fugle_candles_count": candles.get("candles_count") or 0,
    }


def _to_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {}


def _priority_pool_limit() -> int:
    try:
        value = int(os.environ.get("FUGLE_PRIORITY_POOL_LIMIT", str(DEFAULT_FUGLE_PRIORITY_POOL_LIMIT)))
    except (TypeError, ValueError):
        value = DEFAULT_FUGLE_PRIORITY_POOL_LIMIT
    return max(1, min(value, 20))


def _api_budget_message(actual_calls: int, planned_calls: int) -> str:
    interval_seconds = max(_env_int("FUGLE_PRIORITY_REFRESH_SECONDS", DEFAULT_FUGLE_REFRESH_INTERVAL_SECONDS), 60)
    calls_per_minute_limit = _env_int("FUGLE_REST_CALLS_PER_MINUTE", DEFAULT_FUGLE_BASIC_REST_CALLS_PER_MINUTE)
    calls = int(actual_calls or planned_calls or 0)
    estimated_per_minute = (float(calls) / interval_seconds) * 60
    status = "安全" if estimated_per_minute <= calls_per_minute_limit * 0.8 else "接近上限"
    return (
        f"Fugle 雷達本次 {calls} 次 API 呼叫；"
        f"若每 {interval_seconds // 60} 分鐘刷新，估計 {estimated_per_minute:.1f}/min，"
        f"基本限制 {calls_per_minute_limit}/min，狀態：{status}。"
    )


def _entry_radar_health(
    *,
    selected_count: int,
    tracking_limit: int,
    success_count: int,
    failed_count: int,
    skipped_count: int,
    actual_api_calls: int,
    planned_api_calls: int,
    status: str,
    message: str,
) -> dict[str, Any]:
    interval_seconds = max(_env_int("FUGLE_PRIORITY_REFRESH_SECONDS", DEFAULT_FUGLE_REFRESH_INTERVAL_SECONDS), 60)
    calls_per_minute_limit = _env_int("FUGLE_REST_CALLS_PER_MINUTE", DEFAULT_FUGLE_BASIC_REST_CALLS_PER_MINUTE)
    calls = int(actual_api_calls or planned_api_calls or 0)
    estimated_per_minute = round((float(calls) / interval_seconds) * 60, 2)
    api_budget_status = "safe" if estimated_per_minute <= calls_per_minute_limit * 0.8 else "near_limit"
    if status in {"disabled", "not_configured"}:
        operator_status = "not_ready"
        next_action = "先完成 Fugle 設定；設定前只看原模型與資料可信度。"
    elif status == "empty":
        operator_status = "empty"
        next_action = "等待強烈買多 / 買多 / 觀察池出現接近進場的股票。"
    elif failed_count:
        operator_status = "degraded"
        next_action = "先看成功更新的股票；失敗股票等下一次刷新，不要用缺資料進場。"
    elif skipped_count:
        operator_status = "limited"
        next_action = f"只盯前 {tracking_limit} 檔；超過上限的股票保留觀察，不做即時進場確認。"
    else:
        operator_status = "ready"
        next_action = "逐檔確認五檔、逐筆、大單、價格墊高與 VWAP 後再行動。"
    can_use_for_entry = operator_status == "ready"
    can_use_partial = operator_status in {"ready", "limited", "degraded"}
    return {
        "status": status,
        "operator_status": operator_status,
        "message": message,
        "next_action": next_action,
        "selected_count": int(selected_count),
        "tracking_limit": int(tracking_limit),
        "success_count": int(success_count),
        "failed_count": int(failed_count),
        "skipped_count": int(skipped_count),
        "actual_api_calls": int(actual_api_calls),
        "planned_api_calls": int(planned_api_calls),
        "refresh_interval_seconds": interval_seconds,
        "calls_per_minute_limit": calls_per_minute_limit,
        "estimated_calls_per_minute": estimated_per_minute,
        "api_budget_status": api_budget_status,
        "can_use_for_entry_confirmation": can_use_for_entry,
        "can_use_partial_confirmation": can_use_partial,
    }


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
