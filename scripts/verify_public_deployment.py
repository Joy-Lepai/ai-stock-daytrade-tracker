#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "https://stock.letslepai.com"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def fetch_json(base_url: str, path: str, timeout: float = 15.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} failed: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{url} returned non-JSON response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} returned unexpected JSON type")
    return payload


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} failed: {exc.reason}") from exc
    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{url} returned non-JSON response") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"{url} returned unexpected JSON type")
    return result


def fetch_text(base_url: str, path: str, timeout: float = 15.0) -> str:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} failed: {exc.reason}") from exc


def local_git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return ""


def short(value: str, length: int = 12) -> str:
    return str(value or "")[:length]


def commit_matches(actual: str, expected: str) -> bool:
    if not expected:
        return bool(actual)
    return str(expected).startswith(str(actual)) or str(actual).startswith(str(expected)[: len(str(actual))])


def validate_system_version(payload: dict[str, Any], expected_commit: str = "") -> list[Check]:
    runtime = payload.get("runtime") or {}
    tracker = payload.get("tracker_html") or {}
    consistency = payload.get("consistency") or {}
    runtime_commit = str(runtime.get("commit") or "")
    tracker_commit = str(tracker.get("commit") or "")
    checks = [
        Check("system API ok", payload.get("api_status") == "ok", f"api_status={payload.get('api_status')}"),
        Check("runtime commit present", bool(runtime_commit), f"runtime={runtime_commit or '-'}"),
        Check("tracker commit present", bool(tracker_commit), f"tracker={tracker_commit or '-'}"),
        Check(
            "runtime matches tracker",
            bool(consistency.get("runtime_matches_tracker")),
            f"runtime={runtime_commit or '-'} tracker={tracker_commit or '-'}",
        ),
        Check(
            "tracker html ready",
            bool(consistency.get("is_ready")),
            f"warnings={'; '.join(consistency.get('warnings') or []) or '-'}",
        ),
    ]
    if expected_commit:
        checks.append(
            Check(
                "runtime matches expected commit",
                commit_matches(runtime_commit, expected_commit),
                f"runtime={runtime_commit or '-'} expected={short(expected_commit)}",
            )
        )
    return checks


def validate_refresh_status(payload: dict[str, Any]) -> list[Check]:
    mode = str(payload.get("market_mode") or "")
    required_layers = list(payload.get("required_refresh_layers") or [])
    required_stale = list(payload.get("required_stale_layers") or [])
    price = payload.get("price_status_summary") or {}
    operation = payload.get("refresh_operation_summary") or {}
    operation_severity = str(operation.get("severity") or "")
    allow_strong = bool(payload.get("allow_strong_long"))
    checks = [
        Check("refresh API ok", payload.get("api_status") == "ok", f"api_status={payload.get('api_status')}"),
        Check("market mode present", bool(mode), f"market_mode={mode or '-'}"),
        Check("required layers declared", bool(required_layers), f"required={', '.join(required_layers) or '-'}"),
        Check(
            "required layers fresh",
            not required_stale,
            f"required_stale={', '.join(required_stale) or '-'}",
        ),
        Check(
            "price status summary present",
            bool(price),
            f"price_status={price.get('status', '-') if isinstance(price, dict) else '-'}",
        ),
        Check(
            "refresh operation summary present",
            bool(operation.get("message")),
            f"severity={operation_severity or '-'} message={operation.get('message') or '-'}",
        ),
    ]
    if required_stale:
        checks.append(
            Check(
                "required stale layers block operation summary",
                operation_severity == "block",
                f"required_stale={', '.join(required_stale)} operation_severity={operation_severity or '-'}",
            )
        )
    if mode == "stale_data":
        checks.append(
            Check(
                "stale market mode blocks operation summary",
                operation_severity == "block",
                f"market_mode={mode} operation_severity={operation_severity or '-'}",
            )
        )
    if mode != "intraday":
        checks.append(
            Check(
                "non-intraday blocks strong buy",
                not allow_strong,
                f"mode={mode or '-'} allow_strong_long={allow_strong}",
            )
        )
    return checks


def validate_dashboard_html(html: str) -> list[Check]:
    required_markers = [
        "今日決策摘要",
        "今日資料可信度",
        "最接近強烈買多",
        "買多觀察池",
        "進場雷達成績單",
        "資料健康度",
        "台股全市場異動掃描池",
        "漏抓股票診斷",
        "強烈買多漏斗",
        "系統狀態與資料來源",
    ]
    forbidden_terms = [
        "強烈看漲",
        "做多確認",
        "買多推薦",
        "可執行做多",
        "強勢做多觀察",
        "舊版參考：今日看漲焦點",
        "舊版參考：系統自動選股",
    ]
    missing_markers = [marker for marker in required_markers if marker not in html]
    found_forbidden = [term for term in forbidden_terms if term in html]
    checks = [
        Check("dashboard HTML loaded", bool(html.strip()), f"length={len(html)}"),
        Check(
            "dashboard has core decision sections",
            not missing_markers,
            f"missing={', '.join(missing_markers) if missing_markers else '-'}",
        ),
        Check(
            "dashboard has no legacy misleading wording",
            not found_forbidden,
            f"found={', '.join(found_forbidden) if found_forbidden else '-'}",
        ),
    ]
    return checks


