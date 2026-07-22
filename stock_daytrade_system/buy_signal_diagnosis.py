from __future__ import annotations

from typing import Any


BUY_SIGNAL_DIAGNOSIS_VERSION = "buy_signal_diagnosis_v1_plain_answer_2026-07-22"

REVIEW_MODES = {"closed_review", "post_close_review", "pre_open_prepare"}


def build_buy_signal_diagnosis(status_payload: dict[str, Any]) -> dict[str, Any]:
    """Explain whether the site can currently produce intraday long ideas in plain language."""
    market_mode = str(status_payload.get("market_mode") or "unknown")
    market_label = str(status_payload.get("market_mode_label") or market_mode)
    allow_intraday = bool(status_payload.get("allow_intraday_signal"))
    can_show_strong = bool(status_payload.get("can_show_any_strong_long"))
    reason_if_blocked = str(status_payload.get("reason_if_blocked") or "").strip()
    price_status = _as_dict(status_payload.get("price_status_summary"))
    front_summary = _as_dict(status_payload.get("front_category_summary"))
    refresh_summary = _as_dict(status_payload.get("refresh_operation_summary"))
    refresh_guidance = _as_dict(status_payload.get("refresh_guidance"))
    fugle_pool = _as_dict(status_payload.get("fugle_priority_pool"))
    required_stale_layers = _list(status_payload.get("required_stale_layers"))
    stale_layers = _list(status_payload.get("stale_layers"))

    counts = _as_dict(front_summary.get("counts"))
    strong_count = _int(front_summary.get("strong_buy_count", counts.get("強烈買多")))
    buy_count = _int(front_summary.get("buy_count", counts.get("買多")))
    watch_count = _int(front_summary.get("watch_count", counts.get("觀察")))
    bearish_count = _int(front_summary.get("bearish_count", counts.get("看空")))
    total_count = _int(front_summary.get("total"))
    live_count = _int(price_status.get("live_count"))
    delayed_count = _int(price_status.get("delayed_count"))
    cached_count = _int(price_status.get("cached_count"))
    missing_count = _int(price_status.get("missing_count"))
    selected_symbols = [str(item) for item in _list(fugle_pool.get("selected_symbols")) if str(item)]

    blockers: list[str] = []
    if market_mode in REVIEW_MODES or not allow_intraday:
        blockers.append(f"目前是{market_label}，不提供即時做多判斷。")
    if required_stale_layers:
        blockers.append("必要刷新層尚未完成：" + "、".join(_layer_label(layer) for layer in required_stale_layers) + "。")
    if market_mode == "intraday" and live_count <= 0:
        blockers.append("盤中沒有 live 價格資料，不能判斷即時做多。")
    if str(price_status.get("status") or "") in {"嚴重缺漏", "資料異常"}:
        blockers.append(f"資料品質為{price_status.get('status')}，不可作為盤中依據。")
    if reason_if_blocked and not blockers:
        blockers.append(reason_if_blocked)

    if can_show_strong:
        state = "has_strong_buy"
        headline = f"目前有 {strong_count} 檔強烈買多，可進入重點盯盤。"
        primary_reason = "資料與刷新層可用，但仍需逐檔檢查進場雷達、停損距離與部位風控。"
    elif allow_intraday and not blockers and (strong_count or buy_count):
        state = "has_buy_watch"
        headline = f"目前有 {buy_count} 檔買多觀察，但沒有強烈買多。"
        primary_reason = "方向偏多的股票仍差量能、突破、停損距離或進場雷達確認。"
    elif allow_intraday and not blockers:
        state = "wait_signal"
        headline = "現在沒有可做多標的。"
        primary_reason = str(front_summary.get("no_signal_reason") or "候選股尚未通過 VWAP、量比、突破、風險與信心條件。")
    elif market_mode in REVIEW_MODES:
        state = "review_only"
        headline = "現在不能判斷盤中可以做多，只能復盤與準備觀察清單。"
        primary_reason = blockers[0] if blockers else "目前不是盤中即時模式。"
    else:
        state = "data_blocked"
        headline = "現在不能判斷可以做多，先修資料。"
        primary_reason = blockers[0] if blockers else "資料或刷新狀態未準備好。"

    if required_stale_layers and state in {"review_only", "data_blocked"}:
        primary_reason = primary_reason.rstrip("。") + "；" + blockers[1].rstrip("。") + "。" if len(blockers) > 1 else primary_reason

    next_steps = _next_steps(
        state=state,
        market_mode=market_mode,
        required_stale_layers=required_stale_layers,
        refresh_guidance=refresh_guidance,
        selected_symbols=selected_symbols,
    )
    watch_now = _watch_now(
        state=state,
        strong_count=strong_count,
        buy_count=buy_count,
        watch_count=watch_count,
        selected_symbols=selected_symbols,
        live_count=live_count,
        delayed_count=delayed_count,
        cached_count=cached_count,
        missing_count=missing_count,
    )

    return {
        "version": BUY_SIGNAL_DIAGNOSIS_VERSION,
        "state": state,
        "headline": headline,
        "primary_reason": primary_reason,
        "can_answer_intraday_buy": bool(allow_intraday and not blockers),
        "can_show_strong_buy": can_show_strong,
        "can_show_buy_watch": bool(allow_intraday and not blockers and (strong_count or buy_count)),
        "market_mode": market_mode,
        "market_mode_label": market_label,
        "counts": {
            "strong_buy": strong_count,
            "buy": buy_count,
            "watch": watch_count,
            "bearish": bearish_count,
            "total": total_count,
            "live": live_count,
            "delayed": delayed_count,
            "cached": cached_count,
            "missing": missing_count,
        },
        "refresh_blockers": required_stale_layers,
        "stale_layers": stale_layers,
        "selected_fugle_symbols": selected_symbols,
        "what_to_watch_now": watch_now,
        "next_steps": next_steps,
        "do_not_do": _do_not_do(state, delayed_count, cached_count, missing_count),
        "plain_answer": _plain_answer(state, headline, primary_reason, next_steps),
    }


