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
        payload = fetch_json(base_url, "/api/health", timeout=timeout)
        payload["_health_source"] = "/api/health"
        return payload
    except Exception as health_error:
        payload = fetch_json(base_url, "/api/refresh/status", timeout=timeout)
        payload["_health_source"] = f"/api/refresh/status fallback after /api/health failed: {health_error}"
        return payload


def _resolve_health(payload: dict[str, Any]) -> dict[str, Any]:
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
    next_action = _next_action(payload, health)
    plan = refresh_plan(payload, health)
    return {
        "status": status,
        "exit_code": 0 if status in {"ok", "warning"} else 1,
        "summary": health.get("summary") or "",
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
        "summary": f"無法讀取 /api/health 或 /api/refresh/status：{message}",
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
    lines = [
        "Operational health",
        f"[{mark}] {health.get('summary') or '-'}",
        f"watch_readiness: {_watch_readiness_label(status, health, payload)}",
        f"market_mode: {health.get('market_mode') or payload.get('market_mode') or '-'}",
        f"data_quality: {health.get('data_quality_status') or '-'}",
        "counts: "
        f"live={health.get('live_count', price.get('live_count', 0))} "
        f"delayed={health.get('delayed_count', price.get('delayed_count', 0))} "
        f"cached={health.get('cached_count', price.get('cached_count', 0))} "
        f"missing={health.get('missing_count', price.get('missing_count', 0))}",
    ]
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
    next_action = _next_action(payload, health)
    action_label = str(next_action.get("label") or "-")
    action_endpoint = str(next_action.get("endpoint") or "")
    lines.append(f"next_action: {action_label} {action_endpoint}".strip())
    if action_endpoint.startswith("/refresh"):
        lines.append(f"manual_endpoint: POST {action_endpoint}")
        lines.append(f"manual_curl: curl -X POST {base_url.rstrip('/')}{action_endpoint}")
    plan = refresh_plan(payload, health)
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


def refresh_plan(payload: dict[str, Any], health: Optional[dict[str, Any]] = None) -> list[str]:
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
    parser = argparse.ArgumentParser(description="Check dashboard operational readiness from /api/health.")
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
        print("Operational health")
        print(f"[FAIL] 無法讀取 /api/health 或 /api/refresh/status：{exc}")
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
