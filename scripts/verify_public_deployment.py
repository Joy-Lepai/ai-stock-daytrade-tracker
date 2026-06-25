#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
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
    _status, payload = fetch_json_with_status(base_url, path, timeout=timeout, allow_http_error=False)
    return payload


def fetch_json_with_status(
    base_url: str,
    path: str,
    timeout: float = 15.0,
    *,
    allow_http_error: bool = True,
) -> tuple[int, dict[str, Any]]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if not allow_http_error:
            raise RuntimeError(f"{url} returned HTTP {exc.code}") from exc
        status = int(exc.code)
        body = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} failed: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{url} returned non-JSON response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} returned unexpected JSON type")
    return status, payload


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
    health = payload.get("operational_health") or {}
    operation_severity = str(operation.get("severity") or "")
    health_status = str(health.get("status") or "")
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
        Check(
            "operational health present",
            bool(health),
            f"status={health_status or '-'}",
        ),
        Check(
            "operational health status valid",
            health_status in {"ok", "warning", "blocked"},
            f"status={health_status or '-'}",
        ),
        Check(
            "operational health has next action",
            bool((health.get("next_action") or {}).get("label")) if isinstance(health, dict) else False,
            f"next_action={((health.get('next_action') or {}).get('label') if isinstance(health, dict) else '-') or '-'}",
        ),
        Check(
            "operational health has watch readiness",
            bool(health.get("watch_readiness")) if isinstance(health, dict) else False,
            f"watch_readiness={(health.get('watch_readiness') if isinstance(health, dict) else '-') or '-'}",
        ),
        Check(
            "operational health has refresh plan",
            isinstance(health.get("refresh_plan"), list) if isinstance(health, dict) else False,
            f"refresh_plan={health.get('refresh_plan') if isinstance(health, dict) else '-'}",
        ),
    ]
    if health_status == "blocked":
        checks.append(
            Check(
                "blocked operational health blocks strong buy",
                not allow_strong,
                f"health_status={health_status} allow_strong_long={allow_strong}",
            )
        )
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


def validate_health_payload(payload: dict[str, Any]) -> list[Check]:
    status = str(payload.get("status") or "")
    deployment = payload.get("deployment") or {}
    price = payload.get("price_status_summary") or {}
    checks = [
        Check("health API ok", payload.get("api_status") == "ok", f"api_status={payload.get('api_status')}"),
        Check("health status valid", status in {"ok", "warning", "blocked"}, f"status={status or '-'}"),
        Check("health has summary", bool(payload.get("summary")), f"summary={payload.get('summary') or '-'}"),
        Check(
            "health has next action",
            bool((payload.get("next_action") or {}).get("label")),
            f"next_action={(payload.get('next_action') or {}).get('label') or '-'}",
        ),
        Check(
            "health has watch readiness",
            bool(payload.get("watch_readiness")),
            f"watch_readiness={payload.get('watch_readiness') or '-'}",
        ),
        Check(
            "health has refresh plan",
            isinstance(payload.get("refresh_plan"), list),
            f"refresh_plan={payload.get('refresh_plan') if isinstance(payload.get('refresh_plan'), list) else '-'}",
        ),
        Check("health includes market mode", bool(payload.get("market_mode")), f"market_mode={payload.get('market_mode') or '-'}"),
        Check("health includes price summary", bool(price), f"price_status={price.get('status', '-') if isinstance(price, dict) else '-'}"),
        Check(
            "health includes deployment summary",
            bool(deployment),
            f"runtime={deployment.get('runtime_commit', '-') if isinstance(deployment, dict) else '-'}",
        ),
    ]
    if status == "blocked":
        checks.append(
            Check(
                "blocked health blocks strong buy",
                not bool(payload.get("can_show_strong_long")),
                f"status={status} can_show_strong_long={bool(payload.get('can_show_strong_long'))}",
            )
        )
    return checks


def validate_liveness_payload(http_status: int, payload: dict[str, Any]) -> list[Check]:
    return [
        Check("healthz HTTP ok", http_status == 200, f"http_status={http_status}"),
        Check("healthz API ok", payload.get("api_status") == "ok", f"api_status={payload.get('api_status')}"),
        Check("healthz status alive", payload.get("status") == "alive", f"status={payload.get('status') or '-'}"),
        Check("healthz service present", bool(payload.get("service")), f"service={payload.get('service') or '-'}"),
    ]


