import unittest

from stock_daytrade_system.us_symbols import us_symbol_map, us_watchlist


class USSymbolsTests(unittest.TestCase):
    def test_watchlist_has_required_chinese_metadata(self):
        symbols = us_symbol_map()

        self.assertEqual(len(us_watchlist()), 16)
        self.assertEqual(symbols["NVDA"].short_name_zh, "輝達")
        self.assertIn("AI 晶片", symbols["NVDA"].description_zh)
        self.assertEqual(symbols["QQQ"].sector_zh, "指數型 ETF")
        self.assertTrue(symbols["SPY"].is_etf)


if __name__ == "__main__":
    unittest.main()
