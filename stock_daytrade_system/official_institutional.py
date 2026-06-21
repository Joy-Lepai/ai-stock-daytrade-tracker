from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.institutional_context import InstitutionalContext
from stock_daytrade_system.resilience import record_source_health, retry_sync


OFFICIAL_INSTITUTIONAL_VERSION = "official_institutional_v1_twse_tpex_background_2026-06-20"
TAIPEI = ZoneInfo("Asia/Taipei")
TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={date}&selectType=ALLBUT0999&response=json"
TPEX_DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?date={date}&type=Daily&response=json"


@dataclass(frozen=True)
class OfficialInstitutionalRecord:
    symbol: str
    code: str
    name: str
    market: str
    trade_date: str
    foreign_buy_sell: float
    investment_trust_buy_sell: float
    dealer_buy_sell: float
    institutional_total_buy_sell: float
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OfficialInstitutionalResult:
    version: str
    generated_at: str
    contexts: dict[str, dict]
    records_count: int
    symbols_count: int
    latest_dates: dict[str, str]
    source_status: dict

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_official_institutional_contexts(
    project_root: Path,
    *,
    now: Optional[datetime] = None,
    lookback_days: int = 10,
    max_trading_days: int = 5,
) -> OfficialInstitutionalResult:
    captured_at = now or datetime.now(TAIPEI)
    cache_dir = project_root / "data" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = [captured_at.date() - timedelta(days=offset) for offset in range(max(lookback_days, max_trading_days))]
    twse_records, twse_status = _fetch_market_records(
        market="TWSE",
        dates=dates,
        cache_dir=cache_dir,
        max_trading_days=max_trading_days,
    )
    tpex_records, tpex_status = _fetch_market_records(
        market="TPEX",
        dates=dates,
        cache_dir=cache_dir,
        max_trading_days=max_trading_days,
    )
    records = twse_records + tpex_records
    contexts = _build_contexts(records)
    source_status = {
        "twse": twse_status,
        "tpex": tpex_status,
        "ok": bool(twse_status.get("ok") or tpex_status.get("ok") or contexts),
        "used_cache": bool(twse_status.get("used_cache") or tpex_status.get("used_cache")),
    }
    return OfficialInstitutionalResult(
        version=OFFICIAL_INSTITUTIONAL_VERSION,
        generated_at=captured_at.isoformat(timespec="seconds"),
        contexts=contexts,
        records_count=len(records),
        symbols_count=len(contexts),
        latest_dates={
            "twse": str(twse_status.get("latest_date") or ""),
            "tpex": str(tpex_status.get("latest_date") or ""),
        },
        source_status=source_status,
    )


def _fetch_market_records(
    *,
    market: str,
    dates: list[date],
    cache_dir: Path,
    max_trading_days: int,
) -> tuple[list[OfficialInstitutionalRecord], dict]:
    source = "twse_institutional" if market == "TWSE" else "tpex_institutional"
    status = {
        "ok": False,
        "used_cache": False,
        "rows": 0,
        "trading_days": 0,
        "latest_date": "",
        "error": "",
    }
    all_records: list[OfficialInstitutionalRecord] = []
    last_error = ""
    for day in dates:
        if len({item.trade_date for item in all_records}) >= max_trading_days:
            break
        try:
            payload = _fetch_market_payload(market, day)
            records = _parse_records(market, payload)
            if not records:
                continue
            all_records.extend(records)
            status["ok"] = True
            status["rows"] = int(status["rows"]) + len(records)
            status["trading_days"] = len({item.trade_date for item in all_records})
            status["latest_date"] = status["latest_date"] or records[0].trade_date
            _write_cache(cache_dir / _cache_name(market, day), payload)
        except Exception as exc:
            last_error = str(exc)
            cached = _read_cache(cache_dir / _cache_name(market, day))
            if cached is None:
                continue
            try:
                records = _parse_records(market, cached)
            except Exception as cache_exc:
                last_error = str(cache_exc)
                continue
            if records:
                all_records.extend(records)
                status["used_cache"] = True
                status["rows"] = int(status["rows"]) + len(records)
                status["trading_days"] = len({item.trade_date for item in all_records})
                status["latest_date"] = status["latest_date"] or records[0].trade_date
    if all_records:
        health_status = "PARTIAL" if status["used_cache"] or int(status["trading_days"]) < max_trading_days else "OK"
        record_source_health(
            source,
            health_status,
            success_count=len(all_records),
            partial_count=1 if health_status == "PARTIAL" else 0,
            error=last_error if status["used_cache"] else "",
            message=f"{market} 官方法人買賣超擷取成功 {len(all_records)} 筆。",
        )
    else:
        status["error"] = last_error or f"{market} institutional endpoint returned no rows"
        record_source_health(
            source,
            "ERROR",
            failure_count=1,
            error=status["error"],
            message=f"{market} 官方法人買賣超擷取失敗且無 cache。",
        )
    return all_records, status


