import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_daytrade_system.cmoney import CMoneyRanking, market_suffix_for_code, merge_cmoney_symbols, rankings_by_symbol
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

    def test_market_suffix_for_code_uses_official_cache_for_tpex(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "twse_stock_day_all.json").write_text(
                '[{"Code":"2330","Name":"台積電","ClosingPrice":"100","TradeVolume":"1000","TradeValue":"100000"}]',
                encoding="utf-8",
            )
            (cache / "tpex_daily_quotes.json").write_text(
                '[{"SecuritiesCompanyCode":"8936","CompanyName":"國統","Close":"50","TradingShares":"1000","TransactionAmount":"50000"}]',
                encoding="utf-8",
            )

            self.assertEqual(market_suffix_for_code("2330", cache_dir=cache), ".TW")
            self.assertEqual(market_suffix_for_code("8936", cache_dir=cache), ".TWO")

    def test_market_suffix_for_code_falls_back_to_tw_when_cache_is_unavailable(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "tpex_daily_quotes.json").write_text("not json", encoding="utf-8")

            self.assertEqual(market_suffix_for_code("9999", cache_dir=cache), ".TW")


if __name__ == "__main__":
    unittest.main()
