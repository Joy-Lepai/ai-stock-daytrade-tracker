from __future__ import annotations

import json
from pathlib import Path

from stock_daytrade_system.tw_full_market import _parse_tpex_rows, _parse_twse_rows


DEFAULT_TW_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"


def market_suffix_for_code(code: str, *, cache_dir: Path | None = None) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        return ".TW"
    cache_root = cache_dir or DEFAULT_TW_CACHE_DIR
    try:
        twse_cache = cache_root / "twse_stock_day_all.json"
        if twse_cache.exists():
            twse_codes = {item.code for item in _parse_twse_rows(json.loads(twse_cache.read_text(encoding="utf-8")))}
            if normalized in twse_codes:
                return ".TW"
        tpex_cache = cache_root / "tpex_daily_quotes.json"
        if tpex_cache.exists():
            tpex_codes = {item.code for item in _parse_tpex_rows(json.loads(tpex_cache.read_text(encoding="utf-8")))}
            if normalized in tpex_codes:
                return ".TWO"
    except Exception:
        return ".TW"
    return ".TW"


def normalize_tw_stock_symbol(value: str, *, cache_dir: Path | None = None) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    if raw.endswith(".TW") or raw.endswith(".TWO"):
        return raw
    if raw.isdigit():
        return f"{raw}{market_suffix_for_code(raw, cache_dir=cache_dir)}"
    return raw