def validate_readiness_payload(http_status: int, payload: dict[str, Any]) -> list[Check]:
    checks = validate_health_payload(payload)
    status = str(payload.get("status") or "")
    expected = 503 if status == "blocked" else 200
    checks.append(
        Check(
            "readyz HTTP status matches health status",
            http_status == expected,
            f"http_status={http_status} health_status={status or '-'} expected={expected}",
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
        "看盤狀態",
        "刷新順序",
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
    selection_html = _html_section(html, "候選股怎麼選出來", "</details>")
    scan_count_evidence = _html_first_number_after(html, "今日異動候選") or _html_first_number_after(html, "今日異動候選池")
    pool_count_evidence = (
        _html_first_number_after(html, "普通股池")
        or _html_first_number_after(html, "完整普通股池")
        or _html_first_number_after(html, "full market pool symbols")
    )
    selection_pool_count = _html_first_number_after(selection_html, "完整普通股池") if selection_html else None
    selection_scored_count = _html_first_number_after(selection_html, "送入模型評分") if selection_html else None
    selection_counts_consistent = True
    selection_detail = "selection explainer not present"
    if selection_html:
        selection_counts_consistent = not (
            (pool_count_evidence or 0) > 0
            and (scan_count_evidence or 0) > 0
            and ((selection_pool_count == 0) or (selection_scored_count == 0))
        )
        selection_detail = (
            f"pool_evidence={pool_count_evidence if pool_count_evidence is not None else '-'} "
            f"scan_evidence={scan_count_evidence if scan_count_evidence is not None else '-'} "
            f"selection_pool={selection_pool_count if selection_pool_count is not None else '-'} "
            f"selection_scored={selection_scored_count if selection_scored_count is not None else '-'}"
        )
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
        Check(
            "dashboard candidate explainer counts are consistent",
            selection_counts_consistent,
            selection_detail,
        ),
    ]
    return checks


def _html_section(html: str, start_marker: str, end_marker: str) -> str:
    start = html.find(start_marker)
    if start < 0:
        return ""
    end = html.find(end_marker, start)
    if end < 0:
        return html[start:]
    return html[start : end + len(end_marker)]


def _html_first_number_after(html: str, label: str) -> int | None:
    if not html or label not in html:
        return None
    index = html.find(label)
    snippet = html[index : index + 700]
    patterns = [
        r"<strong>\s*([0-9][0-9,]*)\s*</strong>",
        r">\s*([0-9][0-9,]*)\s*檔",
        r":\s*([0-9][0-9,]*)",
        r"\s([0-9][0-9,]*)\s*檔",
    ]
    matches: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, snippet):
            matches.append((match.start(1), match.group(1)))
    for _position, value in sorted(matches, key=lambda item: item[0]):
        try:
            return int(value.replace(",", ""))
        except ValueError:
            continue
    return None


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