def _next_steps(*, state: str, market_mode: str, required_stale_layers: list[str], refresh_guidance: dict[str, Any], selected_symbols: list[str]) -> list[str]:
    if required_stale_layers:
        return [
            "先執行刷新：" + " → ".join(_layer_endpoint(layer) for layer in required_stale_layers),
            "刷新完成後重新看 live_count、強烈買多漏斗與進場雷達。",
            "資料恢復前，只能復盤或觀察指定股票。",
        ]
    if state == "has_strong_buy":
        return [
            "先打開強烈買多股票的 /tw/advisor。",
            "確認進場雷達、VWAP、量比、突破、停損距離都通過。",
            "再用虛擬交易或部位計算器控管風險。",
        ]
    if state == "has_buy_watch":
        return [
            "先看買多觀察股還差哪個條件。",
            "等待量比放大、突破觸發價或進場雷達轉強。",
            "沒有轉強前，不提前追價。",
        ]
    if state == "wait_signal":
        return [
            "等待 VWAP、量比、突破或進場雷達轉強。",
            "把最接近條件的股票放入 Fugle 5 檔追蹤池。",
            "沒有訊號就空手，不為了交易而交易。",
        ]
    if market_mode in REVIEW_MODES:
        return [
            "先看上一交易日復盤與下個交易日觀察清單。",
            "開盤後等資料轉 live，再看 VWAP、量比、突破與進場雷達。",
            "盤前或盤後不顯示即時強烈買多。",
        ]
    endpoint = str(refresh_guidance.get("action_endpoint") or "/refresh_full_market")
    return [
        f"先執行 {endpoint}。",
        "確認資料來源與 refresh_state 正常。",
        "資料沒有恢復前，不看即時買多。",
    ]


def _watch_now(*, state: str, strong_count: int, buy_count: int, watch_count: int, selected_symbols: list[str], live_count: int, delayed_count: int, cached_count: int, missing_count: int) -> list[str]:
    items: list[str] = []
    if selected_symbols:
        items.append("Fugle 指定追蹤：" + "、".join(selected_symbols[:5]))
    if strong_count:
        items.append(f"強烈買多 {strong_count} 檔：逐檔看進場雷達。")
    if buy_count:
        items.append(f"買多 {buy_count} 檔：等待最後觸發條件。")
    if watch_count and not strong_count and not buy_count:
        items.append(f"觀察 {watch_count} 檔：看最大卡關與下一步。")
    if live_count or delayed_count or cached_count or missing_count:
        items.append(f"資料狀態：live {live_count}、延遲 {delayed_count}、上一筆 {cached_count}、不足 {missing_count}。")
    if not items:
        items.append("目前沒有可用候選資料；先確認刷新是否完成。")
    return items


def _do_not_do(state: str, delayed_count: int, cached_count: int, missing_count: int) -> list[str]:
    items = [
        "不要把觀察股當成進場股。",
        "不要把 high_risk 包裝成買多。",
    ]
    if state in {"review_only", "data_blocked"}:
        items.insert(0, "不要在非盤中或資料未恢復時判斷即時做多。")
    if delayed_count or cached_count or missing_count:
        items.append("不要用 delayed / cached / missing 股票判斷強烈買多。")
    return items


def _plain_answer(state: str, headline: str, primary_reason: str, next_steps: list[str]) -> str:
    next_step = next_steps[0] if next_steps else "先確認資料與進場雷達。"
    if state == "has_strong_buy":
        return f"可以開始盯盤，但不是無腦買。{primary_reason} 下一步：{next_step}"
    if state in {"has_buy_watch", "wait_signal"}:
        return f"目前還沒有完整可進場訊號。{primary_reason} 下一步：{next_step}"
    return f"{headline}{primary_reason} 下一步：{next_step}"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _layer_label(layer: str) -> str:
    return {
        "full_market": "全市場掃描",
        "watchlist": "重點觀察",
        "positions": "交易觸發",
        "post_close_validation": "盤後驗證",
        "manual_full_refresh": "手動完整刷新",
    }.get(layer, layer)


def _layer_endpoint(layer: str) -> str:
    return {
        "full_market": "/refresh_full_market",
        "watchlist": "/refresh_watchlist",
        "positions": "/refresh_positions",
        "post_close_validation": "/refresh_post_close_validation",
        "manual_full_refresh": "/refresh",
    }.get(layer, "/refresh_full_market")
