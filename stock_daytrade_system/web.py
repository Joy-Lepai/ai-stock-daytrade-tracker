from __future__ import annotations

import json
import secrets
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.auth import AuthConfig, load_auth_config, verify_password
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
from stock_daytrade_system.us_service import build_us_dashboard_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH = PROJECT_ROOT / "config" / "auth.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"
SESSION_COOKIE = "ai_stock_session"


class WebApp:
    def __init__(self, auth_config: Optional[AuthConfig], report_dir: Path, require_auth: bool = False) -> None:
        self.auth_config = auth_config
        self.report_dir = report_dir
        self.require_auth = require_auth
        self.sessions: Dict[str, str] = {}

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


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    auth_path: Path = DEFAULT_AUTH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    require_auth: bool = False,
) -> None:
    auth_config = load_auth_config(auth_path) if require_auth else None
    app = WebApp(auth_config, report_dir, require_auth=require_auth)

    class Handler(StockWebHandler):
        web_app = app

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving stock tracker on http://{host}:{port}/")
    server.serve_forever()


class StockWebHandler(BaseHTTPRequestHandler):
    web_app: WebApp

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
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
            self._send_html(self._dashboard_html())
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
        from stock_daytrade_system.cli import DEFAULT_CONFIG, run_tracker

        run_tracker(DEFAULT_CONFIG, self.web_app.report_dir, "6mo", "1d", "5m", 3)
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

    def _dashboard_html(self) -> str:
        latest = latest_tracker_file(self.web_app.report_dir)
        if latest is None:
            return render_shell(
                "<p class=\"empty\">尚未產生追蹤器資料。</p>",
                active_file=None,
                show_logout=self.web_app.require_auth,
            )
        html = latest.read_text(encoding="utf-8")
        body = _extract_body(html)
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
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
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
            <th>信心</th><th>衝突</th><th>生命週期 Lifecycle</th><th>理由</th><th>風險理由</th>
          </tr>
        </thead>
        <tbody id="us-candidates"><tr><td colspan="17">讀取中...</td></tr></tbody>
      </table>
    </div>
    <h2 id="debug">Debug</h2>
    <div class="debug-block"><table><tbody id="us-debug"></tbody></table></div>
  </main>
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
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
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
          <input id="manual-symbol" name="symbol" placeholder="NVDA 或 2330.TW" autocomplete="off">
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
    <h2 id="debug">Debug</h2>
    <div class="debug-block"><table><tbody id="paper-debug"></tbody></table></div>
  </main>
  <script>{paper_dashboard_script()}</script>
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
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
    </div>
    <div class="topbar-actions">{logout_link}</div>
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
    <h2>依進場狀態</h2>
    <div class="table-wrap" id="accuracy-status"></div>
    <h2>依分級</h2>
    <div class="table-wrap" id="accuracy-grade"></div>
    <h2>依信心等級</h2>
    <div class="table-wrap" id="accuracy-confidence"></div>
    <h2>依市場</h2>
    <div class="table-wrap" id="accuracy-market"></div>
    <h2>模型調整建議</h2>
    <div class="table-wrap"><table><tbody id="accuracy-suggestions"></tbody></table></div>
  </main>
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
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/accuracy">策略成績單</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
      {file_text}
      <span id="refresh-status" class="refresh-status" data-interval="{refresh_interval_seconds}" data-session="{_escape(clock.session)}">上次更新：{_escape(clock.now_local.strftime('%H:%M:%S'))}</span>
      <form method="post" action="/refresh"><button type="submit">更新</button></form>
      {logout_link}
    </div>
  </nav>
  {content}
  <script>
    (() => {{
      const status = document.getElementById("refresh-status");
      if (!status) return;
      const intervalSeconds = Number(status.dataset.interval || "300");
      let remaining = intervalSeconds;

      const renderCountdown = () => {{
        const minutes = Math.floor(remaining / 60);
        const seconds = String(remaining % 60).padStart(2, "0");
        status.textContent = `上次更新：${{new Date().toLocaleTimeString()}}｜下一次更新 ${{minutes}}:${{seconds}}`;
      }};

      const refreshDashboard = async () => {{
        status.textContent = "正在更新資料...";
        try {{
          const response = await fetch("/refresh", {{
            method: "POST",
            credentials: "same-origin",
            headers: {{ "X-Requested-With": "fetch" }},
          }});
          if (!response.ok && response.status !== 0) throw new Error(`HTTP ${{response.status}}`);
          window.location.href = "/dashboard";
        }} catch (error) {{
          remaining = intervalSeconds;
          status.textContent = "自動更新失敗，請按更新";
        }}
      }};

      renderCountdown();
      window.setInterval(() => {{
        remaining -= 1;
        if (remaining <= 0) {{
          refreshDashboard();
          return;
        }}
        renderCountdown();
      }}, 1000);
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
    .nav-links { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .nav-links a { color:var(--muted); text-decoration:none; padding:5px 8px; border-radius:6px; }
    .nav-links a:hover { color:var(--accent); background:#eef4ff; }
    .topbar form { margin:0; }
    .refresh-status { white-space:nowrap; font-size:13px; color:var(--muted); }
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
    .signal-card { border-top:1px solid var(--line); padding:10px 0; }
    .signal-card:first-of-type { border-top:0; padding-top:0; }
    .signal-title { font-weight:750; }
    .signal-meta { color:var(--muted); font-size:12px; white-space:normal; }
    .signal-next { margin-top:6px; color:var(--accent); font-weight:700; }
    @media (max-width:760px) { .topbar { align-items:flex-start; flex-direction:column; } .topbar-actions { flex-wrap:wrap; } }
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
    @media (max-width:980px) { .manual-form { grid-template-columns:repeat(2,minmax(150px,1fr)); } .manual-reason { grid-column:span 2; } }
    @media (max-width:760px) { .paper-page { padding-left:14px; padding-right:14px; } .paper-header { flex-direction:column; } .manual-form { grid-template-columns:1fr; } .manual-reason { grid-column:span 1; } .close-controls { min-width:220px; } }
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
          metric("executable 可執行", summary.executable || 0),
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
          ["可執行訊號摘要", `${counts.executable || 0} 檔 executable`, data.executable_summary],
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
          ["executable", "可執行 executable"],
          ["b_plus", "B+ 練習觀察"],
          ["waiting", "等待確認"],
          ["risk", "風險過高 / 避開"],
        ];
        $("us-signal-center").innerHTML = `<h2>訊號中心</h2><div class="signal-grid">${columns.map(([key, title]) => {
          const items = Array.isArray(center[key]) ? center[key] : [];
          const cards = items.length ? items.map(signalCard).join("") : '<p class="muted">目前沒有標的。</p>';
          return `<div class="signal-column"><h3>${escapeHtml(title)}（${items.length}）</h3>${cards}</div>`;
        }).join("")}</div>`;
      }

      function signalCard(item) {
        const label = `${escapeHtml(item.symbol)}｜${escapeHtml(item.name_zh)}${item.name_en ? `｜${escapeHtml(item.name_en)}` : ""}`;
        const meta = `${escapeHtml(item.grade)}｜${escapeHtml(item.entry_status)}｜${escapeHtml(item.lifecycle_status)}｜Readiness ${escapeHtml(item.trigger_readiness)}`;
        const metrics = `現價 ${number(item.current_price)}｜VWAP ${number(item.vwap)}｜量比 ${number(item.volume_ratio)}x｜停損 ${number(item.stop_loss)}｜停利 ${number(item.target_price)}`;
        return `<div class="signal-card">
          <div class="signal-title">${label}</div>
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
          $("us-candidates").innerHTML = '<tr><td colspan="17">目前沒有美股候選資料。</td></tr>';
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
            <td><div class="symbol-main">${escapeHtml(item.symbol)}｜${escapeHtml(item.short_name_zh)}｜${escapeHtml(item.name_en)}</div><div class="symbol-sub">${escapeHtml(item.sector_zh)}｜${escapeHtml(item.description_zh)}</div></td>
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
      };
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
          <td class="notes">${escapeHtml(item.entry_reason || item.exit_reason || "")}</td>
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

      async function closeTrade(tradeId, exitPrice) {
        setFormStatus("手動平倉中...", true);
        try {
          const response = await fetch("/api/paper/close-trade", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ trade_id: tradeId, exit_price: exitPrice }),
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
          setFormStatus(payload.message, true);
          await loadDashboard();
        } catch (error) {
          setFormStatus(error.message, false);
        }
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
          closeTrade(current.dataset.tradeId, null);
          return;
        }
        if (specified) {
          const input = document.querySelector(`.manual-close-price[data-trade-id="${CSS.escape(specified.dataset.tradeId)}"]`);
          closeTrade(specified.dataset.tradeId, Number(input?.value || 0));
        }
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
        const suggestions = payload.model_suggestions || [];
        $("accuracy-suggestions").innerHTML = suggestions.length
          ? suggestions.map((item) => `<tr><td>${escapeHtml(item)}</td></tr>`).join("")
          : '<tr><td>目前沒有模型調整建議。</td></tr>';
      }

      function table(rows, label) {
        const body = rows.length ? rows.map((item) => `<tr>
          <td>${escapeHtml(item.group)}</td>
          <td>${item.sample_size}</td>
          <td>${item.is_statistically_meaningful ? "是" : "否"}</td>
          <td>${number(item.win_rate)}%</td>
          <td>${number(item.avg_return_pct)}%</td>
          <td>${number(item.avg_max_gain_pct)}%</td>
          <td>${number(item.avg_max_drawdown_pct)}%</td>
          <td>${number(item.stop_rate)}%</td>
          <td>${number(item.target_rate)}%</td>
        </tr>`).join("") : `<tr><td colspan="9">目前沒有${label}統計資料。</td></tr>`;
        return `<table><thead><tr><th>${label}</th><th>樣本數</th><th>具統計意義</th><th>勝率</th><th>平均報酬</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>停損率</th><th>達標率</th></tr></thead><tbody>${body}</tbody></table>`;
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
