import unittest

from stock_daytrade_system.tw_full_market import _parse_tpex_rows, _parse_twse_rows, _select_candidates


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

    def test_tpex_parser_reads_official_openapi_fields(self):
        rows = [
            {
                "Date": "1150618",
                "SecuritiesCompanyCode": "8936",
                "CompanyName": "國統",
                "Close": "58.10",
                "Change": "+3.00",
                "Open": "55.30",
                "High": "58.80",
                "Low": "55.20",
                "TradingShares": "8888183",
                "TransactionAmount": "509950416",
            },
            {
                "Date": "1150618",
                "SecuritiesCompanyCode": "00679B",
                "CompanyName": "元大美債20年",
                "Close": "27.04",
                "Change": "+0.23",
                "Open": "26.95",
                "High": "27.04",
                "Low": "26.93",
                "TradingShares": "41151682",
                "TransactionAmount": "1110348899",
            },
        ]

        parsed = _parse_tpex_rows(rows)
        by_code = {item.code: item for item in parsed}

        self.assertEqual(by_code["8936"].symbol, "8936.TWO")
        self.assertEqual(by_code["8936"].market, "TPEX")
        self.assertEqual(by_code["8936"].volume, 8888183)
        self.assertEqual(by_code["8936"].turnover, 509950416)
        self.assertEqual(by_code["8936"].close, 58.1)
        self.assertAlmostEqual(by_code["8936"].change_pct or 0, 5.44, places=2)
        self.assertEqual(by_code["00679B"].exclude_reason, "not_common_stock")


if __name__ == "__main__":
    unittest.main()
