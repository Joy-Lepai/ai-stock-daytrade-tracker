from __future__ import annotations

from typing import Any


OPERATIONAL_HEALTH_VERSION = "operational_health_v5_front_category_triage_2026-06-26"

BLOCKING_PRICE_STATUSES = {"嚴重缺漏", "資料異常"}
INTRADAY_MODES = {"intraday"}
REVIEW_MODES = {"closed_review", "post_close_review", "pre_open_prepare"}


def build_operational_health(status_payload: dict[str, Any]) -> dict[str, Any]:
    """Turn low-level refresh/data status into an operator-friendly readiness report."""
    market_mode = str(status_payload.get("market_mode") or "unknown")
    price_status = _as_dict(status_payload.get("price_status_summary"))
    front_category = _as_dict(status_payload.get("front_category_summary"))
    refresh_guidance = _as_dict(status_payload.get("refresh_guidance"))
    refresh_summary = _as_dict(status_payload.get("refresh_operation_summary"))
    limit_up_summary = _as_dict(status_payload.get("limit_up_operational_summary"))
    required_stale_layers = _list(status_payload.get("required_stale_layers"))
    stale_layers = _list(status_payload.get("stale_layers"))
    review_candidates = _as_dict(status_payload.get("review_observation_candidates"))
    has_review_candidates = (
        str(review_candidates.get("status") or "") == "ok"
        and (_int(review_candidates.get("count")) > 0 or bool(review_candidates.get("items")))
    )
    review_snapshot_mode = market_mode in REVIEW_MODES and has_review_candidates
    non_blocking_review_layers: list[str] = []
    blocking_required_layers = list(required_stale_layers)
    if review_snapshot_mode and market_mode == "pre_open_prepare":
        non_blocking_review_layers = [
            layer for layer in blocking_required_layers if layer in {"watchlist", "positions"}
        ]
        blocking_required_layers = [
            layer for layer in blocking_required_layers if layer not in {"watchlist", "positions"}
        ]
    blockers: list[str] = []
    warnings: list[str] = []

    if market_mode == "stale_data":
        blockers.append("資料模式為異常或過期，暫停即時做多判斷。")

    if blocking_required_layers:
        blockers.append("必要刷新層過期：" + "、".join(_layer_label(layer) for layer in blocking_required_layers))
    if non_blocking_review_layers:
        warnings.append(
            "盤前尚未更新"
            + "、".join(_layer_label(layer) for layer in non_blocking_review_layers)
            + "；目前只能使用上一交易日快照整理觀察清單。"
        )

    if refresh_guidance.get("severity") == "block" or refresh_summary.get("severity") == "block":
        message = str(refresh_guidance.get("summary") or refresh_summary.get("message") or "刷新狀態阻擋使用。")
        if review_snapshot_mode and not blocking_required_layers:
            warnings.append(message)
        else:
            blockers.append(message)

    price_label = str(price_status.get("status") or "")
    if price_label in BLOCKING_PRICE_STATUSES:
        if review_snapshot_mode:
            warnings.append("尚無盤中 VWAP / 量比 / 即時價格，只能做下個交易日觀察。")
        else:
            blockers.append(f"資料品質為{price_label}，不適合做盤中判斷。")

    live_count = _int(price_status.get("live_count"))
    missing_ratio = _float(price_status.get("missing_ratio"))
    cached_count = _int(price_status.get("cached_count"))
    delayed_count = _int(price_status.get("delayed_count"))
    missing_count = _int(price_status.get("missing_count"))

    if market_mode in INTRADAY_MODES and live_count <= 0:
        blockers.append("盤中沒有可用即時價格，暫停即時做多判斷。")
    if missing_ratio >= 0.5:
        if review_snapshot_mode:
            warnings.append("盤前快照缺少盤中欄位，開盤後需重新刷新 live、VWAP 與量比。")
        else:
            blockers.append("超過一半股票資料不足，應先修資料源或重跑刷新。")
    elif missing_count:
        warnings.append(f"有 {missing_count} 檔資料不足，該些股票只能觀察。")
    if cached_count:
        warnings.append(f"有 {cached_count} 檔使用上一筆有效資料，不可顯示強烈買多。")
    if delayed_count:
        warnings.append(f"有 {delayed_count} 檔資料延遲，不可顯示強烈買多。")

    if bool(status_payload.get("data_source_degraded")):
        warnings.append("部分資料源降級或失敗，請看資料源健康度。")

    if stale_layers and not blocking_required_layers:
        warnings.append("有非必要刷新層過期：" + "、".join(_layer_label(layer) for layer in stale_layers))

    allow_intraday_signal = bool(status_payload.get("allow_intraday_signal"))
    can_show_strong = bool(status_payload.get("can_show_any_strong_long"))
    if allow_intraday_signal and not front_category:
        can_show_strong = False
        warnings.append("尚未取得四分類摘要，不可信任強烈買多。")
    elif front_category and _int(front_category.get("strong_buy_count")) <= 0:
        can_show_strong = False
    if not allow_intraday_signal and market_mode in REVIEW_MODES:
        warnings.append("目前不是盤中即時模式，只顯示復盤或下個交易日觀察。")
    elif allow_intraday_signal and not can_show_strong:
        no_signal_reason = str(front_category.get("no_signal_reason") or "").strip()
        warnings.append(str(status_payload.get("reason_if_blocked") or no_signal_reason or "目前沒有可顯示強烈買多的即時訊號。"))
    elif allow_intraday_signal and _int(front_category.get("strong_buy_count")) <= 0:
        no_signal_reason = str(front_category.get("no_signal_reason") or "").strip()
        if no_signal_reason:
            warnings.append(no_signal_reason)

    status = "blocked" if blockers else "warning" if warnings else "ok"
    next_action = _next_action(status, refresh_guidance, blocking_required_layers, market_mode)
    summary = _summary(status, market_mode, blockers, warnings)
    watch_readiness = _watch_readiness(status, market_mode)
    refresh_plan = _refresh_plan(blocking_required_layers, refresh_summary, next_action)
    operator_steps = _operator_steps(
        status=status,
        market_mode=market_mode,
        next_action=next_action,
        refresh_plan=refresh_plan,
        blockers=blockers,
        warnings=warnings,
    )
    operator_mode = _operator_mode(
        status=status,
        market_mode=market_mode,
        blockers=blockers,
        warnings=warnings,
        refresh_plan=refresh_plan,
    )
    operator_mode = _apply_limit_up_context(operator_mode, limit_up_summary, status=status, market_mode=market_mode)
    briefing = _operator_briefing(
        status=status,
        market_mode=market_mode,
        watch_readiness=watch_readiness,
        next_action=next_action,
        operator_mode=operator_mode,
        blockers=blockers,
        warnings=warnings,
        can_show_strong=can_show_strong and not blockers,
    )
    preflight = _opening_preflight(
        status=status,
        market_mode=market_mode,
        next_action=next_action,
        blockers=blockers,
        warnings=warnings,
        allow_intraday_signal=allow_intraday_signal,
        can_show_strong=can_show_strong and not blockers,
    )
    operator_decision = _operator_decision(
        status=status,
        market_mode=market_mode,
        watch_readiness=watch_readiness,
        opening_preflight=preflight,
        operator_briefing=briefing,
        operator_mode=operator_mode,
        next_action=next_action,
        blockers=blockers,
        warnings=warnings,
        allow_intraday_signal=allow_intraday_signal,
        can_show_strong=can_show_strong and not blockers,
    )
    return {
        "version": OPERATIONAL_HEALTH_VERSION,
        "status": status,
        "summary": summary,
        "opening_preflight": preflight,
        "operator_decision": operator_decision,
        "operator_briefing": briefing,
        "operator_mode": operator_mode["mode"],
        "primary_focus": operator_mode["primary_focus"],
        "do_now": operator_mode["do_now"],
        "do_not_do": operator_mode["do_not_do"],
        "decision_checklist": operator_mode["decision_checklist"],
        "watch_readiness": watch_readiness["label"],
        "watch_readiness_message": watch_readiness["message"],
        "operator_steps": operator_steps,
        "refresh_plan": refresh_plan,
        "blockers": _dedupe(blockers),
        "warnings": _dedupe(warnings),
        "next_action": next_action,
        "market_mode": market_mode,
        "market_mode_label": status_payload.get("market_mode_label") or "",
        "data_quality_status": _data_quality_label_for_mode(
            price_label=price_label,
            market_mode=market_mode,
            has_review_candidates=has_review_candidates,
        ),
        "front_category_summary": front_category,
        "limit_up_operational_summary": limit_up_summary,
        "live_count": live_count,
        "delayed_count": delayed_count,
        "cached_count": cached_count,
        "missing_count": missing_count,
        "missing_ratio": missing_ratio,
        "required_stale_layers": blocking_required_layers,
        "non_blocking_review_layers": non_blocking_review_layers,
        "stale_layers": stale_layers,
        "allow_intraday_signal": allow_intraday_signal,
        "can_show_strong_long": can_show_strong and not blockers,
        "can_use_dashboard": status != "blocked",
    }


