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
            },
        )

        self.assertEqual(quote.symbol, "6770.TW")
        self.assertEqual(quote.name, "力積電")
        self.assertEqual(quote.price, 72.5)
        self.assertEqual(quote.previous_close, 70.2)
        self.assertEqual(quote.change_pct, 3.28)
        self.assertEqual(quote.quote_time, "2026-06-18 09:22:15")
        self.assertEqual(quote.status, "ok")

    def test_parse_twse_quote_row_handles_missing_current_price(self):
        quote = parse_twse_quote_row("6770.TW", {"z": "-", "y": "70.20"})

        self.assertIsNone(quote.price)
        self.assertEqual(quote.status, "partial")

    def test_parse_twse_quote_row_accepts_colon_time(self):
        quote = parse_twse_quote_row("6770.TW", {"z": "73.90", "y": "70.20", "d": "20260618", "t": "12:05:55"})

        self.assertEqual(quote.quote_time, "2026-06-18 12:05:55")


if __name__ == "__main__":
    unittest.main()
