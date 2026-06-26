# 部署後驗收清單

這份清單用在每次 `Push` 與 Render `Deploy latest commit` 後。目標不是找更多推薦，而是確認公開站可信、資料可信、前台不誤導。

## 1. 版本鏈路

1. 執行：

   ```bash
   python3 scripts/check_release_readiness.py --base-url https://stock.letslepai.com
   ```

2. 必須確認：

   - `worktree clean` 是 PASS。
   - `local pushed to origin/main` 是 PASS。
   - `public runtime reachable` 是 PASS。
   - `public runtime matches local HEAD` 是 PASS。
   - 若 `public tracker matches runtime` 失敗，先跑完整刷新或重新部署，不要用舊 dashboard 看盤。

## 2. 公開功能驗收

執行：

```bash
./scripts/check_public_readiness.sh https://stock.letslepai.com
```

預設會驗證 `6919,2886,8150,3711` 四檔代表個股作戰卡：

- `6919`：高風險 / 追價風險案例。
- `2886`：等待 VWAP / 觀察案例。
- `8150`：強勢但追價風險案例。
- `3711`：大型權值 / 等待突破或練習觀察案例。

## 3. 營運健康

執行：

```bash
python3 scripts/check_operational_health.py --base-url https://stock.letslepai.com
```

看三件事：

- `opening_preflight`：綠燈才進入盤中追蹤；黃燈只觀察；紅燈先修資料。
- `operator_steps`：照順序做，不跳步。
- `do_not_do`：若寫不可依賴強烈買多，就不要用 dashboard 當作即時訊號。

## 4. 前台安全檢查

打開：

- `https://stock.letslepai.com/operator`
- `https://stock.letslepai.com/dashboard`
- `https://stock.letslepai.com/tw/advisor?symbol=6919`
- `https://stock.letslepai.com/tw/advisor?symbol=2886`
- `https://stock.letslepai.com/tw/advisor?symbol=8150`
- `https://stock.letslepai.com/tw/advisor?symbol=3711`

確認：

- 全站沒有「強烈看漲」、「做多確認」、「買多推薦」、「今日做多推薦」。
- high_risk 只顯示觀察，不顯示買多或強烈買多。
- delayed / cached / missing 只顯示觀察或資料不足，不顯示強烈買多。
- 看空比例偏高時，頁面明確寫「這不是做空建議」或同等防誤導文字。
- 休市、盤前、盤後不顯示即時強烈買多。

## 5. 開盤前流程

08:55 到 09:00：

1. 先開 `/operator`。
2. 看開盤檢查燈號。
3. 若黃燈，只整理觀察清單，不提前進場。
4. 09:00 後先等 5 到 10 分鐘，確認資料轉 live、VWAP、量比與突破。

## 6. 盤中流程

1. 先看 `/operator` 是否允許盤中追蹤。
2. 再看 dashboard 的強烈買多 / 買多 / 觀察 / 看空。
3. 點進個股作戰卡，只看三件事：
   - 最大卡關
   - 下一步觸發條件
   - 失效條件
4. Fugle / 五檔 / 逐筆只作進場前確認，不直接產生強烈買多。

## 7. 若沒有訊號

不要急著調模型。照順序查：

1. 是否非盤中或休市。
2. 資料是否 live。
3. watchlist / positions 是否過期。
4. 多數股票是否未站上 VWAP。
5. 量比是否不足。
6. 是否被 high_risk 擋下。
7. 是否只是沒有符合條件，而不是系統壞掉。