def _data_quality_label_for_mode(*, price_label: str, market_mode: str, has_review_candidates: bool) -> str:
    if has_review_candidates and market_mode == "pre_open_prepare":
        return "盤前觀察：使用官方日行情快照"
    if has_review_candidates and market_mode in REVIEW_MODES:
        return "復盤觀察：使用上一交易日資料"
    return price_label or "未知"


def _operator_decision(
    *,
    status: str,
    market_mode: str,
    watch_readiness: dict[str, str],
    opening_preflight: dict[str, Any],
    operator_briefing: dict[str, Any],
    operator_mode: dict[str, Any],
    next_action: dict[str, str],
    blockers: list[str],
    warnings: list[str],
    allow_intraday_signal: bool,
    can_show_strong: bool,
) -> dict[str, Any]:
    if status == "blocked":
        headline = "現在不要看即時買多"
        decision = "暫停"
        reason = blockers[0] if blockers else "資料或刷新狀態未準備好。"
        first_action = next_action.get("label") or "先修資料"
        allowed_actions = ["修復資料", "查看復盤", "等待刷新完成"]
        blocked_actions = ["即時進場", "強烈買多判斷", "依候選股追價"]
        can_trade_now = False
    elif market_mode in REVIEW_MODES:
        headline = "現在只做復盤與觀察"
        decision = "復盤"
        reason = "目前不是盤中即時模式，不提供即時進場判斷。"
        first_action = "整理下個交易日觀察清單"
        allowed_actions = ["看上一交易日復盤", "整理觀察清單", "檢查資料健康度"]
        blocked_actions = ["即時強烈買多", "盤中進場判斷"]
        can_trade_now = False
    elif status == "warning":
        headline = "可以看盤，但先保守"
        decision = "保守觀察"
        reason = warnings[0] if warnings else "部分資料或刷新層有提醒。"
        first_action = "只看 live 且資料完整的股票"
        allowed_actions = ["查看 live 標的", "等待觸發條件", "修正資料提醒"]
        blocked_actions = ["依 delayed / cached / missing 進場", "追 high_risk"]
        can_trade_now = False
    elif allow_intraday_signal and can_show_strong:
        headline = "可以進入盤中追蹤"
        decision = "可盯盤"
        reason = "資料與刷新層可用，但仍需逐檔通過進場雷達與風控。"
        first_action = "先看強烈買多，再確認進場雷達"
        allowed_actions = ["盯強烈買多", "確認 VWAP / 量比 / 突破", "檢查停損距離"]
        blocked_actions = ["忽略停損", "追 high_risk", "用法人或族群背景直接進場"]
        can_trade_now = True
    else:
        headline = "資料可用，但現在等訊號"
        decision = "等待"
        reason = "目前沒有可顯示強烈買多的標的。"
        first_action = "等待 VWAP、量比、突破或雷達轉強"
        allowed_actions = ["等待確認", "查看觀察清單", "設定提醒"]
        blocked_actions = ["為了交易而交易", "把觀察股當進場股"]
        can_trade_now = False

    return {
        "decision": decision,
        "headline": headline,
        "reason": reason,
        "first_action": first_action,
        "can_trade_now": can_trade_now,
        "can_open_dashboard": bool(opening_preflight.get("can_open_dashboard")),
        "can_use_intraday_signals": bool(opening_preflight.get("can_use_intraday_signals")),
        "can_trust_strong_buy": bool(opening_preflight.get("can_trust_strong_buy")),
        "watch_readiness": watch_readiness.get("label") or "",
        "operator_posture": operator_briefing.get("posture") or "",
        "operator_mode": operator_mode.get("mode") or "",
        "next_action_label": next_action.get("label") or "",
        "next_action_endpoint": next_action.get("endpoint") or "",
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
    }


