from datetime import datetime, timedelta
import unittest

from stock_daytrade_system.data import Bar
from stock_daytrade_system.market_context import build_market_indicators
from stock_daytrade_system.taifex import TaifexFutureQuote


def bars():
    return [
        Bar(datetime(2026, 1, 1) + timedelta(days=index), 100 + index, 102 + index, 99 + index, 100 + index, 1_000)
        for index in range(3)
    ]


class MarketContextTests(unittest.TestCase):
    def test_builds_us_and_taifex_indicators(self):
        quote = TaifexFutureQuote(
            product="TX",
            contract_month="202606",
            trade_date="2026/06/12",
            session="盤後",
            open=100,
            high=110,
            low=95,
            last=108,
            change=8,
            change_pct=8,
            volume=1234,
            settlement=None,
            open_interest=None,
            bid=107,
            ask=109,
        )

        result = build_market_indicators({"^GSPC": bars()}, quote)

        self.assertEqual(result[0].group, "前一日美股 / US previous close")
        self.assertEqual(result[0].symbol, "^GSPC")
        self.assertEqual(result[-1].group, "開盤前台指期 / TAIFEX TX")
        self.assertEqual(result[-1].status, "偏多")


if __name__ == "__main__":
    unittest.main()
