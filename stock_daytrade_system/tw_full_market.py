from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.resilience import record_source_health, retry_sync


TW_FULL_MARKET_VERSION = "tw_full_market_v1_official_pool_2026-06-18"
TAIPEI = ZoneInfo("Asia/Taipei")

TWSE_STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAILY_URLS = (
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
)

EXCLUDED_NAME_KEYWORDS = (
    "ETF",
    "ETN",
    "指數",
    "基金",
    "債",
    "期貨",
    "反1",
    "正2",
    "槓桿",
    "元大台灣50",
    "特",
    "權證",
    "購",
    "售",
)


@dataclass(frozen=True)
class FullMarketQuote:
    symbol: str
    code: str
    name: str
    market: str
    sector: str
    is_common_stock: bool
    is_etf: bool
    is_warrant: bool
    is_preferred: bool
    is_daytrade_eligible: Optional[bool]
    price: Optional[float]
    change: Optional[float]
    change_pct: Optional[float]
    volume: Optional[float]
    turnover: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    trade_date: str
    source: str
    exclude_reason: str = ""
    source_reasons: tuple[str, ...] = ()

    def to_watch_symbol(self) -> WatchSymbol:
        suffix = ".TW" if self.market == "TWSE" else ".TWO"
        return WatchSymbol(f"{self.code}{suffix}", self.name, self.sector or "full_market")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FullMarketResult:
    version: str
    generated_at: str
    pool_symbols: list[WatchSymbol]
    candidate_symbols: list[WatchSymbol]
    quotes: list[FullMarketQuote]
    candidate_quotes: list[FullMarketQuote]
    summary: dict
    source_status: dict

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "pool_symbols": [item.__dict__ for item in self.pool_symbols],
            "candidate_symbols": [item.__dict__ for item in self.candidate_symbols],
            "quotes": [item.to_dict() for item in self.quotes],
            "candidate_quotes": [item.to_dict() for item in self.candidate_quotes],
            "summary": self.summary,
            "source_status": self.source_status,
        }


def build_tw_full_market_pool(
    project_root: Path,
    *,
    now: Optional[datetime] = None,
    max_candidates: int = 100,
    min_turnover: float = 10_000_000,
    min_volume: float = 100_000,
) -> FullMarketResult:
    captured_at = now or datetime.now(TAIPEI)
    cache_dir = project_root / "data" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    quotes, source_status = _fetch_quotes_with_cache(cache_dir)
    common_quotes = [
        item for item in quotes
        if item.is_common_stock
        and not item.is_etf
        and not item.is_warrant
        and not item.is_preferred
        and not item.exclude_reason
    ]
    liquid_quotes = [
        item for item in common_quotes
        if (item.volume or 0) >= min_volume
        and (item.turnover or 0) >= min_turnover
        and item.price is not None
    ]
    candidates = _select_candidates(liquid_quotes, max_candidates=max_candidates)
    pool_symbols = [item.to_watch_symbol() for item in liquid_quotes]
    candidate_symbols = [item.to_watch_symbol() for item in candidates]
    summary = {
        "total_raw": len(quotes),
        "pool_symbols": len(pool_symbols),
        "candidate_symbols": len(candidate_symbols),
        "excluded_etf": sum(1 for item in quotes if item.is_etf),
        "excluded_warrant": sum(1 for item in quotes if item.is_warrant),
        "excluded_preferred": sum(1 for item in quotes if item.is_preferred),
        "excluded_low_liquidity": max(len(common_quotes) - len(liquid_quotes), 0),
        "twse_count": sum(1 for item in pool_symbols if item.symbol.endswith(".TW")),
        "tpex_count": sum(1 for item in pool_symbols if item.symbol.endswith(".TWO")),
        "source_ok": bool(source_status.get("twse_ok") or source_status.get("tpex_ok") or quotes),
    }
    return FullMarketResult(
        version=TW_FULL_MARKET_VERSION,
        generated_at=captured_at.isoformat(timespec="seconds"),
        pool_symbols=pool_symbols,
        candidate_symbols=candidate_symbols,
        quotes=common_quotes,
        candidate_quotes=candidates,
        summary=summary,
        source_status=source_status,
    )