def validate_tw_advisor_direct_html(html: str, expected_symbol: str = "") -> list[Check]:
    checks = validate_tw_advisor_html(html)
    expected = str(expected_symbol or "").strip()
    has_query_bootstrap = 'new URLSearchParams(window.location.search).get("symbol")' in html
    has_scan_function = "/api/tw/scan/symbol" in html
    checks.extend(
        [
            Check(
                "advisor direct page can read symbol query",
                has_query_bootstrap,
                "URLSearchParams symbol bootstrap present" if has_query_bootstrap else "missing query bootstrap",
            ),
            Check(
                "advisor direct page can call scan API",
                has_scan_function,
                "/api/tw/scan/symbol present" if has_scan_function else "missing scan API client",
            ),
        ]
    )
    if expected:
        checks.append(
            Check(
                "advisor direct URL requested symbol",
                True,
                f"symbol={expected}",
            )
        )
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
            "advisor scan has next trigger",
            bool(decision_card.get("next_trigger") or radar.get("next_trigger")),
            f"next_trigger={decision_card.get('next_trigger') or radar.get('next_trigger') or '-'}",
        ),
        Check(
            "advisor scan has invalid condition",
            bool(decision_card.get("invalid_condition")),
            f"invalid_condition={decision_card.get('invalid_condition') or '-'}",
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
    checks.extend(_advisor_scan_front_category_safety_checks(payload, category))
    return checks


def _advisor_scan_front_category_safety_checks(payload: dict[str, Any], category: str) -> list[Check]:
    candidate = payload.get("candidate") or {}
    scan = payload.get("scan") or {}
    health = payload.get("data_health") or {}
    market_mode = payload.get("market_mode") or {}
    key_metrics = payload.get("key_metrics") or {}
    buy_labels = {"強烈買多", "買多"}
    is_buy_label = category in buy_labels
    entry_status = str(candidate.get("entry_status") or scan.get("entry_status") or "")
    price_status = str(health.get("price_status") or "")
    mode = str(market_mode.get("mode") or "")
    above_vwap = candidate.get("above_vwap")
    if above_vwap is None:
        above_vwap = scan.get("above_vwap")
    data_intraday_usable = bool(health.get("can_use_for_intraday_signal"))
    explicit_non_live = (
        price_status in {"cached", "delayed", "missing"}
        or bool(health.get("uses_last_known"))
        or bool(health.get("uses_cache"))
        or bool(health.get("is_delayed"))
        or bool(health.get("is_data_missing"))
    )
    unsafe_entry = entry_status in {"high_risk", "wait_vwap", "avoid", "data_missing"}
    stop_loss = _first_positive_number(candidate.get("stop_loss"), scan.get("stop_loss"), key_metrics.get("stop_loss"))
    checks = [
        Check(
            "advisor buy labels require intraday live data",
            (not is_buy_label) or (mode == "intraday" and data_intraday_usable and not explicit_non_live),
            f"category={category or '-'} mode={mode or '-'} price_status={price_status or '-'} intraday_usable={data_intraday_usable}",
        ),
        Check(
            "advisor buy labels require above VWAP when known",
            (not is_buy_label) or above_vwap is not False,
            f"category={category or '-'} above_vwap={above_vwap}",
        ),
        Check(
            "advisor buy labels block high risk and wait-vwap entries",
            (not is_buy_label) or not unsafe_entry,
            f"category={category or '-'} entry_status={entry_status or '-'}",
        ),
        Check(
            "advisor buy labels require stop loss",
            (not is_buy_label) or stop_loss is not None,
            f"category={category or '-'} stop_loss={stop_loss if stop_loss is not None else '-'}",
        ),
    ]
    return checks


def _first_positive_number(*values: Any) -> float | None:
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    return None


def parse_advisor_symbols(values: list[str] | None) -> list[str]:
    symbols: list[str] = []
    for value in values or []:
        for part in str(value or "").split(","):
            symbol = part.strip()
            if symbol:
                symbols.append(symbol)
    return symbols


def print_checks(title: str, checks: list[Check]) -> int:
    print(f"\n{title}")
    failures = 0
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
        if not check.ok:
            failures += 1
    return failures


def print_fetch_failure(title: str, error: Exception) -> int:
    return print_checks(title, [Check("endpoint reachable", False, str(error))])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify public dashboard deployment and refresh health.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--expected-commit", default=local_git_commit())
    parser.add_argument(
        "--advisor-symbol",
        action="append",
        default=[],
        help="Optionally smoke-test /api/tw/scan/symbol. Can be repeated or comma-separated, e.g. 6919,2886,8150.",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    failures = 0
    try:
        system_payload = fetch_json(args.base_url, "/api/system/version", timeout=args.timeout)
        failures += print_checks("Deployment", validate_system_version(system_payload, args.expected_commit))
    except Exception as exc:
        failures += print_fetch_failure("Deployment", exc)
    try:
        refresh_payload = fetch_json(args.base_url, "/api/refresh/status", timeout=args.timeout)
        failures += print_checks("Refresh status", validate_refresh_status(refresh_payload))
    except Exception as exc:
        failures += print_fetch_failure("Refresh status", exc)
    try:
        health_payload = fetch_json(args.base_url, "/api/health", timeout=args.timeout)
        failures += print_checks("Health endpoint", validate_health_payload(health_payload))
    except Exception as exc:
        failures += print_fetch_failure("Health endpoint", exc)
    try:
        healthz_status, healthz_payload = fetch_json_with_status(args.base_url, "/healthz", timeout=args.timeout)
        failures += print_checks("Liveness endpoint", validate_liveness_payload(healthz_status, healthz_payload))
    except Exception as exc:
        failures += print_fetch_failure("Liveness endpoint", exc)
    try:
        readyz_status, readyz_payload = fetch_json_with_status(args.base_url, "/readyz", timeout=args.timeout)
        failures += print_checks("Readiness endpoint", validate_readiness_payload(readyz_status, readyz_payload))
    except Exception as exc:
        failures += print_fetch_failure("Readiness endpoint", exc)
    try:
        dashboard_html = fetch_text(args.base_url, "/dashboard", timeout=args.timeout)
        failures += print_checks("Dashboard HTML", validate_dashboard_html(dashboard_html))
    except Exception as exc:
        failures += print_fetch_failure("Dashboard HTML", exc)
    try:
        advisor_html = fetch_text(args.base_url, "/tw/advisor", timeout=args.timeout)
        failures += print_checks("TW Advisor HTML", validate_tw_advisor_html(advisor_html))
    except Exception as exc:
        failures += print_fetch_failure("TW Advisor HTML", exc)
    for advisor_symbol in parse_advisor_symbols(args.advisor_symbol):
        direct_path = "/tw/advisor?" + urllib.parse.urlencode({"symbol": advisor_symbol})
        try:
            advisor_direct_html = fetch_text(args.base_url, direct_path, timeout=args.timeout)
            failures += print_checks(
                f"TW Advisor Direct URL ({advisor_symbol})",
                validate_tw_advisor_direct_html(advisor_direct_html, advisor_symbol),
            )
        except Exception as exc:
            failures += print_fetch_failure(f"TW Advisor Direct URL ({advisor_symbol})", exc)
        try:
            advisor_payload = post_json(
                args.base_url,
                "/api/tw/scan/symbol",
                {"symbol": advisor_symbol},
                timeout=max(args.timeout, 30.0),
            )
            failures += print_checks(
                f"TW Advisor API ({advisor_symbol})",
                validate_tw_advisor_scan(advisor_payload, advisor_symbol),
            )
        except Exception as exc:
            failures += print_fetch_failure(f"TW Advisor API ({advisor_symbol})", exc)
    print()
    if failures:
        print(f"Deployment verification failed: {failures} check(s) need attention.")
        return 1
    print("Deployment verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
