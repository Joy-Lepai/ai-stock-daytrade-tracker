# AI 股票當沖指標系統

這是一個盤前研究輔助系統，用來在台股開盤前彙整美股、台指期與自選股技術面訊號，產生「今日是否適合當沖、偏多或偏空、優先觀察標的」報告。

> 這不是投資建議，也不會自動下單。第一版重點是把每天的判斷流程自動化、可追溯、可調整。

## 第一版功能

- 追蹤美股主要指數與期貨代理商品：`^GSPC`、`^IXIC`、`^DJI`、`^SOX`、`ES=F`、`NQ=F`
- 追蹤台股大盤：`^TWII`
- 透過 TAIFEX 官方頁面抓取臺股期貨 `TX` 最新行情快照
- `TX=F` 仍保留為 Yahoo 代理資料源；若無資料，報告會明確列出資料警告，不會把缺資料誤判成中性。
- 掃描 `config/watchlist.json` 內的台股標的
- 依據趨勢、量能、波動、相對強弱與市場背景產生多空評分
- 依照設定檔內的 `sector` 產生族群強弱排行
- 依每筆最大可承受損失估算觸發價、停損、目標價與股數上限
- 產生集中式 HTML 追蹤器，彙整市場、族群、個股狀態與交易計畫，畫面使用中英文並列標籤
- 輸出每日 Markdown 報告到 `reports/`
- 內建虛擬交易帳本：交易時段內遇到 `可進場` 訊號會自動建立模擬做多單，依目標價/停損價出場，供回測與調整規則使用。
- 新增台股當沖做多 MVP：今日做多候選股、A/B/C/D 分級、多方分數、風險分數、盤中警示、族群熱度、大盤狀態、個股詳情頁與每日簡單回測。

## 快速開始

```bash
python3 -m stock_daytrade_system.cli report
```

輸出檔案會放在：

```text
reports/YYYY-MM-DD-premarket.md
```

開盤後 15 分鐘，可執行開盤確認：

```bash
python3 -m stock_daytrade_system.cli open-check
```

輸出檔案會放在：

```text
reports/YYYY-MM-DD-opening.md
```

若想看集中式追蹤器：

```bash
python3 -m stock_daytrade_system.cli tracker
```

輸出檔案會放在：

```text
reports/YYYY-MM-DD-tracker.html
```

追蹤器更新時也會自動維護：

```text
reports/signal-performance.json
reports/signal-performance.csv
reports/paper-trades.json
reports/paper-trades.csv
```

`paper-trades` 是虛擬交易紀錄，只做模擬，不會連到券商也不會送出真實委託。

做多 MVP 會自動建立 SQLite 資料庫：

```text
data/daytrade.db
```

目前第一版已支援：

- 每日行情資料
- 盤中資料
- VWAP
- 量比
- 突破昨日高點
- 突破 5 日高點
- 突破 10 日高點
- 多方分數
- 風險分數
- A/B/C/D 分級
- 今日候選股列表
- 個股詳情頁 `/symbol/2330.TW`
- 候選股 API `/api/candidates`
- 個股 API `/api/symbol/2330.TW`
- 回測 API `/api/backtest`

法人買賣超目前先使用 CMoney 法人排行作為加分來源；融資融券、當沖比率與新聞題材欄位已在模型中預留，待接正式資料源後補齊。

若要啟動有帳號密碼保護的本機網站：

```bash
python3 -m stock_daytrade_system.cli web
```

打開：

```text
http://localhost:8000/
```

預設帳號密碼：

```text
帳號：admin
密碼：stock1234
```

## 架設成網站

目前網站預設只在本機 `127.0.0.1` 開放。如果要讓其他裝置或雲端主機可以連線，有三種方式。

### 1. 同一個 Wi-Fi 內使用

這只適合家裡或辦公室內部測試，不建議直接暴露到網際網路。

```bash
python3 -m stock_daytrade_system.cli web --host 0.0.0.0 --port 8000
```

接著用這台電腦的區網 IP 開啟：

```text
http://你的電腦IP:8000/
```

### 2. 雲端主機部署

建議用 Render、Railway、Fly.io、VPS 這類可跑 Python Web Service 的平台。部署時設定：

```text
Start command:
./scripts/start_web.sh
```

環境變數請設定：

```text
STOCK_WEB_USERNAME=你的帳號
STOCK_WEB_PASSWORD=你的強密碼
STOCK_WEB_HOST=0.0.0.0
PORT=平台自動提供，通常不用手動設定
```

如果平台要求 build command，這個專案目前沒有第三方套件，可留空或使用：

```bash
python3 -m py_compile stock_daytrade_system/*.py
```

### 3. 正式使用建議

- 一定要用 HTTPS。
- 不要使用 README 裡的預設密碼。
- 雲端平台若有 persistent disk，建議掛載 `reports/`，否則重新部署後績效追蹤紀錄可能會消失。
- 若要多人使用，下一步應改成資料庫與多帳號權限。
- 若要更即時，建議串券商報價 API，而不是只靠 Yahoo chart endpoint。

