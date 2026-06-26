# 台股做多當沖追蹤器 v1.2 路線圖

這份路線圖的目標不是增加推薦數量，也不是放寬 A / B+ / B 條件，而是把系統變成每天可驗收、可使用、可累積數據的當沖做多工具。

## 產品原則

1. 資料不可信時，不產生即時買多判斷。
2. 強烈買多是重點盯盤，不等於保證進場。
3. 進場雷達通過才進一步檢查停損、停利與部位。
4. high_risk 只做風險降溫觀察，不包裝成買多。
5. 法人、族群、題材、五檔、逐筆只作背景與進場前確認，不直接升級模型。
6. 所有判斷都必須能盤後驗證。

## 已完成的核心能力

- TWSE + TPEX 上市櫃普通股池與異動候選池。
- VWAP、量比、突破、風險、信心、真假突破與進場雷達。
- 四分類：強烈買多、買多、觀察、看空。
- 資料狀態：live、delayed、cached、missing。
- 分層刷新：全市場、重點觀察、持倉 / 觸發。
- Operator runbook：一眼看現在能不能用、要做什麼、不要做什麼。
- 個股當沖作戰卡：結論、最大卡關、下一步、失效條件。
- Paper trading 與平倉覆盤標籤。
- 盤後驗證與策略成績單架構。

## 下一階段優先順序

### P0：盤前 / 盤中可用性

- 讓 `/operator` 成為每天第一個入口，顯示是否可看盤、是否需要刷新、沒有訊號時先查什麼。
- dashboard 第一屏只保留最重要結論：模式、資料可信度、強烈買多 / 買多 / 觀察 / 看空數量、最重要提醒。
- 任何 delayed / cached / missing 標的都要清楚標示，並禁止顯示強烈買多。
- 讓 public readiness、operational health、operator runbook 三者的輸出一致。

### P1：進場前確認能力

- Fugle 基本用戶最多追 5 檔：只追最接近進場的股票，不用它掃全市場。
- 逐筆成交與五檔只做確認，不改 A / B+ / B 條件。
- 進場雷達要回答：買盤是否變強、賣壓是否降低、最新價是否墊高、是否仍站上 VWAP、停損距離是否合理。
- 缺 Tick / 五檔資料時，只顯示確認資料不足，不硬升級也不硬降級。

### P2：盤後驗證與模型檢討

- 每天保存訊號後最高價、最低價、收盤價、最大漲幅、最大回撤。
- 分開統計強烈買多、買多、觀察、看空、high_risk、真假突破與誘多風險。
- 樣本不足時只顯示「樣本不足」，不宣稱準確率。
- 若 high_risk 後續常續漲，只新增觀察層或提示，不直接放寬 A 級。

### P3：使用者體驗

- `/tw/advisor` 保持「作戰卡」形式：一句結論、最大卡關、下一步、失效條件。
- Dashboard 的工程資訊持續收合在開發者資訊。
- 搜尋、快捷鍵、通知、部位計算器只服務操作效率，不干擾核心判斷。
- 任何容易被理解成買進建議的文字都要避免。

## 暫時不做

- 不自動下單。
- 不串真實券商下單 API。
- 不為了讓推薦變多而放寬 A / B+ / B。
- 不把法人買超、族群強、新聞題材直接轉成強烈買多。
- 不用 WebSocket / Redis 重構整站，除非目前分層刷新和 Fugle 5 檔確認已無法支撐。

## 每日驗收

1. `python3 scripts/check_release_readiness.py --no-public --json`
2. 推上 GitHub 後執行 Render Deploy latest commit。
3. `./scripts/check_public_readiness.sh https://stock.letslepai.com`
4. `python3 scripts/check_operational_health.py --base-url https://stock.letslepai.com`
5. 打開 `https://stock.letslepai.com/operator`，先看作戰手冊，再進 dashboard。

