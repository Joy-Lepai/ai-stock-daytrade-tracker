from __future__ import annotations

from typing import Any


OPERATIONAL_HEALTH_VERSION = "operational_health_v1_readiness_2026-06-26"

BLOCKING_PRICE_STATUSES = {"嚴重缺漏", "資料異常"}
INTRADAY_MODES = {"intraday"}
REVIEW_MODES = {"closed_review", "post_close_review", "pre_open_prepare"}


def build_operational_health(status_payload: dict[str, Any]) -> dict[str, Any]:
    """Turn low-level refresh/data status into an operator-friendly readiness report."""
    market_mode = str(status_payload.get("market_mode") or "unknown")
    price_status = _as_dict(status_payload.get("price_status_summary"))
    refresh_guidance = _as_dict(status_payload.get("refresh_guidance"))
    refresh_summary = _as_dict(status_payload.get("refresh_operation_summary"))
    required_stale_layers = _list(status_payload.get("required_stale_layers"))
    stale_layers = _list(status_payload.get("stale_layers"))
    blockers: list[str] = []
    warnings: list[str] = []

    if market_mode == "stale_data":
        blockers.append("資料模式為異常或過期，暫停即時做多判斷。")

    if required_stale_layers:
        blockers.append("必要刷新層過期：" + "、".join(_layer_label(layer) for layer in required_stale_layers))

    if refresh_guidance.get("severity") == "block" or refresh_summary.get("severity") == "block":
        message = str(refresh_guidance.get("summary") or refresh_summary.get("message") or "刷新狀態阻擋使用。")
        blockers.append(message)

    price_label = str(price_status.get("status") or "")
    if price_label in BLOCKING_PRICE_STATUSES:
        blockers.append(f"資料品質為{price_label}，不適合做盤中判斷。")

    live_count = _int(price_status.get("live_count"))
    missing_ratio = _float(price_status.get("missing_ratio"))
    cached_count = _int(price_status.get("cached_count"))
    delayed_count = _int(price_status.get("delayed_count"))
    missing_count = _int(price_status.get("missing_count"))

    if market_mode in INTRADAY_MODES and live_count <= 0:
        blockers.append("盤中沒有可用即時價格，暫停即時做多判斷。")
    if missing_ratio >= 0.5:
        blockers.append("超過一半股票資料不足，應先修資料源或重跑刷新。")
    elif missing_count:
        warnings.append(f"有 {missing_count} 檔資料不足，該些股票只能觀察。")
    if cached_count:
        warnings.append(f"有 {cached_count} 檔使用上一筆有效資料，不可顯示強烈買多。")
    if delayed_count:
        warnings.append(f"有 {delayed_count} 檔資料延遲，不可顯示強烈買多。")

    if bool(status_payload.get("data_source_degraded")):
        warnings.append("部分資料源降級或失敗，請看資料源健康度。")

    if stale_layers and not required_stale_layers:
        warnings.append("有非必要刷新層過期：" + "、".join(_layer_label(layer) for layer in stale_layers))

    allow_intraday_signal = bool(status_payload.get("allow_intraday_signal"))
    can_show_strong = bool(status_payload.get("can_show_any_strong_long") or status_payload.get("allow_strong_long"))
    if not allow_intraday_signal and market_mode in REVIEW_MODES:
        warnings.append("目前不是盤中即時模式，只顯示復盤或下個交易日觀察。")
    elif allow_intraday_signal and not can_show_strong:
        warnings.append(str(status_payload.get("reason_if_blocked") or "目前沒有可顯示強烈買多的即時訊號。"))

    status = "blocked" if blockers else "warning" if warnings else "ok"
    next_action = _next_action(status, refresh_guidance, required_stale_layers, market_mode)
    summary = _summary(status, market_mode, blockers, warnings)
    return {
        "version": OPERATIONAL_HEALTH_VERSION,
        "status": status,
        "summary": summary,
        "blockers": _dedupe(blockers),
        "warnings": _dedupe(warnings),
        "next_action": next_action,
        "market_mode": market_mode,
        "market_mode_label": status_payload.get("market_mode_label") or "",
        "data_quality_status": price_label or "未知",
        "live_count": live_count,
        "delayed_count": delayed_count,
        "cached_count": cached_count,
        "missing_count": missing_count,
        "missing_ratio": missing_ratio,
        "required_stale_layers": required_stale_layers,
        "stale_layers": stale_layers,
        "allow_intraday_signal": allow_intraday_signal,
        "can_show_strong_long": can_show_strong and not blockers,
        "can_use_dashboard": status != "blocked",
    }


def _next_action(status: str, refresh_guidance: dict[str, Any], required_stale_layers: list[str], market_mode: str) -> dict[str, str]:
    endpoint = str(refresh_guidance.get("action_endpoint") or "")
    label = str(refresh_guidance.get("action_label") or "")
    if status == "blocked" and endpoint:
        return {"label": label or "先執行建議刷新", "endpoint": endpoint}
    if status == "blocked" and required_stale_layers:
        layer = required_stale_layers[0]
        return {"label": f"先更新{_layer_label(layer)}", "endpoint": _layer_endpoint(layer)}
    if status == "blocked":
        return {"label": "先修正資料或重新刷新", "endpoint": "/refresh_watchlist"}
    if status == "warning" and endpoint and endpoint != "none":
        return {"label": label or "可視需要刷新", "endpoint": endpoint}
    if market_mode in REVIEW_MODES:
        return {"label": "查看復盤與下個交易日觀察", "endpoint": "/dashboard"}
    return {"label": "不需手動更新，持續觀察", "endpoint": ""}


def _summary(status: str, market_mode: str, blockers: list[str], warnings: list[str]) -> str:
    if status == "blocked":
        return blockers[0] if blockers else "目前狀態阻擋即時判斷。"
    if status == "warning":
        if market_mode in REVIEW_MODES:
            return "目前為非盤中模式，可用於復盤與下個交易日觀察。"
        return warnings[0] if warnings else "目前可使用，但有提醒事項。"
    if market_mode in REVIEW_MODES:
        return "資料可用於復盤與下個交易日觀察。"
    return "系統狀態正常，可依前台訊號與風控規則觀察。"


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
    }.get(layer, "/refresh_watchlist")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