### 4. 部署後驗收

部署前先確認「本機 → GitHub → Render 公開站」版本鏈路是否一致：

```bash
python3 scripts/check_release_readiness.py --base-url https://stock.letslepai.com
```

這個指令會告訴你：

- 本機是否還有未提交修改
- 本機是否 ahead `origin/main`
- 公開站 runtime commit 是否等於本機 HEAD
- 下一步應該先 `Push`，還是到 Render 按 `Deploy latest commit`

每次 Render 部署完成後，可用下列指令確認公開站版本、tracker HTML、分層刷新狀態與強烈買多安全閘門：

```bash
python3 scripts/verify_public_deployment.py --base-url https://stock.letslepai.com
```

若要同時驗證單檔個股作戰卡 API，例如康霈 `6919`：

```bash
python3 scripts/verify_public_deployment.py \
  --base-url https://stock.letslepai.com \
  --advisor-symbol 6919
```

若要指定應該部署的 commit：

```bash
python3 scripts/verify_public_deployment.py \
  --base-url https://stock.letslepai.com \
  --expected-commit 你的_git_commit
```

也可以用一鍵檢查腳本，預設檢查 `https://stock.letslepai.com` 與 `6919`：

```bash
./scripts/check_public_readiness.sh
```

這個一鍵腳本會先執行 release readiness 檢查；如果本機 commit 還沒推到 GitHub，或公開站還沒部署到本機 HEAD，會先停止，避免拿舊版公開站做功能驗收。

參數順序為 `公開網址 個股代號 預期commit`：

```bash
./scripts/check_public_readiness.sh https://stock.letslepai.com 6919 你的_git_commit
```

若只是想臨時檢查目前公開站，不要求它等於本機最新版，可以跳過版本鏈路檢查：

```bash
SKIP_RELEASE_READINESS=1 ./scripts/check_public_readiness.sh
```

驗收會檢查：

- runtime commit 是否存在
- tracker HTML commit 是否與 runtime 一致
- 需要的刷新層是否新鮮
- 非盤中 / 開盤前 / 休市是否禁止顯示即時強烈買多
- `/api/system/version` 與 `/api/refresh/status` 是否正常回傳
- `/api/health` 是否正常回傳營運健康、資料品質、部署摘要與下一步
- `/tw/advisor` 是否顯示個股當沖作戰卡入口
- 指定個股的 `/api/tw/scan/symbol` 是否回傳資料健康度、市場模式、四分類與進場雷達

也可以到 GitHub Actions 手動執行 `Verify Public Deployment` workflow。部署完成後輸入公開網址、預期 commit 與 advisor symbol，即可在 Actions 裡看到 PASS / FAIL。

### Shioaji 永豐金報價接入（選用）

本系統可選擇接入 Shioaji 作為台股即時報價背景資料，用來補強個股頁的盤口確認。第一版只讀行情，不下單、不送委託、不需要把交易功能打開。

建議 Render 環境變數：

```text
SHIOAJI_ENABLED=1
SHIOAJI_API_KEY=你的 Shioaji API Key
SHIOAJI_SECRET_KEY=你的 Shioaji Secret Key
SHIOAJI_TIMEOUT_SECONDS=3
```

若不想在 Web Service 內直接安裝 / 登入 Shioaji，也可以自行架一個內部 quote bridge，並設定：

```text
SHIOAJI_ENABLED=1
SHIOAJI_HTTP_BASE_URL=https://你的內部報價服務
```

目前 MVP 行為：

- `/tw/advisor` 會顯示「Shioaji 即時報價 / 盤口確認」卡片。
- 沒有設定憑證時，網站會顯示「尚未啟用 / 尚未設定憑證」，不會讓 dashboard 壞掉。
- 若 Shioaji 可取得報價，個股頁會優先顯示 Shioaji snapshot 價格。
- 第一版顯示 snapshot / top-of-book，不把它當完整五檔串流。
- Shioaji 報價只作背景確認，不會直接產生強烈做多，也不會自動下單。

### Fugle 5 檔進場雷達（基本用戶）

Fugle 基本用戶即時行情 WebSocket 最多 5 檔訂閱。本系統不會拿 Fugle 掃全市場，而是先用既有模型挑出最接近進場的 5 檔，再把 Fugle 用在「進場前確認」：

- 五檔買賣盤差
- 委買量是否增加
- 委賣量是否減少
- 最新價是否連續墊高
- 是否有大單敲進 / 敲出
- 是否仍站上 VWAP
- 停損距離是否合理

若要固定指定某檔進入 Fugle 追蹤池，請編輯 `config/watchlist.json`：

```json
{
  "fugle_priority_symbols": ["6919.TW"]
}
```

注意：指定追蹤只代表「優先拿 Fugle 做盤口確認」，不會直接升級成強烈買多，也不會改 A / B+ / B 條件。

### 營運健康檢查

開盤前、盤中或部署後，可以用同一個指令確認網站目前是否適合拿來看盤：