def _fetch_market_payload(market: str, day: date) -> dict:
    if market == "TWSE":
        url = TWSE_T86_URL.format(date=day.strftime("%Y%m%d"))
    else:
        url = TPEX_DAILY_TRADE_URL.format(date=day.strftime("%Y/%m/%d"))

    def operation() -> dict:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 AI-stock-daytrade/0.1", "Accept": "application/json"},
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

    return retry_sync(
        operation,
        source="twse_institutional" if market == "TWSE" else "tpex_institutional",
        operation_name=f"{market} official institutional fetch",
    )


def _parse_records(market: str, payload: dict) -> list[OfficialInstitutionalRecord]:
    if market == "TWSE":
        if payload.get("stat") != "OK":
            return []
        trade_date = _normalize_date(payload.get("date"))
        parsed = [_parse_twse_row(row, trade_date) for row in payload.get("data", [])]
        return [item for item in parsed if item is not None]
    tables = payload.get("tables") or []
    if not tables:
        return []
    table = tables[0]
    rows = table.get("data") or []
    trade_date = _normalize_date(table.get("date") or payload.get("date"))
    parsed = [_parse_tpex_row(row, trade_date) for row in rows]
    return [item for item in parsed if item is not None]


def _parse_twse_row(row: list, trade_date: str) -> Optional[OfficialInstitutionalRecord]:
    if len(row) < 19:
        return None
    code = str(row[0]).strip()
    name = str(row[1]).strip()
    if not _is_common_stock_code(code):
        return None
    foreign = _num(row[4]) + _num(row[7])
    trust = _num(row[10])
    dealer = _num(row[11])
    total = _num(row[18])
    return OfficialInstitutionalRecord(
        symbol=f"{code}.TW",
        code=code,
        name=name,
        market="TWSE",
        trade_date=trade_date,
        foreign_buy_sell=foreign,
        investment_trust_buy_sell=trust,
        dealer_buy_sell=dealer,
        institutional_total_buy_sell=total,
        source="TWSE T86 official",
    )


def _parse_tpex_row(row: list, trade_date: str) -> Optional[OfficialInstitutionalRecord]:
    if len(row) < 24:
        return None
    code = str(row[0]).strip()
    name = str(row[1]).strip()
    if not _is_common_stock_code(code):
        return None
    foreign = _num(row[10])
    trust = _num(row[13])
    dealer = _num(row[22])
    total = _num(row[23])
    return OfficialInstitutionalRecord(
        symbol=f"{code}.TWO",
        code=code,
        name=name,
        market="TPEX",
        trade_date=trade_date,
        foreign_buy_sell=foreign,
        investment_trust_buy_sell=trust,
        dealer_buy_sell=dealer,
        institutional_total_buy_sell=total,
        source="TPEX dailyTrade official",
    )


