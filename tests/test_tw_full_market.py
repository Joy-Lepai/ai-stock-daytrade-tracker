import unittest

from stock_daytrade_system.tw_full_market import _parse_twse_rows, _select_candidates


class TWFullMarketTests(unittest.TestCase):
    def test_twse_parser_filters_common_stock_metadata(self):
        rows = [
            {
                "Date": "1150617",
                "Code": "6770",
                "Name": "力積電",
                "TradeVolume": "5000000",
                "TradeValue": "100000000",
                "OpeningPrice": "18",
                "HighestPrice": "20",
                "LowestPrice": "18",
                "ClosingPrice": "20",
                "Change": "1.0",
            },
            {
                "Date": "1150617",
                "Code": "0050",
                "Name": "元大台灣50",
                "TradeVolume": "1000000",
                "TradeValue": "100000000",
                "OpeningPrice": "100",
                "HighestPrice": "101",
                "LowestPrice": "99",
                "ClosingPrice": "101",
                "Change": "1",
            },
            {
                "Date": "1150617",
                "Code": "9941A",
                "Name": "裕融甲特",
                "TradeVolume": "100000",
                "TradeValue": "5000000",
                "OpeningPrice": "50",
                "HighestPrice": "50",
                "LowestPrice": "50",
                "ClosingPrice": "50",
                "Change": "0",
            },
        ]

        parsed = _parse_twse_rows(rows)
        by_code = {item.code: item for item in parsed}

        self.assertTrue(by_code["6770"].is_common_stock)
        self.assertFalse(by_code["6770"].is_etf)
        self.assertEqual(by_code["6770"].symbol, "6770.TW")
        self.assertEqual(by_code["0050"].exclude_reason, "not_common_stock")
        self.assertEqual(by_code["9941A"].exclude_reason, "not_common_stock")

    def test_select_candidates_prefers_strong_and_liquid_stocks(self):
        quotes = [
            item for item in _parse_twse_rows(
                [
                    {
                        "Date": "1150617",
                        "Code": "6770",
                        "Name": "力積電",
                        "TradeVolume": "5000000",
                        "TradeValue": "100000000",
                        "OpeningPrice": "18",
                        "HighestPrice": "20",
                        "LowestPrice": "18",
                        "ClosingPrice": "20",
                        "Change": "1.0",
                    },
                    {
                        "Date": "1150617",
                        "Code": "2330",
                        "Name": "台積電",
                        "TradeVolume": "500000",
                        "TradeValue": "200000000",
                        "OpeningPrice": "1000",
                        "HighestPrice": "1005",
                        "LowestPrice": "995",
                        "ClosingPrice": "1000",
                        "Change": "0",
                    },
                ]
            )
            if not item.exclude_reason
        ]

        selected = _select_candidates(quotes, max_candidates=10)

        self.assertIn("6770.TW", {item.symbol for item in selected})
        self.assertTrue(selected[0].source_reasons)


if __name__ == "__main__":
    unittest.main()
