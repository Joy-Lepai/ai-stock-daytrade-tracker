import unittest

from stock_daytrade_system.cmoney import CMoneyRanking, merge_cmoney_symbols, rankings_by_symbol
from stock_daytrade_system.config import WatchSymbol


class CMoneyTests(unittest.TestCase):
    def test_rankings_by_symbol_indexes_tw_symbols(self):
        ranking = CMoneyRanking(
            rank=1,
            date="2026/06/11",
            symbol="2330.TW",
            code="2330",
            name="台積電",
            foreign_buy_million=100,
            investment_buy_million=50,
            dealers_buy_million=10,
            total_buy_million=160,
        )

        self.assertIs(rankings_by_symbol([ranking])["2330.TW"], ranking)

    def test_merge_cmoney_symbols_adds_missing_ranked_stocks(self):
        ranking = CMoneyRanking(
            rank=1,
            date="2026/06/11",
            symbol="9999.TW",
            code="9999",
            name="法人熱門股",
            foreign_buy_million=100,
            investment_buy_million=50,
            dealers_buy_million=10,
            total_buy_million=160,
        )

        merged = merge_cmoney_symbols(
            [WatchSymbol("2330.TW", "台積電", "semiconductor")],
            [ranking],
        )

        self.assertEqual([item.symbol for item in merged], ["2330.TW", "9999.TW"])
        self.assertEqual(merged[-1].sector, "institutional_buy")


if __name__ == "__main__":
    unittest.main()
