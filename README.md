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

## 建議排程

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