def validate_tw_advisor_html(html: str) -> list[Check]:
    required_markers = [
        "個股當沖作戰卡",
        "輸入股票代號後",
        "本系統不是報明牌",
        "強烈買多、買多、觀察、看空或資料不足",
        "台積電",
        "兆豐金",
        "本系統僅供資料整理",
    ]
    forbidden_terms = [
        "強烈看漲",
        "做多確認",
        "買多推薦",
        "可執行做多",
        "強勢做多觀察",
    ]
    missing_markers = [marker for marker in required_markers if marker not in html]
    found_forbidden = [term for term in forbidden_terms if term in html]
    checks = [
        Check("advisor HTML loaded", bool(html.strip()), f"length={len(html)}"),
        Check(
            "advisor has combat-card entry copy",
            not missing_markers,
            f"missing={', '.join(missing_markers) if missing_markers else '-'}",
        ),
        Check(
            "advisor has no legacy misleading wording",
            not found_forbidden,
            f"found={', '.join(found_forbidden) if found_forbidden else '-'}",
        ),
    ]
    return checks


def validate_tw_advisor_scan(payload: dict[str, Any], expected_symbol: str = "") -> list[Check]:
    symbol = str(payload.get("symbol") or "")
    expected = str(expected_symbol or "").upper().replace(".TWO", ".TWO").replace(".TW", ".TW")
    front_trade = payload.get("front_trade") or {}
    decision_card = payload.get("decision_card") or {}
    radar = payload.get("entry_radar_summary") or {}
    health = payload.get("data_health") or {}
    market_mode = payload.get("market_mode") or {}
    forbidden_categories = {"強烈看漲", "做多確認", "買多推薦", "可執行做多", "強勢做多觀察"}
    category = str(front_trade.get("category") or decision_card.get("final_decision") or "")
    checks = [
        Check("advisor scan returned symbol", bool(symbol), f"symbol={symbol or '-'}"),
        Check(
            "advisor scan symbol matches request",
            (not expected) or symbol.upper().startswith(expected.split(".")[0]),
            f"symbol={symbol or '-'} expected={expected or '-'}",
        ),
        Check("advisor scan has data health", bool(health), f"price_status={health.get('price_status', '-') if isinstance(health, dict) else '-'}"),
        Check(
            "advisor scan has market mode",
            bool(market_mode.get("mode")),
            f"market_mode={market_mode.get('mode', '-') if isinstance(market_mode, dict) else '-'}",
        ),
        Check(
            "advisor scan has front category",
            category in {"強烈買多", "買多", "觀察", "看空", "資料不足"},
            f"category={category or '-'}",
        ),
        Check(
            "advisor scan has decision card",
            bool(decision_card.get("top_reason") or decision_card.get("user_summary")),
            f"top_reason={decision_card.get('top_reason', '-') if isinstance(decision_card, dict) else '-'}",
        ),
        Check(
            "advisor scan has entry radar summary",
            bool(radar.get("blocker_summary") or radar.get("next_trigger")),
            f"blocker={radar.get('blocker_summary', '-') if isinstance(radar, dict) else '-'}",
        ),
        Check(
            "advisor scan has no legacy misleading category",
            category not in forbidden_categories,
            f"category={category or '-'}",
        ),
    ]
    mode = str(market_mode.get("mode") or "") if isinstance(market_mode, dict) else ""
    if mode and mode != "intraday":
        checks.append(
            Check(
                "non-intraday advisor scan blocks strong buy",
                category != "強烈買多",
                f"mode={mode} category={category or '-'}",
            )
        )
    return checks


def print_checks(title: str, checks: list[Check]) -> int:
    print(f"\n{title}")
    failures = 0
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
        if not check.ok:
            failures += 1
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify public dashboard deployment and refresh health.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--expected-commit", default=local_git_commit())
    parser.add_argument("--advisor-symbol", default="", help="Optionally smoke-test /api/tw/scan/symbol for one symbol.")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    system_payload = fetch_json(args.base_url, "/api/system/version", timeout=args.timeout)
    refresh_payload = fetch_json(args.base_url, "/api/refresh/status", timeout=args.timeout)
    dashboard_html = fetch_text(args.base_url, "/dashboard", timeout=args.timeout)
    advisor_html = fetch_text(args.base_url, "/tw/advisor", timeout=args.timeout)
    failures = 0
    failures += print_checks("Deployment", validate_system_version(system_payload, args.expected_commit))
    failures += print_checks("Refresh status", validate_refresh_status(refresh_payload))
    failures += print_checks("Dashboard HTML", validate_dashboard_html(dashboard_html))
    failures += print_checks("TW Advisor HTML", validate_tw_advisor_html(advisor_html))
    if args.advisor_symbol:
        advisor_payload = post_json(
            args.base_url,
            "/api/tw/scan/symbol",
            {"symbol": args.advisor_symbol},
            timeout=max(args.timeout, 30.0),
        )
        failures += print_checks(
            f"TW Advisor API ({args.advisor_symbol})",
            validate_tw_advisor_scan(advisor_payload, args.advisor_symbol),
        )
    print()
    if failures:
        print(f"Deployment verification failed: {failures} check(s) need attention.")
        return 1
    print("Deployment verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
