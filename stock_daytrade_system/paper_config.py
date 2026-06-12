from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperTradingConfig:
    initial_cash_tw: float = 1_000_000.0
    initial_cash_us: float = 30_000.0
    max_position_pct: float = 0.20
    max_risk_per_trade_pct: float = 0.01
    max_open_positions: int = 5
    max_positions_per_sector: int = 2
    default_take_profit_pct: float = 0.02
    default_stop_loss_pct: float = 0.01
    trailing_start_pct: float = 0.015
    trailing_pullback_pct: float = 0.008
    allow_fractional_us_shares: bool = True
    force_exit_before_close_minutes: int = 5


DEFAULT_PAPER_CONFIG = PaperTradingConfig()