def _fetch_quotes_with_cache(cache_dir: Path) -> tuple[list[FullMarketQuote], dict]:
    status = {
        "twse_ok": False,
        "tpex_ok": False,
        "twse_error": "",
        "tpex_error": "",
        "twse_used_cache": False,
        "tpex_used_cache": False,
        "twse_rows": 0,
        "tpex_rows": 0,
        "tpex_endpoint": "",
        "used_cache": False,
        "retry_count": 0,
    }
    quotes: list[FullMarketQuote] = []
    try:
        payload = _fetch_json(TWSE_STOCK_DAY_ALL_URL, status=status)
        parsed = _parse_twse_rows(payload)
        quotes.extend(parsed)
        status["twse_ok"] = True
        status["twse_rows"] = len(parsed)
        _write_cache(cache_dir / "twse_stock_day_all.json", payload)
        record_source_health("twse", "OK", success_count=len(parsed), message="TWSE 上市資料擷取成功。")
    except Exception as exc:
        status["twse_error"] = str(exc)
        cached = _read_cache(cache_dir / "twse_stock_day_all.json")
        if cached is not None:
            status["used_cache"] = True
            status["twse_used_cache"] = True
            parsed = _parse_twse_rows(cached)
            status["twse_rows"] = len(parsed)
            quotes.extend(parsed)
            record_source_health(
                "twse",
                "PARTIAL",
                success_count=len(parsed),
                failure_count=1,
                error=str(exc),
                message="TWSE 抓取失敗，已使用 cache 降級。",
            )
        else:
            record_source_health("twse", "ERROR", failure_count=1, error=str(exc), message="TWSE 抓取失敗且無 cache。")
    tpex_error = ""
    for url in TPEX_DAILY_URLS:
        try:
            payload = _fetch_json(url, status=status)
            parsed = _parse_tpex_rows(payload)
            if parsed:
                quotes.extend(parsed)
                status["tpex_ok"] = True
                status["tpex_rows"] = len(parsed)
                status["tpex_endpoint"] = url
                _write_cache(cache_dir / "tpex_daily_quotes.json", payload)
                record_source_health("tpex", "OK", success_count=len(parsed), message="TPEX 上櫃資料擷取成功。")
                break
        except Exception as exc:
            tpex_error = str(exc)
    if not status["tpex_ok"]:
        status["tpex_error"] = tpex_error or "TPEX endpoint unavailable"
        cached = _read_cache(cache_dir / "tpex_daily_quotes.json")
        if cached is not None:
            status["used_cache"] = True
            status["tpex_used_cache"] = True
            parsed = _parse_tpex_rows(cached)
            status["tpex_rows"] = len(parsed)
            quotes.extend(parsed)
            record_source_health(
                "tpex",
                "PARTIAL",
                success_count=len(parsed),
                failure_count=1,
                error=status["tpex_error"],
                message="TPEX 抓取失敗，已使用 cache 降級。",
            )
        else:
            record_source_health("tpex", "ERROR", failure_count=1, error=status["tpex_error"], message="TPEX 抓取失敗且無 cache。")
    status["health_status"] = {"twse": status["twse_ok"], "tpex": status["tpex_ok"]}
    return quotes, status