def _build_contexts(records: Iterable[OfficialInstitutionalRecord]) -> dict[str, dict]:
    by_symbol: dict[str, list[OfficialInstitutionalRecord]] = {}
    for record in records:
        by_symbol.setdefault(record.symbol, []).append(record)
    contexts: dict[str, dict] = {}
    for symbol, items in by_symbol.items():
        ordered = sorted(items, key=lambda item: item.trade_date, reverse=True)
        latest = ordered[0]
        context = _context_from_records(ordered)
        contexts[symbol] = context.to_dict()
        contexts[symbol]["official_records_count"] = len(ordered)
        contexts[symbol]["official_market"] = latest.market
        contexts[symbol]["unit"] = "股"
    return contexts


def _context_from_records(records: list[OfficialInstitutionalRecord]) -> InstitutionalContext:
    latest = records[0]
    total = latest.institutional_total_buy_sell
    values = (latest.foreign_buy_sell, latest.investment_trust_buy_sell, latest.dealer_buy_sell)
    positives = sum(1 for value in values if value > 0)
    negatives = sum(1 for value in values if value < 0)
    if total > 0 and positives >= 2 and negatives == 0:
        trend = "bullish"
        label = "籌碼偏多"
        reason = "官方三大法人合計買超，且主要法人方向偏一致；只能作為背景支持。"
    elif total < 0 and negatives >= 2:
        trend = "bearish"
        label = "籌碼偏弱"
        reason = "官方三大法人合計賣超或主要法人偏賣，需保守看待。"
    elif positives and negatives:
        trend = "mixed"
        label = "籌碼分歧"
        reason = "官方三大法人方向不一致，籌碼僅作背景參考。"
    else:
        trend = "neutral"
        label = "籌碼中性"
        reason = "官方三大法人買賣方向沒有明顯一致性，不能作為進場依據。"
    return InstitutionalContext(
        foreign_buy_sell=round(latest.foreign_buy_sell, 2),
        investment_trust_buy_sell=round(latest.investment_trust_buy_sell, 2),
        dealer_buy_sell=round(latest.dealer_buy_sell, 2),
        institutional_total_buy_sell=round(latest.institutional_total_buy_sell, 2),
        foreign_3d_sum=_sum(records[:3], "foreign_buy_sell", minimum=3),
        foreign_5d_sum=_sum(records[:5], "foreign_buy_sell", minimum=5),
        investment_trust_3d_sum=_sum(records[:3], "investment_trust_buy_sell", minimum=3),
        investment_trust_5d_sum=_sum(records[:5], "investment_trust_buy_sell", minimum=5),
        dealer_3d_sum=_sum(records[:3], "dealer_buy_sell", minimum=3),
        dealer_5d_sum=_sum(records[:5], "dealer_buy_sell", minimum=5),
        institutional_trend=trend,
        institutional_label=label,
        institutional_reason=reason + " 單位為股，法人資料不會直接產生強烈買多。",
        institutional_data_date=latest.trade_date,
        institutional_data_status="ok" if len(records) >= 5 else "partial",
        source=f"{latest.source} + official history",
    )


def _sum(records: list[OfficialInstitutionalRecord], attr: str, *, minimum: int) -> Optional[float]:
    if len(records) < minimum:
        return None
    return round(sum(float(getattr(item, attr) or 0) for item in records), 2)


def _normalize_date(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if "/" in text:
        parts = text.split("/")
        if len(parts) == 3:
            year = int(parts[0])
            if year < 1911:
                year += 1911
            return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return text


def _is_common_stock_code(code: str) -> bool:
    return code.isdigit() and len(code) == 4 and not code.startswith("00")


def _num(value: object) -> float:
    text = str(value or "").replace(",", "").replace("--", "0").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _cache_name(market: str, day: date) -> str:
    return f"{market.lower()}_official_institutional_{day.strftime('%Y%m%d')}.json"


def _write_cache(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_cache(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
