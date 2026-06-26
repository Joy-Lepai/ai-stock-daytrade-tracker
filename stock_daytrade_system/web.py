from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time as time_module
import urllib.parse
from datetime import datetime, time as dt_time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.auth import AuthConfig, load_auth_config, verify_password
from stock_daytrade_system.app_version import current_commit_info
from stock_daytrade_system.accuracy_service import (
    build_accuracy_dashboard_payload,
    build_accuracy_group_payload,
    build_accuracy_summary,
)
from stock_daytrade_system.db import (
    backtest_summary,
    connect,
    default_db_path,
    latest_candidates,
    latest_symbol_score,
    latest_us_candidates,
    latest_us_symbol,
    symbol_history,
)
from stock_daytrade_system.market_clock import taiwan_market_session, us_market_session
from stock_daytrade_system.paper_broker import close_manual_trade, create_manual_trade, paper_quote
from stock_daytrade_system.paper_service import build_empty_paper_dashboard, build_paper_dashboard, build_paper_performance
from stock_daytrade_system.refresh_service import RefreshCoordinator
from stock_daytrade_system.system_status import build_system_version_payload
from stock_daytrade_system.tw_scan_service import add_tw_watchlist_symbol, scan_tw_symbol_payload
from stock_daytrade_system.us_service import build_us_dashboard_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH = PROJECT_ROOT / "config" / "auth.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"
SESSION_COOKIE = "ai_stock_session"
TRACKER_REFRESH_TIMEOUT_SECONDS = int(os.getenv("STOCK_TRACKER_REFRESH_TIMEOUT_SECONDS", "180"))
WEB_SCHEDULER_POLL_SECONDS = int(os.getenv("STOCK_WEB_SCHEDULER_POLL_SECONDS", "30"))
TW_PREMARKET_REFRESH_SECONDS = int(os.getenv("STOCK_TW_PREMARKET_REFRESH_SECONDS", "1800"))
TW_INTRADAY_REFRESH_SECONDS = int(os.getenv("STOCK_TW_INTRADAY_REFRESH_SECONDS", "900"))
TW_WATCHLIST_REFRESH_SECONDS = int(os.getenv("STOCK_TW_WATCHLIST_REFRESH_SECONDS", "300"))
TW_POSITIONS_REFRESH_SECONDS = int(os.getenv("STOCK_TW_POSITIONS_REFRESH_SECONDS", "300"))
TW_AFTER_CLOSE_REFRESH_SECONDS = int(os.getenv("STOCK_TW_AFTER_CLOSE_REFRESH_SECONDS", "900"))


class WebApp:
    def __init__(self, auth_config: Optional[AuthConfig], report_dir: Path, require_auth: bool = False) -> None:
        self.auth_config = auth_config
        self.report_dir = report_dir
        self.require_auth = require_auth
        self.sessions: Dict[str, str] = {}
        self.refresh_lock = threading.Lock()
        self.refresh_coordinator = RefreshCoordinator(
            PROJECT_ROOT,
            report_dir,
            tracker_timeout_seconds=TRACKER_REFRESH_TIMEOUT_SECONDS,
        )
        self.scheduler_enabled = os.getenv("STOCK_ENABLE_WEB_SCHEDULER", "0").lower() not in {"0", "false", "no"}
        self.scheduler_thread: Optional[threading.Thread] = None
        self.background_refresh_threads: Dict[str, threading.Thread] = {}
        self.background_refresh_lock = threading.Lock()
        self.last_scheduled_refresh_at: Optional[datetime] = None
        self.last_scheduled_refresh_at_by_layer: Dict[str, datetime] = {}
        self.last_scheduled_refresh_status = "尚未執行"

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = username
        return token

    def is_authenticated(self, token: Optional[str]) -> bool:
        if not self.require_auth:
            return True
        return token in self.sessions if token else False

    def destroy_session(self, token: Optional[str]) -> None:
        if token:
            self.sessions.pop(token, None)

    def start_scheduler(self) -> None:
        if not self.scheduler_enabled or self.scheduler_thread is not None:
            return
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, name="tw-tracker-scheduler", daemon=True)
        self.scheduler_thread.start()

    def _scheduler_loop(self) -> None:
        while True:
            now = datetime.now(ZoneInfo("Asia/Taipei"))
            self._run_scheduled_refresh_once(now)
            time_module.sleep(max(WEB_SCHEDULER_POLL_SECONDS, 5))

    def _run_scheduled_refresh_once(self, now: datetime) -> list[str]:
        executed: list[str] = []
        for layer, interval_seconds, label in _scheduled_refresh_layers(now):
            if not self._scheduled_refresh_due(now, interval_seconds, layer):
                continue
            self.last_scheduled_refresh_at = now
            self.last_scheduled_refresh_at_by_layer[layer] = now
            result = self._run_scheduled_layer(layer)
            self.last_scheduled_refresh_status = result.message or f"{label} 更新完成"
            executed.append(layer)
        return executed

    def _scheduled_refresh_due(self, now: datetime, interval_seconds: int, layer: str = "full_market") -> bool:
        last_run = self.last_scheduled_refresh_at_by_layer.get(layer)
        if last_run is None:
            return True
        return (now - last_run).total_seconds() >= interval_seconds

    def _run_scheduled_layer(self, layer: str):
        coordinator = self.refresh_coordinator
        if layer == "full_market":
            return coordinator.refresh_full_market()
        if layer == "watchlist":
            return coordinator.refresh_watchlist()
        if layer == "positions":
            return coordinator.refresh_positions()
        if layer == "post_close_validation":
            return coordinator.refresh_post_close_validation()
        return coordinator.refresh_full_market()

    def start_background_refresh(self, layer: str) -> bool:
        with self.background_refresh_lock:
            existing = self.background_refresh_threads.get(layer)
            if existing and existing.is_alive():
                return False
            thread = threading.Thread(
                target=self._run_background_refresh,
                args=(layer,),
                name=f"tw-refresh-{layer}",
                daemon=True,
            )
            self.background_refresh_threads[layer] = thread
            thread.start()
            return True

    def _run_background_refresh(self, layer: str) -> None:
        coordinator = self.refresh_coordinator
        if layer == "manual_full_refresh":
            coordinator.refresh_manual_full()
        elif layer == "full_market":
            coordinator.refresh_full_market()
        elif layer == "watchlist":
            coordinator.refresh_watchlist()
        elif layer == "positions":
            coordinator.refresh_positions()
        elif layer == "post_close_validation":
            coordinator.refresh_post_close_validation()


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    auth_path: Path = DEFAULT_AUTH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    require_auth: bool = False,
) -> None:
    auth_config = load_auth_config(auth_path) if require_auth else None
    app = WebApp(auth_config, report_dir, require_auth=require_auth)
    app.start_scheduler()

    class Handler(StockWebHandler):
        web_app = app

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving stock tracker on http://{host}:{port}/")
    server.serve_forever()


