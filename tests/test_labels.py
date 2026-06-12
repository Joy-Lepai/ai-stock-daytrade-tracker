import unittest

from stock_daytrade_system.labels import sector_label, stock_label


class LabelTests(unittest.TestCase):
    def test_sector_label_is_bilingual_for_known_sector(self):
        self.assertEqual(sector_label("semiconductor"), "半導體 / semiconductor")

    def test_sector_label_falls_back_to_raw_value(self):
        self.assertEqual(sector_label("custom"), "custom / custom")

    def test_stock_label_combines_name_and_symbol(self):
        self.assertEqual(stock_label("康霈生技", "6919.TW"), "康霈生技 / 6919.TW")


if __name__ == "__main__":
    unittest.main()
