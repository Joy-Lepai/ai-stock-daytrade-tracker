from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class WatchSymbol:
    symbol: str
    name: str
    sector: str = "unknown"


@dataclass(frozen=True)
class MarketConfig:
    timezone: str
    premarket_run_time: str
    benchmark: str
    taiwan_futures: str
    us_market_symbols: List[str]


@dataclass(frozen=True)
class RiskConfig:
    min_price: float
    min_avg_volume: float
    max_loss_per_trade: float
    round_lot_size: int
    max_candidates_per_side: int


@dataclass(frozen=True)
class AppConfig:
    market: MarketConfig
    risk: RiskConfig
    auto_universe: List[WatchSymbol]
    manual_symbols: List[WatchSymbol]
    fugle_priority_symbols: List[str]
    symbols: List[WatchSymbol]


def load_config(path: Path) -> AppConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    auto_universe = [WatchSymbol(**item) for item in raw.get("auto_universe", raw.get("symbols", []))]
    manual_symbols = [WatchSymbol(**item) for item in raw.get("manual_symbols", [])]
    fugle_priority_symbols = _dedupe_text(
        [str(item).strip().upper() for item in raw.get("fugle_priority_symbols", []) if str(item).strip()]
        + _env_list("FUGLE_PRIORITY_SYMBOLS")
    )
    symbols = _dedupe_symbols(auto_universe + manual_symbols)
    return AppConfig(
        market=MarketConfig(**raw["market"]),
        risk=RiskConfig(**raw["risk"]),
        auto_universe=auto_universe,
        manual_symbols=manual_symbols,
        fugle_priority_symbols=fugle_priority_symbols,
        symbols=symbols,
    )


def config_to_dict(config: AppConfig) -> Dict[str, Any]:
    return {
        "market": config.market.__dict__,
        "risk": config.risk.__dict__,
        "auto_universe": [symbol.__dict__ for symbol in config.auto_universe],
        "manual_symbols": [symbol.__dict__ for symbol in config.manual_symbols],
        "fugle_priority_symbols": list(config.fugle_priority_symbols),
        "symbols": [symbol.__dict__ for symbol in config.symbols],
    }


def _dedupe_symbols(symbols: List[WatchSymbol]) -> List[WatchSymbol]:
    seen = set()
    result: List[WatchSymbol] = []
    for item in symbols:
        if item.symbol in seen:
            continue
        seen.add(item.symbol)
        result.append(item)
    return result


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name, "")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _dedupe_text(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
