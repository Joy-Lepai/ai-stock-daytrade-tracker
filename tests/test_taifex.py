import unittest

from stock_daytrade_system.taifex import parse_future_quotes


HTML = """
<p class="h3 fw-bold fs-mid tL">日期：2026/06/12</p>
2026/06/11&nbsp;&nbsp;15:00~次日05:00 盤後交易時段行情表
<table>
<tr>
  <th>契約</th><th>到期<br>月份<br>(週別)</th><th>開盤價</th><th>最高價</th><th>最低價</th>
  <th>最後<br>成交價</th><th>漲跌價</th><th>漲跌%</th><th>*成交量</th><th>結算價</th>
  <th>*未沖銷契約量</th><th>最後<br>最佳買價</th><th>最後<br>最佳賣價</th><th>歷史最高價</th><th>歷史最低價</th>
</tr>
<tr>
  <td><div align="center">TX</div></td><td><div align="center">202606</div></td>
  <td>43375</td><td>44596</td><td>43000</td><td>44382</td>
  <td><span class="red">▲1163</span></td><td><span class="red">▲2.69%</span></td>
  <td>88271</td><td>-</td><td>-</td><td>44385</td><td>44389</td><td>46994</td><td>20819</td>
</tr>
</table>
"""


class TaifexParserTests(unittest.TestCase):
    def test_parses_tx_future_quote(self):
        quotes = parse_future_quotes(HTML)

        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].product, "TX")
        self.assertEqual(quotes[0].contract_month, "202606")
        self.assertEqual(quotes[0].trade_date, "2026/06/12")
        self.assertEqual(quotes[0].session, "盤後")
        self.assertEqual(quotes[0].last, 44382)
        self.assertEqual(quotes[0].change_pct, 2.69)


if __name__ == "__main__":
    unittest.main()
