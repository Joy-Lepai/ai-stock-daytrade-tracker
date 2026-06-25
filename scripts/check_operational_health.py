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


def fetch_health_payload(base_url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    try:
        payload = fetch_json(base_url, "/api/health", timeout=timeout)
        payload["_health_source"] = "/api/health"
        return payload
    except Exception as health_error:
        payload = fetch_json(base_url, "/api/refresh/status", timeout=timeout)
        payload["_health_source"] = f"/api/refresh/status fallback after /api/health failed: {health_error}"
        return payload


def render_report(payload: dict[str, Any], *, base_url: str = DEFAULT_BASE_URL) -> tuple[int, str]:
    health = payload.get("operational_health") if isinstance(payload.get("operational_health"), dict) else None
    if health is None and payload.get("status") in {"ok", "warning", "blocked"}:
        health = payload
    if not isinstance(health, dict):
        health = build_operational_health(payload)
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
    blockers = health.get("blockers") or []
    warnings = health.get("warnings") or []
    if blockers:
        lines.append("blockers:")
        lines.extend(f"- {item}" for item in blockers)
    if warnings:
        lines.append("warnings:")
        lines.extend(f"- {item}" for item in warnings)
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
    return (0 if status in {"ok", "warning"} else 1, "\n".join(lines))


def _watch_readiness_label(status: str, health: dict[str, Any], payload: dict[str, Any]) -> str:
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
    return {"label": "-", "endpoint": ""}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check dashboard operational readiness from /api/health.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args(argv)
    try:
        payload = fetch_health_payload(args.base_url, timeout=args.timeout)
    except Exception as exc:
        print("Operational health")
        print(f"[FAIL] 無法讀取 /api/health 或 /api/refresh/status：{exc}")
        print("next_action: 確認網站是否啟動，或稍後重試。")
        return 1
    exit_code, report = render_report(payload, base_url=args.base_url)
    print(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