def _opening_preflight(
    *,
    status: str,
    market_mode: str,
    next_action: dict[str, str],
    blockers: list[str],
    warnings: list[str],
    allow_intraday_signal: bool,
    can_show_strong: bool,
) -> dict[str, Any]:
    if status == "blocked":
        light = "red"
        label = "暫停使用即時訊號"
        reason = blockers[0] if blockers else "資料或刷新層尚未準備好。"
        action = next_action.get("label") or "先修資料"
    elif market_mode in REVIEW_MODES:
        light = "yellow"
        label = "復盤 / 開盤前觀察"
        reason = "目前不是盤中即時模式，只能看復盤與下個交易日觀察。"
        action = "等待盤中 live 資料"
    elif status == "warning":
        light = "yellow"
        label = "可看盤但需保守"
        reason = warnings[0] if warnings else "有資料延遲、快取或缺漏，需避開有疑慮標的。"
        action = next_action.get("label") or "只看 live 且資料完整的股票"
    elif allow_intraday_signal and can_show_strong:
        light = "green"
        label = "可進入盤中追蹤"
        reason = "資料與刷新層可用，可依強烈買多漏斗與進場雷達逐檔確認。"
        action = "先看強烈買多，再看進場雷達"
    else:
        light = "yellow"
        label = "等待訊號"
        reason = "資料可用，但目前沒有可顯示強烈買多的標的。"
        action = "等待 VWAP、量比、突破或雷達轉強"
    return {
        "light": light,
        "label": label,
        "reason": reason,
        "next_action": action,
        "next_action_endpoint": next_action.get("endpoint") or "",
        "can_open_dashboard": status != "blocked",
        "can_use_intraday_signals": bool(allow_intraday_signal and status != "blocked"),
        "can_trust_strong_buy": bool(can_show_strong and status == "ok"),
        "should_trade_live": bool(light == "green"),
    }


