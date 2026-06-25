import unittest

from stock_daytrade_system.tw_realtime_quote import parse_twse_quote_row


class TWRealtimeQuoteTests(unittest.TestCase):
    def test_parse_twse_quote_row_extracts_price_change_and_time(self):
        quote = parse_twse_quote_row(
            "6770.TW",
            {
                "n": "力積電",
                "z": "72.50",
                "y": "70.20",
                "d": "20260618",
                "t": "092215",
                "a": "72.60_72.70_72.80_72.90_73.00_",
                "b": "72.50_72.40_72.30_72.20_72.10_",
                "f": "20_30_40_50_60_",
                "g": "100_90_80_70_60_",
            },
        )

        self.assertEqual(quote.symbol, "6770.TW")
        self.assertEqual(quote.name, "力積電")
        self.assertEqual(quote.price, 72.5)
        self.assertEqual(quote.previous_close, 70.2)
        self.assertEqual(quote.change_pct, 3.28)
        self.assertEqual(quote.quote_time, "2026-06-18 09:22:15")
        self.assertEqual(quote.status, "ok")
        self.assertEqual(quote.five_level_status, "available")
        self.assertEqual(quote.bid_price, 72.5)
        self.assertEqual(quote.ask_price, 72.6)
        self.assertEqual(len(quote.bid_levels), 5)
        self.assertEqual(len(quote.ask_levels), 5)
        self.assertGreater(quote.orderbook_imbalance, 0)

    def test_parse_twse_quote_row_handles_missing_current_price(self):
        quote = parse_twse_quote_row("6770.TW", {"z": "-", "y": "70.20"})

        self.assertIsNone(quote.price)
        self.assertEqual(quote.status, "partial")

    def test_parse_twse_quote_row_accepts_colon_time(self):
        quote = parse_twse_quote_row("6770.TW", {"z": "73.90", "y": "70.20", "d": "20260618", "t": "12:05:55"})

        self.assertEqual(quote.quote_time, "2026-06-18 12:05:55")

    def test_parse_twse_quote_row_normalizes_numeric_tpex_symbol(self):
        quote = parse_twse_quote_row("8936", {"z": "50.00", "y": "49.00"})

        self.assertEqual(quote.symbol, "8936.TWO")

    def test_parse_twse_quote_row_marks_limit_up_bid_only(self):
        quote = parse_twse_quote_row(
            "6919.TW",
            {
                "n": "康霈*",
                "z": "109.00",
                "y": "99.10",
                "u": "109.00",
                "w": "89.20",
                "a": "-",
                "b": "109.0000_108.5000_108.0000_107.5000_107.0000_",
                "f": "-",
                "g": "7127_755_731_1342_889_",
            },
        )

        self.assertEqual(quote.five_level_status, "limit_up_bid_only")
        self.assertTrue(quote.is_limit_up_locked)
        self.assertEqual(len(quote.ask_levels), 0)
        self.assertEqual(len(quote.bid_levels), 5)


if __name__ == "__main__":
    unittest.main()
