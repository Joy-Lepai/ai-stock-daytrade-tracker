import unittest

from stock_daytrade_system.official_institutional import (
    OfficialInstitutionalRecord,
    _build_contexts,
    _parse_tpex_row,
    _parse_twse_row,
)


class OfficialInstitutionalTests(unittest.TestCase):
    def test_parse_twse_t86_row(self):
        row = [
            "6919",
            "康霈生技",
            "1,000",
            "100",
            "900",
            "0",
            "0",
            "0",
            "2,000",
            "500",
            "1,500",
            "-300",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "2,100",
        ]

        record = _parse_twse_row(row, "2026-06-18")

        self.assertIsNotNone(record)
        self.assertEqual(record.symbol, "6919.TW")
        self.assertEqual(record.foreign_buy_sell, 900)
        self.assertEqual(record.investment_trust_buy_sell, 1500)
        self.assertEqual(record.dealer_buy_sell, -300)
        self.assertEqual(record.institutional_total_buy_sell, 2100)

    def test_parse_tpex_daily_trade_row(self):
        row = [
            "8936",
            "國統",
            "1,000",
            "200",
            "800",
            "0",
            "0",
            "0",
            "1,000",
            "200",
            "800",
            "300",
            "100",
            "200",
            "10",
            "5",
            "5",
            "80",
            "20",
            "60",
            "90",
            "25",
            "65",
            "1,065",
        ]

        record = _parse_tpex_row(row, "2026-06-18")

        self.assertIsNotNone(record)
        self.assertEqual(record.symbol, "8936.TWO")
        self.assertEqual(record.foreign_buy_sell, 800)
        self.assertEqual(record.investment_trust_buy_sell, 200)
        self.assertEqual(record.dealer_buy_sell, 65)
        self.assertEqual(record.institutional_total_buy_sell, 1065)

    def test_build_contexts_calculates_3d_5d_sums(self):
        records = [
            OfficialInstitutionalRecord("6919.TW", "6919", "康霈生技", "TWSE", f"2026-06-{day:02d}", 100, 10, 1, 111, "TWSE")
            for day in range(18, 13, -1)
        ]

        context = _build_contexts(records)["6919.TW"]

        self.assertEqual(context["institutional_label"], "籌碼偏多")
        self.assertEqual(context["foreign_3d_sum"], 300)
        self.assertEqual(context["foreign_5d_sum"], 500)
        self.assertEqual(context["unit"], "股")
        self.assertFalse(context["can_upgrade_signal"])


if __name__ == "__main__":
    unittest.main()