class StockWebHandler(BaseHTTPRequestHandler):
    web_app: WebApp

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)
        if path == "/healthz":
            self._send_json(build_liveness_payload())
            return
        if path == "/readyz":
            refresh_payload = self.web_app.refresh_coordinator.status_payload()
            system_payload = build_system_version_payload(PROJECT_ROOT, self.web_app.report_dir)
            health_payload = build_health_payload(refresh_payload, system_payload)
            self._send_json(health_payload, readiness_http_status(health_payload))
            return
        if path == "/login":
            if not self.web_app.require_auth:
                self._redirect("/dashboard")
                return
            self._send_html(render_login_page())
            return
        if path == "/logout":
            self.web_app.destroy_session(self._session_token())
            self._redirect("/login" if self.web_app.require_auth else "/dashboard", clear_cookie=True)
            return
        if not self._authenticated():
            self._redirect("/login")
            return
        if path in {"/", "/dashboard"}:
            self._send_html(self._dashboard_html(force_refresh="final" in query))
            return
        if path == "/us":
            self._redirect("/us/dashboard")
            return
        if path == "/us/dashboard":
            self._send_html(render_us_dashboard_page(show_logout=self.web_app.require_auth))
            return
        if path == "/paper":
            self._redirect("/paper/dashboard")
            return
        if path == "/paper/dashboard":
            self._send_html(render_paper_dashboard_page(show_logout=self.web_app.require_auth))
            return
        if path in {"/tw/advisor", "/advisor"}:
            self._send_html(render_tw_advisor_page(show_logout=self.web_app.require_auth))
            return
        if path == "/accuracy":
            self._send_html(render_accuracy_page(show_logout=self.web_app.require_auth))
            return
        if path.startswith("/symbol/"):
            self._send_html(self._symbol_html(path.removeprefix("/symbol/")))
            return
        if path == "/api/candidates":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json([dict(row) for row in latest_candidates(conn)])
            return
        if path.startswith("/api/symbol/"):
            self._send_json(self._symbol_payload(path.removeprefix("/api/symbol/")))
            return
        if path == "/api/backtest":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(backtest_summary(conn))
            return
        if path == "/api/us/dashboard":
            self._send_json(self._us_dashboard_payload())
            return
        if path == "/api/us/candidates":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json([dict(row) for row in latest_us_candidates(conn)])
            return
        if path.startswith("/api/us/symbol/"):
            symbol = urllib.parse.unquote(path.removeprefix("/api/us/symbol/")).upper()
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                row = latest_us_symbol(conn, symbol)
                self._send_json(dict(row) if row else {"error": "找不到美股資料", "symbol": symbol}, HTTPStatus.OK if row else HTTPStatus.NOT_FOUND)
            return
        if path == "/api/us/market":
            clock = us_market_session()
            self._send_json(
                {
                    "market": clock.market,
                    "session": clock.session,
                    "status_text": clock.status_text,
                    "refresh_interval_seconds": clock.refresh_interval_seconds,
                    "now_local": clock.now_local.isoformat(timespec="seconds"),
                    "timezone": clock.timezone,
                }
            )
            return
        if path == "/api/paper/dashboard":
            self._send_json(self._paper_dashboard_payload())
            return
        if path == "/api/paper/quote":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            market = query.get("market", [""])[0]
            symbol = query.get("symbol", [""])[0]
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(paper_quote(conn, market, symbol))
            return
        if path == "/api/paper/trades":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                rows = conn.execute("SELECT * FROM paper_trades ORDER BY COALESCE(entry_time, created_at) DESC, symbol LIMIT 200").fetchall()
                self._send_json([dict(row) for row in rows])
            return
        if path == "/api/paper/positions":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                rows = conn.execute(
                    """
                    SELECT p.*, t.name_zh, t.name_en, t.entry_reason, t.source, t.is_manual,
                           t.risk_mode, t.auto_exit_enabled, t.auto_exit_reason
                    FROM paper_positions p
                    LEFT JOIN paper_trades t ON t.id = p.trade_id
                    ORDER BY p.market, p.symbol
                    """
                ).fetchall()
                self._send_json([dict(row) for row in rows])
            return
        if path == "/api/paper/performance":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(build_paper_performance(conn))
            return
        if path == "/api/refresh/status":
            self._send_json(self.web_app.refresh_coordinator.status_payload())
            return
        if path == "/api/system/version":
            self._send_json(build_system_version_payload(PROJECT_ROOT, self.web_app.report_dir))
            return
        if path == "/api/health":
            refresh_payload = self.web_app.refresh_coordinator.status_payload()
            system_payload = build_system_version_payload(PROJECT_ROOT, self.web_app.report_dir)
            self._send_json(build_health_payload(refresh_payload, system_payload))
            return
        if path == "/api/operator/decision":
            refresh_payload = self.web_app.refresh_coordinator.status_payload()
            system_payload = build_system_version_payload(PROJECT_ROOT, self.web_app.report_dir)
            self._send_json(build_operator_decision_payload(refresh_payload, system_payload))
            return
        if path == "/api/notification/signals":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(_notification_signals_payload(conn))
            return
        if path == "/api/accuracy/summary":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(build_accuracy_dashboard_payload(conn))
            return
        if path == "/api/accuracy/by-status":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(build_accuracy_group_payload(conn, "entry_status"))
            return
        if path == "/api/accuracy/by-market":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(build_accuracy_group_payload(conn, "market"))
            return
        if path == "/api/accuracy/by-confidence":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(build_accuracy_group_payload(conn, "confidence_level"))
            return
        if path.startswith("/reports/"):
            self._send_report(path.removeprefix("/reports/"))
            return
        self._send_not_found()

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            if not self.web_app.require_auth:
                self._redirect("/dashboard")
                return
            self._handle_login()
            return
        if not self._authenticated():
            self._redirect("/login")
            return
        if path == "/refresh":
            self._handle_refresh()
            return
        if path == "/refresh_full_market":
            self._handle_layer_refresh("full_market")
            return
        if path == "/refresh_watchlist":
            self._handle_layer_refresh("watchlist")
            return
        if path == "/refresh_positions":
            self._handle_layer_refresh("positions")
            return
        if path == "/refresh_post_close_validation":
            self._handle_layer_refresh("post_close_validation")
            return
        if path == "/api/paper/manual-trade":
            payload = self._read_json_body()
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                result = create_manual_trade(conn, payload)
            self._send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/paper/close-trade":
            payload = self._read_json_body()
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                result = close_manual_trade(conn, payload)
            self._send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/tw/watchlist/add":
            payload = self._read_json_body()
            result = add_tw_watchlist_symbol(PROJECT_ROOT, str(payload.get("symbol") or payload.get("query") or ""))
            self._send_json(result, HTTPStatus.OK if result.get("symbol") else HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/tw/scan/symbol":
            payload = self._read_json_body()
            result = scan_tw_symbol_payload(
                PROJECT_ROOT,
                str(payload.get("symbol") or payload.get("query") or ""),
                prefer_snapshot=not bool(payload.get("force_live") or payload.get("live")),
            )
            self._send_json(result, HTTPStatus.OK if result.get("symbol") else HTTPStatus.BAD_REQUEST)
            return
        self._send_not_found()

    def _handle_login(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
        data = urllib.parse.parse_qs(body)
        username = data.get("username", [""])[0]
        password = data.get("password", [""])[0]
        auth = self.web_app.auth_config
        if auth is None:
            self._redirect("/dashboard")
            return
        if username == auth.username and verify_password(password, auth):
            token = self.web_app.create_session(username)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/dashboard")
            self.send_header("Set-Cookie", _session_cookie(token))
            self.end_headers()
            return
        self._send_html(render_login_page("帳號或密碼錯誤"), status=HTTPStatus.UNAUTHORIZED)

    def _handle_refresh(self) -> None:
        if self.headers.get("X-Requested-With") != "fetch":
            started = self.web_app.start_background_refresh("manual_full_refresh")
            self._redirect(_refresh_redirect_location("manual_full_refresh", started))
            return
        result = self.web_app.refresh_coordinator.refresh_manual_full()
        if self.headers.get("X-Requested-With") == "fetch":
            self._send_json(result.to_dict(), HTTPStatus.OK if result.status in {"success", "skipped"} else HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._redirect("/dashboard")

    def _handle_layer_refresh(self, layer: str) -> None:
        if self.headers.get("X-Requested-With") != "fetch":
            started = self.web_app.start_background_refresh(layer)
            self._redirect(_refresh_redirect_location(layer, started))
            return
        coordinator = self.web_app.refresh_coordinator
        if layer == "full_market":
            result = coordinator.refresh_full_market()
        elif layer == "watchlist":
            result = coordinator.refresh_watchlist()
        elif layer == "positions":
            result = coordinator.refresh_positions()
        elif layer == "post_close_validation":
            result = coordinator.refresh_post_close_validation()
        else:
            self._send_not_found()
            return
        if self.headers.get("X-Requested-With") == "fetch":
            self._send_json(result.to_dict(), HTTPStatus.OK if result.status in {"success", "skipped"} else HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._redirect("/dashboard")

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def _dashboard_html(self, force_refresh: bool = False) -> str:
        latest = latest_tracker_file(self.web_app.report_dir)
        refresh_error = ""
        if force_refresh:
            result = self.web_app.refresh_coordinator.refresh_manual_full()
            refresh_error = result.error if result.status == "failed" else ""
            latest = latest_tracker_file(self.web_app.report_dir)
        if latest is None:
            return render_shell(
                "<p class=\"empty\">尚未產生追蹤器資料。</p>",
                active_file=None,
                show_logout=self.web_app.require_auth,
            )
        html = latest.read_text(encoding="utf-8")
        if _tracker_html_needs_refresh(html):
            refresh_error = "目前顯示的是最近一次可用 dashboard；請使用完整刷新按鈕重建最新 HTML。"
        body = _extract_body(html)
        if refresh_error:
            if _tracker_html_needs_refresh(html):
                notice_html = (
                    '<main><section class="warn"><strong>Dashboard 更新尚未完成</strong><br>'
                    f'{_escape(refresh_error)}<br>目前暫時顯示最近一次可用資料。</section></main>'
                )
            else:
                notice_html = (
                    '<main><section class="notice"><strong>Dashboard 正在更新</strong><br>'
                    f'{_escape(refresh_error)}<br>目前先顯示最新可用資料，請稍後重新整理取得下一輪更新。</section></main>'
                )
            body = notice_html + body
        return render_shell(body, active_file=latest.name, extra_css=_extract_style(html), show_logout=self.web_app.require_auth)

    def _symbol_html(self, raw_symbol: str) -> str:
        symbol = urllib.parse.unquote(raw_symbol)
        payload = self._symbol_payload(symbol)
        if not payload.get("latest"):
            return render_shell("<p class=\"empty\">找不到個股資料。</p>", None, show_logout=self.web_app.require_auth)
        latest = payload["latest"]
        history_rows = "".join(
            "<tr>"
            f"<td>{_escape(str(item.get('date', '')))}</td>"
            f"<td>{_escape(str(item.get('grade', '')))}</td>"
            f"<td>{_escape(str(item.get('bullish_score', '')))}</td>"
            f"<td>{_escape(str(item.get('risk_score', '')))}</td>"
            f"<td>{_escape(str(item.get('outcome', '') or ''))}</td>"
            f"<td>{_escape(str(item.get('return_pct', '') or ''))}</td>"
            "</tr>"
            for item in payload["history"]
        )
        content = f"""
        <main>
          <h1>{_escape(str(latest.get('name', symbol)))} / {_escape(symbol)}</h1>
          <p class="meta">分級：{_escape(str(latest.get('grade', '-')))} ｜ 多方分數：{_escape(str(latest.get('bullish_score', '-')))} ｜ 風險分數：{_escape(str(latest.get('risk_score', '-')))}</p>
          <div class="table-wrap">
            <table><tbody>
              <tr><td>收盤/現價</td><td>{_escape(str(latest.get('close', '-')))}</td></tr>
              <tr><td>今日漲幅</td><td>{_escape(str(latest.get('change_pct', '-')))}%</td></tr>
              <tr><td>量比</td><td>{_escape(str(latest.get('intraday_volume_ratio', latest.get('volume_ratio', '-'))))}</td></tr>
              <tr><td>VWAP</td><td>{_escape(str(latest.get('vwap', '-')))}</td></tr>
              <tr><td>站上VWAP</td><td>{'是' if latest.get('above_vwap') else '否'}</td></tr>
              <tr><td>突破昨日高點</td><td>{'是' if latest.get('break_prev_high') else '否'}</td></tr>
              <tr><td>突破5日高點</td><td>{'是' if latest.get('break_5d_high') else '否'}</td></tr>
              <tr><td>突破10日高點</td><td>{'是' if latest.get('break_10d_high') else '否'}</td></tr>
            </tbody></table>
          </div>
          <h2>推薦與回測紀錄</h2>
          <div class="table-wrap">
            <table><thead><tr><th>日期</th><th>分級</th><th>多方分數</th><th>風險分數</th><th>結果</th><th>報酬率</th></tr></thead><tbody>
              {history_rows or '<tr><td colspan="6">尚無推薦紀錄。</td></tr>'}
            </tbody></table>
          </div>
        </main>
        """
        return render_shell(content, active_file=f"{symbol} detail", show_logout=self.web_app.require_auth)

    def _symbol_payload(self, symbol: str) -> dict:
        symbol = urllib.parse.unquote(symbol)
        with connect(default_db_path(PROJECT_ROOT)) as conn:
            latest = latest_symbol_score(conn, symbol)
            history = symbol_history(conn, symbol)
            return {
                "latest": dict(latest) if latest else None,
                "history": [dict(row) for row in history],
            }

    def _us_dashboard_payload(self) -> dict:
        try:
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                return build_us_dashboard_payload(conn, PROJECT_ROOT)
        except Exception as exc:
            clock = us_market_session()
            return {
                "error": "資料暫時無法更新",
                "error_detail": str(exc),
                "market": {
                    "market": "US",
                    "session": clock.session,
                    "status_text": clock.status_text,
                    "timezone": clock.timezone,
                    "now_local": clock.now_local.isoformat(timespec="seconds"),
                    "refresh_interval_seconds": clock.refresh_interval_seconds,
                },
                "data_source": {
                    "source": "Yahoo Finance chart endpoint",
                    "ok": False,
                    "success_count": 0,
                    "failed_symbols": [],
                    "errors": {"dashboard": str(exc)},
                    "last_success_at": None,
                    "updated_at": clock.now_local.isoformat(timespec="seconds"),
                    "next_update_seconds": clock.refresh_interval_seconds,
                },
                "candidates": [],
                "b_plus_triggers": [],
                "summary": {},
                "debug": {
                    "app_version": "unknown",
                    "model_version": "unavailable",
                    "dashboard_generated_at": clock.now_local.isoformat(timespec="seconds"),
                    "market_session": clock.session,
                    "refresh_interval": clock.refresh_interval_seconds,
                    "candidates_count": 0,
                    "recommendations_count": 0,
                    "data_source_status": "failure",
                },
                "disclaimer": "本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利。",
            }

    def _paper_dashboard_payload(self) -> dict:
        try:
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                return build_paper_dashboard(conn, PROJECT_ROOT)
        except Exception as exc:
            try:
                with connect(default_db_path(PROJECT_ROOT)) as conn:
                    return build_empty_paper_dashboard(conn, PROJECT_ROOT, str(exc))
            except Exception as fallback_exc:
                return _static_paper_fallback_payload(str(exc), str(fallback_exc))

    def _send_report(self, name: str) -> None:
        safe_name = Path(name).name
        path = self.web_app.report_dir / safe_name
        if not path.exists() or not path.is_file():
            self._send_not_found()
            return
        content_type = "text/html; charset=utf-8" if path.suffix == ".html" else "text/plain; charset=utf-8"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(data)

    def _authenticated(self) -> bool:
        return self.web_app.is_authenticated(self._session_token())

    def _session_token(self) -> Optional[str]:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _redirect(self, location: str, clear_cookie: bool = False) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self._send_no_cache_headers()
        if clear_cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.end_headers()

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_no_cache_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _send_not_found(self) -> None:
        self._send_html(render_shell("<p class=\"empty\">找不到頁面。</p>", None, show_logout=self.web_app.require_auth), HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        return


def latest_tracker_file(report_dir: Path) -> Optional[Path]:
    files = sorted(report_dir.glob("*-tracker.html"), reverse=True)
    return files[0] if files else None


def build_liveness_payload() -> dict[str, Any]:
    return {
        "api_status": "ok",
        "status": "alive",
        "service": "tw-daytrade-tracker",
        "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
    }


def readiness_http_status(health_payload: dict[str, Any]) -> HTTPStatus:
    return HTTPStatus.SERVICE_UNAVAILABLE if health_payload.get("status") == "blocked" else HTTPStatus.OK


def build_health_payload(refresh_payload: dict[str, Any], system_payload: dict[str, Any]) -> dict[str, Any]:
    health = refresh_payload.get("operational_health") or {}
    consistency = system_payload.get("consistency") or {}
    runtime = system_payload.get("runtime") or {}
    tracker = system_payload.get("tracker_html") or {}
    db_status = system_payload.get("db") or {}
    status = str(health.get("status") or "unknown")
    return {
        "api_status": "ok",
        "status": status,
        "generated_at": refresh_payload.get("generated_at") or system_payload.get("generated_at"),
        "summary": health.get("summary") or "",
        "opening_preflight": health.get("opening_preflight") or {},
        "operator_decision": health.get("operator_decision") or {},
        "operator_briefing": health.get("operator_briefing") or {},
        "operator_mode": health.get("operator_mode") or "",
        "primary_focus": health.get("primary_focus") or "",
        "do_now": health.get("do_now") or [],
        "do_not_do": health.get("do_not_do") or [],
        "decision_checklist": health.get("decision_checklist") or [],
        "watch_readiness": health.get("watch_readiness") or "",
        "watch_readiness_message": health.get("watch_readiness_message") or "",
        "operator_steps": health.get("operator_steps") or [],
        "refresh_plan": health.get("refresh_plan") or [],
        "next_action": health.get("next_action") or {},
        "blockers": health.get("blockers") or [],
        "warnings": health.get("warnings") or [],
        "can_use_dashboard": bool(health.get("can_use_dashboard")),
        "can_show_strong_long": bool(health.get("can_show_strong_long")),
        "allow_intraday_signal": bool(refresh_payload.get("allow_intraday_signal")),
        "market_mode": refresh_payload.get("market_mode") or "",
        "market_mode_label": refresh_payload.get("market_mode_label") or "",
        "data_quality_status": health.get("data_quality_status") or (refresh_payload.get("price_status_summary") or {}).get("status") or "",
        "price_status_summary": refresh_payload.get("price_status_summary") or {},
        "required_stale_layers": refresh_payload.get("required_stale_layers") or [],
        "stale_layers": refresh_payload.get("stale_layers") or [],
        "refresh_guidance": refresh_payload.get("refresh_guidance") or {},
        "deployment": {
            "runtime_commit": runtime.get("commit") or "",
            "tracker_commit": tracker.get("commit") or "",
            "runtime_matches_tracker": bool(consistency.get("runtime_matches_tracker")),
            "is_ready": bool(consistency.get("is_ready")),
            "warnings": consistency.get("warnings") or [],
        },
        "db": {
            "data_date": db_status.get("data_date") or "",
            "latest_data_at": db_status.get("latest_data_at") or "",
        },
    }


def build_operator_decision_payload(refresh_payload: dict[str, Any], system_payload: dict[str, Any]) -> dict[str, Any]:
    health = build_health_payload(refresh_payload, system_payload)
    return {
        "api_status": "ok",
        "generated_at": health.get("generated_at") or "",
        "status": health.get("status") or "",
        "summary": health.get("summary") or "",
        "operator_decision": health.get("operator_decision") or {},
        "opening_preflight": health.get("opening_preflight") or {},
        "watch_readiness": health.get("watch_readiness") or "",
        "watch_readiness_message": health.get("watch_readiness_message") or "",
        "market_mode": health.get("market_mode") or "",
        "market_mode_label": health.get("market_mode_label") or "",
        "data_quality_status": health.get("data_quality_status") or "",
        "next_action": health.get("next_action") or {},
        "blockers": health.get("blockers") or [],
        "warnings": health.get("warnings") or [],
        "deployment": health.get("deployment") or {},
    }


def _tracker_html_needs_refresh(html: str) -> bool:
    current_commit = _current_commit_hash()
    required_markers = (
        "台股做多當沖追蹤器 v1",
        "今日決策摘要",
        "最接近強烈買多 5 檔",
        "等待確認池 10 檔",
        "最大原因 / 最大卡關",
        "下一步",
        "失效條件",
        "精準分數",
        "我的持倉作戰區",
        "上一交易日復盤",
        "下個交易日觀察清單",
        "模型檢討",
        "精準資料缺口總覽",
        "資料健康度",
        "台股全市場異動掃描池",
        "漏抓股票診斷",
        "模型條件診斷",
        "B+ 觸發條件追蹤",
    )
    if any(marker not in html for marker in required_markers):
        return True
    if "long_model_v2_volume_vwap_2026-06-12" in html:
        return True
    if current_commit != "unknown" and current_commit not in html:
        return True
    return False


def _refresh_redirect_location(layer: str, started: bool) -> str:
    status = "started" if started else "already_running"
    query = urllib.parse.urlencode({"refresh_layer": layer, "refresh_status": status})
    return f"/dashboard?{query}"


def _scheduled_tracker_interval(now: Optional[datetime] = None) -> tuple[Optional[int], str]:
    local_now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    else:
        local_now = local_now.astimezone(ZoneInfo("Asia/Taipei"))
    if local_now.weekday() >= 5:
        return None, "週末休市"
    current = local_now.time()
    if dt_time(7, 0) <= current < dt_time(9, 0):
        return TW_PREMARKET_REFRESH_SECONDS, "開盤前觀察池"
    if dt_time(9, 0) <= current < dt_time(13, 30):
        return TW_INTRADAY_REFRESH_SECONDS, "台股盤中"
    if dt_time(13, 45) <= current < dt_time(15, 0):
        return TW_AFTER_CLOSE_REFRESH_SECONDS, "收盤後回測"
    return None, "非排程更新時段"


def _scheduled_refresh_layers(now: Optional[datetime] = None) -> list[tuple[str, int, str]]:
    local_now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    else:
        local_now = local_now.astimezone(ZoneInfo("Asia/Taipei"))
    if local_now.weekday() >= 5:
        return []
    current = local_now.time()
    if dt_time(7, 0) <= current < dt_time(9, 0):
        return [("full_market", TW_PREMARKET_REFRESH_SECONDS, "開盤前觀察池")]
    if dt_time(9, 0) <= current < dt_time(13, 30):
        return [
            ("full_market", TW_INTRADAY_REFRESH_SECONDS, "台股盤中全市場慢掃"),
            ("watchlist", TW_WATCHLIST_REFRESH_SECONDS, "台股盤中重點觀察快追"),
            ("positions", TW_POSITIONS_REFRESH_SECONDS, "台股盤中持倉與觸發控風險"),
        ]
    if dt_time(13, 45) <= current < dt_time(15, 0):
        return [
            ("full_market", TW_AFTER_CLOSE_REFRESH_SECONDS, "收盤後全市場整理"),
            ("post_close_validation", TW_AFTER_CLOSE_REFRESH_SECONDS, "收盤後盤後驗證"),
        ]
    return []


def _safe_refresh_tracker(report_dir: Path, refresh_lock: Optional[threading.Lock] = None, wait: bool = True) -> str:
    locked = False
    if refresh_lock is not None:
        locked = refresh_lock.acquire(blocking=wait)
        if not locked:
            return "tracker 正在更新中，已先顯示最近一次可用資料。"
    try:
        _run_tracker_refresh(report_dir)
        return ""
    except subprocess.TimeoutExpired:
        return f"tracker 更新超過 {TRACKER_REFRESH_TIMEOUT_SECONDS} 秒，已先顯示最近一次可用資料。"
    except Exception as exc:
        return str(exc)
    finally:
        if refresh_lock is not None and locked:
            refresh_lock.release()


def _run_tracker_refresh(report_dir: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_daytrade_system.cli",
            "tracker",
            "--output-dir",
            str(report_dir),
            "--daily-range",
            "6mo",
            "--intraday-range",
            "1d",
            "--interval",
            "5m",
            "--opening-bars",
            "3",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=TRACKER_REFRESH_TIMEOUT_SECONDS,
    )


def _current_commit_hash() -> str:
    value, _source = current_commit_info(PROJECT_ROOT)
    return value


def _notification_signals_payload(conn) -> dict:
    latest_date = conn.execute("SELECT MAX(date) AS date FROM recommendations").fetchone()["date"]
    if not latest_date:
        return {"ok": True, "date": None, "signals": []}
    rows = conn.execute(
        """
        SELECT
          r.market,
          r.date,
          r.symbol,
          COALESCE(s.name, us.name_zh, r.symbol) AS name_zh,
          COALESCE(us.name_en, s.name, r.symbol) AS name_en,
          r.grade,
          r.entry_status,
          r.lifecycle_status,
          CASE
            WHEN r.lifecycle_status = 'triggered' THEN 'triggered'
            ELSE r.entry_status
          END AS status,
          r.trigger_time,
          r.trigger_price,
          r.trigger_reason,
          r.latest_seen_at
        FROM recommendations r
        LEFT JOIN symbols s ON r.market = 'TW' AND s.symbol = r.symbol
        LEFT JOIN us_symbols us ON r.market = 'US' AND us.symbol = r.symbol
        WHERE r.date = ?
        ORDER BY
          CASE r.lifecycle_status WHEN 'triggered' THEN 0 ELSE 1 END,
          CASE r.entry_status WHEN 'executable' THEN 0 WHEN 'practice_long' THEN 1 WHEN 'wait_breakout' THEN 2 WHEN 'wait_vwap' THEN 3 WHEN 'wait_volume' THEN 4 ELSE 5 END,
          r.symbol
        LIMIT 300
        """,
        (latest_date,),
    ).fetchall()
    return {
        "ok": True,
        "date": latest_date,
        "signals": [dict(row) for row in rows],
    }


def notification_controls_html() -> str:
    return """
      <div class="notification-controls" aria-label="即時通知設定">
        <label class="notification-toggle" title="瀏覽器可能需要先點擊頁面後才允許播放提示音。">
          <input id="notify-sound-toggle" type="checkbox" checked>
          <span>聲音提示</span>
        </label>
        <label class="notification-toggle" title="開啟後會向瀏覽器請求桌面通知權限。">
          <input id="notify-desktop-toggle" type="checkbox">
          <span>桌面通知</span>
        </label>
        <span id="notify-status" class="notification-status">通知準備中</span>
      </div>
    """


def notification_module_script() -> str:
    return r"""
    (() => {
      if (window.StockNotificationModule) return;

      const STORAGE_SOUND = "stockNotifySoundEnabled";
      const STORAGE_DESKTOP = "stockNotifyDesktopEnabled";
      const TRIGGERED_STATUS = "triggered";
      const POLL_MS = 30000;
      const state = {
        soundEnabled: localStorage.getItem(STORAGE_SOUND) !== "0",
        desktopEnabled: localStorage.getItem(STORAGE_DESKTOP) === "1",
        baselineReady: false,
        statuses: new Map(),
        audioContext: null,
        pollTimer: null,
      };

      const $ = (id) => document.getElementById(id);
      const text = (value, fallback = "") => value === null || value === undefined || value === "" ? fallback : String(value);
      const setStatus = (message) => {
        const node = $("notify-status");
        if (node) node.textContent = message;
      };
      const displayName = (item) => {
        const symbol = text(item.symbol, "-");
        const name = text(item.name_zh || item.short_name_zh || item.name_en, "");
        return name && name !== symbol ? `${symbol}｜${name}` : symbol;
      };
      const signalKey = (item) => `${text(item.market, "TW")}:${text(item.symbol)}`;
      const signalStatus = (item) => text(item.status || item.lifecycle_status || item.entry_status || "unknown");

      function syncControls() {
        const sound = $("notify-sound-toggle");
        const desktop = $("notify-desktop-toggle");
        if (sound) sound.checked = state.soundEnabled;
        if (desktop) desktop.checked = state.desktopEnabled && notificationPermission() === "granted";
        if (!("Notification" in window)) {
          if (desktop) {
            desktop.checked = false;
            desktop.disabled = true;
          }
          setStatus("此瀏覽器不支援桌面通知");
        } else if (Notification.permission === "denied") {
          if (desktop) desktop.checked = false;
          setStatus("桌面通知已被瀏覽器封鎖");
        } else {
          setStatus(state.desktopEnabled ? "即時通知已啟用" : "聲音提示可用");
        }
      }

      function notificationPermission() {
        return "Notification" in window ? Notification.permission : "unsupported";
      }

      async function ensureAudioContext() {
        if (!state.soundEnabled) return null;
        const AudioCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtor) {
          setStatus("此瀏覽器不支援音訊提示");
          return null;
        }
        if (!state.audioContext) state.audioContext = new AudioCtor();
        if (state.audioContext.state === "suspended") {
          try {
            await state.audioContext.resume();
          } catch (error) {
            setStatus("音訊尚未解鎖，請先點擊頁面一次");
            return null;
          }
        }
        return state.audioContext;
      }

      async function playBeep() {
        try {
          const ctx = await ensureAudioContext();
          if (!ctx) return;
          const oscillator = ctx.createOscillator();
          const gain = ctx.createGain();
          const start = ctx.currentTime;
          const duration = 0.16;
          oscillator.type = "triangle";
          oscillator.frequency.setValueAtTime(880, start);
          oscillator.frequency.exponentialRampToValueAtTime(1320, start + duration * 0.55);
          gain.gain.setValueAtTime(0.0001, start);
          gain.gain.exponentialRampToValueAtTime(0.055, start + 0.018);
          gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
          oscillator.connect(gain);
          gain.connect(ctx.destination);
          oscillator.start(start);
          oscillator.stop(start + duration + 0.02);
        } catch (error) {
          setStatus(`音訊提示失敗：${error.message}`);
        }
      }

      async function requestDesktopPermission() {
        if (!("Notification" in window)) {
          state.desktopEnabled = false;
          localStorage.setItem(STORAGE_DESKTOP, "0");
          syncControls();
          return false;
        }
        if (Notification.permission === "granted") {
          state.desktopEnabled = true;
          localStorage.setItem(STORAGE_DESKTOP, "1");
          syncControls();
          return true;
        }
        if (Notification.permission === "denied") {
          state.desktopEnabled = false;
          localStorage.setItem(STORAGE_DESKTOP, "0");
          syncControls();
          return false;
        }
        try {
          const permission = await Notification.requestPermission();
          state.desktopEnabled = permission === "granted";
          localStorage.setItem(STORAGE_DESKTOP, state.desktopEnabled ? "1" : "0");
          syncControls();
          return state.desktopEnabled;
        } catch (error) {
          state.desktopEnabled = false;
          localStorage.setItem(STORAGE_DESKTOP, "0");
          setStatus(`通知權限請求失敗：${error.message}`);
          syncControls();
          return false;
        }
      }

      function sendDesktopNotification(item) {
        if (!state.desktopEnabled || notificationPermission() !== "granted") return;
        try {
          const label = displayName(item);
          const notification = new Notification("當沖進場訊號", {
            body: `${label} 已觸發進場訊號，請注意！`,
            tag: `stock-trigger-${signalKey(item)}`,
            renotify: true,
            silent: true,
          });
          notification.onclick = () => {
            window.focus();
            if (item.market === "TW" && item.symbol) {
              window.location.href = `/tw/advisor?symbol=${encodeURIComponent(item.symbol)}`;
            }
          };
        } catch (error) {
          setStatus(`桌面通知失敗：${error.message}`);
        }
      }

      function notifyTriggered(item) {
        playBeep();
        sendDesktopNotification(item);
        setStatus(`${displayName(item)} 已觸發進場訊號`);
      }

      function observeSignals(items, options = {}) {
        const list = Array.isArray(items) ? items : [];
        const suppressInitial = options.suppressInitial !== false;
        for (const item of list) {
          if (!item || !item.symbol) continue;
          const key = signalKey(item);
          const nextStatus = signalStatus(item);
          const prevStatus = state.statuses.get(key);
          state.statuses.set(key, nextStatus);
          if (
            state.baselineReady &&
            prevStatus &&
            prevStatus !== TRIGGERED_STATUS &&
            nextStatus === TRIGGERED_STATUS
          ) {
            notifyTriggered(item);
          }
        }
        if (!state.baselineReady && suppressInitial) {
          state.baselineReady = true;
        }
      }

      async function pollSignals() {
        try {
          const response = await fetch("/api/notification/signals", { cache: "no-store", credentials: "same-origin" });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = await response.json();
          observeSignals(payload.signals || []);
        } catch (error) {
          setStatus(`通知狀態讀取失敗：${error.message}`);
        }
      }

      function bindControls() {
        const sound = $("notify-sound-toggle");
        const desktop = $("notify-desktop-toggle");
        if (sound) {
          sound.addEventListener("change", async () => {
            state.soundEnabled = Boolean(sound.checked);
            localStorage.setItem(STORAGE_SOUND, state.soundEnabled ? "1" : "0");
            if (state.soundEnabled) await ensureAudioContext();
            syncControls();
          });
        }
        if (desktop) {
          desktop.addEventListener("change", async () => {
            if (desktop.checked) {
              await requestDesktopPermission();
            } else {
              state.desktopEnabled = false;
              localStorage.setItem(STORAGE_DESKTOP, "0");
              syncControls();
            }
          });
        }
        document.addEventListener("pointerdown", () => ensureAudioContext(), { once: true, passive: true });
      }

      function start() {
        bindControls();
        syncControls();
        pollSignals();
        state.pollTimer = window.setInterval(pollSignals, POLL_MS);
      }

      window.StockNotificationModule = {
        observeSignals,
        pollSignals,
        playTestBeep: playBeep,
        requestDesktopPermission,
      };

      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
      } else {
        start();
      }
    })();
    """


def hotkeys_script() -> str:
    return r"""
    (() => {
      if (window.StockHotkeys) return;

      const SEARCH_SELECTORS = [
        "[data-stock-search]",
        "#tw-scan-symbol",
        "#tw-advisor-symbol",
        "#manual-symbol",
        "input[type='search']",
      ];
      const TABS = [
        { key: "1", mode: "all", label: "全部觀察股" },
        { key: "2", mode: "triggered", label: "已觸發 / 多頭動能股" },
        { key: "3", mode: "wait_vwap", label: "等待站回 VWAP 股" },
        { key: "4", mode: "high_risk", label: "高風險觀望股" },
      ];
      const state = { mode: "all", observerTimer: null };

      const isTypingTarget = (target) => {
        if (!target) return false;
        const tag = String(target.tagName || "").toLowerCase();
        return target.isContentEditable || tag === "input" || tag === "textarea" || tag === "select";
      };
      const text = (value) => String(value || "");
      const visible = (node) => Boolean(node && node.offsetParent !== null);

      function searchInputs() {
        const seen = new Set();
        const inputs = [];
        for (const selector of SEARCH_SELECTORS) {
          document.querySelectorAll(selector).forEach((input) => {
            if (!seen.has(input) && !input.disabled && !input.readOnly && visible(input)) {
              seen.add(input);
              inputs.push(input);
            }
          });
        }
        return inputs;
      }

      function focusSearch() {
        const input = searchInputs()[0];
        if (!input) return false;
        input.focus({ preventScroll: false });
        if (typeof input.select === "function") input.select();
        input.scrollIntoView({ block: "center", behavior: "smooth" });
        return true;
      }

      function clearSearchAndBlur() {
        searchInputs().forEach((input) => {
          if (input.value) {
            input.value = "";
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
          }
          input.blur();
        });
        if (document.activeElement && typeof document.activeElement.blur === "function") {
          document.activeElement.blur();
        }
      }

      function columnMode(column) {
        const content = text(column.textContent).toLowerCase();
        if (
          content.includes("high_risk") ||
          content.includes("高風險") ||
          content.includes("追價風險") ||
          content.includes("避開")
        ) {
          return "high_risk";
        }
        if (
          content.includes("wait_vwap") ||
          content.includes("等待 vwap") ||
          content.includes("等待站回 vwap") ||
          content.includes("站回 vwap")
        ) {
          return "wait_vwap";
        }
        if (
          content.includes("triggered") ||
          content.includes("已觸發") ||
          content.includes("executable") ||
          content.includes("可執行") ||
          content.includes("強烈買多") ||
          content.includes("買多") ||
          content.includes("多頭動能")
        ) {
          return "triggered";
        }
        return "all";
      }

      function applyGridMode(grid, mode) {
        const columns = Array.from(grid.querySelectorAll(":scope > .signal-column"));
        if (!columns.length) return;
        let visibleCount = 0;
        columns.forEach((column) => {
          const shouldShow = mode === "all" || columnMode(column) === mode;
          column.hidden = !shouldShow;
          column.classList.toggle("hotkey-filter-hidden", !shouldShow);
          if (shouldShow) visibleCount += 1;
        });
        grid.dataset.hotkeyMode = mode;
        const empty = grid.parentElement?.querySelector(".hotkey-empty-message");
        if (empty) empty.hidden = visibleCount > 0;
      }

      function ensureTabsForGrid(grid) {
        if (grid.dataset.hotkeyTabsReady === "1") return;
        grid.dataset.hotkeyTabsReady = "1";
        const nav = document.createElement("div");
        nav.className = "stock-hotkey-tabs";
        nav.setAttribute("aria-label", "股票分類快捷鍵");
        nav.innerHTML = TABS.map((tab) => `<button type="button" data-hotkey-tab="${tab.key}" data-hotkey-mode="${tab.mode}">${tab.key}｜${tab.label}</button>`).join("");
        const empty = document.createElement("p");
        empty.className = "hotkey-empty-message muted";
        empty.hidden = true;
        empty.textContent = "此分類目前沒有可顯示的股票。";
        grid.parentNode.insertBefore(nav, grid);
        grid.parentNode.insertBefore(empty, grid.nextSibling);
        nav.addEventListener("click", (event) => {
          const button = event.target.closest("[data-hotkey-mode]");
          if (!button) return;
          activateMode(button.dataset.hotkeyMode, { scroll: false });
        });
      }

      function decorateSignalGrids() {
        document.querySelectorAll(".signal-grid").forEach((grid) => {
          ensureTabsForGrid(grid);
          applyGridMode(grid, state.mode);
        });
        updateTabButtons();
      }

      function updateTabButtons() {
        document.querySelectorAll("[data-hotkey-mode]").forEach((button) => {
          const active = button.dataset.hotkeyMode === state.mode;
          button.classList.toggle("is-active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
        });
      }

      function activateNativeTab(key) {
        const native = Array.from(document.querySelectorAll(`[data-hotkey-tab="${key}"]`))
          .find((node) => !node.closest(".stock-hotkey-tabs"));
        if (native) {
          native.click();
          return true;
        }
        return false;
      }

      function activateMode(mode, options = {}) {
        state.mode = mode || "all";
        decorateSignalGrids();
        updateTabButtons();
        const firstGrid = document.querySelector(".signal-grid");
        if (firstGrid && options.scroll !== false) {
          firstGrid.scrollIntoView({ block: "start", behavior: "smooth" });
        }
      }

      function handleKeydown(event) {
        const key = event.key;
        if (key === "Escape") {
          clearSearchAndBlur();
          return;
        }
        if (isTypingTarget(event.target)) return;
        if (key === "/") {
          event.preventDefault();
          focusSearch();
          return;
        }
        const tab = TABS.find((item) => item.key === key);
        if (tab) {
          event.preventDefault();
          if (!activateNativeTab(key)) activateMode(tab.mode);
        }
      }

      function scheduleDecorate() {
        window.clearTimeout(state.observerTimer);
        state.observerTimer = window.setTimeout(decorateSignalGrids, 80);
      }

      function start() {
        decorateSignalGrids();
        document.addEventListener("keydown", handleKeydown);
        const observer = new MutationObserver(scheduleDecorate);
        observer.observe(document.body, { childList: true, subtree: true });
      }

      window.StockHotkeys = {
        focusSearch,
        clearSearchAndBlur,
        activateMode,
        decorateSignalGrids,
      };

      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
      } else {
        start();
      }
    })();
    """


def position_sizing_controls_html() -> str:
    return """
      <details class="position-sizing-settings">
        <summary>部位風控</summary>
        <div class="position-sizing-panel">
          <label>今日最大可接受總虧損
            <input id="position-daily-risk" type="number" min="0" step="100" inputmode="decimal" placeholder="例如 10000">
          </label>
          <label>單筆最大冒險金額
            <input id="position-risk-per-trade" type="number" min="0" step="100" inputmode="decimal" placeholder="例如 2000">
          </label>
          <span id="position-sizing-status" class="position-sizing-status">預設單筆 2000 元</span>
        </div>
      </details>
    """


def position_sizing_calculator_script() -> str:
    return r"""
    (() => {
      if (window.PositionSizingCalculator) return;

      const STORAGE_DAILY = "stockPositionDailyRisk";
      const STORAGE_TRADE = "stockPositionTradeRisk";
      const DEFAULT_TRADE_RISK = 2000;
      const state = {
        dailyRisk: Number(localStorage.getItem(STORAGE_DAILY) || 0),
        tradeRisk: Number(localStorage.getItem(STORAGE_TRADE) || DEFAULT_TRADE_RISK),
        refreshTimer: null,
      };

      const $ = (id) => document.getElementById(id);
      const numeric = (value) => {
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
      };
      const formatInt = (value) => Math.max(0, Math.floor(Number(value) || 0)).toLocaleString();
      const riskBudget = () => {
        const trade = numeric(state.tradeRisk);
        const daily = numeric(state.dailyRisk);
        if (trade && trade > 0) return trade;
        if (daily && daily > 0) return daily;
        return DEFAULT_TRADE_RISK;
      };
      const setStatus = () => {
        const node = $("position-sizing-status");
        if (!node) return;
        const daily = numeric(state.dailyRisk);
        const trade = numeric(state.tradeRisk);
        const source = trade && trade > 0 ? `單筆 ${formatInt(trade)} 元` : daily && daily > 0 ? `今日總虧損 ${formatInt(daily)} 元` : `預設單筆 ${formatInt(DEFAULT_TRADE_RISK)} 元`;
        node.textContent = `計算基準：${source}`;
      };

      function syncInputs() {
        const daily = $("position-daily-risk");
        const trade = $("position-risk-per-trade");
        if (daily) daily.value = state.dailyRisk > 0 ? String(state.dailyRisk) : "";
        if (trade) trade.value = state.tradeRisk > 0 ? String(state.tradeRisk) : String(DEFAULT_TRADE_RISK);
        setStatus();
      }

      function renderTag(tag) {
        const entry = numeric(tag.dataset.positionEntry);
        const stop = numeric(tag.dataset.positionStop);
        const budget = riskBudget();
        tag.classList.remove("position-size-ok", "position-size-danger", "position-size-muted");
        if (!entry || !stop || entry <= 0 || stop <= 0) {
          if (tag.textContent !== "部位：缺進場/停損") tag.textContent = "部位：缺進場/停損";
          tag.title = "缺少建議進場價或停損價，無法計算部位。";
          tag.classList.add("position-size-muted");
          return;
        }
        const spread = entry - stop;
        if (!Number.isFinite(spread) || spread <= 0) {
          if (tag.textContent !== "部位：停損價需低於進場價") tag.textContent = "部位：停損價需低於進場價";
          tag.title = `進場 ${entry}，停損 ${stop}，價差無法計算。`;
          tag.classList.add("position-size-danger");
          return;
        }
        const shares = Math.floor(budget / spread);
        const lots = Math.floor(shares / 1000);
        const oddShares = shares % 1000;
        const riskPerLot = spread * 1000;
        const danger = lots < 1;
        const nextText = danger
          ? `建議點火：${lots} 張（零股：${formatInt(shares)} 股）｜風暴比不佳，建議放棄`
          : `建議點火：${lots} 張（零股：${formatInt(oddShares)} 股）`;
        if (tag.textContent !== nextText) tag.textContent = nextText;
        tag.title = `單筆風險 ${formatInt(budget)} 元；進場 ${entry.toFixed(2)}，停損 ${stop.toFixed(2)}，單張風險約 ${formatInt(riskPerLot)} 元。`;
        tag.classList.add(danger ? "position-size-danger" : "position-size-ok");
      }

      function refresh() {
        document.querySelectorAll(".position-size-tag[data-position-entry][data-position-stop]").forEach(renderTag);
      }

      function scheduleRefresh() {
        window.clearTimeout(state.refreshTimer);
        state.refreshTimer = window.setTimeout(refresh, 50);
      }

      function bindInputs() {
        const daily = $("position-daily-risk");
        const trade = $("position-risk-per-trade");
        if (daily) {
          daily.addEventListener("input", () => {
            state.dailyRisk = Number(daily.value || 0);
            localStorage.setItem(STORAGE_DAILY, state.dailyRisk > 0 ? String(state.dailyRisk) : "");
            setStatus();
            refresh();
          });
        }
        if (trade) {
          trade.addEventListener("input", () => {
            state.tradeRisk = Number(trade.value || 0);
            localStorage.setItem(STORAGE_TRADE, state.tradeRisk > 0 ? String(state.tradeRisk) : "");
            setStatus();
            refresh();
          });
        }
      }

      function start() {
        syncInputs();
        bindInputs();
        refresh();
        const observer = new MutationObserver(scheduleRefresh);
        observer.observe(document.body, { childList: true, subtree: true });
      }

      window.PositionSizingCalculator = { refresh, riskBudget };
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
      } else {
        start();
      }
    })();
    """


def render_login_page(error: str = "") -> str:
    error_html = f"<div class=\"error\">{_escape(error)}</div>" if error else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登入股票當沖系統</title>
  <style>{base_css()}</style>
</head>
<body class="login-body">
  <main class="login-panel">
    <h1>股票當沖系統</h1>
    {error_html}
    <form method="post" action="/login">
      <label>帳號<input name="username" autocomplete="username" required autofocus></label>
      <label>密碼<input name="password" type="password" autocomplete="current-password" required></label>
      <button type="submit">登入</button>
    </form>
  </main>
</body>
</html>"""


def render_us_dashboard_page(show_logout: bool = False) -> str:
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>美股當沖追蹤器</title>
  <style>{base_css()}{us_dashboard_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/tw/advisor">個股建議</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
      {notification_controls_html()}
      {position_sizing_controls_html()}
      <span id="us-refresh-status" class="refresh-status">準備更新</span>
      {logout_link}
    </div>
  </nav>
  <main class="us-page">
    <header class="us-header">
      <div>
        <h1>美股追蹤</h1>
        <p class="meta">大型熱門股 watchlist｜Yahoo Finance｜前端 polling 即時更新</p>
      </div>
      <div id="us-session" class="session-pill">讀取中</div>
    </header>
    <section class="notice">本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利。</section>
    <section id="us-error" class="warn" hidden>資料暫時無法更新。</section>
    <section id="us-decision-center" class="decision-center"></section>
    <section id="us-signal-center" class="decision-center"></section>
    <section class="summary" id="us-summary"></section>
    <h2>資料來源狀態</h2>
    <div class="table-wrap"><table><tbody id="us-source"></tbody></table></div>
    <h2>指數狀態</h2>
    <div class="table-wrap"><table><tbody id="us-market"></tbody></table></div>
    <h2>B+ 觸發條件追蹤</h2>
    <div class="table-wrap">
      <table><thead><tr><th>標的</th><th>市場</th><th>現價</th><th>VWAP</th><th>量比</th><th>進場狀態</th><th>生命週期</th><th>觸發條件</th><th>距離觸發</th><th>Readiness</th><th>下一步</th><th>信心</th></tr></thead><tbody id="us-b-plus-triggers"></tbody></table>
    </div>
    <h2>美股候選股</h2>
    <div class="table-wrap">
      <table class="us-table">
        <thead>
          <tr>
            <th>標的</th><th>價格</th><th>漲跌幅</th><th>成交量</th><th>量比 Volume Ratio</th>
            <th>均價線 VWAP</th><th>盤前高點</th><th>突破</th><th>多方分數 Bullish Score</th>
            <th>風險分數 Risk Score</th><th>分級</th><th>進場狀態 Entry Status</th>
            <th>當下狀態</th><th>信心</th><th>衝突</th><th>生命週期 Lifecycle</th><th>理由</th><th>風險理由</th>
          </tr>
        </thead>
        <tbody id="us-candidates"><tr><td colspan="18">讀取中...</td></tr></tbody>
      </table>
    </div>
    <h2 id="debug">Debug</h2>
    <div class="debug-block"><table><tbody id="us-debug"></tbody></table></div>
  </main>
  <script>{hotkeys_script()}</script>
  <script>{position_sizing_calculator_script()}</script>
  <script>{notification_module_script()}</script>
  <script>{us_dashboard_script()}</script>
</body>
</html>"""


def render_paper_dashboard_page(show_logout: bool = False) -> str:
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>虛擬交易</title>
  <style>{base_css()}{paper_dashboard_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/tw/advisor">個股建議</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
      {notification_controls_html()}
      {position_sizing_controls_html()}
      <span id="paper-refresh-status" class="refresh-status">準備更新</span>
      {logout_link}
    </div>
  </nav>
  <main class="paper-page">
    <header class="paper-header">
      <div>
        <h1>虛擬交易 Paper Trading</h1>
        <p class="meta">依照 recommendations / entry_status 模擬買進、持倉、停損、停利與收盤出場。</p>
      </div>
      <div class="session-pill">不串接券商｜不自動下單</div>
    </header>
    <section class="notice">本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利；本頁不會送出任何真實委託。</section>
    <section id="paper-error" class="warn" hidden>正在載入虛擬交易資料……</section>
    <section id="paper-decision-summary" class="decision-center"></section>
    <section class="manual-panel" aria-labelledby="manual-trade-title">
      <div class="section-title-row">
        <h2 id="manual-trade-title">手動虛擬交易</h2>
        <span id="manual-quote-status" class="muted">輸入股票代號後可嘗試帶入名稱與參考行情。</span>
      </div>
      <form id="manual-trade-form" class="manual-form">
        <label>市場 market
          <select id="manual-market" name="market">
            <option value="US">US</option>
            <option value="TW">TW</option>
          </select>
        </label>
        <label>股票代號 symbol
          <input id="manual-symbol" name="symbol" data-stock-search placeholder="NVDA 或 2330.TW" autocomplete="off">
        </label>
        <label>中文名稱 name_zh
          <input id="manual-name-zh" name="name_zh" placeholder="輝達 / 台積電">
        </label>
        <label>英文名稱 name_en
          <input id="manual-name-en" name="name_en" placeholder="NVIDIA Corporation">
        </label>
        <label>方向 side
          <select id="manual-side" name="side" disabled><option value="buy">buy 手動買進</option></select>
        </label>
        <label>虛擬進場價格 entry_price
          <input id="manual-entry-price" name="entry_price" type="number" min="0" step="0.01" placeholder="0.00">
        </label>
        <label>數量 quantity
          <input id="manual-quantity" name="quantity" type="number" min="0" step="0.0001" placeholder="1">
        </label>
        <label>停損 stop_loss
          <input id="manual-stop-loss" name="stop_loss" type="number" min="0" step="0.01" placeholder="低於進場價">
        </label>
        <label>停利 target_price
          <input id="manual-target-price" name="target_price" type="number" min="0" step="0.01" placeholder="高於進場價">
        </label>
        <label>帳戶 account
          <select id="manual-account" name="account">
            <option value="US">US paper account</option>
            <option value="TW">TW paper account</option>
          </select>
        </label>
        <label>風控模式 risk_mode
          <select id="manual-risk-mode" name="risk_mode">
            <option value="manual_only">manual_only 手動管理</option>
            <option value="auto_stop_take_profit">auto_stop_take_profit 自動停損 / 停利</option>
            <option value="follow_system">follow_system 跟隨系統風控</option>
          </select>
        </label>
        <label class="manual-reason">進場理由 entry_reason
          <input id="manual-entry-reason" name="entry_reason" placeholder="例如：測試突破 VWAP 後的虛擬買進">
        </label>
        <div class="manual-actions">
          <button type="submit">建立虛擬買進</button>
          <button type="button" id="manual-clear">清除表單</button>
        </div>
      </form>
      <div id="manual-form-status" class="manual-status" aria-live="polite"></div>
    </section>
    <h2>帳戶總覽</h2>
    <section class="summary" id="paper-accounts"></section>
    <h2>等待觸發的 B+ 練習訊號</h2>
    <div class="table-wrap"><table><thead><tr><th>市場</th><th>標的</th><th>進場狀態</th><th>生命週期</th><th>觸發條件</th><th>距離觸發</th><th>Readiness</th></tr></thead><tbody id="paper-b-plus-waiting"></tbody></table></div>
    <h2>目前持倉</h2>
    <div class="table-wrap"><table><thead><tr><th>市場</th><th>標的</th><th>來源</th><th>風控模式</th><th>自動出場</th><th>風控提醒</th><th>進場價</th><th>現價</th><th>數量</th><th>未實現損益</th><th>停損</th><th>停利</th><th>操作</th></tr></thead><tbody id="paper-positions"></tbody></table></div>
    <h2>今日交易 / 最近交易</h2>
    <div class="table-wrap"><table><thead><tr><th>市場</th><th>標的</th><th>來源</th><th>風控模式</th><th>自動出場</th><th>狀態</th><th>分級</th><th>進場狀態</th><th>進場</th><th>出場</th><th>損益</th><th>原因</th></tr></thead><tbody id="paper-trades"></tbody></table></div>
    <h2>跳過紀錄</h2>
    <div class="table-wrap"><table><thead><tr><th>市場</th><th>標的</th><th>訊號</th><th>原因</th><th>時間</th></tr></thead><tbody id="paper-skipped"></tbody></table></div>
    <h2>策略績效</h2>
    <div class="table-wrap" id="paper-performance"></div>
    <div id="close-review-modal" class="review-modal" hidden role="dialog" aria-modal="true" aria-labelledby="close-review-title">
      <div class="review-dialog">
        <div class="review-dialog-head">
          <div>
            <h2 id="close-review-title">虛擬平倉覆盤</h2>
            <p class="muted">平倉前請勾選本次交易檢討標籤，可複選。這些資料會寫入策略成績單。</p>
          </div>
          <button type="button" id="close-review-cancel-x" class="ghost-button" aria-label="關閉">×</button>
        </div>
        <section id="close-review-summary" class="review-summary"></section>
        <fieldset class="review-tags">
          <legend>本次交易檢討標籤</legend>
          <div id="close-review-tags"></div>
        </fieldset>
        <div id="close-review-error" class="manual-status fail" hidden>請至少勾選一個本次交易檢討標籤。</div>
        <div class="review-actions">
          <button type="button" id="close-review-cancel">取消</button>
          <button type="button" id="close-review-confirm" disabled>確認平倉</button>
        </div>
      </div>
    </div>
    <h2 id="debug">Debug</h2>
    <div class="debug-block"><table><tbody id="paper-debug"></tbody></table></div>
  </main>
  <script>{hotkeys_script()}</script>
  <script>{position_sizing_calculator_script()}</script>
  <script>{notification_module_script()}</script>
  <script>{paper_dashboard_script()}</script>
</body>
</html>"""


def render_tw_advisor_page(show_logout: bool = False) -> str:
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>個股當沖作戰卡</title>
  <style>{base_css()}{tw_advisor_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/tw/advisor">個股建議</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
    </div>
    <div class="topbar-actions">{notification_controls_html()}{position_sizing_controls_html()}{logout_link}</div>
  </nav>
  <main class="advisor-page">
    <section class="advisor-hero">
      <div>
        <h1>個股當沖作戰卡</h1>
        <p class="muted">輸入股票代號後，系統會檢查這檔股票目前是否符合當沖追蹤條件，包括 VWAP、量比、突破、追價風險、資料可信度與歷史驗證。本系統不是報明牌，而是協助判斷目前是強烈買多、買多、觀察、看空或資料不足。</p>
        <div class="advisor-examples" aria-label="範例股票">
          <button type="button" data-symbol="2330">台積電</button>
          <button type="button" data-symbol="1301">台塑</button>
          <button type="button" data-symbol="2344">華邦電</button>
          <button type="button" data-symbol="3189">景碩</button>
          <button type="button" data-symbol="2886">兆豐金</button>
        </div>
      </div>
      <form id="tw-advisor-form" class="advisor-form">
        <label>
          股票代號或名稱
          <input id="tw-advisor-symbol" data-stock-search autocomplete="off" placeholder="例如 1301、1301.TW、6603.TWO、台塑" value="">
        </label>
        <div class="advisor-form-actions">
          <button type="submit">快速查詢</button>
          <button type="button" id="tw-advisor-live-scan" class="secondary-button">即時重算</button>
        </div>
        <p class="advisor-form-hint">快速查詢讀取最新模型快照；即時重算會重新抓取行情，可能較慢。</p>
      </form>
    </section>
    <section id="tw-advisor-status" class="notice">準備查詢。可輸入 1301、1301.TW、6603.TWO，或已知股票名稱。</section>
    <section id="tw-advisor-result" class="advisor-result empty">
      <strong>請輸入股票代號或點選範例。</strong><br>
      查詢後會顯示結論卡、資料可信度、關鍵指標、理由、下一步條件、歷史驗證、來源排名與開發者資訊。
    </section>
    <section class="notice">本系統僅供資料整理、策略追蹤、虛擬交易與回測，不構成投資建議，也不保證獲利。</section>
  </main>
  <script>{hotkeys_script()}</script>
  <script>{position_sizing_calculator_script()}</script>
  <script>{notification_module_script()}</script>
  <script>{tw_advisor_script()}</script>
</body>
</html>"""


def render_accuracy_page(show_logout: bool = False) -> str:
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>策略成績單</title>
  <style>{base_css()}{paper_dashboard_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/tw/advisor">個股建議</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
    </div>
    <div class="topbar-actions">{notification_controls_html()}{position_sizing_controls_html()}{logout_link}</div>
  </nav>
  <main class="paper-page">
    <header class="paper-header">
      <div>
        <h1>策略成績單</h1>
        <p class="meta">根據 recommendations / backtest / paper trades 統計訊號準確度；樣本不足時不做過度判斷。</p>
      </div>
      <div class="session-pill">Accuracy / Confidence</div>
    </header>
    <section class="notice">本系統僅供資料整理、策略追蹤與回測，不構成投資建議，也不保證獲利。</section>
    <section id="accuracy-error" class="warn" hidden>策略成績單 API 暫時無法更新。</section>
    <h2>整體摘要</h2>
    <section class="summary" id="accuracy-summary"></section>
    <h2>資料完整度</h2>
    <section id="accuracy-data-completeness" class="review-chart-card"></section>
    <h2>依進場狀態</h2>
    <div class="table-wrap" id="accuracy-status"></div>
    <h2>依分級</h2>
    <div class="table-wrap" id="accuracy-grade"></div>
    <h2>依信心等級</h2>
    <div class="table-wrap" id="accuracy-confidence"></div>
    <h2>依市場</h2>
    <div class="table-wrap" id="accuracy-market"></div>
    <h2>20 / 40 / 60 日策略成績</h2>
    <div class="table-wrap" id="accuracy-scorecard"></div>
    <h2>進場雷達成績單</h2>
    <section id="accuracy-entry-radar" class="review-chart-card"></section>
    <div class="table-wrap" id="accuracy-entry-radar-table"></div>
    <h2>真假突破診斷成績單</h2>
    <section id="accuracy-breakout-trap" class="review-chart-card"></section>
    <div class="table-wrap" id="accuracy-breakout-trap-table"></div>
    <h2>心魔分佈（錯誤原因統計）</h2>
    <section id="accuracy-review-chart" class="review-chart-card"></section>
    <h2>漏抓率報告</h2>
    <section class="summary" id="accuracy-missed"></section>
    <div class="table-wrap" id="accuracy-missed-examples"></div>
    <h2>模型調整建議</h2>
    <div class="table-wrap"><table><tbody id="accuracy-suggestions"></tbody></table></div>
  </main>
  <script>{hotkeys_script()}</script>
  <script>{position_sizing_calculator_script()}</script>
  <script>{notification_module_script()}</script>
  <script>{accuracy_dashboard_script()}</script>
</body>
</html>"""


def render_shell(content: str, active_file: Optional[str], extra_css: str = "", show_logout: bool = False) -> str:
    file_text = f"<span>{_escape(active_file)}</span>" if active_file else "<span>無資料檔</span>"
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    clock = taiwan_market_session()
    refresh_interval_seconds = clock.refresh_interval_seconds
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>股票當沖追蹤器</title>
  <style>{extra_css}{base_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/tw/advisor">個股建議</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
      {notification_controls_html()}
      {position_sizing_controls_html()}
      {file_text}
      <span id="refresh-status" class="refresh-status" data-interval="{refresh_interval_seconds}" data-session="{_escape(clock.session)}">準備讀取分層更新狀態</span>
      <details class="manual-refresh-menu">
        <summary>手動更新</summary>
        <div class="manual-refresh-panel">
          <p class="muted">盤中優先用「重點觀察」或「持倉/觸發」；完整刷新會重跑全市場。</p>
          <form method="post" action="/refresh_watchlist" title="只更新 A/B+/B、等待條件、手動加入與今日重點觀察股。"><button type="submit">更新重點觀察</button></form>
          <form method="post" action="/refresh_positions" title="只更新 B+ 觸發、虛擬交易持倉與停損停利狀態。"><button type="submit">更新持倉/觸發</button></form>
          <form method="post" action="/refresh_full_market" title="更新 TWSE + TPEX 全市場異動候選池。"><button type="submit">更新全市場</button></form>
          <form method="post" action="/refresh_post_close_validation" title="只更新盤後驗證與策略成績單基礎資料。"><button type="submit">更新盤後驗證</button></form>
          <form method="post" action="/refresh" title="完整刷新會重跑全市場掃描與 tracker。"><button type="submit">完整刷新</button></form>
        </div>
      </details>
      {logout_link}
    </div>
  </nav>
  <details class="refresh-layer-panel" aria-label="分層更新狀態">
    <summary>系統狀態與資料來源</summary>
    <div id="system-version-status" class="refresh-layer-status">正在讀取部署驗收狀態...</div>
    <div id="refresh-layer-status" class="refresh-layer-status">正在讀取分層更新狀態...</div>
    <p class="muted">完整刷新會重跑全市場；盤中一般只需更新重點觀察或持倉/觸發。</p>
  </details>
  <div id="refresh-flash" class="refresh-flash" role="status" aria-live="polite" hidden></div>
  {content}
  <script>{hotkeys_script()}</script>
  <script>{position_sizing_calculator_script()}</script>
  <script>{notification_module_script()}</script>
  <script>
    (() => {{
      const status = document.getElementById("refresh-status");
      const panel = document.getElementById("refresh-layer-status");
      const systemPanel = document.getElementById("system-version-status");
      if (!status) return;
      const escapeHtml = (value) => String(value ?? "-")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      const labelMap = {{
        full_market: "全市場掃描",
        watchlist: "重點觀察",
        positions: "交易觸發",
        post_close_validation: "盤後驗證",
        manual_full_refresh: "手動完整刷新",
      }};
      const showRefreshFlash = () => {{
        const flash = document.getElementById("refresh-flash");
        if (!flash) return;
        const params = new URLSearchParams(window.location.search);
        const refreshStatus = params.get("refresh_status");
        const layer = params.get("refresh_layer");
        if (!refreshStatus || !layer) return;
        const layerText = labelMap[layer] || layer;
        const messages = {{
          started: `已開始更新：${{layerText}}。更新完成後系統狀態會自動刷新。`,
          already_running: `${{layerText}}正在更新中，已略過重複請求。`,
        }};
        const message = messages[refreshStatus];
        if (!message) return;
        flash.textContent = message;
        flash.hidden = false;
        flash.classList.toggle("refresh-flash-warn", refreshStatus === "already_running");
        params.delete("refresh_status");
        params.delete("refresh_layer");
        const nextQuery = params.toString();
        const nextUrl = `${{window.location.pathname}}${{nextQuery ? `?${{nextQuery}}` : ""}}${{window.location.hash}}`;
        window.history.replaceState(null, "", nextUrl);
      }};
      const timeText = (value) => {{
        if (!value) return "尚未更新";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString();
      }};
      const layerHtml = (layer) => {{
        const state = layer || {{}};
        const stale = state.is_stale ? "已過期" : "正常";
        const statusValue = String(state.status || "");
        const cls = state.is_stale || statusValue === "failed" ? "health-bad"
          : statusValue === "running" || statusValue === "skipped" ? "health-warn"
          : "health-ok";
        const nextDue = state.next_due_at ? timeText(state.next_due_at) : "尚未排定";
        const secondsUntilStale = state.seconds_until_stale == null ? "-" : `${{Math.round(Number(state.seconds_until_stale) || 0)}} 秒`;
        const errorText = state.error ? `｜原因 ${{escapeHtml(state.error)}}` : "";
        return `<span class="refresh-layer-item"><strong>${{escapeHtml(labelMap[state.layer] || state.layer)}}：</strong>
          <span class="${{cls}}">${{escapeHtml(stale)}}</span>｜
          最後成功 ${{escapeHtml(timeText(state.last_success_at))}}｜
          下次到期 ${{escapeHtml(nextDue)}}｜
          距離過期 ${{escapeHtml(secondsUntilStale)}}｜
          狀態 ${{escapeHtml(state.status)}}｜
          檔數 ${{escapeHtml(state.symbols_count || 0)}}${{errorText}}</span>`;
      }};
      const sourceHealthHtml = (payload) => {{
        const health = payload.data_source_health || {{}};
        const entries = Object.entries(health);
        if (!entries.length) return '<span class="refresh-layer-item"><strong>資料源：</strong>尚無即時健康狀態</span>';
        const body = entries.map(([source, item]) => {{
          const state = item || {{}};
          const statusText = state.status || "-";
          const cls = statusText === "OK" ? "health-ok" : "health-warn";
          const detail = state.message || state.last_error || "";
          return `<span class="refresh-layer-item"><strong>${{escapeHtml(source)}}：</strong><span class="${{cls}}">${{escapeHtml(statusText)}}</span>${{detail ? `｜${{escapeHtml(detail)}}` : ""}}</span>`;
        }}).join("");
        const degraded = payload.data_source_degraded ? '<div class="warn-mini">部分數據維護中，系統已降級處理；正常資料仍會繼續更新。</div>' : "";
        return body + degraded;
      }};
      const providerStatusHtml = (payload) => {{
        const provider = payload.provider_status || {{}};
        const providers = Array.isArray(provider.providers) ? provider.providers : [];
        if (!provider.active_provider && !providers.length) {{
          return '<span class="refresh-layer-item"><strong>行情 provider：</strong>尚無狀態</span>';
        }}
        const providerRows = providers.map((item) => {{
          const enabled = item.enabled && item.configured;
          const cls = enabled ? "health-ok" : "health-warn";
          return `<span class="refresh-layer-item"><strong>${{escapeHtml(item.name)}}：</strong><span class="${{cls}}">${{escapeHtml(item.role || "available")}}</span>｜${{escapeHtml(item.mode || "-")}}｜WebSocket ${{escapeHtml(item.websocket_status || "-")}}</span>`;
        }}).join("");
        return `<span class="refresh-layer-item"><strong>行情 provider：</strong>active=${{escapeHtml(provider.active_provider || "-")}}｜primary=${{escapeHtml(provider.primary_provider || "-")}}｜fallback=${{escapeHtml(provider.fallback_provider || "-")}}</span>${{providerRows}}`;
      }};
      const deploymentStatusHtml = (payload) => {{
        const deploy = payload.deployment_status || {{}};
        return `<span class="refresh-layer-item"><strong>部署版本：</strong>commit=${{escapeHtml(deploy.commit || "-")}}｜來源=${{escapeHtml(deploy.source || "-")}}｜signal_guard=${{escapeHtml(payload.signal_guard_version || "-")}}</span>`;
      }};
      const priceStatusHtml = (payload) => {{
        const price = payload.price_status_summary || {{}};
        return `<span class="refresh-layer-item"><strong>價格資料品質：</strong>${{escapeHtml(price.status || "-")}}｜live=${{escapeHtml(price.live_count || 0)}}｜delayed=${{escapeHtml(price.delayed_count || 0)}}｜cached=${{escapeHtml(price.cached_count || 0)}}｜missing=${{escapeHtml(price.missing_count || 0)}}｜missing ratio=${{escapeHtml(price.missing_ratio || 0)}}%</span>`;
      }};
      const refreshGuidanceHtml = (payload) => {{
        const guidance = payload.refresh_guidance || {{}};
        const severity = guidance.severity || "ok";
        const cls = severity === "ok" ? "health-ok" : severity === "block" ? "health-bad" : "health-warn";
        const action = escapeHtml(guidance.action_label || "不需手動更新");
        const endpointHint = guidance.action_endpoint ? `｜請用右上「手動更新」執行${{escapeHtml(guidance.action_label || "更新")}}` : "";
        const actionButton = guidance.action_endpoint
          ? `<form class="refresh-guidance-action" method="post" action="${{escapeHtml(guidance.action_endpoint)}}"><button type="submit">立即執行：${{action}}</button></form>`
          : "";
        return `<span class="refresh-layer-item refresh-guidance-item"><strong>建議動作：</strong><span class="${{cls}}">${{action}}</span>｜${{escapeHtml(guidance.summary || "必要資料層正常。")}}${{endpointHint}}${{actionButton}}</span>`;
      }};
      const operationalHealthHtml = (payload) => {{
        const health = payload.operational_health || {{}};
        const briefing = health.operator_briefing || {{}};
        const preflight = health.opening_preflight || {{}};
        const decision = health.operator_decision || {{}};
        const statusValue = String(health.status || "warning");
        const cls = statusValue === "ok" ? "health-ok" : statusValue === "blocked" ? "health-bad" : "health-warn";
        const label = statusValue === "ok" ? "可用" : statusValue === "blocked" ? "阻擋" : "提醒";
        const preflightCls = preflight.light === "green" ? "health-ok" : preflight.light === "red" ? "health-bad" : "health-warn";
        const preflightHtml = preflight.label
          ? `<div class="warn-mini"><strong>開盤檢查：</strong><span class="${{preflightCls}}">${{escapeHtml(preflight.label)}}</span>｜${{escapeHtml(preflight.reason || "-")}}｜下一步：${{escapeHtml(preflight.next_action || "-")}}</div>`
          : "";
        const decisionCls = decision.can_trade_now ? "health-ok" : statusValue === "blocked" ? "health-bad" : "health-warn";
        const decisionHtml = decision.headline
          ? `<div class="warn-mini"><strong>現在決策：</strong><span class="${{decisionCls}}">${{escapeHtml(decision.decision || "-")}}</span>｜${{escapeHtml(decision.headline)}}｜原因：${{escapeHtml(decision.reason || "-")}}｜第一步：${{escapeHtml(decision.first_action || "-")}}</div>`
          : "";
        const watchReadiness = health.watch_readiness
          ? `｜看盤狀態：${{escapeHtml(health.watch_readiness)}}${{health.watch_readiness_message ? "，" + escapeHtml(health.watch_readiness_message) : ""}}`
          : "";
        const refreshPlan = Array.isArray(health.refresh_plan) && health.refresh_plan.length
          ? `｜刷新順序：${{escapeHtml(health.refresh_plan.join(" → "))}}`
          : "";
        const next = health.next_action || {{}};
        const blockers = Array.isArray(health.blockers) && health.blockers.length
          ? `<div class="warn-mini">阻擋：${{escapeHtml(health.blockers.join(" "))}}</div>` : "";
        const warnings = Array.isArray(health.warnings) && health.warnings.length
          ? `<div class="warn-mini">提醒：${{escapeHtml(health.warnings.join(" "))}}</div>` : "";
        const steps = Array.isArray(health.operator_steps) && health.operator_steps.length
          ? `<ol class="operator-steps">${{health.operator_steps.map((step) => `<li>${{escapeHtml(step)}}</li>`).join("")}}</ol>`
          : "";
        const mode = health.operator_mode
          ? `<div class="warn-mini">作戰模式：${{escapeHtml(health.operator_mode)}}｜重點：${{escapeHtml(health.primary_focus || "-")}}</div>` : "";
        const doNow = Array.isArray(health.do_now) && health.do_now.length
          ? `<div class="warn-mini">現在要做：${{health.do_now.map((item) => escapeHtml(item)).join(" / ")}}</div>` : "";
        const doNot = Array.isArray(health.do_not_do) && health.do_not_do.length
          ? `<div class="warn-mini">不要做：${{health.do_not_do.map((item) => escapeHtml(item)).join(" / ")}}</div>` : "";
        const briefingHtml = briefing.headline
          ? `<div class="warn-mini"><strong>作戰簡報：</strong>${{escapeHtml(briefing.headline)}}｜姿態：${{escapeHtml(briefing.posture || "-")}}｜下一個檢查：${{escapeHtml(briefing.next_check || "-")}}｜風控閘門：${{escapeHtml(briefing.risk_gate || "-")}}</div>`
          : "";
        return `<span class="refresh-layer-item refresh-guidance-item"><strong>營運健康：</strong><span class="${{cls}}">${{label}}</span>｜${{escapeHtml(health.summary || "尚無營運健康摘要。")}}${{watchReadiness}}${{refreshPlan}}｜下一步：${{escapeHtml(next.label || "-")}}</span>${{preflightHtml}}${{decisionHtml}}${{briefingHtml}}${{mode}}${{doNow}}${{doNot}}${{steps}}${{blockers}}${{warnings}}`;
      }};
      const operationSummaryHtml = (payload) => {{
        const summary = payload.refresh_operation_summary || {{}};
        const severity = summary.severity || "ok";
        const cls = severity === "ok" ? "health-ok" : severity === "block" ? "health-bad" : "health-warn";
        return `<span class="refresh-layer-item refresh-guidance-item"><strong>刷新摘要：</strong><span class="${{cls}}">${{escapeHtml(summary.message || "必要資料層正常。")}}</span></span>`;
      }};
      const loadRefreshStatus = async () => {{
        try {{
          const response = await fetch("/api/refresh/status", {{ credentials: "same-origin" }});
          if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
          const payload = await response.json();
          const layers = payload.layers || {{}};
          const health = payload.operational_health || {{}};
          status.textContent = `營運健康：${{health.status || "-"}}｜看盤：${{health.watch_readiness || "-"}}｜模式：${{payload.market_mode_label || payload.market_mode || "-"}}｜必要資料層：${{payload.any_stale ? "需更新" : "正常"}}｜強烈買多：${{payload.allow_strong_long ? "允許" : "禁止"}}`;
          if (panel) {{
            panel.innerHTML = [
              `<span class="refresh-layer-item"><strong>市場模式：</strong>${{escapeHtml(payload.market_mode_label || payload.market_mode || "-")}}｜market_mode=${{escapeHtml(payload.market_mode || "-")}}｜是否交易日=${{payload.is_trading_day ? "是" : "否"}}｜是否休市日=${{payload.is_holiday ? "是" : "否"}}｜last_trading_date=${{escapeHtml(payload.last_trading_date || "-")}}｜資料日 ${{escapeHtml(payload.data_date || "-")}}｜${{escapeHtml(payload.review_mode_message || "")}}</span>`,
              operationalHealthHtml(payload),
              operationSummaryHtml(payload),
              refreshGuidanceHtml(payload),
              `<span class="refresh-layer-item"><strong>必要刷新層：</strong>${{escapeHtml((payload.required_refresh_layers || []).join("、") || "-")}}｜必要層過期=${{payload.any_stale ? "是" : "否"}}｜全部過期層=${{escapeHtml((payload.stale_layers || []).join("、") || "無")}}</span>`,
              layerHtml(layers.full_market),
              layerHtml(layers.watchlist),
              layerHtml(layers.positions),
              layerHtml(layers.post_close_validation),
              layerHtml(layers.manual_full_refresh),
              priceStatusHtml(payload),
              deploymentStatusHtml(payload),
              providerStatusHtml(payload),
              sourceHealthHtml(payload),
            ].join("");
            if (!payload.allow_strong_long) {{
              panel.innerHTML += `<div class="warn-mini">${{escapeHtml(payload.strong_long_block_reason || "資料層狀態不完整，禁止顯示強烈買多。")}}</div>`;
            }}
          }}
        }} catch (error) {{
          status.textContent = "分層狀態讀取失敗";
          if (panel) panel.textContent = `狀態 API 讀取失敗：${{error.message}}`;
        }}
      }};
      const loadSystemVersionStatus = async () => {{
        if (!systemPanel) return;
        try {{
          const response = await fetch("/api/system/version", {{ credentials: "same-origin" }});
          if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
          const payload = await response.json();
          const consistency = payload.consistency || {{}};
          const runtime = payload.runtime || {{}};
          const tracker = payload.tracker_html || {{}};
          const db = payload.db || {{}};
          const ok = Boolean(consistency.runtime_matches_tracker);
          const cls = ok ? "health-ok" : "health-warn";
          const warnings = Array.isArray(consistency.warnings) && consistency.warnings.length
            ? `<div class="warn-mini">${{escapeHtml(consistency.warnings.join(" "))}}</div>` : "";
          systemPanel.innerHTML = `
            <span class="refresh-layer-item"><strong>版本驗收：</strong><span class="${{cls}}">${{ok ? "runtime 與 tracker 一致" : "runtime 與 tracker 不一致"}}</span>｜runtime ${{escapeHtml(runtime.commit || "-")}}｜tracker ${{escapeHtml(tracker.commit || "-")}}｜HTML ${{escapeHtml(tracker.file || "-")}}</span>
            <span class="refresh-layer-item"><strong>資料新鮮度：</strong>資料日 ${{escapeHtml(db.data_date || "-")}}｜最新資料 ${{escapeHtml(timeText(db.latest_data_at))}}｜全市場 ${{escapeHtml(db.full_market?.symbols || 0)}} 檔｜盤中 ${{escapeHtml(db.intraday?.symbols || 0)}} 檔</span>
            ${{warnings}}`;
        }} catch (error) {{
          systemPanel.textContent = `版本驗收 API 讀取失敗：${{error.message}}`;
        }}
      }};
      loadRefreshStatus();
      loadSystemVersionStatus();
      showRefreshFlash();
      window.setInterval(loadRefreshStatus, 60000);
      window.setInterval(loadSystemVersionStatus, 60000);
    }})();
  </script>
</body>
</html>"""


def base_css() -> str:
    return """
    :root { --ink:#18202a; --muted:#667085; --line:#d7dde5; --bg:#f5f7fa; --panel:#fff; --accent:#175cd3; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    .topbar { position:sticky; top:0; z-index:10; display:flex; align-items:center; justify-content:space-between; gap:16px; padding:10px 18px; background:#fff; border-bottom:1px solid var(--line); }
    .topbar-actions { display:flex; align-items:center; gap:10px; color:var(--muted); }
    .notification-controls { display:flex; align-items:center; gap:8px; padding:4px 8px; border:1px solid var(--line); border-radius:8px; background:#f8fafc; }
    .notification-toggle { display:flex; align-items:center; gap:4px; margin:0; font-size:12px; font-weight:650; color:#344054; white-space:nowrap; }
    .notification-toggle input { width:auto; margin:0; accent-color:var(--accent); }
    .notification-status { max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; color:var(--muted); }
    .position-sizing-settings { position:relative; }
    .position-sizing-settings summary { list-style:none; border:1px solid var(--line); background:#fff; border-radius:6px; padding:6px 10px; cursor:pointer; white-space:nowrap; }
    .position-sizing-settings summary::-webkit-details-marker { display:none; }
    .position-sizing-panel { position:absolute; right:0; top:calc(100% + 6px); z-index:40; width:280px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fff; box-shadow:0 16px 36px rgba(16,24,40,.14); }
    .position-sizing-panel label { margin:0 0 8px; font-size:12px; color:#344054; font-weight:650; }
    .position-sizing-panel input { width:100%; margin-top:4px; padding:7px 8px; border:1px solid var(--line); border-radius:6px; font:inherit; }
    .position-sizing-status { display:block; color:var(--muted); font-size:12px; }
    .position-size-tag { display:inline-flex; align-items:center; margin-left:8px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); background:#f8fafc; color:#344054; font-size:12px; font-weight:750; vertical-align:middle; white-space:nowrap; }
    .position-size-ok { border-color:#bbf7d0; background:#f0fdf4; color:#067647; }
    .position-size-danger { border-color:#fecdd3; background:#fff1f2; color:#b42318; }
    .position-size-muted { color:var(--muted); font-weight:650; }
    .manual-refresh-menu { position:relative; }
    .manual-refresh-menu summary { list-style:none; border:1px solid var(--line); background:#fff; border-radius:6px; padding:6px 10px; cursor:pointer; white-space:nowrap; color:var(--ink); }
    .manual-refresh-menu summary::-webkit-details-marker { display:none; }
    .manual-refresh-panel { position:absolute; right:0; top:calc(100% + 6px); z-index:45; width:260px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fff; box-shadow:0 16px 36px rgba(16,24,40,.14); display:grid; gap:8px; }
    .manual-refresh-panel p { margin:0; font-size:12px; }
    .manual-refresh-panel button { width:100%; text-align:left; }
    .nav-links { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .nav-links a { color:var(--muted); text-decoration:none; padding:5px 8px; border-radius:6px; }
    .nav-links a:hover { color:var(--accent); background:#eef4ff; }
    .topbar form { margin:0; }
    .refresh-status { white-space:nowrap; font-size:13px; color:var(--muted); }
    .refresh-layer-panel { padding:10px 18px; background:#f8fafc; border-bottom:1px solid var(--line); }
    .refresh-layer-panel summary { cursor:pointer; font-weight:800; color:#344054; }
    .refresh-layer-panel[open] summary { margin-bottom:8px; }
    .refresh-layer-panel p { margin:6px 0 0; font-size:12px; }
    .refresh-flash { margin:10px 18px 0; padding:10px 12px; border:1px solid #bbf7d0; background:#f0fdf4; color:#067647; border-radius:8px; font-weight:750; }
    .refresh-flash-warn { border-color:#fed7aa; background:#fff7ed; color:#9a3412; }
    .refresh-layer-status { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; color:#344054; font-size:13px; }
    .refresh-layer-item { display:inline-flex; gap:4px; align-items:center; white-space:nowrap; }
    .refresh-guidance-item { white-space:normal; }
    .refresh-guidance-action { display:inline-flex; margin-left:6px; }
    .refresh-guidance-action button { border:1px solid var(--blue); background:var(--blue); color:#fff; border-radius:6px; padding:4px 8px; font:inherit; cursor:pointer; }
    .health-ok { color:#067647; font-weight:750; }
    .health-warn { color:#8a5a00; font-weight:750; }
    .health-bad { color:#b42318; font-weight:750; }
    .warn-mini { flex-basis:100%; color:#7c2d12; font-weight:700; }
    .operator-steps { flex-basis:100%; margin:2px 0 0 18px; padding-left:16px; color:#344054; font-size:12px; line-height:1.55; }
    button, .topbar a { border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:6px 10px; font:inherit; text-decoration:none; cursor:pointer; }
    button:hover, .topbar a:hover { border-color:var(--accent); color:var(--accent); }
    .summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:12px 0; }
    .metric { background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px 14px; }
    .metric strong { display:block; font-size:20px; margin-top:2px; }
    .muted { color:var(--muted); }
    .warn { margin:12px 0; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; color:#7c2d12; }
    .login-body { min-height:100vh; display:grid; place-items:center; padding:20px; }
    .login-panel { width:min(360px,100%); background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:22px; }
    .login-panel h1 { margin:0 0 18px; font-size:24px; }
    label { display:block; margin:12px 0; font-weight:650; }
    input { width:100%; margin-top:6px; padding:9px 10px; border:1px solid var(--line); border-radius:6px; font:inherit; }
    .login-panel button { width:100%; margin-top:8px; background:var(--accent); color:white; border-color:var(--accent); }
    .error { margin-bottom:10px; padding:8px 10px; border:1px solid #fecdd3; background:#fff1f2; color:#9f1239; border-radius:6px; }
    .empty { margin:28px; padding:16px; background:#fff; border:1px solid var(--line); border-radius:8px; }
    .decision-center { margin:16px 0; background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; }
    .decision-center h2 { margin:0 0 10px; font-size:18px; }
    .decision-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-top:12px; }
    .decision-panel { border:1px solid var(--line); border-radius:8px; padding:12px; background:#fbfcfe; }
    .decision-panel strong { display:block; margin-bottom:4px; }
    .signal-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; margin-top:12px; }
    .signal-column { border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px; min-height:120px; }
    .signal-column h3 { margin:0 0 10px; font-size:15px; }
    .stock-hotkey-tabs { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:10px 0; }
    .stock-hotkey-tabs button { padding:6px 10px; border-radius:999px; font-size:12px; background:#fff; color:#344054; }
    .stock-hotkey-tabs button.is-active { background:#175cd3; border-color:#175cd3; color:#fff; }
    .hotkey-empty-message { margin:8px 0; }
    .hotkey-filter-hidden { display:none !important; }
    .signal-card { border-top:1px solid var(--line); padding:10px 0; }
    .signal-card:first-of-type { border-top:0; padding-top:0; }
    .signal-title { font-weight:750; }
    .signal-meta { color:var(--muted); font-size:12px; white-space:normal; }
    .signal-next { margin-top:6px; color:var(--accent); font-weight:700; }
    @media (max-width:760px) { .topbar { align-items:flex-start; flex-direction:column; } .topbar-actions { flex-wrap:wrap; } .manual-refresh-panel { left:0; right:auto; } .refresh-layer-item { white-space:normal; } }
    """


def us_dashboard_css() -> str:
    return """
    .us-page { padding:0 28px 32px; }
    .us-header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:24px 0 12px; }
    .us-header h1 { margin:0 0 4px; font-size:26px; }
    .session-pill { border:1px solid var(--line); background:#fff; border-radius:999px; padding:7px 12px; font-weight:700; white-space:nowrap; }
    .notice { margin:12px 0; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; color:#7c2d12; }
    .manual-panel { margin:18px 0 10px; padding:14px; background:#fff; border:1px solid var(--line); border-radius:8px; }
    .section-title-row { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .section-title-row h2 { margin:0; }
    .manual-form { display:grid; grid-template-columns:repeat(4,minmax(160px,1fr)); gap:10px 12px; margin-top:12px; }
    .manual-form label { margin:0; font-size:12px; color:#344054; }
    .manual-form input, .manual-form select { width:100%; margin-top:5px; padding:8px 9px; border:1px solid var(--line); border-radius:6px; font:inherit; background:#fff; }
    .manual-reason { grid-column:span 2; }
    .manual-actions { display:flex; align-items:end; gap:8px; }
    .manual-actions button:first-child { background:#175cd3; border-color:#175cd3; color:#fff; }
    .manual-status { margin-top:10px; min-height:20px; color:var(--muted); }
    .manual-status.ok { color:#067647; font-weight:700; }
    .manual-status.fail { color:#b42318; font-weight:700; }
    .source-pill { display:inline-block; padding:2px 8px; border:1px solid var(--line); border-radius:999px; background:#f8fafc; font-size:12px; font-weight:700; }
    .close-controls { display:flex; align-items:center; gap:6px; min-width:260px; }
    .close-controls input { width:92px; margin:0; padding:6px 8px; }
    .debug-block { margin-top:10px; padding:10px 12px; background:#f8fafc; border:1px solid var(--line); border-radius:8px; }
    .table-wrap { overflow-x:auto; }
    table { width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
    th, td { padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; white-space:nowrap; }
    th { background:#eef2f6; color:#344054; font-size:12px; }
    tr:last-child td { border-bottom:0; }
    h2 { margin:26px 0 10px; font-size:18px; }
    .symbol-main { font-weight:750; }
    .symbol-sub { color:var(--muted); font-size:12px; white-space:normal; min-width:220px; }
    .num-up { color:#067647; font-weight:700; }
    .num-down { color:#b42318; font-weight:700; }
    .badge { display:inline-block; min-width:42px; padding:2px 8px; border-radius:999px; text-align:center; font-size:12px; font-weight:700; border:1px solid var(--line); background:#f8fafc; }
    .grade-A { color:#fff; background:#067647; border-color:#067647; }
    .grade-B { color:#175cd3; background:#eff6ff; border-color:#bfdbfe; }
    .grade-C { color:#9a3412; background:#fff7ed; border-color:#fed7aa; }
    .grade-D { color:#475467; background:#f2f4f7; }
    .notes { white-space:normal; min-width:220px; color:var(--muted); }
    @media (max-width:760px) { .us-page { padding-left:14px; padding-right:14px; } .us-header { flex-direction:column; } }
    """


def paper_dashboard_css() -> str:
    return """
    .paper-page { padding:0 28px 32px; }
    .paper-header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:24px 0 12px; }
    .paper-header h1 { margin:0 0 4px; font-size:26px; }
    .session-pill { border:1px solid var(--line); background:#fff; border-radius:999px; padding:7px 12px; font-weight:700; white-space:nowrap; }
    .notice { margin:12px 0; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; color:#7c2d12; }
    .debug-block { margin-top:10px; padding:10px 12px; background:#f8fafc; border:1px solid var(--line); border-radius:8px; }
    .table-wrap { overflow-x:auto; }
    table { width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
    th, td { padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; white-space:nowrap; }
    th { background:#eef2f6; color:#344054; font-size:12px; }
    tr:last-child td { border-bottom:0; }
    h2 { margin:26px 0 10px; font-size:18px; }
    .num-up { color:#067647; font-weight:700; }
    .num-down { color:#b42318; font-weight:700; }
    .notes { white-space:normal; min-width:180px; color:var(--muted); }
    .ghost-button { background:#fff; color:#344054; border:1px solid var(--line); }
    .review-modal[hidden] { display:none; }
    .review-modal { position:fixed; inset:0; z-index:60; display:flex; align-items:center; justify-content:center; padding:18px; background:rgba(15,23,42,.42); }
    .review-dialog { width:min(560px,100%); max-height:92vh; overflow:auto; background:#fff; border:1px solid var(--line); border-radius:8px; box-shadow:0 20px 40px rgba(15,23,42,.22); padding:16px; }
    .review-dialog-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
    .review-dialog h2 { margin:0 0 4px; }
    .review-summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:8px; margin:12px 0; }
    .review-tags { border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
    .review-tags legend { padding:0 6px; font-weight:800; color:#344054; }
    .review-tags label { display:flex; gap:8px; align-items:flex-start; margin:8px 0; color:#344054; }
    .review-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:12px; }
    .review-chart-card { background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; }
    .review-chart-layout { display:grid; grid-template-columns:minmax(180px,240px) 1fr; gap:18px; align-items:center; }
    .review-pie { width:100%; max-width:240px; aspect-ratio:1; transform:rotate(-90deg); }
    .review-pie circle { fill:none; stroke-width:10; }
    .review-pie-bg { stroke:#eef2f6; }
    .review-legend { display:grid; gap:8px; }
    .review-legend-row { display:grid; grid-template-columns:14px 1fr auto; gap:8px; align-items:center; }
    .review-dot { width:12px; height:12px; border-radius:999px; }
    @media (max-width:980px) { .manual-form { grid-template-columns:repeat(2,minmax(150px,1fr)); } .manual-reason { grid-column:span 2; } }
    @media (max-width:760px) { .paper-page { padding-left:14px; padding-right:14px; } .paper-header { flex-direction:column; } .manual-form { grid-template-columns:1fr; } .manual-reason { grid-column:span 1; } .close-controls { min-width:220px; } .review-chart-layout { grid-template-columns:1fr; } }
    """


def us_dashboard_script() -> str:
    return r"""
    (() => {
      const state = { interval: 60, remaining: 60, timer: null, lastPayload: null };
      const $ = (id) => document.getElementById(id);
      const text = (value) => value === null || value === undefined || value === "" ? "-" : String(value);
      const number = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "-";
      const pct = (value) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return "-";
        const cls = n > 0 ? "num-up" : n < 0 ? "num-down" : "";
        return `<span class="${cls}">${n > 0 ? "+" : ""}${n.toFixed(2)}%</span>`;
      };
      const volume = (value) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return "-";
        if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
        if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
        return n.toLocaleString();
      };
      const escapeHtml = (value) => text(value)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      const metric = (label, value) => `<div class="metric"><span class="muted">${label}</span><strong>${value}</strong></div>`;
      const row = (label, value) => `<tr><td>${label}</td><td>${value}</td></tr>`;
      const setStatus = (message) => { $("us-refresh-status").textContent = message; };
      const positionSizeTag = (entry, stop) => `<span class="position-size-tag" data-position-entry="${escapeHtml(entry)}" data-position-stop="${escapeHtml(stop)}"></span>`;

      async function loadDashboard() {
        setStatus("更新中...");
        $("us-error").hidden = true;
        try {
          const response = await fetch("/api/us/dashboard", { cache: "no-store" });
          const payload = await response.json();
          if (!response.ok || payload.error) throw new Error(payload.error || `HTTP ${response.status}`);
          state.lastPayload = payload;
          render(payload);
          state.interval = Number(payload.market?.refresh_interval_seconds || 60);
          state.remaining = state.interval;
          setStatus(`上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`);
        } catch (error) {
          $("us-error").hidden = false;
          $("us-error").textContent = `資料暫時無法更新：${error.message}`;
          state.remaining = Math.max(state.interval, 60);
          setStatus("更新失敗，稍後自動重試");
        }
      }

      function render(payload) {
        const market = payload.market || {};
        const summary = payload.summary || {};
        const source = payload.data_source || {};
        const debug = payload.debug || {};
        $("us-session").textContent = `${market.status_text || "-"}｜${market.session || "-"}｜${market.market_status_text || "-"}`;
        $("us-summary").innerHTML = [
          metric("候選股", summary.candidate_count || 0),
          metric("A級", summary.grade_a || 0),
          metric("B+練習觀察", summary.grade_b_plus || 0),
          metric("B級", summary.grade_b || 0),
          metric("進場雷達通過", summary.executable || 0),
          metric("practice_long 練習買多", summary.practice_long || 0),
          metric("當下買多", summary.trade_long || 0),
          metric("當下賣空", summary.trade_short || 0),
          metric("當下觀察", summary.trade_watch || 0),
          metric("wait_volume 等量能", summary.wait_volume || 0),
          metric("wait_vwap 等VWAP", summary.wait_vwap || 0),
          metric("wait_breakout 等突破", summary.wait_breakout || 0),
          metric("wait_pullback 等回測", summary.wait_pullback || 0),
          metric("高信心", summary.confidence_high || 0),
          metric("中等信心", summary.confidence_medium || 0),
          metric("低信心", summary.confidence_low || 0),
          metric("不可信", summary.confidence_unreliable || 0),
          metric("指標衝突", summary.conflicts_total || 0),
          metric("常見衝突", escapeHtml(summary.top_conflict || "無明顯衝突")),
          metric("recommendations", summary.recommendations || 0),
          metric("B+ ready", summary.b_plus_ready || 0),
          metric("B+ near", summary.b_plus_near || 0),
          metric("triggered 已觸發", summary.triggered || 0),
        ].join("");
        $("us-source").innerHTML = [
          row("資料來源", escapeHtml(source.source)),
          row("Yahoo Finance 狀態", source.ok ? "抓取成功" : "部分失敗 / 資料暫時無法更新"),
          row("成功筆數", text(source.success_count)),
          row("失敗 symbol", escapeHtml((source.failed_symbols || []).join(", ") || "無")),
          row("最後成功更新時間", escapeHtml(source.last_success_at || "-")),
          row("下次更新", `${text(source.next_update_seconds)} 秒`),
        ].join("");
        $("us-market").innerHTML = [
          row("市場狀態", escapeHtml(market.status_text)),
          row("Session", escapeHtml(market.session)),
          row("QQQ 漲跌幅", pct(payload.indices?.qqq_change_pct || 0)),
          row("SPY 漲跌幅", pct(payload.indices?.spy_change_pct || 0)),
          row("指數環境", escapeHtml(market.market_status_text)),
        ].join("");
        $("us-debug").innerHTML = [
          row("commit hash", escapeHtml(debug.app_version)),
          row("model version", escapeHtml(debug.model_version)),
          row("dashboard generated_at", escapeHtml(debug.dashboard_generated_at)),
          row("market session", escapeHtml(debug.market_session)),
          row("refresh interval", `${text(debug.refresh_interval)} 秒`),
          row("candidates count", text(debug.candidates_count)),
          row("recommendations count", text(debug.recommendations_count)),
          row("data source status", escapeHtml(debug.data_source_status)),
        ].join("");
        renderDecisionCenter(payload.decision_center || {});
        renderSignalCenter((payload.decision_center || {}).signal_center || {});
        renderCandidates(payload.candidates || []);
        renderBPlusTriggers(payload.b_plus_triggers || []);
        window.StockNotificationModule?.observeSignals(payload.b_plus_triggers || []);
      }

      function renderDecisionCenter(data) {
        if (!data || !data.operation_tendency) {
          $("us-decision-center").innerHTML = '<h2>AI 今日決策中心</h2><p class="muted">目前資料不足，系統僅能提供有限判斷。</p>';
          return;
        }
        const counts = data.counts || {};
        const confidence = data.confidence_summary || {};
        const panels = [
          ["今日操作傾向", data.operation_tendency, data.summary_text],
          ["進場雷達通過摘要", `${counts.executable || 0} 檔通過`, data.executable_summary],
          ["主要等待條件", (data.main_waiting_conditions || []).join("、") || "無明顯等待條件", data.main_waiting_summary],
          ["主要風險", (data.major_risks || []).join("、") || "無明顯集中風險", data.major_risk_summary],
          ["今日建議動作", "策略追蹤與虛擬交易", data.action_suggestion],
        ];
        const radar = [
          metric("A級數量", counts.grade_a || 0),
          metric("B+數量", counts.grade_b_plus || 0),
          metric("B級數量", counts.grade_b || 0),
          metric("triggered", counts.triggered || 0),
          metric("paper open positions", data.paper_stats?.paper_open_positions || 0),
          metric("manual trades", data.paper_stats?.manual_trades || 0),
          metric("system trades", data.paper_stats?.system_trades || 0),
          metric("信心摘要", `高 ${confidence.high || 0} / 中 ${confidence.medium || 0} / 低 ${confidence.low || 0}`),
        ].join("");
        const panelHtml = panels.map(([title, headline, body]) => `<div class="decision-panel">
          <strong>${escapeHtml(title)}</strong>
          <div>${escapeHtml(headline)}</div>
          <p class="muted">${escapeHtml(body || "")}</p>
        </div>`).join("");
        const noTrade = data.no_trade_reason
          ? `<div class="warn"><strong>今日不交易理由</strong><br>${escapeHtml(data.no_trade_reason)}</div>`
          : "";
        $("us-decision-center").innerHTML = `<h2>AI 今日決策中心</h2><section class="summary">${radar}</section><div class="decision-grid">${panelHtml}</div>${noTrade}<section class="notice">${escapeHtml(data.disclaimer || "")}</section>`;
      }

      function renderSignalCenter(center) {
        const columns = [
          ["executable", "進場雷達通過"],
          ["practice_long", "練習買多 practice_long"],
          ["b_plus", "B+ 練習觀察"],
          ["waiting", "等待確認"],
          ["risk", "風險過高 / 避開"],
        ];
        $("us-signal-center").innerHTML = `<h2>訊號中心</h2><div class="signal-grid">${columns.map(([key, title]) => {
          const items = Array.isArray(center[key]) ? center[key] : [];
          const cards = items.length ? items.map(signalCard).join("") : signalCenterEmpty(key);
          return `<div class="signal-column"><h3>${escapeHtml(title)}（${items.length}）</h3>${signalCenterNote(key)}${cards}</div>`;
        }).join("")}</div>`;
      }

      function signalCenterEmpty(key) {
        if (key === "executable") return '<p class="muted">今日沒有進場雷達通過標的。</p>';
        if (key === "practice_long") return '<p class="muted">目前沒有練習買多標的。</p>';
        return '<p class="muted">目前沒有標的。</p>';
      }

      function signalCenterNote(key) {
        if (key === "practice_long") return '<p class="muted">僅供虛擬交易與樣本累積，不是正式可執行訊號。</p>';
        return "";
      }

      function signalCard(item) {
        const label = `${escapeHtml(item.symbol)}｜${escapeHtml(item.name_zh)}${item.name_en ? `｜${escapeHtml(item.name_en)}` : ""}`;
        const meta = `${escapeHtml(item.grade)}｜${escapeHtml(item.entry_status)}｜${escapeHtml(item.lifecycle_status)}｜Readiness ${escapeHtml(item.trigger_readiness)}`;
        const metrics = `現價 ${number(item.current_price)}｜VWAP ${number(item.vwap)}｜量比 ${number(item.volume_ratio)}x｜停損 ${number(item.stop_loss)}｜停利 ${number(item.target_price)}`;
        return `<div class="signal-card">
          <div class="signal-title">${label}${positionSizeTag(item.trigger_price || item.current_price || item.latest_price, item.stop_loss)}</div>
          <div class="signal-meta">當下狀態：${escapeHtml(displayTradeBias(item))}</div>
          <div class="signal-meta">${meta}</div>
          <div class="signal-meta">${escapeHtml(metrics)}</div>
          <div class="signal-meta">信心：${escapeHtml(item.confidence_level || "-")}</div>
          <div class="signal-meta">${escapeHtml(item.reason || "")}</div>
          <div class="signal-next">下一步：${escapeHtml(item.next_step || "-")}</div>
        </div>`;
      }

      function renderBPlusTriggers(items) {
        if (!items.length) {
          $("us-b-plus-triggers").innerHTML = '<tr><td colspan="12">目前沒有 B+ 練習觀察訊號。</td></tr>';
          return;
        }
        $("us-b-plus-triggers").innerHTML = items.map((item) => `<tr>
          <td>${escapeHtml(item.symbol)}｜${escapeHtml(item.name_zh)}${item.name_en ? `｜${escapeHtml(item.name_en)}` : ""}</td>
          <td>${escapeHtml(item.market)}</td>
          <td>${number(item.current_price)}</td>
          <td>${number(item.vwap)}</td>
          <td>${number(item.volume_ratio)}x</td>
          <td>${escapeHtml(item.entry_status)}</td>
          <td>${escapeHtml(item.lifecycle_status)}</td>
          <td>${escapeHtml(item.trigger_condition)}</td>
          <td>${escapeHtml(item.distance_to_trigger)}</td>
          <td>${escapeHtml(item.trigger_readiness_label || item.trigger_readiness)}</td>
          <td class="notes">${escapeHtml(item.trigger_next_action)}</td>
          <td>${number(item.confidence_score)}<br><span class="muted">${escapeHtml(item.confidence_summary || "")}</span></td>
        </tr>`).join("");
      }

      function renderCandidates(items) {
        if (!items.length) {
          $("us-candidates").innerHTML = '<tr><td colspan="18">目前沒有美股候選資料。</td></tr>';
          return;
        }
        $("us-candidates").innerHTML = items.map((item) => {
          const lifecycle = item.lifecycle_status || "observed";
          const breakout = [
            item.break_premarket_high ? "破盤前高" : "",
            item.break_previous_high ? "破昨高" : "",
            item.break_opening_range_high ? "破開盤區間" : "",
          ].filter(Boolean).join(" / ") || "尚未突破";
          return `<tr>
            <td><div class="symbol-main">${escapeHtml(item.symbol)}｜${escapeHtml(item.short_name_zh)}｜${escapeHtml(item.name_en)}${positionSizeTag(item.trigger_price || item.latest_price, item.stop_loss)}</div><div class="symbol-sub">${escapeHtml(item.sector_zh)}｜${escapeHtml(item.description_zh)}</div></td>
            <td>${number(item.latest_price)}</td>
            <td>${pct(item.change_pct)}</td>
            <td>${volume(item.volume)}</td>
            <td>${number(item.volume_ratio)}x</td>
            <td>${item.above_vwap ? "是" : "否"}<br><span class="muted">${number(item.vwap)}</span></td>
            <td>${number(item.premarket_high)}</td>
            <td>${escapeHtml(breakout)}</td>
            <td>${number(item.bullish_score)}</td>
            <td>${number(item.risk_score)}</td>
            <td><span class="badge grade-${escapeHtml(item.grade)}">${escapeHtml(item.grade)}</span></td>
            <td>${escapeHtml(item.entry_status)}</td>
            <td>${escapeHtml(displayTradeBias(item))}<br><span class="muted">${escapeHtml(displayTradeReason(item))}</span></td>
            <td>${number(item.confidence_score)}<br><span class="muted">${escapeHtml(item.confidence_level_label || item.confidence_level)}</span></td>
            <td>${escapeHtml(item.conflicts_count || 0)}<br><span class="muted">${escapeHtml(item.conflict_summary || "無明顯衝突")}</span></td>
            <td>${escapeHtml(lifecycle)}</td>
            <td class="notes">${escapeHtml(item.confidence_summary || (item.reasons || []).join("；"))}</td>
            <td class="notes">${escapeHtml((item.risk_reasons || []).join("；"))}</td>
          </tr>`;
        }).join("");
      }

      function tick() {
        state.remaining -= 1;
        if (state.remaining <= 0) {
          loadDashboard();
          return;
        }
        setStatus(`上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`);
      }

      loadDashboard();
      state.timer = window.setInterval(tick, 1000);
    })();
    """


def tw_advisor_script() -> str:
    return r"""
    (() => {
      const $ = (id) => document.getElementById(id);
      const form = $("tw-advisor-form");
      const input = $("tw-advisor-symbol");
      const status = $("tw-advisor-status");
      const result = $("tw-advisor-result");
      const liveScanButton = $("tw-advisor-live-scan");

      const escapeHtml = (value) => String(value ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
      const text = (value, fallback = "-") => value === null || value === undefined || value === "" ? fallback : String(value);
      const number = (value, digits = 2) => {
        const n = Number(value);
        return Number.isFinite(n) ? n.toFixed(digits) : "-";
      };
      const pct = (value) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return "-";
        const cls = n > 0 ? "num-up" : n < 0 ? "num-down" : "num-flat";
        return `<span class="${cls}">${n >= 0 ? "+" : ""}${n.toFixed(2)}%</span>`;
      };
      const yesNo = (value) => value ? "是" : "否";
      const decisionClass = (bias) => bias === "long" ? "decision-long" : bias === "short" ? "decision-short" : "decision-watch";
      const decisionLabel = (candidate) => displayTradeBias(candidate || {});
      const list = (items) => {
        const rows = Array.isArray(items) && items.length ? items : ["目前沒有明確訊息。"];
        return `<ul>${rows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
      };
      const metric = (label, value) => `<div class="advisor-metric"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`;
      const planRow = (label, value) => `<div class="plan-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "-")}</strong></div>`;
      const positionSizeTag = (entry, stop) => `<span class="position-size-tag" data-position-entry="${escapeHtml(entry)}" data-position-stop="${escapeHtml(stop)}"></span>`;
      const money = (value) => {
        const n = Number(value);
        return Number.isFinite(n) ? n.toFixed(2) : "-";
      };
      const compactMoney = (value) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return "-";
        if (Math.abs(n) >= 100000000) return `${(n / 100000000).toFixed(2)} 億`;
        if (Math.abs(n) >= 10000) return `${Math.round(n / 10000)} 萬`;
        return Math.round(n).toLocaleString();
      };
      const statusZh = (entry) => ({
        executable: "進場雷達通過",
        practice_long: "練習買多",
        wait_volume: "等待量能",
        wait_vwap: "等待 VWAP",
        wait_breakout: "等待突破",
        wait_pullback: "等待拉回",
        high_risk: "避開",
        avoid: "避開",
        data_missing: "資料不足",
        ok: "成功",
        partial: "部分具備",
        missing: "缺少",
        snapshot: "快照",
        top_of_book: "最佳一檔",
        not_streaming: "尚未串流",
        available: "可用",
        limit_up_bid_only: "漲停買盤",
        limit_down_ask_only: "跌停賣盤",
        bid_only: "僅委買",
        ask_only: "僅委賣",
        confirmed: "確認",
        divergence: "背離",
        strong: "偏強",
        neutral: "中性",
        weak: "偏弱",
        unknown: "未知",
        rising: "轉強",
        stable: "穩定",
        weak: "偏弱",
        above: "站上",
        below: "跌破",
        near: "接近",
        controlled: "可控",
        high: "偏高",
        live: "即時",
        delayed: "延遲",
        cached: "使用上一筆",
        not_live: "非即時",
        supportive: "買盤支持",
        sell_pressure: "賣壓偏重",
        limit_up_locked: "漲停鎖住",
        limit_down_locked: "跌停鎖住",
        improving: "轉強",
        deteriorating: "轉弱",
        buy_sweep: "大單敲進",
        sell_sweep: "大單敲出",
        large_buy: "大單買進",
        large_sell: "大單賣出",
        inflow: "大單流入",
        outflow: "大單流出",
        high_precision: "高品質確認",
        standard: "標準確認",
        limited: "確認資料不足",
        blocked: "暫不進場",
      }[entry] || entry || "-");
      const displayTradeBias = (item) => {
        const entry = item?.entry_status || "";
        const label = item?.trade_bias_label || "";
        if (entry === "high_risk") return "方向偏多";
        if (entry === "practice_long") return "練習買多";
        if (entry === "executable") return "進場雷達通過";
        if (label === "強烈" + "看漲") return "方向偏多";
        if (label === "看漲") return "偏多";
        if ((item?.trade_bias || "") === "long" && (label === "買多" || (label.startsWith("做多") && label.endsWith("確認")))) return "進場雷達通過";
        return label || item?.trade_bias || "觀察";
      };
      const displayTradeReason = (item) => {
        const entry = item?.entry_status || "";
        if (entry === "high_risk") return "方向偏多，但追價風險高，不列入今日做多。";
        if (entry === "practice_long") return "可作為練習買多觀察，不是正式可執行。";
        return item?.trade_bias_reason || "";
      };
      const conclusionClass = (state) => state === "進場雷達通過" || state === "強烈買多" || state === "買多" ? "conclusion-ok" : state === "資料不足" ? "conclusion-missing" : state === "避開" || state === "看空" ? "conclusion-risk" : "conclusion-watch";
      const advisorLink = (symbol) => `/tw/advisor?symbol=${encodeURIComponent(symbol || "")}`;
      const renderReasonCodes = (codes) => {
        const rows = Array.isArray(codes) && codes.length ? codes : ["no_reason_code"];
        return `<div class="reason-code-list">${rows.map((code) => `<code>${escapeHtml(code)}</code>`).join("")}</div>`;
      };
      const renderHistory = (history) => {
        const windows = history?.windows || {};
        const rows = ["20", "40", "60"].map((key) => {
          const item = windows[key] || {};
          const value = (v, suffix = "") => v === null || v === undefined ? "樣本不足" : `${number(v)}${suffix}`;
          return `<tr>
            <td>近 ${key} 日</td>
            <td>${item.sample_size || 0}</td>
            <td>${item.grade_a_count || 0}</td>
            <td>${value(item.grade_a_win_rate, "%")}</td>
            <td>${item.grade_b_plus_count || 0}</td>
            <td>${item.grade_b_plus_triggered_count || 0}</td>
            <td>${value(item.grade_b_plus_triggered_win_rate, "%")}</td>
            <td>${value(item.high_risk_continue_up_rate, "%")}</td>
            <td>${value(item.avoid_big_up_rate, "%")}</td>
            <td>${value(item.avg_max_gain_pct, "%")}</td>
            <td>${value(item.avg_max_drawdown_pct, "%")}</td>
            <td>${value(item.take_profit_rate, "%")}</td>
            <td>${value(item.stop_loss_rate, "%")}</td>
          </tr>`;
        }).join("");
        return `
          <p class="${history?.is_statistically_meaningful ? "muted" : "warn-inline"}">${escapeHtml(history?.message || "這檔歷史樣本不足，不建議依個股勝率判斷。")}</p>
          <div class="table-wrap advisor-history-table"><table>
            <thead><tr><th>期間</th><th>樣本</th><th>A次數</th><th>A後勝率</th><th>B+次數</th><th>B+觸發</th><th>B+觸發後勝率</th><th>high_risk續漲</th><th>avoid後大漲</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>停利率</th><th>停損率</th></tr></thead>
            <tbody>${rows}</tbody>
          </table></div>
        `;
      };
      const renderIntradayChart = (chart) => {
        const bars = Array.isArray(chart?.bars) ? chart.bars : [];
        if (bars.length < 2) return `<div class="advisor-chart-empty">目前缺少足夠分時資料，暫時無法繪圖。</div>`;
        const width = 920;
        const height = 320;
        const pad = { left: 48, right: 92, top: 22, bottom: 34 };
        const values = [];
        bars.forEach((bar) => {
          ["high", "low", "close", "vwap"].forEach((key) => {
            const n = Number(bar[key]);
            if (Number.isFinite(n)) values.push(n);
          });
        });
        (chart.levels || []).forEach((level) => {
          const n = Number(level.value);
          if (Number.isFinite(n)) values.push(n);
        });
        const minRaw = Math.min(...values);
        const maxRaw = Math.max(...values);
        const span = Math.max(maxRaw - minRaw, 0.01);
        const min = minRaw - span * 0.08;
        const max = maxRaw + span * 0.08;
        const x = (index) => pad.left + (index / Math.max(bars.length - 1, 1)) * (width - pad.left - pad.right);
        const y = (value) => pad.top + ((max - value) / (max - min)) * (height - pad.top - pad.bottom);
        const path = (key) => bars
          .map((bar, index) => {
            const n = Number(bar[key]);
            if (!Number.isFinite(n)) return "";
            return `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(n).toFixed(1)}`;
          })
          .filter(Boolean)
          .join(" ");
        const levelClass = (label) => label.includes("停損") ? "stop" : label.includes("停利") ? "target" : label.includes("VWAP") ? "vwap" : "level";
        const levelRows = (chart.levels || [])
          .filter((level) => Number.isFinite(Number(level.value)))
          .slice(0, 10)
          .map((level) => {
            const yy = y(Number(level.value));
            const cls = levelClass(level.label || "");
            return `
              <g class="chart-level ${cls}">
                <line x1="${pad.left}" y1="${yy.toFixed(1)}" x2="${width - pad.right}" y2="${yy.toFixed(1)}"></line>
                <text x="${width - pad.right + 8}" y="${(yy + 4).toFixed(1)}">${escapeHtml(level.label)} ${money(level.value)}</text>
              </g>
            `;
          })
          .join("");
        const first = bars[0];
        const last = bars[bars.length - 1];
        return `
          <svg class="advisor-chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="分時走勢圖">
            <rect x="0" y="0" width="${width}" height="${height}" rx="8"></rect>
            <line class="axis" x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}"></line>
            <line class="axis" x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}"></line>
            <text class="axis-label" x="8" y="${pad.top + 4}">${money(max)}</text>
            <text class="axis-label" x="8" y="${height - pad.bottom}">${money(min)}</text>
            <text class="axis-label" x="${pad.left}" y="${height - 10}">${escapeHtml(first.time || "")}</text>
            <text class="axis-label" x="${width - pad.right - 44}" y="${height - 10}">${escapeHtml(last.time || "")}</text>
            ${levelRows}
            <path class="vwap-line" d="${path("vwap")}"></path>
            <path class="price-line" d="${path("close")}"></path>
          </svg>
          <div class="chart-legend">
            <span><i class="legend-price"></i>價格</span>
            <span><i class="legend-vwap"></i>VWAP</span>
            <span><i class="legend-stop"></i>停損</span>
            <span><i class="legend-target"></i>停利</span>
          </div>
        `;
      };
      const renderTrendDiagnosis = (candidate) => {
        const diagnosis = candidate?.trend_diagnosis || {};
        const timeframe = candidate?.timeframe_diagnostics || {};
        const intraday = timeframe.intraday_window || {};
        const reasons = Array.isArray(diagnosis.reasons) ? diagnosis.reasons : [];
        const risks = Array.isArray(diagnosis.risk_reasons) ? diagnosis.risk_reasons : [];
        const blockers = Array.isArray(diagnosis.blockers) ? diagnosis.blockers : [];
        const status = diagnosis.status || candidate?.trend_status || "";
        const title = diagnosis.label || candidate?.trend_label || (status ? status : "趨勢延續尚未確認");
        const notice = status === "trend_continuation_watch"
          ? "此訊號為趨勢延續觀察，不代表正式可執行；仍需即時資料、VWAP、量能、停損距離與主模型條件同步通過。"
          : status === "high_risk_chase"
          ? "目前較偏追價風險，避免直接追高。"
          : "目前盤中曲線尚未形成完整趨勢延續判斷。";
        return `
          <article class="advisor-card">
            <h3>趨勢延續診斷</h3>
            <p><strong>${escapeHtml(title)}</strong></p>
            <p class="muted">${escapeHtml(diagnosis.summary || notice)}</p>
            <div class="advisor-grid">
              ${metric("盤中 K 棒數", escapeHtml(intraday.bars_count ?? "-"))}
              ${metric("VWAP 上方時間", intraday.vwap_above_minutes === undefined ? "-" : `${escapeHtml(intraday.vwap_above_minutes)} 分`)}
              ${metric("高點墊高", escapeHtml(yesNo(intraday.higher_high)))}
              ${metric("低點墊高", escapeHtml(yesNo(intraday.higher_low)))}
              ${metric("回檔深度", intraday.pullback_depth_pct === null || intraday.pullback_depth_pct === undefined ? "-" : `${escapeHtml(number(intraday.pullback_depth_pct))}%`)}
              ${metric("量能延續", escapeHtml(yesNo(intraday.volume_continuation)))}
              ${metric("量能退潮", escapeHtml(yesNo(intraday.volume_decay)))}
              ${metric("上影線偏長", escapeHtml(yesNo(intraday.long_upper_shadow)))}
              ${metric("停損距離", diagnosis.stop_distance_pct === null || diagnosis.stop_distance_pct === undefined ? "-" : `${escapeHtml(number(diagnosis.stop_distance_pct))}%`)}
              ${metric("reason code", escapeHtml(diagnosis.reason_code || candidate?.trend_reason_code || "-"))}
            </div>
            <div class="advisor-sections">
              <section class="advisor-panel">
                <h3>延續理由</h3>
                ${list(reasons.length ? reasons : [notice])}
              </section>
              <section class="advisor-panel">
                <h3>風險 / 阻擋</h3>
                ${list([...(risks || []), ...(blockers || [])])}
              </section>
              <section class="advisor-panel">
                <h3>下一步 / 失效條件</h3>
                <p>${escapeHtml(diagnosis.next_step || "等待 VWAP、量能、突破與曲線結構同步確認。")}</p>
                <p class="muted">${escapeHtml(diagnosis.invalidation || "跌破 VWAP、量能退潮或資料轉為延遲 / 快取時失效。")}</p>
              </section>
            </div>
          </article>
        `;
      };
      const renderEntryRadarSummaryCard = (summary) => {
        summary = summary || {};
        const reasons = Array.isArray(summary.reason_rank) ? summary.reason_rank : [];
        const reasonRows = reasons.slice(0, 5).map((item) => `
          <li>
            <strong>${escapeHtml(item.confirmation_only ? "確認資料" : "核心條件")}｜${escapeHtml(item.code || "-")}</strong>
            <span class="muted">${escapeHtml(item.message || "")}</span>
          </li>
        `).join("");
        return `
          <article class="advisor-card entry-radar-summary-card">
            <h3>進場雷達</h3>
            <p><strong>${escapeHtml(summary.entry_state || "等待確認")}</strong></p>
            <div class="advisor-sections">
              <section class="advisor-panel">
                <h3>目前進場狀態</h3>
                <p>${escapeHtml(summary.entry_state || "等待確認")}</p>
              </section>
              <section class="advisor-panel">
                <h3>最大卡關原因</h3>
                <p>${escapeHtml(summary.blocker_summary || "等待 VWAP、量能、突破或風控確認。")}</p>
              </section>
              <section class="advisor-panel">
                <h3>下一個觸發條件</h3>
                <p>${escapeHtml(summary.next_trigger || "等待條件確認。")}</p>
              </section>
            </div>
            ${summary.confirmation_note ? `<p class="warn-inline">${escapeHtml(summary.confirmation_note)}</p>` : ""}
            <ul class="decision-list">${reasonRows || "<li>目前沒有明顯卡關原因，仍需依停損與部位控管執行。</li>"}</ul>
            <p class="warn-inline">此雷達只整理原因與下一步，不會調整 A / B+ / B 條件，也不會因缺逐筆或缺五檔資料直接降級。</p>
          </article>
        `;
      };
      const renderAdvisorQuickReadCard = ({ decisionCard, entryRadarSummary, dataHealth, safety, frontTrade }) => {
        decisionCard = decisionCard || {};
        entryRadarSummary = entryRadarSummary || {};
        dataHealth = dataHealth || {};
        safety = safety || {};
        frontTrade = frontTrade || {};
        const state = decisionCard.final_decision || frontTrade.category || entryRadarSummary.entry_state || "觀察";
        const blocker = decisionCard.top_reason || entryRadarSummary.blocker_summary || frontTrade.reason || "等待 VWAP、量比、突破與風控確認。";
        const nextStep = decisionCard.next_trigger || entryRadarSummary.next_trigger || frontTrade.next_step || "等待條件確認。";
        const invalidation = decisionCard.invalid_condition || "跌破 VWAP、量能退潮、觸發失敗或資料轉為延遲時失效。";
        const dataUsable = Boolean(dataHealth.can_use_for_daytrade && !dataHealth.uses_cache && !dataHealth.is_delayed && !dataHealth.is_data_missing);
        const dataStatus = dataHealth.is_live
          ? "資料即時，可進入盤中判斷"
          : dataHealth.uses_cache
          ? "使用上一筆，僅供觀察"
          : dataHealth.is_delayed
          ? "資料延遲，僅供觀察"
          : dataHealth.is_data_missing
          ? "資料不足，不能判斷"
          : "非即時資料，先觀察";
        const action = advisorNowAction({ state, dataHealth, nextStep });
        const blocked = Array.isArray(safety.blocked_reasons) ? safety.blocked_reasons.map((item) => item.message).filter(Boolean) : [];
        return `
          <article class="advisor-card quick-read-card ${dataUsable ? "" : "data-limited-card"}">
            <h3>作戰速讀</h3>
            <div class="advisor-grid">
              ${metric("現在狀態", escapeHtml(state))}
              ${metric("資料狀態", escapeHtml(dataStatus))}
              ${metric("建議動作", escapeHtml(action))}
              ${metric("最大卡關", escapeHtml(blocker))}
              ${metric("下一步", escapeHtml(nextStep))}
              ${metric("失效條件", escapeHtml(invalidation))}
            </div>
            ${blocked.length ? `<p class="warn-inline">安全限制：${escapeHtml(blocked.slice(0, 3).join("；"))}</p>` : ""}
          </article>
        `;
      };
      const advisorNowAction = ({ state, dataHealth, nextStep }) => {
        dataHealth = dataHealth || {};
        const dataUsable = Boolean(dataHealth.can_use_for_daytrade && !dataHealth.uses_cache && !dataHealth.is_delayed && !dataHealth.is_data_missing);
        if (!dataUsable) return "先等資料恢復即時，再重新判斷。";
        if (state === "強烈買多") return "進入重點盯盤；仍要檢查停損距離與部位大小。";
        if (state === "買多") return "方向偏多，但需等待觸發或進場雷達確認。";
        if (state === "看空") return "多方結構失效，暫不做多。";
        if (nextStep && nextStep !== "等待條件確認。") return nextStep;
        return "只觀察，不提前進場。";
      };
      const renderBreakoutTrapCard = (diagnosis) => {
        diagnosis = diagnosis || {};
        const evidence = Array.isArray(diagnosis.evidence) ? diagnosis.evidence : [];
        const warnings = Array.isArray(diagnosis.warnings) ? diagnosis.warnings : [];
        return `
          <article class="advisor-card breakout-trap-card">
            <h3>真假突破 / 假跌破診斷</h3>
            <p><strong>${escapeHtml(diagnosis.status_label || "等待判斷")}</strong></p>
            <p>${escapeHtml(diagnosis.summary || "等待 VWAP、突破、盤口與逐筆資料確認。")}</p>
            <div class="advisor-grid">
              ${metric("目前診斷", escapeHtml(diagnosis.status_label || "-"))}
              ${metric("風險等級", escapeHtml(statusZh(diagnosis.risk_level || "-")))}
              ${metric("狀態碼", escapeHtml(diagnosis.status || "-"))}
              ${metric("不改模型", escapeHtml(yesNo(diagnosis.does_not_change_model !== false)))}
            </div>
            <div class="advisor-sections">
              <section class="advisor-panel">
                <h3>支持證據</h3>
                ${list(evidence.length ? evidence : ["目前尚無足夠證據判定為真突破。"])}
              </section>
              <section class="advisor-panel">
                <h3>風險提醒</h3>
                ${list(warnings.length ? warnings : ["目前沒有額外假突破 / 誘多提醒。"])}
              </section>
              <section class="advisor-panel">
                <h3>下一步</h3>
                <p>${escapeHtml(diagnosis.next_step || "等待突破後守穩、站回 VWAP、量能與五檔同步確認。")}</p>
              </section>
            </div>
            <p class="warn-inline">失效條件：${escapeHtml(diagnosis.invalidation || "跌破 VWAP、量能退潮、五檔賣壓轉強或資料轉為延遲時失效。")}</p>
          </article>
        `;
      };
      const renderInstitutionalCard = (candidate) => {
        const chip = candidate?.institutional_context || {};
        return `
          <article class="advisor-card">
            <h3>籌碼背景</h3>
            <p><strong>${escapeHtml(chip.institutional_label || "籌碼資料不足")}</strong></p>
            <p class="muted">${escapeHtml(chip.institutional_reason || "目前沒有可用籌碼資料；籌碼只作背景，不作為強烈買多依據。")}</p>
            <div class="advisor-grid">
              ${metric("外資近一日", escapeHtml(number(chip.foreign_buy_sell)))}
              ${metric("投信近一日", escapeHtml(number(chip.investment_trust_buy_sell)))}
              ${metric("自營商近一日", escapeHtml(number(chip.dealer_buy_sell)))}
              ${metric("三大法人合計", escapeHtml(number(chip.institutional_total_buy_sell)))}
              ${metric("外資近3日", chip.foreign_3d_sum === null || chip.foreign_3d_sum === undefined ? "資料不足" : escapeHtml(number(chip.foreign_3d_sum)))}
              ${metric("外資近5日", chip.foreign_5d_sum === null || chip.foreign_5d_sum === undefined ? "資料不足" : escapeHtml(number(chip.foreign_5d_sum)))}
              ${metric("投信近3日", chip.investment_trust_3d_sum === null || chip.investment_trust_3d_sum === undefined ? "資料不足" : escapeHtml(number(chip.investment_trust_3d_sum)))}
              ${metric("投信近5日", chip.investment_trust_5d_sum === null || chip.investment_trust_5d_sum === undefined ? "資料不足" : escapeHtml(number(chip.investment_trust_5d_sum)))}
              ${metric("資料日期", escapeHtml(chip.institutional_data_date || "-"))}
              ${metric("資料狀態", escapeHtml(chip.institutional_data_status || "missing"))}
              ${metric("單位", escapeHtml(chip.unit || "依來源"))}
            </div>
            <p class="warn-inline">法人買超不能取代 VWAP、量比、突破與風控；即使籌碼偏多，也不會直接產生強烈買多。</p>
          </article>
        `;
      };
      const renderSectorContextCard = (candidate) => {
        const sector = candidate?.sector_context || {};
        const topSymbols = Array.isArray(sector.sector_top_symbols) && sector.sector_top_symbols.length
          ? sector.sector_top_symbols.join("、")
          : "暫無同族群領先標的";
        return `
          <article class="advisor-card">
            <h3>族群狀態</h3>
            <p><strong>${escapeHtml(sector.sector_status_label || "暫無族群資料")}</strong></p>
            <p class="muted">${escapeHtml(sector.sector_reason || "目前沒有足夠族群資料；族群強弱只作背景，不作為強烈買多依據。")}</p>
            <div class="advisor-grid">
              ${metric("所屬族群", escapeHtml(sector.industry_label || sector.industry || "-"))}
              ${metric("族群排名", escapeHtml(sector.sector_rank || "-"))}
              ${metric("族群分數", escapeHtml(number(sector.sector_strength_score)))}
              ${metric("上漲家數", escapeHtml(sector.sector_advancers_count ?? "-"))}
              ${metric("下跌家數", escapeHtml(sector.sector_decliners_count ?? "-"))}
              ${metric("族群均量比", escapeHtml(number(sector.sector_volume_ratio_avg)))}
              ${metric("是否領漲", escapeHtml(sector.is_sector_leader ? "是" : "否"))}
              ${metric("是否落後", escapeHtml(sector.is_sector_lagging ? "是" : "否"))}
            </div>
            <p class="muted">同族群重點：${escapeHtml(topSymbols)}</p>
            <p class="warn-inline">族群強只是背景；個股仍須通過即時資料、VWAP、量比、突破與停損風控。</p>
          </article>
        `;
      };
      const renderFugleTradesCard = (trades) => {
        trades = trades || {};
        const warnings = Array.isArray(trades.warnings) ? trades.warnings : [];
        const enabledText = trades.enabled ? "已啟用" : "尚未啟用";
        const configuredText = trades.configured ? "已設定" : "尚未設定";
        return `
          <article class="advisor-card">
            <h3>Fugle 逐筆成交 / 大單偵測</h3>
            <p><strong>${escapeHtml(trades.status_label || "尚未啟用")}</strong></p>
            <p class="muted">此區只讀富果行情 API，不串接下單。若有 API Key，系統會用 REST Trades 判斷大單敲進 / 敲出；WebSocket 可作為下一階段即時升級。</p>
            <div class="advisor-grid">
              ${metric("啟用狀態", escapeHtml(enabledText))}
              ${metric("API Key", escapeHtml(configuredText))}
              ${metric("資料來源", escapeHtml(trades.source || "Fugle"))}
              ${metric("狀態", escapeHtml(trades.status || "disabled"))}
              ${metric("逐筆筆數", escapeHtml(trades.trades_count ?? 0))}
              ${metric("最新成交價", escapeHtml(number(trades.latest_price)))}
              ${metric("最新成交量", escapeHtml(number(trades.latest_size, 0)))}
              ${metric("最新成交時間", escapeHtml(trades.latest_time || "-"))}
              ${metric("大單狀態", escapeHtml(statusZh(trades.large_trade_status)))}
              ${metric("大單門檻", escapeHtml(number(trades.large_trade_threshold, 0)))}
              ${metric("大單價格", escapeHtml(number(trades.large_trade_price)))}
              ${metric("大單量", escapeHtml(number(trades.large_trade_size, 0)))}
              ${metric("大單時間", escapeHtml(trades.large_trade_time || "-"))}
              ${metric("敲進次數", escapeHtml(trades.large_buy_count ?? 0))}
              ${metric("敲出次數", escapeHtml(trades.large_sell_count ?? 0))}
              ${metric("方向不明", escapeHtml(trades.large_unknown_count ?? 0))}
            </div>
            <p class="muted">${escapeHtml(trades.large_trade_summary || "目前缺逐筆成交資料，無法判斷大單敲進 / 敲出。")}</p>
            ${warnings.length ? `<p class="warn-inline">${escapeHtml(warnings.join("；"))}</p>` : ""}
            ${trades.error ? `<p class="warn-inline">${escapeHtml(trades.error)}</p>` : ""}
            <p class="warn-inline">Fugle 逐筆成交只作進場確認背景；不會直接產生強烈買多，也不會自動下單。</p>
          </article>
        `;
      };
      const renderFugleQuoteCard = (quote) => {
        quote = quote || {};
        const warnings = Array.isArray(quote.warnings) ? quote.warnings : [];
        const enabledText = quote.enabled ? "已啟用" : "尚未啟用";
        const configuredText = quote.configured ? "已設定" : "尚未設定";
        return `
          <article class="advisor-card">
            <h3>Fugle 即時行情 / 五檔力道</h3>
            <p><strong>${escapeHtml(quote.status_label || "尚未啟用")}</strong></p>
            <p class="muted">此區使用富果 Quote API，優先用於重點標的的最新價、五檔委買委賣、成交量流向與漲跌停狀態確認；不作全市場掃描，也不會下單。</p>
            <div class="advisor-grid">
              ${metric("啟用狀態", escapeHtml(enabledText))}
              ${metric("API Key", escapeHtml(configuredText))}
              ${metric("資料來源", escapeHtml(quote.source || "Fugle REST Quote"))}
              ${metric("狀態", escapeHtml(quote.status || "disabled"))}
              ${metric("最新價", escapeHtml(number(quote.price)))}
              ${metric("漲跌幅", pct(quote.change_pct))}
              ${metric("昨收 / 參考價", escapeHtml(number(quote.previous_close)))}
              ${metric("均價", escapeHtml(number(quote.avg_price)))}
              ${metric("最新成交量", escapeHtml(number(quote.last_size, 0)))}
              ${metric("報價時間", escapeHtml(quote.quote_time || quote.last_updated || "-"))}
              ${metric("五檔狀態", escapeHtml(quote.five_level_status_label || "五檔資料不足"))}
              ${metric("最佳委買", quote.bid_price === null || quote.bid_price === undefined ? "-" : `${escapeHtml(number(quote.bid_price))} / ${escapeHtml(number(quote.bid_volume, 0))}`)}
              ${metric("最佳委賣", quote.ask_price === null || quote.ask_price === undefined ? "-" : `${escapeHtml(number(quote.ask_price))} / ${escapeHtml(number(quote.ask_volume, 0))}`)}
              ${metric("買賣盤差", quote.orderbook_imbalance === null || quote.orderbook_imbalance === undefined ? "-" : `${escapeHtml(number(quote.orderbook_imbalance))}%`)}
              ${metric("委買總量", escapeHtml(number(quote.bid_total_volume, 0)))}
              ${metric("委賣總量", escapeHtml(number(quote.ask_total_volume, 0)))}
              ${metric("內外盤力道", quote.intraday_flow_ratio === null || quote.intraday_flow_ratio === undefined ? "-" : `${escapeHtml(number(quote.intraday_flow_ratio))}%`)}
              ${metric("總成交量", escapeHtml(number(quote.total_trade_volume, 0)))}
              ${metric("總成交金額", escapeHtml(compactMoney(quote.total_trade_value)))}
              ${metric("主動買量", escapeHtml(number(quote.total_trade_volume_at_ask, 0)))}
              ${metric("主動賣量", escapeHtml(number(quote.total_trade_volume_at_bid, 0)))}
              ${metric("最後成交方向", escapeHtml(statusZh(quote.last_trade_side || "unknown")))}
            </div>
            <p class="muted">${escapeHtml(quote.last_trade_summary || "最新成交方向不足。")}</p>
            ${(quote.is_limit_up_price || quote.is_limit_up_bid) ? `<p class="warn-inline">接近或處於漲停相關狀態，追價風險升高，不可直接視為強烈買多。</p>` : ""}
            ${(quote.is_limit_down_price || quote.is_limit_down_ask) ? `<p class="warn-inline">接近或處於跌停相關狀態，不適合作為做多依據。</p>` : ""}
            ${warnings.length ? `<p class="warn-inline">${escapeHtml(warnings.join("；"))}</p>` : ""}
            ${quote.error ? `<p class="warn-inline">${escapeHtml(quote.error)}</p>` : ""}
            <p class="warn-inline">Fugle Quote 只作進場確認背景；不會直接產生強烈買多，也不會自動下單。</p>
          </article>
        `;
      };
      const renderFugleCandlesCard = (candles) => {
        candles = candles || {};
        const warnings = Array.isArray(candles.warnings) ? candles.warnings : [];
        return `
          <article class="advisor-card">
            <h3>Fugle 1分K / 均價確認</h3>
            <p><strong>${escapeHtml(candles.status_label || "尚未啟用")}</strong></p>
            <p class="muted">此區使用富果 Candles API，作為重點標的分時走勢、價格連續墊高、VWAP / 均價與量能延續確認的輔助來源。</p>
            <div class="advisor-grid">
              ${metric("資料來源", escapeHtml(candles.source || "Fugle REST Candles"))}
              ${metric("狀態", escapeHtml(candles.status || "disabled"))}
              ${metric("週期", escapeHtml(`${candles.timeframe || "1"} 分K`))}
              ${metric("K線筆數", escapeHtml(candles.candles_count ?? 0))}
              ${metric("最新收盤", escapeHtml(number(candles.latest_close)))}
              ${metric("最新均價", escapeHtml(number(candles.latest_average)))}
              ${metric("最新時間", escapeHtml(candles.latest_time || "-"))}
            </div>
            ${warnings.length ? `<p class="warn-inline">${escapeHtml(warnings.join("；"))}</p>` : ""}
            ${candles.error ? `<p class="warn-inline">${escapeHtml(candles.error)}</p>` : ""}
            <p class="warn-inline">Fugle 1分K 只作進場確認背景；不會直接產生強烈買多，也不會自動下單。</p>
          </article>
        `;
      };
      const renderTwseOrderbookCard = (quote) => {
        quote = quote || {};
        const bids = Array.isArray(quote.bid_levels) ? quote.bid_levels : [];
        const asks = Array.isArray(quote.ask_levels) ? quote.ask_levels : [];
        const maxRows = Math.max(bids.length, asks.length, 5);
        const rows = Array.from({ length: maxRows }, (_, index) => {
          const bid = bids[index] || {};
          const ask = asks[index] || {};
          return `
            <tr>
              <td>${index + 1}</td>
              <td class="num-up">${bid.price === undefined || bid.price === null ? "-" : escapeHtml(number(bid.price))}</td>
              <td>${bid.volume === undefined || bid.volume === null ? "-" : escapeHtml(number(bid.volume, 0))}</td>
              <td class="num-down">${ask.price === undefined || ask.price === null ? "-" : escapeHtml(number(ask.price))}</td>
              <td>${ask.volume === undefined || ask.volume === null ? "-" : escapeHtml(number(ask.volume, 0))}</td>
            </tr>
          `;
        }).join("");
        const warning = quote.is_limit_up_locked
          ? "目前接近或處於漲停鎖住狀態，賣盤可能為空；這代表追價風險升高，不代表可以直接追高。"
          : quote.is_limit_down_locked
          ? "目前接近或處於跌停鎖住狀態，買盤可能為空；不適合作為做多依據。"
          : "公開五檔只作盤口確認背景，不會直接產生強烈買多。";
        return `
          <article class="advisor-card">
            <h3>TWSE MIS 公開五檔委買委賣</h3>
            <p><strong>${escapeHtml(quote.five_level_status_label || "五檔資料不足")}</strong></p>
            <p class="muted">此區使用 TWSE MIS 公開行情欄位，免券商帳號；資料可能延遲或受交易所端點限制，僅作盤口背景。</p>
            <div class="advisor-grid">
              ${metric("資料來源", escapeHtml(quote.source || "TWSE MIS"))}
              ${metric("五檔狀態", escapeHtml(statusZh(quote.five_level_status)))}
              ${metric("成交價", escapeHtml(number(quote.price)))}
              ${metric("最佳委買", quote.bid_price === null || quote.bid_price === undefined ? "-" : `${escapeHtml(number(quote.bid_price))} / ${escapeHtml(number(quote.bid_volume, 0))}`)}
              ${metric("最佳委賣", quote.ask_price === null || quote.ask_price === undefined ? "-" : `${escapeHtml(number(quote.ask_price))} / ${escapeHtml(number(quote.ask_volume, 0))}`)}
              ${metric("買賣盤差", quote.orderbook_imbalance === null || quote.orderbook_imbalance === undefined ? "-" : `${escapeHtml(number(quote.orderbook_imbalance))}%`)}
              ${metric("委買總量", escapeHtml(number(quote.bid_total_volume, 0)))}
              ${metric("委賣總量", escapeHtml(number(quote.ask_total_volume, 0)))}
              ${metric("漲停價", escapeHtml(number(quote.limit_up)))}
              ${metric("跌停價", escapeHtml(number(quote.limit_down)))}
              ${metric("報價時間", escapeHtml(quote.quote_time || "-"))}
            </div>
            <div class="orderbook-table">
              <table>
                <thead><tr><th>檔</th><th>委買價</th><th>委買量</th><th>委賣價</th><th>委賣量</th></tr></thead>
                <tbody>${rows}</tbody>
              </table>
            </div>
            <p class="warn-inline">${escapeHtml(warning)}</p>
          </article>
        `;
      };
      const renderEntryConfirmationCard = (radar) => {
        radar = radar || {};
        const checks = Array.isArray(radar.checks) ? radar.checks : [];
        const blockers = Array.isArray(radar.blockers) ? radar.blockers : [];
        const warnings = Array.isArray(radar.warnings) ? radar.warnings : [];
        const checkRows = checks.length ? checks.map((item) => `
          <li>
            <strong>${item.ok ? "通過" : "未通過"}｜${escapeHtml(item.label)}</strong>
            <span class="muted">${escapeHtml(item.detail || "")}</span>
          </li>
        `).join("") : "<li>目前缺少雷達資料。</li>";
        return `
          <article class="advisor-card">
            <h3>進場確認雷達</h3>
            <p><strong>${escapeHtml(radar.status_label || "等待確認")}</strong></p>
            <p class="muted">${escapeHtml(radar.summary || "目前仍需等待 VWAP、量能、盤口與風控確認。")}</p>
            <div class="advisor-grid">
              ${metric("雷達分數", escapeHtml(number(radar.score)))}
              ${metric("可考慮進場", escapeHtml(yesNo(radar.can_consider_entry)))}
              ${metric("確認品質", escapeHtml(radar.confirmation_quality_label || statusZh(radar.confirmation_quality)))}
              ${metric("品質原因", escapeHtml(radar.confirmation_quality_reason || "等待雷達資料更新。"))}
              ${metric("價格動能", escapeHtml(statusZh(radar.price_momentum_status)))}
              ${metric("VWAP", escapeHtml(statusZh(radar.vwap_status)))}
              ${metric("量能", escapeHtml(statusZh(radar.volume_status)))}
              ${metric("五檔盤口", escapeHtml(statusZh(radar.orderbook_status)))}
              ${metric("買賣盤差", radar.orderbook_imbalance === null || radar.orderbook_imbalance === undefined ? "-" : `${escapeHtml(number(radar.orderbook_imbalance))}%`)}
              ${metric("委買總量", escapeHtml(number(radar.bid_total_volume, 0)))}
              ${metric("委賣總量", escapeHtml(number(radar.ask_total_volume, 0)))}
              ${metric("委買量變化", escapeHtml(statusZh(radar.bid_volume_trend)))}
              ${metric("委賣量變化", escapeHtml(statusZh(radar.ask_volume_trend)))}
              ${metric("最新價墊高", escapeHtml(statusZh(radar.price_tick_trend)))}
              ${metric("盤口快照數", escapeHtml(radar.orderbook_history_count ?? "-"))}
              ${metric("風險", escapeHtml(statusZh(radar.risk_status)))}
              ${metric("資料", escapeHtml(statusZh(radar.data_status)))}
              ${metric("大單敲進 / 敲出", escapeHtml(statusZh(radar.large_trade_status)))}
            </div>
            <div class="advisor-sections">
              <section class="advisor-panel"><h3>檢查項目</h3><ul class="decision-list">${checkRows}</ul></section>
              <section class="advisor-panel"><h3>阻擋原因</h3>${list(blockers.length ? blockers : ["無硬性阻擋，但仍需依原模型與風控判斷。"])}</section>
              <section class="advisor-panel"><h3>盤口 / 大單提醒</h3>${list(warnings.length ? warnings : [
                radar.bid_volume_trend_summary || "委買量變化尚無足夠快照。",
                radar.ask_volume_trend_summary || "委賣量變化尚無足夠快照。",
                radar.price_tick_summary || "最新價快照尚不足。",
                radar.large_trade_summary || "逐筆大單資料尚未接入。",
              ])}</section>
            </div>
            <div class="advisor-sections">
              <section class="advisor-panel advisor-plan"><h3>下一步</h3><p>${escapeHtml(radar.next_step || "等待條件確認。")}</p></section>
              <section class="advisor-panel advisor-plan"><h3>失效條件</h3><p>${escapeHtml(radar.invalidation || "跌破 VWAP、量能退潮或資料延遲時失效。")}</p></section>
            </div>
            <p class="warn-inline">進場確認雷達只做進場前檢查，不會調整 A / B+ / B 分級，也不會單獨產生強烈買多。</p>
          </article>
        `;
      };
      const renderPrecisionContextCard = (precision) => {
        precision = precision || {};
        const available = Array.isArray(precision.available_data) ? precision.available_data : [];
        const missing = Array.isArray(precision.missing_data) ? precision.missing_data : [];
        const nextData = Array.isArray(precision.next_data_to_add) ? precision.next_data_to_add : [];
        const hasTick = precision.tick_data_status === "ok";
        const hasOrderbook = precision.orderbook_status === "ok" || precision.orderbook_status === "partial";
        const precisionWarning = hasTick && hasOrderbook
          ? "Fugle 逐筆成交與 TWSE MIS 公開五檔已作為 MVP 進場確認背景；仍不會直接產生強烈買多或自動下單。"
          : "缺少逐筆成交或五檔委買委賣時，系統不會把訊號視為高精準即時進場依據。";
        return `
          <article class="advisor-card">
            <h3>精準當沖資料檢查</h3>
            <p><strong>${escapeHtml(precision.precision_label || "資料不足")}</strong></p>
            <p class="muted">${escapeHtml(precision.summary || "目前資料不足，不能作為精準當沖依據。")}</p>
            <div class="advisor-grid">
              ${metric("精準度分數", escapeHtml(number(precision.readiness_score)))}
              ${metric("可作精準當沖", escapeHtml(yesNo(precision.can_use_for_precise_daytrade)))}
              ${metric("逐筆成交 Tick", escapeHtml(statusZh(precision.tick_data_status)))}
              ${metric("五檔委買委賣", escapeHtml(statusZh(precision.orderbook_status)))}
              ${metric("即時新聞題材", escapeHtml(statusZh(precision.news_status)))}
              ${metric("盤中 K 線", escapeHtml(statusZh(precision.intraday_k_status)))}
              ${metric("VWAP 品質", escapeHtml(statusZh(precision.vwap_quality)))}
              ${metric("量能分布", escapeHtml(statusZh(precision.volume_profile_status)))}
              ${metric("量能加速", precision.volume_acceleration_ratio === null || precision.volume_acceleration_ratio === undefined ? "-" : `${escapeHtml(number(precision.volume_acceleration_ratio))}x`)}
              ${metric("價漲量增", escapeHtml(yesNo(precision.price_up_volume_up)))}
              ${metric("價漲量縮", escapeHtml(yesNo(precision.price_up_volume_down)))}
              ${metric("VWAP 守穩", escapeHtml(yesNo(precision.vwap_hold_ok)))}
            </div>
            <div class="advisor-sections">
              <section class="advisor-panel"><h3>目前已具備</h3>${list(available.length ? available : ["尚無足夠資料"])}</section>
              <section class="advisor-panel"><h3>仍缺資料</h3>${list(missing.length ? missing : ["無明顯缺口"])}</section>
              <section class="advisor-panel"><h3>下一步可補</h3>${list(nextData.length ? nextData : ["暫無"])}</section>
            </div>
            <p class="warn-inline">${escapeHtml(precisionWarning)}</p>
          </article>
        `;
      };
      const renderEntryChecklistCard = (payload) => {
        payload = payload || {};
        const candidate = payload.candidate || {};
        const dataHealth = payload.data_health || {};
        const safety = payload.safety || {};
        const keyMetrics = payload.key_metrics || {};
        const precision = payload.precision_context || {};
        const entryPrice = Number(candidate.trigger_price || keyMetrics.intraday_high || keyMetrics.current_price || candidate.last_price || 0);
        const stopLoss = Number(keyMetrics.stop_loss || candidate.stop_loss || 0);
        const stopDistancePct = entryPrice > 0 && stopLoss > 0 ? ((entryPrice - stopLoss) / entryPrice) * 100 : null;
        const volumeRatio = Number(keyMetrics.volume_ratio ?? candidate.volume_ratio ?? 0);
        const riskScore = Number(keyMetrics.risk_score ?? candidate.risk_score ?? 999);
        const distanceToVwap = Number(keyMetrics.distance_to_vwap_pct ?? candidate.distance_to_vwap_pct ?? 999);
        const hasTick = precision.tick_data_status === "ok";
        const hasOrderbook = precision.orderbook_status === "ok";
        const checks = [
          {
            label: "資料即時",
            ok: Boolean(dataHealth.is_live && dataHealth.can_use_for_daytrade && !dataHealth.uses_cache),
            detail: dataHealth.is_live ? "可作盤中判斷" : (dataHealth.advice || "資料非即時，僅供觀察"),
            hard: true,
          },
          {
            label: "站上 VWAP",
            ok: Boolean(candidate.above_vwap || keyMetrics.above_vwap),
            detail: keyMetrics.vwap ? `VWAP ${number(keyMetrics.vwap)}` : "缺 VWAP 不可執行",
            hard: true,
          },
          {
            label: "量能確認",
            ok: volumeRatio >= 1,
            detail: volumeRatio ? `量比 ${number(volumeRatio)}x` : "缺量比",
            hard: true,
          },
          {
            label: "突破或觸發",
            ok: Boolean(keyMetrics.break_prev_high || candidate.break_prev_high || candidate.entry_status === "executable"),
            detail: keyMetrics.break_prev_high || candidate.break_prev_high ? "已突破昨日高點" : "等待突破觸發價",
            hard: false,
          },
          {
            label: "停損距離合理",
            ok: Boolean(stopDistancePct !== null && stopDistancePct > 0 && stopDistancePct <= 3),
            detail: stopDistancePct === null ? "缺停損價" : `停損距離 ${number(stopDistancePct)}%`,
            hard: true,
          },
          {
            label: "追價風險可控",
            ok: riskScore <= 55 && distanceToVwap <= 3,
            detail: `風險分數 ${number(riskScore)}，距 VWAP ${number(distanceToVwap)}%`,
            hard: true,
          },
          {
            label: "逐筆 / 五檔資料",
            ok: Boolean(hasTick && hasOrderbook),
            detail: hasTick && hasOrderbook ? "高精準即時資料已具備" : "缺 Tick / 五檔時不作高精準進場",
            hard: false,
          },
        ];
        const passed = checks.filter((item) => item.ok).length;
        const hardBlocked = checks.some((item) => item.hard && !item.ok);
        const conclusion = hardBlocked
          ? "尚未通過進場前檢查，僅可觀察或等待。"
          : (candidate.entry_status === "executable" ? "核心條件通過，仍需按部位風控執行。" : "核心條件接近，等待系統觸發或下一次更新確認。");
        const rows = checks.map((item) => `
          <li>
            <strong>${item.ok ? "通過" : "未通過"}｜${escapeHtml(item.label)}</strong>
            <span class="muted">${escapeHtml(item.detail)}</span>
          </li>
        `).join("");
        return `
          <article class="advisor-card">
            <h3>進場前檢查表</h3>
            <p><strong>${escapeHtml(conclusion)}</strong></p>
            <div class="advisor-grid">
              ${metric("檢查通過", `${passed} / ${checks.length}`)}
              ${metric("資料即時", escapeHtml(yesNo(dataHealth.is_live)))}
              ${metric("可作盤中判斷", escapeHtml(yesNo(dataHealth.can_use_for_daytrade && safety.is_executable_allowed)))}
              ${metric("停損距離", stopDistancePct === null ? "-" : `${escapeHtml(number(stopDistancePct))}%`)}
              ${metric("高精準資料", escapeHtml(hasTick && hasOrderbook ? "已具備" : "尚未具備"))}
            </div>
            <ul class="decision-list">${rows}</ul>
            <p class="warn-inline">此檢查表只做進場前風控與資料完整度提醒，不會調整模型評級；資料延遲、使用上一筆、缺 VWAP、缺量比或缺停損價時，不顯示強烈買多。</p>
          </article>
        `;
      };
      const renderStrongLongDecisionCard = (payload) => {
        payload = payload || {};
        const frontTrade = payload.front_trade || {};
        const candidate = payload.candidate || {};
        const scan = payload.scan || {};
        const dataHealth = payload.data_health || {};
        const safety = payload.safety || {};
        const category = frontTrade.category || "觀察";
        const subtitle = frontTrade.subtitle || "等待確認";
        const isStrongCandidate = category === "強烈買多";
        const isExecutable = Boolean(safety.is_executable_allowed);
        const statusLabel = isExecutable
          ? "進場確認：已通過觸發與安全條件"
          : isStrongCandidate
          ? "強烈買多候選：值得盯盤，但尚未等於進場"
          : "尚未達強烈買多：等待條件補齊";
        const blockedMessages = (safety.blocked_reasons || [])
          .map((item) => item.message || item.code || String(item))
          .filter(Boolean);
        const notes = [];
        if (dataHealth.can_show_strong_long === false) {
          notes.push(dataHealth.advice || "資料狀態未達即時強烈買多條件。");
        }
        if (isStrongCandidate && !isExecutable) {
          notes.push("目前屬於強烈買多候選，仍需等待突破、量能、VWAP、停損距離或資料即時性確認。");
        }
        if (!isStrongCandidate) {
          notes.push(frontTrade.reason || "等待 VWAP、量能、突破、風控或資料條件確認。");
        }
        const reasonCodes = frontTrade.reason_codes || safety.reason_codes || [];
        return `
          <article class="advisor-card strong-long-card">
            <h3>強烈買多 / 進場確認</h3>
            <p><strong>${escapeHtml(statusLabel)}</strong></p>
            <p class="muted">${escapeHtml(frontTrade.reason || "等待條件確認。")}</p>
            <div class="advisor-grid">
              ${metric("前台分類", escapeHtml(category))}
              ${metric("狀態說明", escapeHtml(subtitle))}
              ${metric("強烈買多候選", escapeHtml(yesNo(isStrongCandidate)))}
              ${metric("內部觸發狀態", escapeHtml(yesNo(isExecutable)))}
              ${metric("entry_status", escapeHtml(candidate.entry_status || scan.entry_status || "-"))}
              ${metric("effective entry", escapeHtml(safety.effective_entry_status || "-"))}
              ${metric("資料允許強烈買多", escapeHtml(yesNo(dataHealth.can_show_strong_long)))}
              ${metric("price_status", escapeHtml(dataHealth.price_status || dataHealth.quote_state || "-"))}
            </div>
            <div class="advisor-sections">
              <section class="advisor-panel">
                <h3>下一步</h3>
                <p>${escapeHtml(frontTrade.next_step || "等待條件確認。")}</p>
              </section>
              <section class="advisor-panel">
                <h3>尚未通過進場確認原因</h3>
                ${list(blockedMessages.length ? blockedMessages : notes)}
              </section>
              <section class="advisor-panel">
                <h3>reason code</h3>
                ${list(reasonCodes.length ? reasonCodes : ["no_reason_code"])}
              </section>
            </div>
            <p class="warn-inline">強烈買多代表盤中條件高度符合、值得立即盯盤；通過進場確認才代表已觸發進場條件並通過資料與風控安全檢查。</p>
          </article>
        `;
      };

      const renderEmpty = (message) => {
        result.className = "advisor-result empty";
        result.textContent = message;
      };

      const renderResult = (payload) => {
        const candidate = payload.candidate || {};
        const scan = payload.scan || {};
        const display = payload.display || {};
        const dataHealth = payload.data_health || {};
        const safety = payload.safety || {};
        const keyMetrics = payload.key_metrics || {};
        const reasonGroups = payload.reason_groups || {};
        const history = payload.historical_validation || {};
        const sourceRanking = payload.source_ranking || {};
        const analysis = payload.advisor_analysis || {};
        const frontTrade = payload.front_trade || {};
        const decisionCard = payload.decision_card || {};
        const precision = payload.precision_context || {};
        const entryConfirmation = payload.entry_confirmation || {};
        const entryRadarSummary = payload.entry_radar_summary || {};
        const breakoutTrapDiagnosis = payload.breakout_trap_diagnosis || {};
        const fugleQuote = payload.fugle_quote || {};
        const fugleTrades = payload.fugle_trades || {};
        const fugleCandles = payload.fugle_candles || {};
        const marketMode = payload.market_mode || {};
        const positionAction = payload.position_action || null;
        const symbol = candidate.symbol || payload.symbol || scan.symbol || "";
        const name = candidate.name || payload.name || scan.name || "";
        const sector = payload.sector || scan.sector || "";
        const bias = candidate.trade_bias || scan.trade_bias || "watch";
        const price = display.current_price ?? scan.latest_price ?? candidate.last_price;
        const changePct = display.change_pct ?? scan.change_pct ?? candidate.change_pct;
        const statusText = candidate.trade_bias_reason || scan.trade_bias_reason || payload.message || "";
        const errors = payload.errors && Object.keys(payload.errors).length
          ? Object.entries(payload.errors).map(([key, value]) => `${key}: ${value}`)
          : [];
        const warnings = payload.warnings && Object.keys(payload.warnings).length
          ? Object.entries(payload.warnings).map(([key, value]) => `${key}: ${value}`)
          : [];
        const quoteMeta = [
          display.price_source ? `價格來源：${display.price_source}` : "",
          display.quote_time ? `報價時間：${display.quote_time}` : "",
        ].filter(Boolean).join("｜");
        const analysisLabel = analysis.action_label || decisionLabel(candidate);
        const plan = analysis.action_plan || {};
        const effectiveEntry = safety.effective_entry_status || candidate.entry_status || scan.entry_status || "data_missing";
        const effectiveGrade = safety.effective_grade || candidate.grade || scan.ai_grade || "data_missing";
        const conclusionState = positionAction ? positionAction.action : (decisionCard.final_decision || frontTrade.category || safety.conclusion_state || statusZh(effectiveEntry));
        const nowAction = advisorNowAction({
          state: decisionCard.final_decision || frontTrade.category || conclusionState,
          dataHealth,
          nextStep: decisionCard.next_trigger || entryRadarSummary.next_trigger || frontTrade.next_step || "",
        });
        const conclusion = positionAction
          ? `目前已有持倉，持倉動作：${positionAction.action}。${positionAction.next_step || ""}`
          : decisionCard.user_summary
          ? decisionCard.user_summary
          : frontTrade.headline
          ? frontTrade.headline
          : conclusionState === "資料不足"
          ? "資料不足，不能產生有效當沖建議。"
          : `目前為 ${effectiveGrade} 級${conclusionState}，${analysis.next_step || statusText || "請等待條件確認。"}`
        const positionCard = positionAction ? `
          <article class="advisor-card position-action-card">
            <h3>我的持倉作戰卡</h3>
            <div class="advisor-grid">
              ${metric("持倉動作", escapeHtml(positionAction.action))}
              ${metric("成本價", escapeHtml(number(positionAction.cost_price)))}
              ${metric("現價", escapeHtml(number(positionAction.current_price)))}
              ${metric("數量", escapeHtml(number(positionAction.quantity)))}
              ${metric("未實現損益", `<span class="${Number(positionAction.unrealized_pnl) >= 0 ? "num-up" : "num-down"}">${escapeHtml(number(positionAction.unrealized_pnl))}</span>`)}
              ${metric("未實現損益率", pct(positionAction.unrealized_pnl_pct))}
              ${metric("VWAP", escapeHtml(number(positionAction.vwap)))}
              ${metric("停損價", escapeHtml(number(positionAction.stop_loss)))}
              ${metric("停利價", escapeHtml(number(positionAction.target_price)))}
              ${metric("移動停損", escapeHtml(number(positionAction.trailing_stop)))}
              ${metric("可否加碼", escapeHtml(positionAction.can_add ? "可" : "不可"))}
              ${metric("reason code", escapeHtml(positionAction.reason_code))}
            </div>
            <div class="advisor-sections">
              <section class="advisor-panel">
                <h3>下一步</h3>
                <p>${escapeHtml(positionAction.next_step || "-")}</p>
              </section>
              <section class="advisor-panel">
                <h3>失效條件</h3>
                <p>${escapeHtml(positionAction.invalidation || "-")}</p>
              </section>
              <section class="advisor-panel">
                <h3>不可加碼原因</h3>
                ${list(positionAction.add_forbidden_reasons || [])}
              </section>
            </div>
          </article>
        ` : "";
        const longReasons = reasonGroups.long_reasons || candidate.reasons || scan.source_reasons || [];
        const riskReasons = reasonGroups.risk_reasons || candidate.risk_reasons || scan.risk_reasons || [candidate.not_selected_reason || scan.not_selected_reason || "目前無額外風險提醒。"];
        const notExecutableReasons = reasonGroups.not_executable_reasons || [];
        const keyLevels = Array.isArray(analysis.key_levels) ? analysis.key_levels : [];
        const chart = payload.intraday_chart || {};
        const blockedMessages = (safety.blocked_reasons || []).map((item) => item.message);
        const sizingEntry = candidate.trigger_price || keyMetrics.intraday_high || keyMetrics.current_price || price;
        const sizingStop = keyMetrics.stop_loss || candidate.stop_loss;
        result.className = "advisor-result";
        result.innerHTML = `
          ${renderAdvisorQuickReadCard({ decisionCard, entryRadarSummary, dataHealth, safety, frontTrade })}
          <article class="advisor-card conclusion-card ${conclusionClass(conclusionState)}">
            <div class="advisor-title">
              <div>
                <h2>${escapeHtml(symbol)}｜${escapeHtml(name)}${positionSizeTag(sizingEntry, sizingStop)}</h2>
                <div class="muted">${escapeHtml(sector)}｜資料來源：${escapeHtml(payload.data_source || "Yahoo Finance chart endpoint")}</div>
                <div class="muted">${escapeHtml(quoteMeta || "價格來源：Yahoo Finance chart endpoint")}</div>
              </div>
              <span class="decision-badge ${decisionClass(bias)}">${escapeHtml(conclusionState)}</span>
            </div>
            <div class="advisor-decision">
              <strong>結論：${escapeHtml(conclusion)}</strong>
              <span>${escapeHtml(positionAction ? positionAction.invalidation : (decisionCard.top_reason || frontTrade.reason || analysis.action_summary || statusText || dataHealth.advice || ""))}</span>
              ${renderReasonCodes(positionAction ? [positionAction.reason_code] : (decisionCard.reason_codes || frontTrade.reason_codes || safety.reason_codes))}
            </div>
            <div class="advisor-grid">
              ${metric("目前結論", escapeHtml(decisionCard.final_decision || conclusionState))}
              ${metric("進場狀態", escapeHtml(decisionCard.entry_state || entryRadarSummary.entry_state || "-"))}
              ${metric("最大原因", escapeHtml(decisionCard.top_reason || entryRadarSummary.blocker_summary || "-"))}
              ${metric("下一步", escapeHtml(decisionCard.next_trigger || entryRadarSummary.next_trigger || "-"))}
              ${metric("失效條件", escapeHtml(decisionCard.invalid_condition || "跌破 VWAP、量能退潮或資料延遲時失效。"))}
              ${metric("精準分數", `${escapeHtml(number(decisionCard.precision_score))} / 100`)}
              ${metric("最新成交價", escapeHtml(number(price)))}
              ${metric("漲跌幅", pct(changePct))}
              ${metric("資料可信度", escapeHtml(dataHealth.credibility || "-"))}
              ${metric("現在要做", escapeHtml(nowAction))}
              ${metric("行情狀態", escapeHtml(dataHealth.quote_state_label || dataHealth.quote_state || "-"))}
              ${metric("可用於當沖判斷", escapeHtml(dataHealth.can_use_for_daytrade && safety.is_executable_allowed ? "是" : "否"))}
            </div>
          </article>
          ${positionCard}
          ${renderEntryRadarSummaryCard(entryRadarSummary)}
          ${renderBreakoutTrapCard(breakoutTrapDiagnosis)}
          ${renderStrongLongDecisionCard(payload)}

          <article class="advisor-card">
            <h3>資料可信度</h3>
            <div class="advisor-grid">
              ${metric("目前模式", escapeHtml(marketMode.label || dataHealth.market_mode_label || safety.market_mode_label || "-"))}
              ${metric("market_mode", escapeHtml(marketMode.mode || dataHealth.market_mode || safety.market_mode || "-"))}
              ${metric("模式說明", escapeHtml(marketMode.review_mode_message || dataHealth.review_mode_message || safety.market_mode_message || "-"))}
              ${metric("資料符合模式", escapeHtml(yesNo(marketMode.is_data_current_for_mode ?? dataHealth.is_data_current_for_mode)))}
              ${metric("允許即時進場", escapeHtml(yesNo(marketMode.allow_intraday_signal)))}
              ${metric("是否今天資料", escapeHtml(yesNo(dataHealth.is_today_data)))}
              ${metric("是否盤中資料", escapeHtml(yesNo(dataHealth.is_intraday_data)))}
              ${metric("最後更新時間", escapeHtml(dataHealth.quote_time || display.quote_time || "-"))}
              ${metric("資料年齡", dataHealth.age_minutes === null || dataHealth.age_minutes === undefined ? "-" : `${escapeHtml(number(dataHealth.age_minutes, 1))} 分鐘`)}
              ${metric("即時 / 延遲", escapeHtml(dataHealth.is_live ? "即時" : dataHealth.is_delayed ? "延遲 / 上一筆" : "非即時"))}
              ${metric("上一筆有效價格", escapeHtml(number(dataHealth.last_known_price)))}
              ${metric("Yahoo 日線", escapeHtml(dataHealth.yahoo_daily_success ? "成功" : "失敗"))}
              ${metric("Yahoo 5分K", escapeHtml(dataHealth.yahoo_intraday_5m_success ? "成功" : "失敗"))}
              ${metric("Yahoo 1分K", escapeHtml(dataHealth.yahoo_intraday_1m_success ? "成功" : "失敗 / 回退"))}
              ${metric("TWSE / TPEX", escapeHtml(dataHealth.twse_tpex_quote_success ? "成功" : "失敗"))}
              ${metric("公開五檔", escapeHtml(dataHealth.twse_mis_five_level_status_label || "五檔資料不足"))}
              ${metric("Fugle Quote", escapeHtml(dataHealth.fugle_quote_status_label || "尚未啟用"))}
              ${metric("Fugle 五檔", escapeHtml(dataHealth.fugle_quote_five_level_status_label || "五檔資料不足"))}
              ${metric("Fugle 1分K", escapeHtml(dataHealth.fugle_candles_status_label || "尚未啟用"))}
              ${metric("Fugle 1分K 筆數", escapeHtml(dataHealth.fugle_candles_count ?? 0))}
              ${metric("Fugle 逐筆成交", escapeHtml(dataHealth.fugle_status_label || "尚未啟用"))}
              ${metric("Fugle 逐筆筆數", escapeHtml(dataHealth.fugle_trades_count ?? 0))}
              ${metric("Fugle 大單", escapeHtml(statusZh(dataHealth.fugle_large_trade_status || "missing")))}
              ${metric("使用 cache", escapeHtml(yesNo(dataHealth.uses_cache)))}
              ${metric("data_missing", escapeHtml(yesNo(dataHealth.is_data_missing)))}
              ${metric("市場時段", escapeHtml(`${safety.market_session || "-"}｜${safety.market_status_text || "-"}`))}
              ${metric("安全判斷", escapeHtml(dataHealth.advice || "-"))}
            </div>
            ${blockedMessages.length ? `<div class="warn-inline">${list(blockedMessages)}</div>` : ""}
          </article>

          ${renderEntryChecklistCard(payload)}
          ${renderEntryConfirmationCard(entryConfirmation)}
          ${renderFugleQuoteCard(fugleQuote)}
          ${renderFugleCandlesCard(fugleCandles)}
          ${renderFugleTradesCard(fugleTrades)}
          ${renderTwseOrderbookCard(payload.realtime_quote || {})}
          ${renderPrecisionContextCard(precision)}

          <article class="advisor-card">
            <h3>關鍵指標</h3>
            <div class="advisor-grid">
              ${metric("現價", escapeHtml(number(keyMetrics.current_price ?? price)))}
              ${metric("漲跌幅", pct(keyMetrics.change_pct ?? changePct))}
              ${metric("成交量", escapeHtml(compactMoney(keyMetrics.volume)))}
              ${metric("成交金額", escapeHtml(compactMoney(keyMetrics.turnover)))}
              ${metric("量比", `${escapeHtml(number(keyMetrics.volume_ratio))}x`)}
              ${metric("VWAP", escapeHtml(number(keyMetrics.vwap)))}
              ${metric("距離 VWAP", keyMetrics.distance_to_vwap_pct === null || keyMetrics.distance_to_vwap_pct === undefined ? "-" : `${escapeHtml(number(keyMetrics.distance_to_vwap_pct))}%`)}
              ${metric("昨日高點", escapeHtml(number(keyMetrics.previous_high)))}
              ${metric("突破昨日高點", escapeHtml(yesNo(keyMetrics.break_prev_high)))}
              ${metric("盤中高點 / 觸發", escapeHtml(number(keyMetrics.intraday_high ?? candidate.trigger_price)))}
              ${metric("接近漲停", escapeHtml(yesNo(keyMetrics.near_limit_up)))}
              ${metric("風險分數", escapeHtml(number(keyMetrics.risk_score ?? candidate.risk_score)))}
              ${metric("信心等級", escapeHtml(keyMetrics.confidence_level || "-"))}
              ${metric("停損價", escapeHtml(number(keyMetrics.stop_loss)))}
              ${metric("停利價", escapeHtml(number(keyMetrics.target_price)))}
              ${metric("預估賺賠比", keyMetrics.risk_reward_ratio === null || keyMetrics.risk_reward_ratio === undefined ? "-" : `${escapeHtml(number(keyMetrics.risk_reward_ratio))}R`)}
            </div>
            <section class="advisor-chart">
              <div class="chart-head">
                <h3>分時走勢圖</h3>
                <span>價格 / VWAP / 關鍵價位 / 停損停利</span>
              </div>
              ${renderIntradayChart(chart)}
            </section>
          </article>

          ${renderTrendDiagnosis(candidate)}
          ${renderInstitutionalCard(candidate)}
          ${renderSectorContextCard(candidate)}

          <article class="advisor-card">
            <div class="advisor-sections">
              <section class="advisor-panel">
                <h3>做多理由</h3>
                ${list(longReasons)}
                <p><strong>${escapeHtml(analysis.technical_status || "-")}</strong></p>
                <p>${escapeHtml(analysis.technical_summary || "目前技術結構尚無明確結論。")}</p>
              </section>
              <section class="advisor-panel">
                <h3>風險理由</h3>
                ${list(riskReasons)}
                <p><strong>${escapeHtml(analysis.chase_risk_status || "-")}</strong></p>
                <p>${escapeHtml(analysis.risk_summary || "目前追價風險尚無明確結論。")}</p>
              </section>
              <section class="advisor-panel">
                <h3>目前不執行原因</h3>
                ${list(notExecutableReasons.length ? notExecutableReasons : [analysis.next_step || statusText || "等待條件確認。"])}
                <p><strong>${escapeHtml(analysis.volume_status || "-")}</strong></p>
                <p>${escapeHtml(analysis.volume_summary || "目前量能尚無明確結論。")}</p>
              </section>
            </div>
          </article>

          <article class="advisor-card">
            <h3>下一步條件與失效條件</h3>
            <div class="plan-grid">
              ${planRow("突破價重新評估", plan.trigger_condition || `突破 ${money(candidate.trigger_price || keyMetrics.intraday_high)} 後重新評估`)}
              ${planRow("量比升級條件", "量比達 1.0x 以上；B+ 練習觀察至少需維持 0.8x 以上")}
              ${planRow("VWAP 條件", `站回 VWAP ${money(keyMetrics.vwap)} 並維持，才重新觀察`)}
              ${planRow("拉回觀察區", `拉回 VWAP 附近但不跌破；參考 ${money(keyMetrics.vwap)}`)}
              ${planRow("B 變 B+", "站上 VWAP、量比達標、突破昨高或觸發價，且風險分數不升高")}
              ${planRow("B+ 變 A", "突破確認、量能延續、風險可控且信心分數足夠")}
              ${planRow("失效條件", plan.invalidation_condition || "跌破 VWAP、停損價、盤中支撐，或資料過期")}
              ${planRow("停損 / 停利", `停損 ${money(keyMetrics.stop_loss)}｜停利 ${money(keyMetrics.target_price)}`)}
              ${planRow("大盤 / 資料失效", "大盤轉弱、量能退潮、風險分數升高或資料來源失敗時，不顯示可執行")}
            </div>
          </article>

          <article class="advisor-card">
            <h3>這檔過去表現</h3>
            ${renderHistory(history)}
          </article>

          <article class="advisor-card">
            <h3>來源與全市場排名</h3>
            <div class="advisor-grid">
              ${metric("來源", escapeHtml(sourceRanking.source_scope || "manual_scan"))}
              ${metric("來自 watchlist", escapeHtml(yesNo(sourceRanking.from_watchlist)))}
              ${metric("來自 full_market", escapeHtml(yesNo(sourceRanking.from_full_market)))}
              ${metric("out_of_pool 新找到", escapeHtml(yesNo(sourceRanking.out_of_pool)))}
              ${metric("進入異動候選池", escapeHtml(yesNo(sourceRanking.entered_candidate_pool)))}
              ${metric("今日異動池排名", escapeHtml(sourceRanking.today_rank || "-"))}
              ${metric("今日 AI 分級", escapeHtml(sourceRanking.ai_grade || effectiveGrade))}
              ${metric("reason code", escapeHtml(sourceRanking.reason_code || "-"))}
            </div>
            <p class="muted">${escapeHtml(sourceRanking.message || sourceRanking.not_selected_reason || "此區說明今天系統如何找到或排除此股票。")}</p>
          </article>

          <details class="advisor-card debug-card">
            <summary>開發者資訊</summary>
            <div class="advisor-sections">
              <section class="advisor-panel">
                <h3>API / 資料來源</h3>
                ${list([
                  `generated_at: ${payload.generated_at || "-"}`,
                  `data_source: ${payload.data_source || "-"}`,
                  `db_path: ${payload.db_path || "-"}`,
                  `advisor version: ${analysis.version || "-"}`,
                  ...warnings,
                  ...errors,
                ])}
              </section>
              <section class="advisor-panel">
                <h3>關鍵價位</h3>
                <div class="level-list">
                  ${keyLevels.length ? keyLevels.map((item) => `
                    <div class="level-row">
                      <span>${escapeHtml(item.label)}</span>
                      <strong>${escapeHtml(money(item.value))}</strong>
                      <em>${escapeHtml(item.note || "")}</em>
                    </div>
                  `).join("") : "<p>目前缺少關鍵價位資料。</p>"}
                </div>
              </section>
            </div>
          </details>
        `;
      };

      const scan = async (symbol, options = {}) => {
        const forceLive = Boolean(options.forceLive);
        status.textContent = forceLive ? `正在即時重算 ${symbol}...` : `正在讀取 ${symbol} 最新快照...`;
        renderEmpty("正在整理個股資料。");
        try {
          const response = await fetch("/api/tw/scan/symbol", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol, force_live: forceLive }),
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
          if (!payload.symbol && payload.ok === false) throw new Error(payload.message || "查無股票資料");
          const modeText = payload.response_mode === "snapshot"
            ? "快照模式：未重新抓取即時行情，僅供觀察。"
            : "即時重算完成。";
          status.textContent = `完成：${payload.symbol || symbol}｜${modeText}`;
          renderResult(payload);
        } catch (error) {
          status.textContent = "掃描失敗";
          renderEmpty(`目前無法取得個股建議：${error.message}`);
        }
      };

      form?.addEventListener("submit", (event) => {
        event.preventDefault();
        const symbol = input.value.trim();
        if (!symbol) {
          status.textContent = "請輸入股票代號。";
          return;
        }
        window.history.replaceState({}, "", advisorLink(symbol));
        scan(symbol);
      });

      liveScanButton?.addEventListener("click", () => {
        const symbol = input.value.trim();
        if (!symbol) {
          status.textContent = "請先輸入股票代號，再執行即時重算。";
          return;
        }
        window.history.replaceState({}, "", advisorLink(symbol));
        scan(symbol, { forceLive: true });
      });

      document.querySelectorAll("[data-symbol]").forEach((button) => {
        button.addEventListener("click", () => {
          const symbol = button.getAttribute("data-symbol") || "";
          input.value = symbol;
          window.history.replaceState({}, "", advisorLink(symbol));
          scan(symbol);
        });
      });

      const initial = new URLSearchParams(window.location.search).get("symbol") || "";
      if (initial.trim()) {
        input.value = initial.trim();
        scan(initial.trim());
      }
    })();
    """


def paper_dashboard_script() -> str:
    return r"""
    (() => {
      const state = {
        interval: 300,
        remaining: 300,
        quoteTimer: null,
        fetchStatus: "idle",
        lastFetchTime: "",
        renderStatus: "pending",
        renderErrorMessage: "",
        positionsByTradeId: new Map(),
        pendingClose: null,
      };
      const REVIEW_TAGS = [
        { code: "discipline", label: "紀律執行（完美停損/停利）" },
        { code: "fomo", label: "FOMO（衝動追高）" },
        { code: "hold_loser", label: "凹單（未依系統提示停損）" },
        { code: "revenge_trade", label: "報復性交易（過度頻繁交易）" },
        { code: "early_exit", label: "提前離場（少賺）" },
      ];
      const $ = (id) => document.getElementById(id);
      const text = (value) => value === null || value === undefined || value === "" ? "-" : String(value);
      const money = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "-";
      const pct = (value) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : "-";
      const asArray = (value) => Array.isArray(value) ? value : [];
      const asObject = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
      const escapeHtml = (value) => text(value)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      const cls = (value) => Number(value) > 0 ? "num-up" : Number(value) < 0 ? "num-down" : "";
      const metric = (label, value) => `<div class="metric"><span class="muted">${label}</span><strong>${value}</strong></div>`;
      const row = (label, value) => `<tr><td>${label}</td><td>${value}</td></tr>`;
      const sourceLabel = (value) => value === "manual" ? "手動模擬" : "系統訊號";
      const autoExitLabel = (value) => Number(value) ? "是" : "否";
      const reviewTagsLabel = (value) => {
        let parsed = [];
        if (Array.isArray(value)) parsed = value;
        else if (value) {
          try { parsed = JSON.parse(value); } catch (error) { parsed = String(value).split(","); }
        }
        const labels = parsed.map((code) => REVIEW_TAGS.find((item) => item.code === String(code).trim())?.label).filter(Boolean);
        return labels.length ? labels.join("、") : "-";
      };
      const status = $("paper-refresh-status");
      const formStatus = $("manual-form-status");

      async function loadDashboard() {
        status.textContent = "更新中...";
        $("paper-error").hidden = true;
        let payload = null;
        try {
          const response = await fetch("/api/paper/dashboard", { cache: "no-store" });
          payload = await response.json();
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          state.fetchStatus = "success";
          state.lastFetchTime = new Date().toLocaleString();
          render(payload);
          state.interval = Number(payload.refresh_interval_seconds || 300);
          state.remaining = state.interval;
          status.textContent = `上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`;
        } catch (error) {
          state.fetchStatus = "failed";
          state.lastFetchTime = new Date().toLocaleString();
          state.renderStatus = "failed";
          state.renderErrorMessage = error.message;
          $("paper-error").hidden = false;
          $("paper-error").textContent = `虛擬交易 API 暫時無法更新：${error.message}`;
          renderDebug(payload?.debug || {}, payload?.run || {}, payload || {});
          state.remaining = Math.max(state.interval, 60);
          status.textContent = "更新失敗，稍後自動重試";
        }
      }

      function render(payload) {
        const renderErrors = [];
        const safeRender = (label, fn) => {
          try {
            fn();
          } catch (error) {
            renderErrors.push(`${label}: ${error.message}`);
          }
        };
        const performance = asObject(payload.performance);
        safeRender("AI 虛擬交易摘要", () => renderPaperDecision(asObject(payload.decision_summary)));
        safeRender("帳戶總覽", () => renderAccounts(asArray(payload.accounts), performance));
        safeRender("B+ 等待觸發", () => renderBPlusWaiting(asArray(payload.b_plus_triggers)));
        safeRender("目前持倉", () => renderPositions(asArray(payload.positions)));
        safeRender("今日交易", () => renderTrades(asArray(payload.trades)));
        safeRender("跳過紀錄", () => renderSkipped(asArray(payload.skipped_trades).length ? asArray(payload.skipped_trades) : asArray(payload.skipped)));
        safeRender("策略績效", () => renderPerformance(performance));
        window.StockNotificationModule?.observeSignals(asArray(payload.b_plus_triggers));
        state.renderStatus = renderErrors.length ? "partial" : "success";
        state.renderErrorMessage = renderErrors.join("；");
        renderDebug(asObject(payload.debug), asObject(payload.run), payload);
        const errors = asArray(payload.errors);
        if (payload.api_status === "error") {
          $("paper-error").hidden = false;
          $("paper-error").textContent = `虛擬交易 API 暫時無法更新：${errors.join("；") || payload.message || "unknown error"}`;
        } else if (payload.api_status === "degraded" && errors.length) {
          $("paper-error").hidden = false;
          $("paper-error").textContent = `虛擬交易以空狀態顯示：${errors.join("；")}`;
        } else if (renderErrors.length) {
          $("paper-error").hidden = false;
          $("paper-error").textContent = `部分區塊暫時無法顯示：${state.renderErrorMessage}`;
        } else {
          $("paper-error").hidden = true;
          $("paper-error").textContent = "";
        }
      }

      function renderPaperDecision(data) {
        if (!data || !data.summary_text) {
          $("paper-decision-summary").innerHTML = '<h2>AI 虛擬交易摘要</h2><p class="muted">目前資料不足，系統僅能提供有限判斷。</p>';
          return;
        }
        $("paper-decision-summary").innerHTML = `<h2>AI 虛擬交易摘要</h2>
          <p>${escapeHtml(data.summary_text)}</p>
          <section class="summary">
            ${metric("手動交易數", data.manual_trades || 0)}
            ${metric("系統交易數", data.system_trades || 0)}
            ${metric("open positions", data.open_positions || 0)}
            ${metric("今日 realized pnl", `<span class="${cls(data.today_realized_pnl)}">${money(data.today_realized_pnl)}</span>`)}
            ${metric("B+ waiting", data.b_plus_waiting || 0)}
            ${metric("B+ triggered", data.b_plus_triggered || 0)}
            ${metric("可練習標的", data.practice_available ? "有" : "無")}
          </section>
          <section class="notice">${escapeHtml(data.disclaimer || "")}</section>`;
      }

      function renderAccounts(accounts, performance) {
        $("paper-accounts").innerHTML = accounts.map((account) => [
          metric(`${escapeHtml(account.market)} 帳戶資金`, `${escapeHtml(account.currency)} ${money(account.equity)}`),
          metric(`${escapeHtml(account.market)} 現金`, money(account.cash_balance)),
          metric(`${escapeHtml(account.market)} 已實現損益`, `<span class="${cls(account.realized_pnl)}">${money(account.realized_pnl)}</span>`),
          metric(`${escapeHtml(account.market)} 未實現損益`, `<span class="${cls(account.unrealized_pnl)}">${money(account.unrealized_pnl)}</span>`),
          metric(`${escapeHtml(account.market)} 最大回撤`, pct(account.max_drawdown)),
        ].join("")).join("") + metric("整體勝率", pct(performance.win_rate || 0));
      }

      function renderBPlusWaiting(items) {
        const waiting = items.filter((item) => item.lifecycle_status === "observed" || item.trigger_readiness !== "ready");
        if (!waiting.length) {
          $("paper-b-plus-waiting").innerHTML = '<tr><td colspan="7">目前沒有等待觸發的 B+ 練習訊號。</td></tr>';
          return;
        }
        $("paper-b-plus-waiting").innerHTML = waiting.map((item) => `<tr>
          <td>${escapeHtml(item.market)}</td>
          <td>${escapeHtml(item.symbol)}<br><span class="muted">${escapeHtml(item.name_zh)}</span></td>
          <td>${escapeHtml(item.entry_status)}</td>
          <td>${escapeHtml(item.lifecycle_status)}</td>
          <td>${escapeHtml(item.trigger_condition)}</td>
          <td>${escapeHtml(item.distance_to_trigger)}</td>
          <td>${escapeHtml(item.trigger_readiness_label || item.trigger_readiness)}<br><span class="muted">${escapeHtml(item.trigger_next_action)}</span></td>
        </tr>`).join("");
      }

      function renderPositions(items) {
        state.positionsByTradeId = new Map(items.map((item) => [String(item.trade_id), item]));
        if (!items.length) {
          $("paper-positions").innerHTML = '<tr><td colspan="13">目前尚無持倉。你可以等待系統訊號觸發，也可以使用上方手動虛擬交易建立一筆模擬交易。</td></tr>';
          return;
        }
        $("paper-positions").innerHTML = items.map((item) => `<tr>
          <td>${escapeHtml(item.market)}</td><td>${escapeHtml(item.symbol)}<br><span class="muted">${escapeHtml(item.name_zh)}</span></td>
          <td><span class="source-pill">${sourceLabel(item.source)}</span></td>
          <td>${escapeHtml(item.risk_mode || "-")}</td>
          <td>${autoExitLabel(item.auto_exit_enabled)}</td>
          <td>${escapeHtml(item.risk_alert || "無")}</td>
          <td>${money(item.entry_price)}</td><td>${money(item.current_price)}</td>
          <td>${money(item.quantity)}</td><td class="${cls(item.unrealized_pnl)}">${money(item.unrealized_pnl)}<br><span class="muted">${pct(item.unrealized_pnl_pct)}</span></td>
          <td>${money(item.stop_loss)}</td><td>${money(item.target_price)}</td>
          <td><div class="close-controls">
            <button type="button" class="manual-close-current" data-trade-id="${escapeHtml(item.trade_id)}">手動平倉</button>
            <input class="manual-close-price" data-trade-id="${escapeHtml(item.trade_id)}" type="number" min="0" step="0.01" placeholder="指定價">
            <button type="button" class="manual-close-specified" data-trade-id="${escapeHtml(item.trade_id)}">指定價平倉</button>
          </div></td>
        </tr>`).join("");
      }

      function renderTrades(items) {
        const tradable = items.filter((item) => item.status !== "skipped").slice(0, 40);
        if (!tradable.length) {
          $("paper-trades").innerHTML = '<tr><td colspan="12">今日尚無虛擬交易，等待符合條件的訊號，或使用上方手動虛擬交易測試。</td></tr>';
          return;
        }
        $("paper-trades").innerHTML = tradable.map((item) => `<tr>
          <td>${escapeHtml(item.market)}</td><td>${escapeHtml(item.symbol)}<br><span class="muted">${escapeHtml(item.name_zh)}</span></td>
          <td><span class="source-pill">${sourceLabel(item.source)}</span></td>
          <td>${escapeHtml(item.risk_mode || "-")}</td>
          <td>${autoExitLabel(item.auto_exit_enabled)}<br><span class="muted">${escapeHtml(item.auto_exit_reason || "")}</span></td>
          <td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.grade)}</td><td>${escapeHtml(item.entry_status)}</td>
          <td>${escapeHtml(item.entry_time)}<br>${money(item.entry_price)}</td>
          <td>${escapeHtml(item.exit_time)}<br>${money(item.exit_price)}</td>
          <td class="${cls(item.realized_pnl)}">${money(item.realized_pnl)}<br><span class="muted">${pct(item.realized_pnl_pct)}</span></td>
          <td class="notes">${escapeHtml(item.entry_reason || item.exit_reason || "")}<br><span class="muted">覆盤：${escapeHtml(reviewTagsLabel(item.review_tags))}</span></td>
        </tr>`).join("");
      }

      function renderSkipped(items) {
        if (!items.length) {
          $("paper-skipped").innerHTML = '<tr><td colspan="5">尚無跳過紀錄。</td></tr>';
          return;
        }
        $("paper-skipped").innerHTML = items.slice(0, 40).map((item) => `<tr>
          <td>${escapeHtml(item.market)}</td><td>${escapeHtml(item.symbol)}<br><span class="muted">${escapeHtml(item.name_zh)}</span></td>
          <td>${escapeHtml(item.grade)} / ${escapeHtml(item.entry_status)} / ${escapeHtml(item.lifecycle_status)}</td>
          <td>${escapeHtml(item.skipped_reason)}</td><td>${escapeHtml(item.created_at)}</td>
        </tr>`).join("");
      }

      function renderPerformance(performance) {
        const sections = [
          tableFor("依來源", asArray(performance.by_source), "source"),
          tableFor("依市場", asArray(performance.by_market), "market"),
          tableFor("依分級", asArray(performance.by_grade), "grade"),
          tableFor("依進場狀態", asArray(performance.by_entry_status), "entry_status"),
        ];
        $("paper-performance").innerHTML = sections.join("");
      }

      function tableFor(title, rows, key) {
        const body = rows.length ? rows.map((item) => `<tr><td>${escapeHtml(item[key])}</td><td>${item.trades}</td><td>${pct(item.win_rate)}</td><td class="${cls(item.realized_pnl)}">${money(item.realized_pnl)}</td></tr>`).join("") : '<tr><td colspan="4">尚無資料。</td></tr>';
        return `<h3>${title}</h3><table><thead><tr><th>分類</th><th>筆數</th><th>勝率</th><th>已實現損益</th></tr></thead><tbody>${body}</tbody></table>`;
      }

      function renderDebug(debug, run, payload = {}) {
        const accounts = asArray(payload.accounts);
        const positions = asArray(payload.positions);
        const trades = asArray(payload.trades);
        const skipped = asArray(payload.skipped_trades).length ? asArray(payload.skipped_trades) : asArray(payload.skipped);
        const bPlusTriggers = asArray(payload.b_plus_triggers);
        const manualTrades = trades.filter((item) => item.source === "manual").length;
        const systemTrades = trades.filter((item) => (item.source || "system") === "system").length;
        const bPlusWaiting = bPlusTriggers.filter((item) => item.lifecycle_status === "observed").length;
        const bPlusTriggered = bPlusTriggers.filter((item) => item.lifecycle_status === "triggered").length;
        $("paper-debug").innerHTML = [
          row("commit hash", escapeHtml(debug.app_version)),
          row("engine version", escapeHtml(debug.engine_version)),
          row("generated_at", escapeHtml(debug.generated_at)),
          row("API status", escapeHtml(payload.api_status || debug.api_status || "ok")),
          row("API fetch status", escapeHtml(state.fetchStatus)),
          row("last fetch time", escapeHtml(state.lastFetchTime)),
          row("render status", escapeHtml(state.renderStatus)),
          row("render error message", escapeHtml(state.renderErrorMessage || "")),
          row("refresh interval", `${text(debug.refresh_interval || payload.refresh_interval_seconds)} 秒`),
          row("accounts count", text(debug.accounts_count ?? accounts.length)),
          row("positions count", text(debug.open_positions_count ?? positions.length)),
          row("trades count", text(debug.trades_count ?? trades.length)),
          row("skipped count", text(debug.skipped_count ?? skipped.length)),
          row("recommendations scanned count", text(debug.recommendations_scanned_count)),
          row("executable / triggered count", text(debug.executable_triggered_count)),
          row("B+ waiting count", text(debug.b_plus_waiting_count ?? bPlusWaiting)),
          row("B+ triggered count", text(bPlusTriggered)),
          row("B+ ready count", text(debug.b_plus_ready_count)),
          row("manual trades count", text(debug.manual_trades_count ?? manualTrades)),
          row("system trades count", text(debug.system_trades_count ?? systemTrades)),
          row("open manual positions count", text(debug.open_manual_positions_count)),
          row("last manual trade created_at", escapeHtml(debug.last_manual_trade_created_at || "")),
          row("last close trade status", escapeHtml(debug.last_close_trade_status || "")),
          row("quote API status", escapeHtml(debug.quote_api_status || "")),
          row("last error", escapeHtml(debug.last_error || "")),
          row("本次開倉", text(run.opened)),
          row("本次平倉", text(run.closed)),
          row("本次跳過", text(run.skipped)),
        ].join("");
      }

      function setFormStatus(message, ok = true) {
        formStatus.textContent = message || "";
        formStatus.className = `manual-status ${message ? (ok ? "ok" : "fail") : ""}`;
      }

      function formPayload() {
        return {
          market: $("manual-market").value,
          symbol: $("manual-symbol").value.trim().toUpperCase(),
          name_zh: $("manual-name-zh").value.trim(),
          name_en: $("manual-name-en").value.trim(),
          side: "buy",
          entry_price: Number($("manual-entry-price").value),
          quantity: Number($("manual-quantity").value),
          stop_loss: Number($("manual-stop-loss").value),
          target_price: Number($("manual-target-price").value),
          risk_mode: $("manual-risk-mode").value,
          entry_reason: $("manual-entry-reason").value.trim(),
        };
      }

      async function loadQuote() {
        const market = $("manual-market").value;
        const symbol = $("manual-symbol").value.trim().toUpperCase();
        $("manual-account").value = market;
        if (!symbol) {
          $("manual-quote-status").textContent = "輸入股票代號後可嘗試帶入名稱與參考行情。";
          return;
        }
        $("manual-quote-status").textContent = "查詢參考行情中...";
        try {
          const response = await fetch(`/api/paper/quote?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`, { cache: "no-store" });
          const payload = await response.json();
          if (payload.name_zh && !$("manual-name-zh").value) $("manual-name-zh").value = payload.name_zh;
          if (payload.name_en && !$("manual-name-en").value) $("manual-name-en").value = payload.name_en;
          if (payload.latest_price && !$("manual-entry-price").value) $("manual-entry-price").value = Number(payload.latest_price).toFixed(2);
          const label = `${escapeHtml(symbol)}｜${escapeHtml(payload.name_zh || "")}｜${escapeHtml(payload.name_en || "")}`;
          $("manual-quote-status").innerHTML = payload.ok
            ? `${label}｜參考價 ${money(payload.latest_price)}｜VWAP ${money(payload.vwap)}｜${escapeHtml(payload.entry_status || "-")}`
            : `${label}｜目前無法取得即時行情，請自行確認虛擬進場價格。`;
        } catch (error) {
          $("manual-quote-status").textContent = "目前無法取得即時行情，請自行確認虛擬進場價格。";
        }
      }

      async function submitManualTrade(event) {
        event.preventDefault();
        setFormStatus("建立手動虛擬買進中...", true);
        try {
          const response = await fetch("/api/paper/manual-trade", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(formPayload()),
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
          setFormStatus(payload.quote_warning ? `${payload.message}；${payload.quote_warning}` : payload.message, true);
          await loadDashboard();
        } catch (error) {
          setFormStatus(error.message, false);
        }
      }

      async function closeTrade(tradeId, exitPrice, reviewTags) {
        setFormStatus("手動平倉中...", true);
        try {
          const response = await fetch("/api/paper/close-trade", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ trade_id: tradeId, exit_price: exitPrice, review_tags: reviewTags }),
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
          setFormStatus(payload.message, true);
          await loadDashboard();
        } catch (error) {
          setFormStatus(error.message, false);
        }
      }

      function openCloseReviewModal(tradeId, exitPrice) {
        const position = state.positionsByTradeId.get(String(tradeId));
        if (!position) {
          setFormStatus("找不到可平倉的持倉資料，請重新整理後再試。", false);
          return;
        }
        const resolvedExit = Number(exitPrice) > 0 ? Number(exitPrice) : Number(position.current_price || 0);
        if (!(resolvedExit > 0)) {
          setFormStatus("平倉價格必須大於 0", false);
          return;
        }
        state.pendingClose = { tradeId, exitPrice: resolvedExit, position };
        const quantity = Number(position.quantity || 0);
        const entry = Number(position.entry_price || 0);
        const pnl = (resolvedExit - entry) * quantity;
        const pnlPct = entry > 0 ? (resolvedExit - entry) / entry * 100 : 0;
        $("close-review-summary").innerHTML = [
          metric("標的", `${escapeHtml(position.symbol)}｜${escapeHtml(position.name_zh || "")}`),
          metric("進場價", money(entry)),
          metric("平倉價", money(resolvedExit)),
          metric("數量", money(quantity)),
          metric("預估損益", `<span class="${cls(pnl)}">${money(pnl)}</span>`),
          metric("預估報酬", `<span class="${cls(pnlPct)}">${pct(pnlPct)}</span>`),
        ].join("");
        $("close-review-tags").innerHTML = REVIEW_TAGS.map((item) => `<label>
          <input type="checkbox" name="close-review-tag" value="${escapeHtml(item.code)}">
          <span>${escapeHtml(item.label)}</span>
        </label>`).join("");
        $("close-review-error").hidden = true;
        $("close-review-confirm").disabled = true;
        $("close-review-modal").hidden = false;
      }

      function closeReviewModal() {
        state.pendingClose = null;
        $("close-review-modal").hidden = true;
        $("close-review-tags").innerHTML = "";
        $("close-review-error").hidden = true;
      }

      function selectedReviewTags() {
        return Array.from(document.querySelectorAll('input[name="close-review-tag"]:checked')).map((item) => item.value);
      }

      function updateCloseReviewConfirm() {
        const hasTags = selectedReviewTags().length > 0;
        $("close-review-confirm").disabled = !hasTags;
        $("close-review-error").hidden = hasTags;
      }

      async function submitCloseReview() {
        const tags = selectedReviewTags();
        if (!state.pendingClose || !tags.length) {
          $("close-review-error").hidden = false;
          return;
        }
        const pending = state.pendingClose;
        closeReviewModal();
        await closeTrade(pending.tradeId, pending.exitPrice, tags);
      }

      $("manual-trade-form").addEventListener("submit", submitManualTrade);
      $("manual-clear").addEventListener("click", () => {
        $("manual-trade-form").reset();
        $("manual-market").value = "US";
        $("manual-account").value = "US";
        $("manual-risk-mode").value = "manual_only";
        $("manual-quote-status").textContent = "輸入股票代號後可嘗試帶入名稱與參考行情。";
        setFormStatus("");
      });
      $("manual-market").addEventListener("change", loadQuote);
      $("manual-account").addEventListener("change", () => {
        $("manual-market").value = $("manual-account").value;
        loadQuote();
      });
      $("manual-symbol").addEventListener("input", () => {
        window.clearTimeout(state.quoteTimer);
        state.quoteTimer = window.setTimeout(loadQuote, 450);
      });
      $("manual-symbol").addEventListener("blur", loadQuote);
      $("paper-positions").addEventListener("click", (event) => {
        const current = event.target.closest(".manual-close-current");
        const specified = event.target.closest(".manual-close-specified");
        if (current) {
          openCloseReviewModal(current.dataset.tradeId, null);
          return;
        }
        if (specified) {
          const input = document.querySelector(`.manual-close-price[data-trade-id="${CSS.escape(specified.dataset.tradeId)}"]`);
          openCloseReviewModal(specified.dataset.tradeId, Number(input?.value || 0));
        }
      });
      $("close-review-tags").addEventListener("change", updateCloseReviewConfirm);
      $("close-review-confirm").addEventListener("click", submitCloseReview);
      $("close-review-cancel").addEventListener("click", closeReviewModal);
      $("close-review-cancel-x").addEventListener("click", closeReviewModal);
      $("close-review-modal").addEventListener("click", (event) => {
        if (event.target.id === "close-review-modal") closeReviewModal();
      });

      function tick() {
        state.remaining -= 1;
        if (state.remaining <= 0) {
          loadDashboard();
          return;
        }
        status.textContent = `上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`;
      }

      loadDashboard();
      window.setInterval(tick, 1000);
    })();
    """


def tw_advisor_css() -> str:
    return """
    .advisor-page { padding:0 28px 32px; }
    .advisor-hero { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; padding:24px 0 12px; }
    .advisor-hero h1 { margin:0 0 4px; font-size:26px; }
    .advisor-hero p { max-width:840px; }
    .advisor-examples { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
    .advisor-examples button { height:32px; padding:0 10px; background:#fff; border:1px solid var(--line); color:#344054; }
    .advisor-form { display:grid; grid-template-columns:minmax(220px,320px) auto; align-items:end; gap:10px; background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px; }
    .advisor-form label { margin:0; font-size:12px; color:#344054; }
    .advisor-form button { background:#175cd3; border-color:#175cd3; color:#fff; height:38px; }
    .advisor-form .secondary-button { background:#fff; border-color:#98a2b3; color:#344054; }
    .advisor-form-actions { display:flex; gap:8px; align-items:center; }
    .advisor-form-hint { grid-column:1 / -1; margin:0; font-size:12px; color:#667085; }
    .advisor-result { margin-top:14px; }
    .advisor-card { background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:12px; }
    .quick-read-card { border-width:2px; border-color:#bfdbfe; background:#eff6ff; }
    .quick-read-card.data-limited-card { border-color:#fed7aa; background:#fff7ed; }
    .quick-read-card h3 { margin-top:0; }
    .quick-read-card .advisor-metric { background:rgba(255,255,255,.78); }
    .quick-read-card .advisor-metric strong { font-size:16px; line-height:1.35; }
    .conclusion-card { border-width:2px; }
    .conclusion-ok { border-color:#16a34a; background:#f0fdf4; }
    .conclusion-watch { border-color:#bfdbfe; background:#eff6ff; }
    .conclusion-risk { border-color:#fed7aa; background:#fff7ed; }
    .conclusion-missing { border-color:#fecaca; background:#fef2f2; }
    .advisor-title { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .advisor-title h2 { margin:0; font-size:20px; }
    .advisor-decision { margin-top:12px; border:1px solid #bfdbfe; background:#eff6ff; border-radius:8px; padding:12px; color:#1e3a8a; }
    .advisor-decision strong { display:block; font-size:18px; margin-bottom:2px; }
    .decision-badge { display:inline-flex; align-items:center; justify-content:center; min-width:72px; padding:5px 12px; border-radius:999px; font-weight:800; border:1px solid var(--line); }
    .decision-long { color:#fff; background:#067647; border-color:#067647; }
    .decision-short { color:#fff; background:#b42318; border-color:#b42318; }
    .decision-watch { color:#344054; background:#f2f4f7; }
    .advisor-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:12px; }
    .advisor-metric { border:1px solid var(--line); border-radius:8px; padding:10px; background:#fbfcfe; }
    .advisor-metric span { display:block; color:var(--muted); font-size:12px; }
    .advisor-metric strong { display:block; margin-top:2px; font-size:18px; }
    .reason-code-list { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .reason-code-list code { background:#eef2f6; border:1px solid var(--line); border-radius:6px; padding:3px 6px; color:#344054; }
    .advisor-chart { margin-top:12px; border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px; overflow:hidden; }
    .chart-head { display:flex; justify-content:space-between; gap:10px; align-items:center; margin-bottom:8px; }
    .chart-head h3 { margin:0; font-size:15px; }
    .chart-head span { color:var(--muted); font-size:12px; }
    .advisor-chart-svg { width:100%; height:auto; min-height:260px; display:block; }
    .advisor-chart-svg rect { fill:#fbfcfe; }
    .advisor-chart-svg .axis { stroke:#98a2b3; stroke-width:1; }
    .advisor-chart-svg .axis-label { fill:#667085; font-size:12px; }
    .advisor-chart-svg .price-line { fill:none; stroke:#175cd3; stroke-width:3; }
    .advisor-chart-svg .vwap-line { fill:none; stroke:#f59e0b; stroke-width:2; stroke-dasharray:6 4; }
    .advisor-chart-svg .chart-level line { stroke:#98a2b3; stroke-width:1; stroke-dasharray:3 4; }
    .advisor-chart-svg .chart-level text { fill:#475467; font-size:11px; }
    .advisor-chart-svg .chart-level.stop line, .advisor-chart-svg .chart-level.stop text { stroke:#b42318; fill:#b42318; }
    .advisor-chart-svg .chart-level.target line, .advisor-chart-svg .chart-level.target text { stroke:#067647; fill:#067647; }
    .advisor-chart-svg .chart-level.vwap line, .advisor-chart-svg .chart-level.vwap text { stroke:#f59e0b; fill:#b45309; }
    .chart-legend { display:flex; gap:12px; flex-wrap:wrap; margin-top:8px; color:var(--muted); font-size:12px; }
    .chart-legend span { display:inline-flex; align-items:center; gap:5px; }
    .chart-legend i { width:16px; height:3px; display:inline-block; border-radius:99px; }
    .legend-price { background:#175cd3; }
    .legend-vwap { background:#f59e0b; }
    .legend-stop { background:#b42318; }
    .legend-target { background:#067647; }
    .advisor-chart-empty { padding:18px; color:var(--muted); background:#fbfcfe; border:1px dashed var(--line); border-radius:8px; }
    .advisor-sections { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; margin-top:12px; }
    .advisor-panel { border:1px solid var(--line); border-radius:8px; padding:12px; background:#fff; }
    .advisor-plan { grid-column:1 / -1; border-color:#bfdbfe; background:#f8fbff; }
    .advisor-panel h3 { margin:0 0 8px; font-size:15px; }
    .advisor-panel ul { margin:0; padding-left:18px; }
    .advisor-panel li { margin:4px 0; }
    .advisor-history-table { overflow:auto; }
    .advisor-history-table table { min-width:980px; }
    .orderbook-table { margin-top:12px; overflow:auto; }
    .orderbook-table table { min-width:520px; }
    .orderbook-table th, .orderbook-table td { text-align:right; }
    .orderbook-table th:first-child, .orderbook-table td:first-child { text-align:center; width:48px; color:var(--muted); }
    .warn-inline { margin-top:10px; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; color:#7c2d12; }
    .debug-card summary { cursor:pointer; font-weight:800; }
    .plan-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:8px; }
    .plan-row, .level-row { border:1px solid var(--line); border-radius:8px; background:#fff; padding:9px 10px; }
    .plan-row span, .level-row span { display:block; color:var(--muted); font-size:12px; }
    .plan-row strong, .level-row strong { display:block; margin-top:2px; font-size:15px; }
    .level-list { display:grid; gap:8px; }
    .level-row em { display:block; margin-top:2px; color:var(--muted); font-style:normal; font-size:12px; }
    .num-up { color:#c1121f; font-weight:700; }
    .num-down { color:#067647; font-weight:700; }
    .num-flat { color:#475467; font-weight:700; }
    .notice { margin:12px 0; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; color:#7c2d12; }
    @media (max-width:760px) { .advisor-page { padding-left:14px; padding-right:14px; } .advisor-hero { flex-direction:column; align-items:stretch; } .advisor-form { grid-template-columns:1fr; } }
    """


def accuracy_dashboard_script() -> str:
    return r"""
    (() => {
      const $ = (id) => document.getElementById(id);
      const text = (value) => value === null || value === undefined || value === "" ? "-" : String(value);
      const number = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "-";
      const escapeHtml = (value) => text(value)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      const metric = (label, value) => `<div class="metric"><span class="muted">${label}</span><strong>${value}</strong></div>`;

      async function loadAccuracy() {
        $("accuracy-error").hidden = true;
        try {
          const response = await fetch("/api/accuracy/summary", { cache: "no-store" });
          const payload = await response.json();
          if (!response.ok || payload.api_status !== "ok") throw new Error(payload.error || `HTTP ${response.status}`);
          render(payload);
        } catch (error) {
          $("accuracy-error").hidden = false;
          $("accuracy-error").textContent = `策略成績單 API 暫時無法更新：${error.message}`;
        }
      }

      function render(payload) {
        const summary = payload.summary || {};
        $("accuracy-summary").innerHTML = [
          metric("樣本數", summary.sample_size || 0),
          metric("最低樣本門檻", summary.min_sample_size || 20),
          metric("樣本品質", sampleQualityLabel(summary.sample_quality)),
          metric("統計是否具意義", summary.is_statistically_meaningful ? "是" : "否"),
          metric("整體勝率", `${number(summary.win_rate)}%`),
          metric("平均報酬", `${number(summary.avg_return_pct)}%`),
          metric("平均最大回撤", `${number(summary.avg_max_drawdown_pct)}%`),
          metric("停損率", `${number(summary.stop_rate)}%`),
          metric("達標率", `${number(summary.target_rate)}%`),
          metric("A級勝率", `${number(summary.grade_a?.win_rate)}%`),
          metric("B+勝率", `${number(summary.grade_b_plus?.win_rate)}%`),
          metric("B+觸發後勝率", `${number(summary.grade_b_plus_triggered?.win_rate)}%`),
          metric("B+未觸發比例", `${number(payload.b_plus_lifecycle?.untriggered_ratio)}%`),
          metric("樣本提示", escapeHtml(summary.message || "")),
        ].join("");
        $("accuracy-status").innerHTML = table(payload.by_status || [], "進場狀態");
        $("accuracy-grade").innerHTML = table(payload.by_grade || [], "分級");
        $("accuracy-confidence").innerHTML = table(payload.by_confidence || [], "信心等級");
        $("accuracy-market").innerHTML = table(payload.by_market || [], "市場");
        renderDataCompleteness(payload.data_completeness || {});
        renderScorecard(payload.strategy_scorecard || {});
        renderEntryRadarScorecard(payload.entry_radar_scorecard || {});
        renderBreakoutTrapScorecard(payload.breakout_trap_scorecard || {});
        renderReviewChart(payload.review_tag_loss_distribution || {});
        renderMissed(payload.missed_rate_report || {});
        const suggestions = payload.model_suggestions || [];
        $("accuracy-suggestions").innerHTML = suggestions.length
          ? suggestions.map((item) => `<tr><td>${escapeHtml(item)}</td></tr>`).join("")
          : '<tr><td>目前沒有模型調整建議。</td></tr>';
      }

      function table(rows, label) {
        const body = rows.length ? rows.map((item) => `<tr>
          <td>${escapeHtml(item.group)}</td>
          <td>${item.sample_size}</td>
          <td>${escapeHtml(sampleQualityLabel(item.sample_quality))}</td>
          <td>${item.is_statistically_meaningful ? "是" : "否"}</td>
          <td>${number(item.win_rate)}%</td>
          <td>${number(item.avg_return_pct)}%</td>
          <td>${number(item.avg_max_gain_pct)}%</td>
          <td>${number(item.avg_max_drawdown_pct)}%</td>
          <td>${number(item.stop_rate)}%</td>
          <td>${number(item.target_rate)}%</td>
          <td>${escapeHtml(item.sample_message || "")}</td>
        </tr>`).join("") : `<tr><td colspan="11">目前沒有${label}統計資料。</td></tr>`;
        return `<table><thead><tr><th>${label}</th><th>樣本數</th><th>樣本品質</th><th>具統計意義</th><th>勝率</th><th>平均報酬</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>停損率</th><th>達標率</th><th>提示</th></tr></thead><tbody>${body}</tbody></table>`;
      }

      function renderDataCompleteness(data) {
        const missing = Array.isArray(data.missing_items) ? data.missing_items : [];
        $("accuracy-data-completeness").innerHTML = `
          <div class="summary">
            ${metric("成績樣本", data.sample_size || 0)}
            ${metric("20日窗口", data.has_20_day_window ? "有" : "不足")}
            ${metric("40日窗口", data.has_40_day_window ? "有" : "不足")}
            ${metric("60日窗口", data.has_60_day_window ? "有" : "不足")}
            ${metric("漏抓診斷", data.has_missed_diagnostic ? "有" : "不足")}
            ${metric("可否調參", data.ready_for_model_tuning ? "可初步觀察" : "不建議")}
          </div>
          <p class="muted">${escapeHtml(data.message || "資料完整度尚在累積。")}</p>
          <p class="muted"><strong>缺口：</strong>${escapeHtml(missing.length ? missing.join("、") : "暫無明顯缺口")}</p>
        `;
      }

      function sampleQualityLabel(value) {
        return {
          insufficient: "樣本不足",
          early: "初步樣本",
          meaningful: "具參考性",
          trusted: "較可信",
        }[value] || "樣本不足";
      }

      function renderScorecard(scorecard) {
        const windows = scorecard.windows || {};
        const rows = [];
        for (const [windowName, data] of Object.entries(windows)) {
          const groups = data.groups || {};
          for (const grade of ["A", "B+", "B", "high_risk", "avoid", "data_missing"]) {
            const item = groups[grade] || {};
            rows.push(`<tr>
              <td>${escapeHtml(windowName)}日</td>
              <td>${escapeHtml(grade)}</td>
              <td>${item.sample_size || 0}</td>
              <td>${item.verified || 0}</td>
              <td>${escapeHtml(sampleQualityLabel(item.sample_quality))}</td>
              <td>${number(item.trigger_rate)}%</td>
              <td>${number(item.win_rate)}%</td>
              <td>${number(item.avg_max_gain)}%</td>
              <td>${number(item.avg_max_drawdown)}%</td>
              <td>${number(item.stop_rate)}%</td>
              <td>${number(item.target_rate)}%</td>
              <td>${number(item.reward_risk_ratio)}</td>
              <td>${escapeHtml(item.sample_message || "")}</td>
            </tr>`);
          }
        }
        $("accuracy-scorecard").innerHTML = `<table><thead><tr><th>期間</th><th>類別</th><th>出現次數</th><th>已驗證</th><th>樣本品質</th><th>觸發率</th><th>勝率</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>停損率</th><th>停利率</th><th>平均賺賠比</th><th>提示</th></tr></thead><tbody>${rows.join("") || '<tr><td colspan="13">目前沒有策略成績資料。</td></tr>'}</tbody></table>`;
      }

      function renderEntryRadarScorecard(scorecard) {
        const windows = scorecard.windows || {};
        const window20 = windows["20"] || {};
        $("accuracy-entry-radar").innerHTML = `
          <div class="summary">
            ${metric("20日樣本", window20.sample_size || 0)}
            ${metric("20日已驗證", window20.verified || 0)}
            ${metric("樣本品質", sampleQualityLabel(window20.sample_quality))}
            ${metric("可否判斷", window20.is_statistically_meaningful ? "可初步觀察" : "樣本不足")}
          </div>
          <p class="muted">${escapeHtml(scorecard.message || "進場雷達只做驗證，不會自動調整模型。")}</p>
          <p class="muted">${escapeHtml(window20.message || "樣本不足，不建議依卡關原因調整模型。")}</p>
        `;
        const rows = [];
        for (const [windowName, data] of Object.entries(windows)) {
          for (const item of (data.rows || []).slice(0, 12)) {
            rows.push(`<tr>
              <td>${escapeHtml(windowName)}日</td>
              <td><strong>${escapeHtml(item.blocker_label || item.blocker_code)}</strong><br><span class="muted">${escapeHtml(item.blocker_code)}</span></td>
              <td>${item.sample_size || 0}</td>
              <td>${item.verified || 0}</td>
              <td>${escapeHtml(sampleQualityLabel(item.sample_quality))}</td>
              <td>${number(item.win_rate)}%</td>
              <td>${number(item.target_0_5_rate)}%</td>
              <td>${number(item.target_1_rate)}%</td>
              <td>${number(item.target_2_rate)}%</td>
              <td>${number(item.avg_max_gain)}%</td>
              <td>${number(item.avg_max_drawdown)}%</td>
              <td>${number(item.pullback_rate)}%</td>
              <td>${escapeHtml(item.interpretation || item.sample_message || "")}</td>
            </tr>`);
          }
        }
        $("accuracy-entry-radar-table").innerHTML = `<table><thead><tr><th>期間</th><th>最大卡關</th><th>出現次數</th><th>已驗證</th><th>樣本品質</th><th>1%勝率</th><th>0.5%命中</th><th>1%命中</th><th>2%命中</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>回撤率</th><th>解讀</th></tr></thead><tbody>${rows.join("") || '<tr><td colspan="13">目前沒有進場雷達成績資料。</td></tr>'}</tbody></table>`;
      }

      function renderBreakoutTrapScorecard(scorecard) {
        const windows = scorecard.windows || {};
        const window20 = windows["20"] || {};
        $("accuracy-breakout-trap").innerHTML = `
          <div class="summary">
            ${metric("20日樣本", window20.sample_size || 0)}
            ${metric("20日已驗證", window20.verified || 0)}
            ${metric("樣本品質", sampleQualityLabel(window20.sample_quality))}
            ${metric("可否判斷", window20.is_statistically_meaningful ? "可初步觀察" : "樣本不足")}
          </div>
          <p class="muted">${escapeHtml(scorecard.message || "真假突破診斷只做盤後驗證，不會自動調整模型。")}</p>
          <p class="muted">${escapeHtml(window20.message || "樣本不足，不建議依真假突破診斷調整模型。")}</p>
        `;
        const rows = [];
        for (const [windowName, data] of Object.entries(windows)) {
          for (const item of (data.rows || []).slice(0, 12)) {
            rows.push(`<tr>
              <td>${escapeHtml(windowName)}日</td>
              <td><strong>${escapeHtml(item.status_label || item.status)}</strong><br><span class="muted">${escapeHtml(item.status || "-")}</span></td>
              <td>${item.sample_size || 0}</td>
              <td>${item.verified || 0}</td>
              <td>${escapeHtml(sampleQualityLabel(item.sample_quality))}</td>
              <td>${number(item.target_0_5_rate)}%</td>
              <td>${number(item.target_1_rate)}%</td>
              <td>${number(item.target_2_rate)}%</td>
              <td>${number(item.pullback_rate)}%</td>
              <td>${number(item.avg_max_gain)}%</td>
              <td>${number(item.avg_max_drawdown)}%</td>
              <td>${escapeHtml(item.interpretation || item.sample_message || "")}</td>
            </tr>`);
          }
        }
        $("accuracy-breakout-trap-table").innerHTML = `<table><thead><tr><th>期間</th><th>診斷</th><th>出現次數</th><th>已驗證</th><th>樣本品質</th><th>0.5%命中</th><th>1%命中</th><th>2%命中</th><th>回撤率</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>解讀</th></tr></thead><tbody>${rows.join("") || '<tr><td colspan="12">目前沒有真假突破診斷成績資料。</td></tr>'}</tbody></table>`;
      }

      function renderReviewChart(distribution) {
        const rows = distribution.rows || [];
        if (!rows.length) {
          $("accuracy-review-chart").innerHTML = `<p class="muted">${escapeHtml(distribution.message || "目前尚無已覆盤的虧損交易，暫無心魔分佈。")}</p>`;
          return;
        }
        const colors = ["#b42318", "#f97316", "#7c3aed", "#175cd3", "#067647"];
        let offset = 0;
        const circles = rows.map((item, index) => {
          const pctValue = Number(item.pct || 0);
          const dash = `${pctValue} ${Math.max(0, 100 - pctValue)}`;
          const html = `<circle r="15.9155" cx="21" cy="21" stroke="${colors[index % colors.length]}" stroke-dasharray="${dash}" stroke-dashoffset="${-offset}"></circle>`;
          offset += pctValue;
          return html;
        }).join("");
        const legend = rows.map((item, index) => `<div class="review-legend-row">
          <span class="review-dot" style="background:${colors[index % colors.length]}"></span>
          <span>${escapeHtml(item.label)}</span>
          <strong>${item.count} 次｜${number(item.pct)}%</strong>
        </div>`).join("");
        $("accuracy-review-chart").innerHTML = `<div class="review-chart-layout">
          <svg class="review-pie" viewBox="0 0 42 42" aria-label="心魔分佈圓餅圖">
            <circle class="review-pie-bg" r="15.9155" cx="21" cy="21"></circle>
            ${circles}
          </svg>
          <div>
            <strong>虧損交易覆盤標籤</strong>
            <p class="muted">統計使用者在虧損平倉時勾選的檢討標籤，用來觀察最常見的錯誤原因。</p>
            <div class="review-legend">${legend}</div>
          </div>
        </div>`;
      }

      function renderMissed(report) {
        const seen = report.seen_but_filtered || {};
        const missedByPool = report.missed_by_pool || {};
        const regret = report.regret_after_close || {};
        $("accuracy-missed").innerHTML = [
          metric("強勢股總數", report.strong_stock_count || 0),
          metric("進入 A/B+/B", report.selected_count || 0),
          metric("強勢股未進 A/B+/B", `${number(report.not_in_ab_rate)}%`),
          metric("已看到但未推薦", seen.count || report.seen_but_filtered_count || 0),
          metric("已看到未推薦比例", `${number(report.seen_but_filtered_rate)}%`),
          metric("真漏抓", missedByPool.count || report.missed_by_pool_count || report.missed_count || 0),
          metric("真漏抓率", `${number(missedByPool.rate ?? report.missed_by_pool_rate ?? report.missed_rate)}%`),
          metric("盤後可惜漏掉率", `${number(regret.rate)}%`),
          metric("樣本提示", escapeHtml(report.message || "")),
        ].join("");
        const trueMissed = missedByPool.examples || report.missed_examples || [];
        const missedBody = trueMissed.length ? trueMissed.map((item) => `<tr>
          <td>${escapeHtml(item.date)}</td>
          <td>${escapeHtml(item.symbol)}｜${escapeHtml(item.name)}</td>
          <td>${number(item.change_pct)}%</td>
          <td>${number(item.turnover, 0)}</td>
          <td>${number(item.volume, 0)}</td>
          <td>${escapeHtml(item.reason_code)}</td>
        </tr>`).join("") : '<tr><td colspan="6">目前沒有漏抓案例，或樣本不足。</td></tr>';
        const filteredExamples = seen.examples || [];
        const filteredBody = filteredExamples.length ? filteredExamples.map((item) => `<tr>
          <td>${escapeHtml(item.date)}</td>
          <td>${escapeHtml(item.symbol)}｜${escapeHtml(item.name)}</td>
          <td>${number(item.change_pct)}%</td>
          <td>${escapeHtml(item.ai_grade || "-")}</td>
          <td>${escapeHtml(item.entry_status || "-")}</td>
          <td>${escapeHtml(item.reason_code || "-")}</td>
          <td>${number(item.max_gain_after_scan)}%</td>
        </tr>`).join("") : '<tr><td colspan="7">目前沒有已看到但未推薦案例，或樣本不足。</td></tr>';
        const regretBody = (regret.examples || []).length ? regret.examples.map((item) => `<tr>
          <td>${escapeHtml(item.date)}</td>
          <td>${escapeHtml(item.symbol)}｜${escapeHtml(item.name)}</td>
          <td>${escapeHtml(item.entry_status || "-")}</td>
          <td>${number(item.max_gain_after_scan)}%</td>
          <td>${number(item.max_drawdown_after_scan)}%</td>
          <td>${escapeHtml(item.verification_outcome || "-")}</td>
        </tr>`).join("") : '<tr><td colspan="6">需累積盤後驗證資料後才會列出。</td></tr>';
        $("accuracy-missed-examples").innerHTML = `
          <h3>真漏抓 missed_by_pool</h3>
          <table><thead><tr><th>日期</th><th>股票</th><th>漲幅</th><th>成交金額</th><th>成交量</th><th>真漏抓原因</th></tr></thead><tbody>${missedBody}</tbody></table>
          <h3>有看到但未推薦 seen_but_filtered</h3>
          <table><thead><tr><th>日期</th><th>股票</th><th>漲幅</th><th>分級</th><th>狀態</th><th>reason code</th><th>訊號後最大漲幅</th></tr></thead><tbody>${filteredBody}</tbody></table>
          <h3>盤後可惜漏掉 regret_after_close</h3>
          <p class="muted">${escapeHtml(regret.message || "需累積盤後驗證資料。")}</p>
          <table><thead><tr><th>日期</th><th>股票</th><th>原狀態</th><th>訊號後最大漲幅</th><th>訊號後最大回撤</th><th>驗證結果</th></tr></thead><tbody>${regretBody}</tbody></table>
        `;
      }

      loadAccuracy();
    })();
    """


def _static_paper_fallback_payload(primary_error: str, fallback_error: str) -> dict:
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    generated_at = now.isoformat(timespec="seconds")
    accounts = [
        {
            "id": "TW",
            "market": "TW",
            "currency": "NTD",
            "initial_cash": 1_000_000,
            "cash_balance": 1_000_000,
            "equity": 1_000_000,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "max_drawdown": 0,
            "created_at": generated_at,
            "updated_at": generated_at,
        },
        {
            "id": "US",
            "market": "US",
            "currency": "USD",
            "initial_cash": 30_000,
            "cash_balance": 30_000,
            "equity": 30_000,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "max_drawdown": 0,
            "created_at": generated_at,
            "updated_at": generated_at,
        },
    ]
    last_error = f"{primary_error}; fallback: {fallback_error}"
    return {
        "api_status": "error",
        "data_source_status": "error",
        "errors": [last_error],
        "message": "虛擬交易資料庫暫時忙碌，系統會在下一次更新自動重試。",
        "engine_version": "paper_trading_v1_manual_trade",
        "generated_at": generated_at,
        "run": {
            "opened": 0,
            "closed": 0,
            "skipped": 0,
            "positions": 0,
            "recommendations_scanned": 0,
            "executable_triggered": 0,
            "last_error": last_error,
        },
        "accounts": accounts,
        "positions": [],
        "trades": [],
        "skipped": [],
        "skipped_trades": [],
        "b_plus_triggers": [],
        "decision_summary": {
            "version": "decision_center_v1_2026-06-13",
            "summary_text": "目前虛擬交易資料庫暫時忙碌，系統僅能顯示空狀態。請稍後重試或等待下一次更新。",
            "manual_trades": 0,
            "system_trades": 0,
            "open_positions": 0,
            "today_realized_pnl": 0,
            "b_plus_waiting": 0,
            "b_plus_triggered": 0,
            "practice_available": False,
            "disclaimer": "本系統僅供資料整理、策略追蹤、虛擬交易與回測，不構成投資建議，也不保證獲利。",
        },
        "performance": {
            "total_trades": 0,
            "closed_trades": 0,
            "system_trades": 0,
            "manual_trades": 0,
            "win_rate": 0,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "by_market": {},
            "by_source": {},
            "by_grade": {},
            "by_entry_status": {},
        },
        "refresh_interval_seconds": 300,
        "debug": {
            "app_version": "unknown",
            "engine_version": "paper_trading_v1_manual_trade",
            "generated_at": generated_at,
            "refresh_interval": 300,
            "api_status": "error",
            "accounts_count": len(accounts),
            "open_positions_count": 0,
            "trades_count": 0,
            "skipped_count": 0,
            "recommendations_scanned_count": 0,
            "executable_triggered_count": 0,
            "b_plus_waiting_count": 0,
            "b_plus_ready_count": 0,
            "manual_trades_count": 0,
            "system_trades_count": 0,
            "open_manual_positions_count": 0,
            "last_manual_trade_created_at": "",
            "last_close_trade_status": "",
            "quote_api_status": "unavailable",
            "last_error": last_error,
        },
        "disclaimer": "本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利；本頁不會送出任何真實委託。",
    }


def _extract_body(html: str) -> str:
    lower = html.lower()
    start = lower.find("<body>")
    end = lower.rfind("</body>")
    if start == -1 or end == -1:
        return html
    return html[start + len("<body>"):end]


def _extract_style(html: str) -> str:
    lower = html.lower()
    start = lower.find("<style>")
    end = lower.find("</style>", start)
    if start == -1 or end == -1:
        return ""
    return html[start + len("<style>"):end]


def _session_cookie(token: str) -> str:
    return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def datetime_now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")