```bash
python3 scripts/check_operational_health.py --base-url https://stock.letslepai.com
```

這個指令會優先讀輕量 `/api/health`；若公開站尚未部署新端點，會 fallback 到 `/api/refresh/status`，並整理成：

- 目前是可用、警告，還是阻擋狀態
- market mode 是否為盤中 / 開盤前 / 休市復盤
- live / delayed / cached / missing 各幾檔
- 哪些刷新層過期
- 下一步應該更新全市場、重點觀察，還是持倉觸發

若狀態為 `blocked`，前台不應顯示即時強烈買多；若狀態為 `warning`，代表可看，但要注意資料延遲、使用上一筆或非盤中模式。

`Refresh Stock Dashboard` GitHub Actions 每次分層刷新後也會讀取 `operational_health`；若狀態為 `blocked`，該次 workflow 會失敗，方便及早發現資料源或刷新層問題。

外部監控可以分兩層：

- `/healthz`：只檢查 Web Service 是否活著，適合 uptime ping。
- `/readyz`：檢查營運健康；若資料或必要刷新層阻擋即時判斷，會回 HTTP 503，適合資料可用性告警，不建議拿來當 Render 重啟健康檢查。

## 建議排程

公開 Render 站目前可用 web service 內建排程更新 tracker，資料會寫在同一個 web service 的 `reports/` 與 SQLite 檔案中：

- 台股交易日 07:00-09:00：每 30 分鐘更新開盤前觀察池。
- 台股交易日 09:00-13:30：全市場慢掃每 15 分鐘；重點觀察與持倉 / 觸發控風險每 5 分鐘。
- 台股交易日 13:30-14:30：每 15 分鐘更新收盤後回測與明日觀察池。

可用環境變數調整：

```text
STOCK_ENABLE_WEB_SCHEDULER=1
STOCK_TW_PREMARKET_REFRESH_SECONDS=1800
STOCK_TW_INTRADAY_REFRESH_SECONDS=900
STOCK_TW_WATCHLIST_REFRESH_SECONDS=300
STOCK_TW_POSITIONS_REFRESH_SECONDS=300
STOCK_TW_AFTER_CLOSE_REFRESH_SECONDS=900
```

注意：Render Free 方案如果服務睡著，內建排程也會暫停；第一次有人打開網站時會喚醒並重新整理。若需要完全無人值守的 07:00 固定更新，建議再加外部 uptime ping 或升級成不休眠方案。

台股一般交易日 09:00 開盤，若要在開盤前兩小時執行，可在 macOS/Linux 用 cron 設定台北時間 07:00 執行：

```cron
0 7 * * 1-5 cd /Users/dengjoy/Documents/AI股票系統 && /usr/bin/python3 -m stock_daytrade_system.cli report
```

開盤後確認可排在 09:15：

```cron
15 9 * * 1-5 cd /Users/dengjoy/Documents/AI股票系統 && /usr/bin/python3 -m stock_daytrade_system.cli open-check
```

追蹤器可排在 09:20 或手動重跑：

```cron
20 9 * * 1-5 cd /Users/dengjoy/Documents/AI股票系統 && /usr/bin/python3 -m stock_daytrade_system.cli tracker
```

## 調整追蹤標的

編輯 `config/watchlist.json`：

```json
{
  "auto_universe": [
    {"symbol": "2330.TW", "name": "台積電", "sector": "semiconductor"}
  ],
  "manual_symbols": [
    {"symbol": "6919.TW", "name": "康霈生技", "sector": "biotech"},
    {"symbol": "5388.TW", "name": "中磊", "sector": "networking"}
  ]
}
```

- `auto_universe`：系統自動掃描池，追蹤器只顯示符合條件的自動候選。
- `manual_symbols`：你的自選追蹤清單，無論是否進入候選都會固定顯示。

Yahoo Finance 的台股代號通常使用：

- 上市：`2330.TW`
- 上櫃：`6488.TWO`

## 評分邏輯摘要

- 市場背景：美股與台股指數/期貨的短期漲跌與趨勢
- 族群強弱：同族群個股的 1 日、5 日平均漲跌與相對大盤強弱
- 多方候選：價格站上短均與長均、相對強於大盤、量能擴張、波動足夠
- 空方候選：價格跌破短均與長均、相對弱於大盤、量能擴張、波動足夠
- 風控欄位：ATR、前日高低點、建議觀察方向與分數
- 交易計畫：以前高/前低作為觸發基準，ATR 輔助停損，並用 `max_loss_per_trade` 估算股數上限

## 下一步可以加的模組

- 串接券商即時報價 API
- 串接 TAIFEX 台指期官方資料源
- 串接 TWSE / TPEx 股票官方資料源
- 加入開盤後 5/15 分鐘 K 棒確認
- 加入即時委買委賣與滑價風險
- 加入成交值、借券、外資買賣超、融資券變化
- 加入 Telegram/LINE 通知
- 加入回測與交易日曆
