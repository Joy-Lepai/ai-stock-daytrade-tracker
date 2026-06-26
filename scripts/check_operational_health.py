#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_daytrade_system.operational_health import build_operational_health


DEFAULT_BASE_URL = "https://stock.letslepai.com"


def fetch_json(base_url: str, path: str, *, timeout: float = 12.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def post_json(base_url: str, path: str, *, timeout: float = 240.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=b"",
        headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        payload = {"raw_response": body}
    return payload if isinstance(payload, dict) else {"response": payload}


def fetch_health_payload(base_url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    try:
        payload = fetch_json(base_url, "/api/operator/runbook", timeout=timeout)
        payload["_health_source"] = "/api/operator/runbook"
        return payload
    except Exception as runbook_error:
        try:
            payload = fetch_json(base_url, "/api/health", timeout=timeout)
            payload["_health_source"] = f"/api/health fallback after /api/operator/runbook failed: {runbook_error}"
            return payload
        except Exception as health_error:
            payload = fetch_json(base_url, "/api/refresh/status", timeout=timeout)
            payload["_health_source"] = (
                "/api/refresh/status fallback after /api/operator/runbook and /api/health failed: "
                f"{runbook_error}; {health_error}"
            )
            return payload


def _is_runbook_payload(payload: dict[str, Any]) -> bool:
    return payload.get("api_status") == "ok" and (
        "now_steps" in payload or "first_action" in payload or "refresh_actions" in payload
    )


def _resolve_health(payload: dict[str, Any]) -> dict[str, Any]:
    if _is_runbook_payload(payload):
        front_summary = dict(payload.get("front_category_summary") or {})
        warnings = list(payload.get("warnings") or [])
        can_trust_strong_buy = bool(payload.get("can_trust_strong_buy"))
        if not front_summary:
            can_trust_strong_buy = False
            warnings.append("尚未取得四分類摘要，不可信任強烈買多。")
        status = "blocked" if payload.get("blockers") else "ok" if payload.get("can_trade_now") else "warning"
        if status == "ok" and not can_trust_strong_buy:
            status = "warning"
        return {
            "status": status,
            "summary": payload.get("headline") or payload.get("decision") or "",
            "operator_decision": {
                "decision": payload.get("decision") or "",
                "headline": payload.get("headline") or "",
                "reason": payload.get("watch_readiness_message") or "",
                "first_action": payload.get("first_action") or "",
                "can_trade_now": bool(payload.get("can_trade_now")),
                "can_use_intraday_signals": bool(payload.get("can_use_intraday_signals")),
                "can_trust_strong_buy": can_trust_strong_buy,
            },
            "operator_briefing": {
                "headline": payload.get("headline") or "",
                "posture": payload.get("mode") or "",
                "next_check": payload.get("first_action") or "",
                "risk_gate": "只依資料可信、盤中且進場雷達通過的標的判斷。",
            },
            "watch_readiness": payload.get("watch_readiness") or "",
            "watch_readiness_message": payload.get("watch_readiness_message") or "",
            "operator_mode": payload.get("mode") or "",
            "primary_focus": payload.get("first_action") or "",
            "do_now": list(payload.get("now_steps") or []),
            "do_not_do": list(payload.get("do_not_do") or []),
            "decision_checklist": list(payload.get("checklist") or []),
            "market_mode": payload.get("market_mode") or "",
            "market_mode_label": payload.get("market_mode_label") or "",
            "data_quality_status": payload.get("data_quality_status") or "",
            "front_category_summary": front_summary,
            "blockers": list(payload.get("blockers") or []),
            "warnings": warnings,
            "operator_steps": list(payload.get("now_steps") or []),
            "next_action": _runbook_next_action(payload),
            "refresh_plan": [str(item) for item in (payload.get("refresh_actions") or []) if str(item).startswith("/refresh")],
            "deployment": dict(payload.get("deployment") or {}),
        }
    health = payload.get("operational_health") if isinstance(payload.get("operational_health"), dict) else None
    if health is None and payload.get("status") in {"ok", "warning", "blocked"}:
        health = payload
    if not isinstance(health, dict):
        health = build_operational_health(payload)
    elif not health.get("operator_steps") and (
        "price_status_summary" in payload or "required_stale_layers" in payload or "refresh_operation_summary" in payload
    ):
        inferred = build_operational_health(payload)
        health = {**inferred, **health, "operator_steps": inferred.get("operator_steps") or []}
    return health


def build_json_report(payload: dict[str, Any], *, base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    health = _resolve_health(payload)
    status = str(health.get("status") or "blocked")
    price = health.get("price_status_summary") if isinstance(health.get("price_status_summary"), dict) else {}
    front = health.get("front_category_summary") if isinstance(health.get("front_category_summary"), dict) else {}
    next_action = _next_action(payload, health)
    plan = refresh_plan(payload, health)
    task_card = _operator_task_card(health, payload, next_action=next_action, refresh_plan=plan, base_url=base_url)
    return {
        "status": status,
        "exit_code": 0 if status in {"ok", "warning"} else 1,
        "summary": health.get("summary") or "",
        "operator_task_card": task_card,
        "operator_url": f"{base_url.rstrip('/')}/operator",
        "opening_preflight": dict(health.get("opening_preflight") or {}),
        "operator_decision": dict(health.get("operator_decision") or {}),
        "operator_briefing": dict(health.get("operator_briefing") or {}),
        "watch_readiness": health.get("watch_readiness") or "",
        "watch_readiness_message": health.get("watch_readiness_message") or "",
        "watch_readiness_label": _watch_readiness_label(status, health, payload),
        "operator_mode": health.get("operator_mode") or "",
        "primary_focus": health.get("primary_focus") or "",
        "do_now": list(health.get("do_now") or []),
        "do_not_do": list(health.get("do_not_do") or []),
        "decision_checklist": list(health.get("decision_checklist") or []),
        "market_mode": health.get("market_mode") or payload.get("market_mode") or "",
        "data_quality_status": health.get("data_quality_status") or "",
        "counts": {
            "live": health.get("live_count", price.get("live_count", 0)),
            "delayed": health.get("delayed_count", price.get("delayed_count", 0)),
            "cached": health.get("cached_count", price.get("cached_count", 0)),
            "missing": health.get("missing_count", price.get("missing_count", 0)),
        },
        "front_category_summary": _front_category_report(front),
        "blockers": list(health.get("blockers") or []),
        "warnings": list(health.get("warnings") or []),
        "operator_steps": [str(item) for item in (health.get("operator_steps") or [])],
        "required_stale_layers": list(payload.get("required_stale_layers") or []),
        "stale_layers": list(payload.get("stale_layers") or []),
        "next_action": next_action,
        "manual_endpoint": (
            f"POST {next_action.get('endpoint')}" if str(next_action.get("endpoint") or "").startswith("/refresh") else ""
        ),
        "manual_curl": f"curl -X POST {base_url.rstrip('/')}{next_action.get('endpoint')}"
        if str(next_action.get("endpoint") or "").startswith("/refresh")
        else "",
        "refresh_plan": plan,
        "source": payload.get("_health_source") or "",
    }


def build_failure_json(error: Exception | str) -> dict[str, Any]:
    message = str(error)
    return {
        "status": "blocked",
        "exit_code": 1,
        "summary": f"無法讀取 /api/operator/runbook、/api/health 或 /api/refresh/status：{message}",
        "operator_briefing": {
            "headline": "健康檢查讀取失敗",
            "posture": "暫停進場判斷",
            "watch_readiness": "blocked",
            "next_check": "確認網站是否啟動，或稍後重試。",
            "next_action_label": "確認網站是否啟動，或稍後重試。",
            "next_action_endpoint": "",
            "risk_gate": "讀不到健康狀態時，不可依公開畫面做即時進場判斷。",
            "do_now": ["確認網站是否啟動", "稍後重試健康檢查"],
            "do_not_do": ["不要依此狀態判斷盤中訊號"],
        },
        "operator_decision": {
            "decision": "暫停",
            "headline": "健康檢查讀取失敗",
            "reason": message,
            "first_action": "確認網站是否啟動，或稍後重試。",
            "can_trade_now": False,
        },
        "watch_readiness": "blocked",
        "watch_readiness_message": "網站健康檢查讀取失敗",
        "watch_readiness_label": "暫不適合進場判斷，先確認網站是否啟動。",
        "operator_mode": "health_check_failed",
        "primary_focus": "確認網站服務與健康檢查 API",
        "do_now": ["確認網站是否啟動", "稍後重試健康檢查"],
        "do_not_do": ["不要依此狀態判斷盤中訊號"],
        "decision_checklist": [],
        "market_mode": "",
        "data_quality_status": "",
        "counts": {"live": 0, "delayed": 0, "cached": 0, "missing": 0},
        "blockers": [message],
        "warnings": [],
        "operator_steps": ["確認網站是否啟動，或稍後重試。"],
        "required_stale_layers": [],
        "stale_layers": [],
        "next_action": {"label": "確認網站是否啟動，或稍後重試。", "endpoint": ""},
        "manual_endpoint": "",
        "manual_curl": "",
        "refresh_plan": [],
        "source": "",
    }


def render_report(payload: dict[str, Any], *, base_url: str = DEFAULT_BASE_URL) -> tuple[int, str]:
    health = _resolve_health(payload)
    status = str(health.get("status") or "blocked")
    mark = "PASS" if status == "ok" else "WARN" if status == "warning" else "FAIL"
    price = health.get("price_status_summary") if isinstance(health.get("price_status_summary"), dict) else {}
    front = health.get("front_category_summary") if isinstance(health.get("front_category_summary"), dict) else {}
    next_action = _next_action(payload, health)
    plan = refresh_plan(payload, health)
    task_card = _operator_task_card(health, payload, next_action=next_action, refresh_plan=plan, base_url=base_url)
    lines = [
        "Operator runbook" if _is_runbook_payload(payload) else "Operational health",
        f"[{mark}] {health.get('summary') or '-'}",
        f"operator_page: {base_url.rstrip('/')}/operator",
        "operator_task_card:",
        f"- status: {task_card.get('status_label') or '-'}",
        f"- first_step: {task_card.get('first_step') or '-'}",
        f"- do_not: {task_card.get('do_not') or '-'}",
        f"- refresh: {task_card.get('refresh_command') or '-'}",
        f"watch_readiness: {_watch_readiness_label(status, health, payload)}",
        f"market_mode: {health.get('market_mode') or payload.get('market_mode') or '-'}",
        f"data_quality: {health.get('data_quality_status') or '-'}",
        "counts: "
        f"live={health.get('live_count', price.get('live_count', 0))} "
        f"delayed={health.get('delayed_count', price.get('delayed_count', 0))} "
        f"cached={health.get('cached_count', price.get('cached_count', 0))} "
        f"missing={health.get('missing_count', price.get('missing_count', 0))}",
    ]
    front_report = _front_category_report(front)
    if front_report:
        lines.append(
            "front_category_summary: "
            f"strong_buy={front_report.get('strong_buy', 0)} "
            f"buy={front_report.get('buy', 0)} "
            f"watch={front_report.get('watch', 0)} "
            f"bearish={front_report.get('bearish', 0)}"
        )
        if front_report.get("no_signal_reason"):
            lines.append(f"front_no_signal_reason: {front_report.get('no_signal_reason')}")
    if payload.get("_health_source"):
        lines.append(f"source: {payload.get('_health_source')}")
    preflight = health.get("opening_preflight") if isinstance(health.get("opening_preflight"), dict) else {}
    if preflight:
        lines.append("opening_preflight:")
        lines.append(f"- light: {preflight.get('light') or '-'}")
        lines.append(f"- label: {preflight.get('label') or '-'}")
        lines.append(f"- reason: {preflight.get('reason') or '-'}")
        lines.append(f"- next_action: {preflight.get('next_action') or '-'}")
        lines.append(f"- can_trust_strong_buy: {bool(preflight.get('can_trust_strong_buy'))}")
    decision = health.get("operator_decision") if isinstance(health.get("operator_decision"), dict) else {}
    if decision:
        lines.append("operator_decision:")
        lines.append(f"- decision: {decision.get('decision') or '-'}")
        lines.append(f"- headline: {decision.get('headline') or '-'}")
        lines.append(f"- reason: {decision.get('reason') or '-'}")
        lines.append(f"- first_action: {decision.get('first_action') or '-'}")
        lines.append(f"- can_trade_now: {bool(decision.get('can_trade_now'))}")
        if "can_trust_strong_buy" in decision:
            lines.append(f"- can_trust_strong_buy: {bool(decision.get('can_trust_strong_buy'))}")
    briefing = health.get("operator_briefing") if isinstance(health.get("operator_briefing"), dict) else {}
    if briefing:
        lines.append("operator_briefing:")
        lines.append(f"- headline: {briefing.get('headline') or '-'}")
        lines.append(f"- posture: {briefing.get('posture') or '-'}")
        lines.append(f"- next_check: {briefing.get('next_check') or '-'}")
        lines.append(f"- risk_gate: {briefing.get('risk_gate') or '-'}")
    blockers = health.get("blockers") or []
    warnings = health.get("warnings") or []
    if blockers:
        lines.append("blockers:")
        lines.extend(f"- {item}" for item in blockers)
    if warnings:
        lines.append("warnings:")
        lines.extend(f"- {item}" for item in warnings)
    operator_steps = [str(item) for item in (health.get("operator_steps") or [])]
    if operator_steps:
        lines.append("operator_steps:")
        lines.extend(f"{index}. {item}" for index, item in enumerate(operator_steps, start=1))
    required_stale = payload.get("required_stale_layers") or []
    stale_layers = payload.get("stale_layers") or []
    if required_stale:
        lines.append(f"required_stale_layers: {', '.join(str(item) for item in required_stale)}")
    if stale_layers:
        lines.append(f"stale_layers: {', '.join(str(item) for item in stale_layers)}")
    action_label = str(next_action.get("label") or "-")
    action_endpoint = str(next_action.get("endpoint") or "")
    lines.append(f"next_action: {action_label} {action_endpoint}".strip())
    if action_endpoint.startswith("/refresh"):
        lines.append(f"manual_endpoint: POST {action_endpoint}")
        lines.append(f"manual_curl: curl -X POST {base_url.rstrip('/')}{action_endpoint}")
    if plan:
        lines.append(f"refresh_plan: {' -> '.join(plan)}")
    return (0 if status in {"ok", "warning"} else 1, "\n".join(lines))


def _watch_readiness_label(status: str, health: dict[str, Any], payload: dict[str, Any]) -> str:
    if health.get("watch_readiness"):
        message = str(health.get("watch_readiness_message") or "")
        return f"{health.get('watch_readiness')}，{message}" if message else str(health.get("watch_readiness"))
    mode = str(health.get("market_mode") or payload.get("market_mode") or "")
    if status == "blocked":
        return "暫不適合進場判斷，先處理資料或刷新層。"
    if mode != "intraday":
        return "非盤中模式，僅供復盤或開盤前觀察。"
    if status == "warning":
        return "可看但需保守，延遲或缺漏標的不可作為進場依據。"
    return "可正常看盤，仍需依停損與進場雷達確認。"


def _front_category_report(summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, dict) or not summary:
        return {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return {
        "strong_buy": _safe_int(summary.get("strong_buy_count", counts.get("強烈買多", 0))),
        "buy": _safe_int(summary.get("buy_count", counts.get("買多", 0))),
        "watch": _safe_int(summary.get("watch_count", counts.get("觀察", 0))),
        "bearish": _safe_int(summary.get("bearish_count", counts.get("看空", 0))),
        "data_missing": _safe_int(summary.get("data_missing_count", counts.get("資料不足", 0))),
        "no_signal_reason": str(summary.get("no_signal_reason") or ""),
    }


def _operator_task_card(
    health: dict[str, Any],
    payload: dict[str, Any],
    *,
    next_action: dict[str, Any],
    refresh_plan: list[str],
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, str]:
    status = str(health.get("status") or "blocked")
    decision = health.get("operator_decision") if isinstance(health.get("operator_decision"), dict) else {}
    preflight = health.get("opening_preflight") if isinstance(health.get("opening_preflight"), dict) else {}
    front = _front_category_report(health.get("front_category_summary") if isinstance(health.get("front_category_summary"), dict) else {})
    strong = _safe_int(front.get("strong_buy"))
    buy = _safe_int(front.get("buy"))
    watch = _safe_int(front.get("watch"))
    bearish = _safe_int(front.get("bearish"))
    mode = str(health.get("market_mode") or payload.get("market_mode") or "")
    can_trust_strong = bool(
        decision.get("can_trust_strong_buy")
        if "can_trust_strong_buy" in decision
        else preflight.get("can_trust_strong_buy") if "can_trust_strong_buy" in preflight else health.get("can_show_strong_long")
    )
    blockers = [str(item) for item in (health.get("blockers") or []) if str(item).strip()]
    warnings = [str(item) for item in (health.get("warnings") or []) if str(item).strip()]
    do_not = [str(item) for item in (health.get("do_not_do") or []) if str(item).strip()]
    endpoint = str(next_action.get("endpoint") or "")

    if status == "blocked":
        status_label = "暫停：先修資料或刷新層"
        first_step = next_action.get("label") or (blockers[0] if blockers else "先修資料")
    elif mode != "intraday":
        status_label = "非盤中：只做復盤與觀察"
        first_step = "看上一交易日復盤與下個交易日觀察清單"
    elif can_trust_strong and strong > 0:
        status_label = f"可盯盤：強烈買多 {strong} 檔"
        first_step = "先看強烈買多，再逐檔確認進場雷達與停損距離"
    elif buy > 0:
        status_label = f"等待觸發：買多 {buy} 檔、觀察 {watch} 檔"
        first_step = "先看買多清單的下一步觸發條件，不提前追"
    else:
        status_label = f"沒有可用買多：觀察 {watch} 檔、看空 {bearish} 檔"
        first_step = front.get("no_signal_reason") or (warnings[0] if warnings else "先看最大卡關原因與資料狀態")

    return {
        "status_label": str(status_label),
        "first_step": str(first_step),
        "do_not": do_not[0] if do_not else "不要把觀察、high_risk、delayed、cached 當成可進場",
        "refresh_command": f"POST {endpoint}" if endpoint.startswith("/refresh") else (" -> ".join(refresh_plan) if refresh_plan else "不需手動刷新"),
        "operator_page": f"{base_url.rstrip('/')}/operator",
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _next_action(payload: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    required_stale = list(payload.get("required_stale_layers") or [])
    if "full_market" in required_stale:
        return {"label": "更新全市場", "endpoint": "/refresh_full_market"}
    if "watchlist" in required_stale:
        return {"label": "更新重點觀察", "endpoint": "/refresh_watchlist"}
    if "positions" in required_stale:
        return {"label": "更新持倉/觸發", "endpoint": "/refresh_positions"}
    if "post_close_validation" in required_stale:
        return {"label": "更新盤後驗證", "endpoint": "/refresh_post_close_validation"}
    if "manual_full_refresh" in required_stale:
        return {"label": "完整刷新", "endpoint": "/refresh"}
    health_action = health.get("next_action") if isinstance(health.get("next_action"), dict) else {}
    if health_action.get("label"):
        return health_action
    guidance = payload.get("refresh_guidance") if isinstance(payload.get("refresh_guidance"), dict) else {}
    if guidance.get("action_label"):
        return {"label": guidance.get("action_label"), "endpoint": guidance.get("action_endpoint") or ""}
    operation = payload.get("refresh_operation_summary") if isinstance(payload.get("refresh_operation_summary"), dict) else {}
    blocking = list(operation.get("blocking_layers") or [])
    if "full_market" in blocking:
        return {"label": "更新全市場", "endpoint": "/refresh_full_market"}
    if "watchlist" in blocking:
        return {"label": "更新重點觀察", "endpoint": "/refresh_watchlist"}
    if "positions" in blocking:
        return {"label": "更新持倉/觸發", "endpoint": "/refresh_positions"}
    if "post_close_validation" in blocking:
        return {"label": "更新盤後驗證", "endpoint": "/refresh_post_close_validation"}
    if "manual_full_refresh" in blocking:
        return {"label": "完整刷新", "endpoint": "/refresh"}
    return {"label": "-", "endpoint": ""}


def _runbook_next_action(payload: dict[str, Any]) -> dict[str, Any]:
    refresh_actions = [str(item) for item in (payload.get("refresh_actions") or []) if str(item).startswith("/refresh")]
    first_action = str(payload.get("first_action") or "")
    if refresh_actions:
        return {"label": first_action or "執行建議刷新", "endpoint": refresh_actions[0]}
    return {"label": first_action or "-", "endpoint": ""}


def refresh_plan(payload: dict[str, Any], health: Optional[dict[str, Any]] = None) -> list[str]:
    if _is_runbook_payload(payload):
        return [str(endpoint) for endpoint in (payload.get("refresh_actions") or []) if str(endpoint).startswith("/refresh")]
    health = health or payload.get("operational_health") or {}
    provided_plan = health.get("refresh_plan") if isinstance(health, dict) else None
    if isinstance(provided_plan, list):
        return [str(endpoint) for endpoint in provided_plan if str(endpoint).startswith("/refresh")]
    endpoints: list[str] = []
    required_stale = [str(item) for item in (payload.get("required_stale_layers") or [])]
    operation = payload.get("refresh_operation_summary") if isinstance(payload.get("refresh_operation_summary"), dict) else {}
    blocking = [str(item) for item in (operation.get("blocking_layers") or [])]
    for layer in ("full_market", "watchlist", "positions", "post_close_validation", "manual_full_refresh"):
        if layer in required_stale or layer in blocking:
            endpoints.append(_layer_endpoint(layer))
    next_endpoint = str((_next_action(payload, health) or {}).get("endpoint") or "")
    if next_endpoint.startswith("/refresh"):
        endpoints.append(next_endpoint)
    result: list[str] = []
    for endpoint in endpoints:
        if endpoint and endpoint not in result:
            result.append(endpoint)
    return result


def apply_refresh_plan(base_url: str, endpoints: list[str], *, timeout: float = 240.0) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for endpoint in endpoints:
        try:
            payload = post_json(base_url, endpoint, timeout=timeout)
        except Exception as exc:
            results.append((endpoint, False, str(exc)))
            continue
        status = str(payload.get("status") or payload.get("api_status") or "ok")
        error = str(payload.get("error") or "")
        ok = status not in {"failed", "error"} and not error
        detail = error or status
        results.append((endpoint, ok, detail))
    return results


def _layer_endpoint(layer: str) -> str:
    return {
        "full_market": "/refresh_full_market",
        "watchlist": "/refresh_watchlist",
        "positions": "/refresh_positions",
        "post_close_validation": "/refresh_post_close_validation",
        "manual_full_refresh": "/refresh",
    }.get(layer, "")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check dashboard operational readiness from /api/operator/runbook.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument(
        "--apply-refresh-plan",
        action="store_true",
        help="POST the ordered refresh plan, then fetch and print health again.",
    )
    parser.add_argument("--refresh-timeout", type=float, default=240.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    args = parser.parse_args(argv)
    try:
        payload = fetch_health_payload(args.base_url, timeout=args.timeout)
    except Exception as exc:
        if args.json:
            print(json.dumps(build_failure_json(exc), ensure_ascii=False, indent=2))
            return 1
        print("Operator runbook")
        print(f"[FAIL] 無法讀取 /api/operator/runbook、/api/health 或 /api/refresh/status：{exc}")
        print("next_action: 確認網站是否啟動，或稍後重試。")
        return 1
    if args.json and args.apply_refresh_plan:
        initial_report = build_json_report(payload, base_url=args.base_url)
        plan = list(initial_report.get("refresh_plan") or [])
        apply_results: list[dict[str, Any]] = []
        refreshed_report: dict[str, Any] | None = None
        refresh_failures = 0
        if plan:
            for endpoint, ok, detail in apply_refresh_plan(args.base_url, plan, timeout=args.refresh_timeout):
                apply_results.append({"endpoint": endpoint, "ok": ok, "detail": detail or ""})
                refresh_failures += 0 if ok else 1
            try:
                refreshed_payload = fetch_health_payload(args.base_url, timeout=args.timeout)
                refreshed_report = build_json_report(refreshed_payload, base_url=args.base_url)
            except Exception as exc:
                refreshed_report = build_failure_json(f"refresh completed but health recheck failed: {exc}")
                refresh_failures += 1
        result_payload = {
            "status": refreshed_report.get("status") if refreshed_report else initial_report.get("status"),
            "exit_code": 1 if refresh_failures else int((refreshed_report or initial_report).get("exit_code") or 0),
            "initial": initial_report,
            "apply_results": apply_results,
            "refreshed": refreshed_report,
        }
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        return int(result_payload["exit_code"])
    if args.json:
        report_payload = build_json_report(payload, base_url=args.base_url)
        print(json.dumps(report_payload, ensure_ascii=False, indent=2))
        return int(report_payload.get("exit_code") or 0)
    exit_code, report = render_report(payload, base_url=args.base_url)
    print(report)
    if args.apply_refresh_plan:
        plan = refresh_plan(payload)
        if not plan:
            print()
            print("Refresh plan: no refresh endpoint needed.")
            return exit_code
        print()
        print("Applying refresh plan")
        refresh_failures = 0
        for endpoint, ok, detail in apply_refresh_plan(args.base_url, plan, timeout=args.refresh_timeout):
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] POST {endpoint}: {detail or '-'}")
            refresh_failures += 0 if ok else 1
        print()
        try:
            refreshed_payload = fetch_health_payload(args.base_url, timeout=args.timeout)
        except Exception as exc:
            print(f"[FAIL] refresh completed but health recheck failed: {exc}")
            return 1
        refreshed_exit_code, refreshed_report = render_report(refreshed_payload, base_url=args.base_url)
        print(refreshed_report)
        return 1 if refresh_failures else refreshed_exit_code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
