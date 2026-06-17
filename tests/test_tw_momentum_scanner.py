import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.long_model import build_long_candidates
from stock_daytrade_system.scoring import MarketBias
from stock_daytrade_system.tw_momentum_scanner import (
    build_momentum_universe,
    momentum_seed_symbols,
    normalize_tw_symbol,
    scan_momentum_candidates,
    scan_single_symbol,
)


def daily_bars(close=100, previous_close=98, volume=1_000_000):
    start = datetime(2026, 1, 1)
    bars = []
    for index in range(24):
        price = previous_close - 2 + index * 0.05
        bars.append(Bar(start + timedelta(days=index), price - 1, price + 1, price - 2, price, volume))
    bars.append(Bar(start + timedelta(days=24), previous_close - 1, previous_close + 1, previous_close - 2, previous_close, volume))
    bars.append(Bar(start + timedelta(days=25), close - 1, close + 1, close - 2, close, volume))
    return bars


def intraday_bars(last=105, volume=1_800_000, vwap_base=101):
    start = datetime(2026, 1, 26, 9, 0)
    return [
        Bar(start, vwap_base, vwap_base + 1, vwap_base - 1, vwap_base, volume / 3),
        Bar(start + timedelta(minutes=5), vwap_base + 1, vwap_base + 2, vwap_base, vwap_base + 1, volume / 3),
        Bar(start + timedelta(minutes=10), last - 1, last + 1, last - 2, last, volume / 3),
    ]


class TWMomentumScannerTests(unittest.TestCase):
    def test_seed_universe_contains_user_reported_symbols(self):
        symbols = {item.symbol for item in momentum_seed_symbols()}

        self.assertIn("6770.TW", symbols)
        self.assertIn("3016.TW", symbols)
        self.assertIn("2327.TW", symbols)
        self.assertIn("6191.TW", symbols)
        self.assertIn("6919.TW", symbols)
        self.assertIn("8110.TW", symbols)

    def test_normalizes_manual_symbol_and_alias(self):
        self.assertEqual(normalize_tw_symbol("6770"), "6770.TW")
        self.assertEqual(normalize_tw_symbol("力積電"), "6770.TW")

    def test_momentum_universe_adds_missing_momentum_symbols(self):
        universe = build_momentum_universe([WatchSymbol("2330.TW", "台積電", "semiconductor")])
        symbols = {item.symbol for item in universe}

        self.assertIn("2330.TW", symbols)
        self.assertIn("6770.TW", symbols)

    def test_momentum_stock_can_enter_long_model(self):
        symbol = WatchSymbol("6770.TW", "力積電", "semiconductor")
        daily = {symbol.symbol: daily_bars(close=105, previous_close=98)}
        intraday = {symbol.symbol: intraday_bars(last=106, volume=2_000_000)}

        candidates = build_long_candidates(
            [symbol],
            daily,
            intraday,
            [],
            [],
            MarketBias(score=2, direction="偏多", notes=[]),
        )
        result = scan_momentum_candidates([symbol], daily, intraday, candidates)

        self.assertEqual(result.summary.model_scored, 1)
        self.assertEqual(result.items[0].symbol, "6770.TW")
        self.assertIn(result.items[0].ai_grade, {"A", "B+", "B", "C", "D"})
        self.assertIsNotNone(result.items[0].risk_score)
        self.assertIsNotNone(result.items[0].upper_shadow_pct)
        self.assertIsNotNone(result.items[0].confidence_score)

    def test_high_risk_reason_for_overextended_stock(self):
        symbol = WatchSymbol("3016.TW", "嘉晶", "semiconductor")
        item = scan_single_symbol(
            symbol,
            daily_bars(close=110, previous_close=100),
            intraday_bars(last=112, volume=2_000_000, vwap_base=100),
            SimpleNamespace(
                grade="C",
                entry_status="high_risk",
                risk_score=70,
                confidence_score=80,
                above_vwap=True,
                volume_ratio=2.0,
                last_price=112,
                vwap=100,
            ),
        )

        self.assertEqual(item.entry_status, "high_risk")
        self.assertIn("追價風險高", item.not_selected_reason)

    def test_wait_volume_reason_when_volume_is_low(self):
        symbol = WatchSymbol("2327.TW", "國巨", "passive_components")
        item = scan_single_symbol(
            symbol,
            daily_bars(close=102, previous_close=100),
            intraday_bars(last=102, volume=500_000),
            SimpleNamespace(
                grade="D",
                entry_status="wait_volume",
                risk_score=20,
                confidence_score=65,
                above_vwap=True,
                volume_ratio=0.5,
                last_price=102,
                vwap=101,
            ),
        )

        self.assertEqual(item.not_selected_reason, "量比不足")

    def test_wait_vwap_reason_when_below_vwap(self):
        symbol = WatchSymbol("6191.TW", "精成科", "pcb")
        item = scan_single_symbol(
            symbol,
            daily_bars(close=102, previous_close=100),
            intraday_bars(last=100, volume=1_500_000, vwap_base=103),
            SimpleNamespace(
                grade="D",
                entry_status="wait_vwap",
                risk_score=20,
                confidence_score=65,
                above_vwap=False,
                volume_ratio=1.2,
                last_price=100,
                vwap=103,
            ),
        )

        self.assertEqual(item.not_selected_reason, "未站上 VWAP")


if __name__ == "__main__":
    unittest.main()
