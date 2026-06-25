import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_daytrade_system.tw_symbols import market_suffix_for_code, normalize_tw_stock_symbol


class TWSymbolTests(unittest.TestCase):
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
            self.assertEqual(normalize_tw_stock_symbol("8936", cache_dir=cache), "8936.TWO")

    def test_market_suffix_for_code_falls_back_to_tw_when_cache_is_unavailable(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "tpex_daily_quotes.json").write_text("not json", encoding="utf-8")

            self.assertEqual(market_suffix_for_code("9999", cache_dir=cache), ".TW")
            self.assertEqual(normalize_tw_stock_symbol("9999", cache_dir=cache), "9999.TW")

    def test_normalize_keeps_existing_suffix_and_uppercases(self):
        self.assertEqual(normalize_tw_stock_symbol("8936.two"), "8936.TWO")
        self.assertEqual(normalize_tw_stock_symbol("2330.tw"), "2330.TW")


if __name__ == "__main__":
    unittest.main()
