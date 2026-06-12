import unittest

from stock_daytrade_system.cli import _select_auto_symbols
from stock_daytrade_system.cmoney import CMoneyRanking
from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.scoring import CandidateScore


def candidate(symbol, direction="做多觀察", score=5, suggested_shares=1000):
    return CandidateScore(
        symbol=symbol,
        name=symbol,
        sector="semiconductor",
        direction=direction,
        score=score,
        close=100,
        day_change_pct=1,
        avg_volume=1000000,
        atr=2,
        previous_high=101,
        previous_low=99,
        trigger_price=101,
        stop_loss=99,
        target_price=104,
        risk_per_share=2,
        suggested_shares=suggested_shares,
        reasons=[],
    )


class CliSelectionTests(unittest.TestCase):
    def test_select_auto_symbols_returns_long_candidates_only(self):
        symbols = [
            WatchSymbol("2330.TW", "台積電", "semiconductor"),
            WatchSymbol("2317.TW", "鴻海", "electronics"),
            WatchSymbol("2454.TW", "聯發科", "semiconductor"),
        ]
        candidates = [
            candidate("2330.TW", "做多觀察", 6),
            candidate("2317.TW", "做空觀察", 7),
            candidate("2454.TW", "做多觀察", 5),
        ]

        selected = _select_auto_symbols(symbols, candidates, max_per_side=20)

        self.assertEqual([item.symbol for item in selected], ["2330.TW", "2454.TW"])

    def test_select_auto_symbols_limits_long_candidates(self):
        symbols = [WatchSymbol(f"{index}.TW", str(index), "semiconductor") for index in range(25)]
        candidates = [candidate(item.symbol, "做多觀察", 25 - index) for index, item in enumerate(symbols)]

        selected = _select_auto_symbols(symbols, candidates, max_per_side=20)

        self.assertEqual(len(selected), 20)

    def test_select_auto_symbols_prioritizes_tradable_long_candidates(self):
        symbols = [
            WatchSymbol("high-score.TW", "High", "semiconductor"),
            WatchSymbol("tradable.TW", "Tradable", "semiconductor"),
            WatchSymbol("backup.TW", "Backup", "semiconductor"),
        ]
        candidates = [
            candidate("high-score.TW", "做多觀察", score=8, suggested_shares=0),
            candidate("tradable.TW", "做多觀察", score=6, suggested_shares=1000),
            candidate("backup.TW", "做多觀察", score=5, suggested_shares=1000),
        ]

        selected = _select_auto_symbols(symbols, candidates, max_per_side=2)

        self.assertEqual([item.symbol for item in selected], ["tradable.TW", "backup.TW"])

    def test_select_auto_symbols_prioritizes_institutional_ranked_candidates(self):
        symbols = [
            WatchSymbol("high-score.TW", "High", "semiconductor"),
            WatchSymbol("ranked.TW", "Ranked", "institutional_buy"),
        ]
        candidates = [
            candidate("high-score.TW", "做多觀察", score=8, suggested_shares=1000),
            candidate("ranked.TW", "做多觀察", score=6, suggested_shares=1000),
        ]
        rankings = {
            "ranked.TW": CMoneyRanking(
                rank=1,
                date="2026/06/11",
                symbol="ranked.TW",
                code="ranked",
                name="Ranked",
                foreign_buy_million=100,
                investment_buy_million=0,
                dealers_buy_million=0,
                total_buy_million=100,
            )
        }

        selected = _select_auto_symbols(symbols, candidates, max_per_side=2, institutional_rankings=rankings)

        self.assertEqual([item.symbol for item in selected], ["ranked.TW", "high-score.TW"])


if __name__ == "__main__":
    unittest.main()
