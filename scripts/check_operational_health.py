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


def fetch_refresh_status(base_url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/refresh/status"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def render_report(payload: dict[str, Any]) -> tuple[int, str]:
    health = payload.get("operational_health")
    if not isinstance(health, dict):
        health = build_operational_health(payload)
    status = str(health.get("status") or "blocked")
    mark = "PASS" if status == "ok" else "WARN" if status == "warning" else "FAIL"
    lines = [
        "Operational health",
        f"[{mark}] {health.get('summary') or '-'}",
        f"market_mode: {health.get('market_mode') or payload.get('market_mode') or '-'}",
        f"data_quality: {health.get('data_quality_status') or '-'}",
        "counts: "
        f"live={health.get('live_count', 0)} "
        f"delayed={health.get('delayed_count', 0)} "
        f"cached={health.get('cached_count', 0)} "
        f"missing={health.get('missing_count', 0)}",
    ]
    blockers = health.get("blockers") or []
    warnings = health.get("warnings") or []
    if blockers:
        lines.append("blockers:")
        lines.extend(f"- {item}" for item in blockers)
    if warnings:
        lines.append("warnings:")
        lines.extend(f"- {item}" for item in warnings)
    next_action = health.get("next_action") or {}
    lines.append(f"next_action: {next_action.get('label') or '-'} {next_action.get('endpoint') or ''}".strip())
    return (0 if status == "ok" else 1, "\n".join(lines))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check dashboard operational readiness from /api/refresh/status.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args(argv)
    try:
        payload = fetch_refresh_status(args.base_url, timeout=args.timeout)
    except Exception as exc:
        print("Operational health")
        print(f"[FAIL] 無法讀取 /api/refresh/status：{exc}")
        print("next_action: 確認網站是否啟動，或稍後重試。")
        return 1
    exit_code, report = render_report(payload)
    print(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
