#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional


DEFAULT_BASE_URL = "https://stock.letslepai.com"


@dataclass(frozen=True)
class ReleaseState:
    head: str
    origin: str
    ahead_count: int
    dirty: bool
    status_line: str
    public_runtime: str = ""
    public_tracker: str = ""


@dataclass(frozen=True)
class ReleaseCheck:
    name: str
    ok: bool
    detail: str


class ReleaseReadinessError(RuntimeError):
    pass


def git_output(args: list[str], *, runner: Callable[..., str] = subprocess.check_output) -> str:
    try:
        return runner(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError as exc:
        raise ReleaseReadinessError(describe_git_failure(args, exc)) from exc
    except OSError as exc:
        raise ReleaseReadinessError(f"`git {' '.join(args)}` failed: {exc}") from exc


def describe_git_failure(args: list[str], exc: subprocess.CalledProcessError) -> str:
    command = "git " + " ".join(args)
    code = int(exc.returncode or 0)
    if code < 0:
        try:
            reason = signal.Signals(-code).name
        except ValueError:
            reason = f"signal {-code}"
        return f"`{command}` failed: {reason}"
    return f"`{command}` failed: exit status {code}"


def load_release_state(
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 12.0,
    runner: Callable[..., str] = subprocess.check_output,
    fetch_public: bool = True,
) -> ReleaseState:
    head = git_output(["rev-parse", "HEAD"], runner=runner)
    origin = git_output(["rev-parse", "origin/main"], runner=runner)
    status = git_output(["status", "-sb"], runner=runner)
    first_line = status.splitlines()[0] if status else ""
    public_runtime = ""
    public_tracker = ""
    if fetch_public:
        try:
            payload = fetch_system_version(base_url, timeout=timeout)
            public_runtime = str(((payload.get("runtime") or {}).get("commit")) or "")
            public_tracker = str(((payload.get("tracker_html") or {}).get("commit")) or "")
        except Exception as exc:
            public_runtime = f"ERROR:{exc}"
    return ReleaseState(
        head=head,
        origin=origin,
        ahead_count=parse_ahead_count(first_line),
        dirty=is_worktree_dirty(status),
        status_line=first_line,
        public_runtime=public_runtime,
        public_tracker=public_tracker,
    )


def parse_ahead_count(status_line: str) -> int:
    match = re.search(r"\bahead\s+(\d+)", status_line or "")
    return int(match.group(1)) if match else 0


def is_worktree_dirty(status_output: str) -> bool:
    lines = [line for line in (status_output or "").splitlines()[1:] if line.strip()]
    return bool(lines)


def fetch_system_version(base_url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/system/version"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def commit_matches(actual: str, expected: str) -> bool:
    if not actual or not expected or actual.startswith("ERROR:"):
        return False
    return actual.startswith(expected[: len(actual)]) or expected.startswith(actual[: len(expected)])


def evaluate_release_state(state: ReleaseState) -> list[ReleaseCheck]:
    checks = [
        ReleaseCheck("worktree clean", not state.dirty, "clean" if not state.dirty else "有未提交或未 staged 的修改"),
        ReleaseCheck(
            "local pushed to origin/main",
            state.head == state.origin and state.ahead_count == 0,
            f"local={short(state.head)} origin={short(state.origin)} ahead={state.ahead_count}",
        ),
    ]
    if state.public_runtime:
        checks.append(
            ReleaseCheck(
                "public runtime matches local HEAD",
                commit_matches(state.public_runtime, state.head),
                f"public={short(state.public_runtime)} local={short(state.head)}",
            )
        )
    if state.public_tracker and not state.public_tracker.startswith("ERROR:"):
        checks.append(
            ReleaseCheck(
                "public tracker matches runtime",
                commit_matches(state.public_tracker, state.public_runtime),
                f"tracker={short(state.public_tracker)} runtime={short(state.public_runtime)}",
            )
        )
    return checks


def recommended_next_action(state: ReleaseState) -> str:
    if state.dirty:
        return "先 commit 目前修改，再檢查是否需要 push。"
    if state.head != state.origin or state.ahead_count:
        return "本機 commit 尚未推到 GitHub：請先 Push origin/main，成功後再到 Render Deploy latest commit。"
    if state.public_runtime.startswith("ERROR:"):
        return "無法讀取公開站版本：請確認網站是否啟動，再跑公開站驗收。"
    if state.public_runtime and not commit_matches(state.public_runtime, state.head):
        return "GitHub 已是最新版，但公開站仍舊版：請在 Render 執行 Deploy latest commit。"
    if state.public_tracker and not commit_matches(state.public_tracker, state.public_runtime):
        return "runtime 與 tracker HTML 不一致：請執行完整刷新或重新部署。"
    return "版本鏈路看起來一致，可以執行公開站功能驗收。"


def short(value: str, length: int = 12) -> str:
    return str(value or "-")[:length]


def print_release_report(state: ReleaseState, checks: list[ReleaseCheck]) -> int:
    failures = 0
    print("Release readiness")
    print(f"local HEAD:   {state.head}")
    print(f"origin/main:  {state.origin}")
    print(f"status:       {state.status_line or '-'}")
    if state.public_runtime:
        print(f"public:       runtime={state.public_runtime} tracker={state.public_tracker or '-'}")
    print()
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
        failures += 0 if check.ok else 1
    print()
    print("Next action:", recommended_next_action(state))
    return failures


def print_load_failure(error: Exception) -> int:
    print("Release readiness")
    print()
    print(f"[FAIL] local Git state readable: {error}")
    print()
    print("Next action: 無法讀取本機 Git 狀態；請先確認此目錄是正確 repo，或重跑 git status 後再驗收。")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check local Git, origin/main, and public runtime release readiness.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--no-public", action="store_true", help="Skip public /api/system/version probe.")
    args = parser.parse_args(argv)
    try:
        state = load_release_state(base_url=args.base_url, timeout=args.timeout, fetch_public=not args.no_public)
    except ReleaseReadinessError as exc:
        return print_load_failure(exc)
    checks = evaluate_release_state(state)
    return 1 if print_release_report(state, checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
