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
    ]
    if mode != "intraday":
        checks.append(
            Check(
                "non-intraday blocks strong buy",
                not allow_strong,
                f"mode={mode or '-'} allow_strong_long={allow_strong}",
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
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    system_payload = fetch_json(args.base_url, "/api/system/version", timeout=args.timeout)
    refresh_payload = fetch_json(args.base_url, "/api/refresh/status", timeout=args.timeout)
    failures = 0
    failures += print_checks("Deployment", validate_system_version(system_payload, args.expected_commit))
    failures += print_checks("Refresh status", validate_refresh_status(refresh_payload))
    print()
    if failures:
        print(f"Deployment verification failed: {failures} check(s) need attention.")
        return 1
    print("Deployment verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