def _fetch_json(url: str, *, status: dict) -> list[dict]:
    attempts = {"count": 0}

    def operation() -> list[dict]:
        attempts["count"] += 1
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 AI-stock-daytrade/0.1",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            try:
                completed = subprocess.run(
                    ["curl", "-L", "--max-time", "20", "-s", url],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if completed.stdout.strip():
                    return json.loads(completed.stdout)
            except Exception:
                pass
            raise RuntimeError(f"failed to fetch {url}: {exc}") from exc

    try:
        return retry_sync(
            operation,
            source="twse" if "twse" in url else "tpex",
            operation_name=f"official quote fetch {url}",
        )
    finally:
        status["retry_count"] = int(status.get("retry_count", 0)) + max(attempts["count"] - 1, 0)


def _parse_twse_rows(rows: Iterable[dict]) -> list[FullMarketQuote]:
    parsed = []
    for row in rows or []:
        code = str(row.get("Code") or "").strip()
        name = str(row.get("Name") or "").strip()
        quote = _quote_from_row(
            code=code,
            name=name,
            market="TWSE",
            source="TWSE STOCK_DAY_ALL",
            trade_date=str(row.get("Date") or ""),
            volume=_num(row.get("TradeVolume")),
            turnover=_num(row.get("TradeValue")),
            open_price=_num(row.get("OpeningPrice")),
            high=_num(row.get("HighestPrice")),
            low=_num(row.get("LowestPrice")),
            close=_num(row.get("ClosingPrice")),
            change=_num(row.get("Change")),
        )
        if quote:
            parsed.append(quote)
    return parsed


def _parse_tpex_rows(rows: Iterable[dict]) -> list[FullMarketQuote]:
    parsed = []
    for row in rows or []:
        code = _first(row, "SecuritiesCompanyCode", "Code", "代號", "股票代號")
        name = _first(row, "CompanyName", "Name", "名稱", "股票名稱")
        quote = _quote_from_row(
            code=str(code or "").strip(),
            name=str(name or "").strip(),
            market="TPEX",
            source="TPEX OpenAPI",
            trade_date=str(_first(row, "Date", "資料日期", "date") or ""),
            volume=_num(_first(row, "TradingShares", "TradeVolume", "成交股數", "成交量")),
            turnover=_num(_first(row, "TransactionAmount", "TradeValue", "成交金額")),
            open_price=_num(_first(row, "Open", "OpeningPrice", "開盤")),
            high=_num(_first(row, "High", "HighestPrice", "最高")),
            low=_num(_first(row, "Low", "LowestPrice", "最低")),
            close=_num(_first(row, "Close", "ClosingPrice", "收盤")),
            change=_num(_first(row, "Change", "漲跌")),
        )
        if quote:
            parsed.append(quote)
    return parsed


def _quote_from_row(
    *,
    code: str,
    name: str,
    market: str,
    source: str,
    trade_date: str,
    volume: Optional[float],
    turnover: Optional[float],
    open_price: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
    change: Optional[float],
) -> Optional[FullMarketQuote]:
    if not code or not name:
        return None
    is_etf = _is_etf(code, name)
    is_warrant = _is_warrant(code, name)
    is_preferred = _is_preferred(code, name)
    is_common = code.isdigit() and len(code) == 4 and not code.startswith("00")
    previous = close - change if close is not None and change is not None else None
    change_pct = (change / previous * 100) if previous and previous > 0 and change is not None else None
    exclude_reason = ""
    if not is_common:
        exclude_reason = "not_common_stock"
    elif is_etf:
        exclude_reason = "etf"
    elif is_warrant:
        exclude_reason = "warrant"
    elif is_preferred:
        exclude_reason = "preferred"
    elif close is None or volume is None or turnover is None:
        exclude_reason = "data_missing"
    return FullMarketQuote(
        symbol=f"{code}{'.TW' if market == 'TWSE' else '.TWO'}",
        code=code,
        name=name,
        market=market,
        sector="full_market",
        is_common_stock=is_common,
        is_etf=is_etf,
        is_warrant=is_warrant,
        is_preferred=is_preferred,
        is_daytrade_eligible=None,
        price=close,
        change=change,
        change_pct=round(change_pct, 2) if change_pct is not None else None,
        volume=volume,
        turnover=turnover,
        high=high,
        low=low,
        close=close,
        trade_date=trade_date,
        source=source,
        exclude_reason=exclude_reason,
        source_reasons=tuple(_source_reasons(change_pct, turnover, volume, close, high)),
    )


def _select_candidates(quotes: list[FullMarketQuote], *, max_candidates: int) -> list[FullMarketQuote]:
    selected: dict[str, FullMarketQuote] = {}
    changed = sorted(quotes, key=lambda item: item.change_pct or -999, reverse=True)
    turnover = sorted(quotes, key=lambda item: item.turnover or 0, reverse=True)
    volume = sorted(quotes, key=lambda item: item.volume or 0, reverse=True)
    for bucket in (
        [item for item in changed if (item.change_pct or 0) >= 3][:100],
        turnover[:160],
        volume[:120],
        [item for item in quotes if _near_limit_up(item)][:80],
    ):
        for item in bucket:
            if item.source_reasons:
                selected[item.symbol] = item
            if len(selected) >= max_candidates:
                break
        if len(selected) >= max_candidates:
            break
    rows = list(selected.values())
    rows.sort(key=lambda item: (-(item.change_pct or -999), -(item.turnover or 0), item.symbol))
    return rows[:max_candidates]


def _source_reasons(
    change_pct: Optional[float],
    turnover: Optional[float],
    volume: Optional[float],
    close: Optional[float],
    high: Optional[float],
) -> list[str]:
    reasons: list[str] = []
    if change_pct is not None and change_pct >= 3:
        reasons.append("今日漲幅大於3%")
    if turnover is not None and turnover >= 100_000_000:
        reasons.append("成交金額前段")
    if volume is not None and volume >= 3_000_000:
        reasons.append("成交量放大")
    if close and high and close >= high * 0.98:
        reasons.append("收盤接近高點")
    return reasons


def _near_limit_up(item: FullMarketQuote) -> bool:
    return bool((item.change_pct or 0) >= 7 or (item.close and item.high and item.close >= item.high * 0.995))


def _is_etf(code: str, name: str) -> bool:
    return code.startswith("00") or any(key in name.upper() for key in EXCLUDED_NAME_KEYWORDS[:10])


def _is_warrant(code: str, name: str) -> bool:
    return len(code) > 4 or "權證" in name or "購" in name or "售" in name


def _is_preferred(code: str, name: str) -> bool:
    return any(ch.isalpha() for ch in code) or "甲特" in name or "特" in name


def _first(row: dict, *keys: str):
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _num(value) -> Optional[float]:
    if value in (None, "", "--", "----"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _write_cache(path: Path, payload: list[dict]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_cache(path: Path) -> Optional[list[dict]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