def _operator_briefing(
    *,
    status: str,
    market_mode: str,
    watch_readiness: dict[str, str],
    next_action: dict[str, str],
    operator_mode: dict[str, Any],
    blockers: list[str],
    warnings: list[str],
    can_show_strong: bool,
) -> dict[str, Any]:
    if status == "blocked":
        headline = "先修資料，不看即時訊號"
        posture = "暫停進場判斷"
        next_check = blockers[0] if blockers else "先確認資料源與刷新層。"
        risk_gate = "資料恢復前，強烈買多與買多都不可作為盤中依據。"
    elif market_mode in REVIEW_MODES:
        headline = "休市 / 盤後只做復盤與明日觀察"
        posture = "復盤觀察"
        next_check = "整理下個交易日觀察清單，開盤後等待 live 價格、VWAP 與量比。"
        risk_gate = "非盤中模式不顯示即時強烈買多。"
    elif status == "warning":
        headline = "可以看盤，但要先避開資料有疑慮的股票"
        posture = "保守看盤"
        next_check = warnings[0] if warnings else "只看 live 且資料完整的股票。"
        risk_gate = "cached、delayed、missing 一律只能觀察。"
    elif can_show_strong:
        headline = "資料可用，照強烈買多漏斗與進場雷達看盤"
        posture = "盤中作戰"
        next_check = "先看強烈買多候選，再逐檔確認 VWAP、量比、突破與停損距離。"
        risk_gate = "high_risk、未站上 VWAP、停損距離過大都不可當作買多。"
    else:
        headline = "資料可用，但目前沒有強烈買多可執行"
        posture = "等待確認"
        next_check = "等待量能、VWAP、突破或進場雷達轉強。"
        risk_gate = "沒有訊號就空手，不為了交易而交易。"
    limit_context = _as_dict(operator_mode.get("limit_up_context"))
    if (
        market_mode == "intraday"
        and status != "blocked"
        and _int(limit_context.get("near_limit_up_count")) > 0
    ):
        phase_label = str(limit_context.get("market_phase_label") or "急拉 / 漲停盤")
        headline = f"{phase_label}：先看追價風險與進場雷達"
        next_check = str(
            limit_context.get("operator_priority")
            or limit_context.get("market_phase_summary")
            or limit_context.get("summary")
            or next_check
        )
        risk_gate = str(limit_context.get("risk_gate") or "接近漲停不可直接升級買多。")

    return {
        "headline": headline,
        "posture": posture,
        "watch_readiness": watch_readiness.get("label") or "",
        "next_check": next_check,
        "next_action_label": next_action.get("label") or "",
        "next_action_endpoint": next_action.get("endpoint") or "",
        "risk_gate": risk_gate,
        "do_now": list(operator_mode.get("do_now") or [])[:3],
        "do_not_do": list(operator_mode.get("do_not_do") or [])[:3],
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


def _refresh_plan(
    required_stale_layers: list[str],
    refresh_summary: dict[str, Any],
    next_action: dict[str, str],
) -> list[str]:
    endpoints: list[str] = []
    blocking_layers = _list(refresh_summary.get("blocking_layers"))
    for layer in ("full_market", "watchlist", "positions", "post_close_validation", "manual_full_refresh"):
        if layer in required_stale_layers or layer in blocking_layers:
            endpoints.append(_layer_endpoint(layer))
    next_endpoint = str(next_action.get("endpoint") or "")
    if next_endpoint.startswith("/refresh"):
        endpoints.append(next_endpoint)
    return _dedupe(endpoints)


def _operator_steps(
    *,
    status: str,
    market_mode: str,
    next_action: dict[str, str],
    refresh_plan: list[str],
    blockers: list[str],
    warnings: list[str],
) -> list[str]:
    if status == "blocked":
        if refresh_plan:
            return [
                "先執行刷新計畫：" + " → ".join(refresh_plan),
                "刷新完成後重新檢查 /api/health 或公開驗收腳本。",
                "資料恢復前只做復盤與觀察，不使用即時買多判斷。",
            ]
        first_step = str(next_action.get("label") or (blockers[0] if blockers else "先修正資料狀態。"))
        return [
            first_step,
            "必要時執行完整刷新，並檢查資料源健康度。",
            "狀態未恢復前，不顯示或依賴強烈買多。",
        ]
    if market_mode == "pre_open_prepare":
        return [
            "08:55 先看 /dashboard 的開盤檢查、資料可信度與下個交易日觀察清單。",
            "09:00 後先等 5 到 10 分鐘，確認今日 VWAP、量比、突破與進場雷達更新。",
            "資料未轉 live、未站上 VWAP 或缺量比前，不提前當作即時買多。",
        ]
    if market_mode in {"closed_review", "post_close_review"}:
        return [
            "先看上一交易日復盤與盤後驗證結果。",
            "整理下個交易日觀察清單，不把盤後資料當即時訊號。",
            "下個交易日開盤後，再用 live 資料重新確認。",
        ]
    if status == "warning":
        return [
            warnings[0] if warnings else "目前可看但需保守。",
            "只把 live 且資料完整的標的納入盤中判斷。",
            "cached、delayed、missing 標的只能觀察，不可顯示強烈買多。",
        ]
    if market_mode == "intraday":
        return [
            "先看強烈買多與買多清單，但不為了交易而交易。",
            "逐檔確認 VWAP、量比、突破、停損距離與進場雷達。",
            "持倉只依停損、停利與失效條件管理。",
        ]
    return [
        "先確認市場模式與資料可信度。",
        "再看候選股清單與個股作戰卡。",
        "不確定時維持觀察，不勉強進場。",
    ]


def _operator_mode(
    *,
    status: str,
    market_mode: str,
    blockers: list[str],
    warnings: list[str],
    refresh_plan: list[str],
) -> dict[str, Any]:
    if status == "blocked":
        return {
            "mode": "資料修復模式",
            "primary_focus": blockers[0] if blockers else "先修復資料與刷新層，再看訊號。",
            "do_now": [
                "依刷新計畫執行：" + " → ".join(refresh_plan) if refresh_plan else "先執行 /refresh_watchlist 或 /refresh_full_market。",
                "確認 TWSE / TPEX / Yahoo / Fugle 資料健康度。",
                "修復前只看復盤與觀察，不使用即時買多判斷。",
            ],
            "do_not_do": [
                "不要把 delayed / cached / missing 當成即時訊號。",
                "不要因畫面有候選股就進場。",
            ],
            "decision_checklist": [
                "必要刷新層是否恢復？",
                "價格資料是否 live？",
                "資料日期是否符合目前模式？",
            ],
        }
    if market_mode == "pre_open_prepare":
        return {
            "mode": "開盤前準備模式",
            "primary_focus": "先整理觀察清單，09:00 後等 VWAP、量比、突破重新確認。",
            "do_now": [
                "08:55 先確認 /dashboard 開盤檢查與資料可信度。",
                "標記下個交易日觀察清單中最接近 VWAP / 突破 / 量能確認的股票。",
                "09:00 後先等 5 到 10 分鐘，資料轉 live 再看進場雷達。",
            ],
            "do_not_do": [
                "不要把昨日資料當成即時買多。",
                "不要在沒有 VWAP 與量比前下結論。",
            ],
            "decision_checklist": [
                "股票是否站上 VWAP？",
                "量比是否接近 1.0？",
                "是否突破昨日高點或觸發價？",
            ],
        }
    if market_mode in {"closed_review", "post_close_review"}:
        return {
            "mode": "復盤準備模式",
            "primary_focus": "檢查上一交易日結果，整理下個交易日觀察，不做即時進場判斷。",
            "do_now": [
                "看上一交易日復盤與盤後驗證。",
                "檢查 high_risk / avoid 是否有可惜漏掉。",
                "把 B+、買多、強勢但追價高的股票放入下個交易日觀察。",
            ],
            "do_not_do": [
                "不要顯示或依賴即時強烈買多。",
                "不要用收盤後資料回推成盤中可買。",
            ],
            "decision_checklist": [
                "盤後驗證是否寫入？",
                "漏抓診斷是 missed_by_pool 還是 seen_but_filtered？",
                "下個交易日需要盯哪 5 到 10 檔？",
            ],
        }
    if market_mode == "intraday" and status == "warning":
        return {
            "mode": "盤中保守模式",
            "primary_focus": warnings[0] if warnings else "盤中可看，但資料或刷新層有提醒，需保守。",
            "do_now": [
                "只看 live 且資料完整的股票。",
                "cached / delayed / missing 只列觀察。",
                "先確認最大卡關原因，再看下一步觸發條件。",
            ],
            "do_not_do": [
                "不要把資料提醒中的股票當成強烈買多。",
                "不要忽略停損距離與進場雷達。",
            ],
            "decision_checklist": [
                "price_status 是否 live？",
                "watchlist 與 positions 層是否未過期？",
                "VWAP、量比、停損價是否完整？",
            ],
        }
    if market_mode == "intraday":
        return {
            "mode": "盤中作戰模式",
            "primary_focus": "先看強烈買多與買多，再逐檔確認進場雷達與風控。",
            "do_now": [
                "先看強烈買多候選與買多清單。",
                "逐檔確認 VWAP、量比、突破、停損距離。",
                "有持倉時優先看停損、停利與失效條件。",
            ],
            "do_not_do": [
                "不要追 high_risk。",
                "不要因法人或族群背景直接升級買多。",
                "沒有訊號就空手。",
            ],
            "decision_checklist": [
                "資料是否 live？",
                "是否站上 VWAP？",
                "量比是否足夠？",
                "突破是否成立？",
                "停損距離是否合理？",
            ],
        }
    return {
        "mode": "檢查模式",
        "primary_focus": "先確認市場模式與資料品質，再看候選股。",
        "do_now": ["檢查市場模式。", "檢查資料健康度。", "只看資料完整的個股。"],
        "do_not_do": ["不要在模式不明時進場。"],
        "decision_checklist": ["市場模式是否明確？", "資料品質是否正常？"],
    }


def _apply_limit_up_context(operator_mode: dict[str, Any], limit_up_summary: dict[str, Any], *, status: str, market_mode: str) -> dict[str, Any]:
    if status == "blocked" or market_mode != "intraday":
        return operator_mode
    if _int(limit_up_summary.get("near_limit_up_count")) <= 0:
        return operator_mode
    updated = dict(operator_mode)
    do_now = list(updated.get("do_now") or [])
    do_not_do = list(updated.get("do_not_do") or [])
    checklist = list(updated.get("decision_checklist") or [])
    action = str(limit_up_summary.get("action") or "先看漲停強勢速讀與急拉作戰卡。")
    risk_gate = str(limit_up_summary.get("risk_gate") or "接近漲停不可直接升級買多。")
    phase_summary = str(limit_up_summary.get("market_phase_summary") or limit_up_summary.get("summary") or "")
    operator_priority = str(limit_up_summary.get("operator_priority") or "")
    updated["primary_focus"] = phase_summary or str(updated.get("primary_focus") or "")
    updated["do_now"] = _dedupe([action, operator_priority] + do_now)
    updated["do_not_do"] = _dedupe([risk_gate] + do_not_do)
    updated["decision_checklist"] = _dedupe(
        [
            "急拉股是否仍站上 VWAP？",
            "停損距離是否合理？",
            "進場雷達是否轉強？",
        ]
        + checklist
    )
    updated["limit_up_context"] = limit_up_summary
    return updated


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


def _watch_readiness(status: str, market_mode: str) -> dict[str, str]:
    if status == "blocked":
        return {
            "label": "暫不適合進場判斷",
            "message": "先處理資料或刷新層，再重新檢查。",
        }
    if market_mode not in INTRADAY_MODES:
        return {
            "label": "僅供復盤或開盤前觀察",
            "message": "目前不是盤中即時模式，不提供即時買多判斷。",
        }
    if status == "warning":
        return {
            "label": "可看但需保守",
            "message": "延遲、使用上一筆或資料不足標的不可作為進場依據。",
        }
    return {
        "label": "可正常看盤",
        "message": "仍需依停損、失效條件與進場雷達確認。",
    }


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
