# my_stock_app — 個人版 AI 投資組合 Agent

每天收盤後，Agent 會依「觀察值 → 推論 → 決策」產出可追溯的台股建議，並推播給你。

- **只給建議，不會下單**：沒有串接券商，下單由你手動操作。
- **零股**：`lot_size: 1` 時以股為單位計算。
- **10 種選股策略**：可以加上 LLM（Claude）審查，也可以自己寫 Python 策略。
- **現金池（T+2）**：預測未來每天的交割帳戶餘額；買進金額以「交割日付得出來的錢」為上限，並預警違約交割風險。
- **當沖模組**：資金與風險和波段組合分開管理，包含盤前名單、盤中觸發推播、分 K 回測。
- **推播**：Telegram／Discord／ntfy／Email。

> ⚠️ 僅供個人研究參考，不構成投資建議。模擬資料的回測結果不代表真實績效。

## 快速開始

```bash
git clone https://github.com/solocombo/my_stock_app_draft.git
cd my_stock_app_draft
pip install -r requirements.txt
streamlit run app.py                    # 開啟網頁介面 → 瀏覽器自動打開 http://localhost:8501
```

**Windows**：到專案資料夾雙擊 `start_gui.bat`。第一次執行會自動安裝套件，並顯示手機要連的網址。
如果 `streamlit` 指令找不到，請改用 `py -m streamlit run app.py`。

預設使用**模擬資料**，所以不需要任何金鑰就能先試玩。照下面的順序試一輪：

1. **現金池**：輸入你的交割帳戶餘額，按「更新餘額」。
2. **今日建議**：選一個策略，按「產生建議」。
3. 在建議單上勾選你實際下單的項目（股數和價格可以修改），按「記錄成交」，持股和交割帳會一起更新。
4. 切到**持股**、**現金池**，看部位和未來每天的預估餘額。
5. **策略比較**：選幾個策略一起回測。
6. **當沖**：產生明日觀察名單。

### 在手機上使用

- **同一個 Wi-Fi**：在電腦執行下面的指令，手機瀏覽器開 `http://<電腦的區網 IP>:8501`。

```bash
streamlit run app.py --server.address 0.0.0.0
```

- **在外面也要用**：電腦和手機都裝 [Tailscale](https://tailscale.com)（免費的私人 VPN），手機開 `http://<電腦的 Tailscale IP>:8501`。不建議直接把服務開放到公開網路，這個介面沒有登入機制。
- **加到主畫面**（像 App 一樣有圖示）：
  - Android：用 Chrome 開啟後，按右上角「⋮ → 加到主畫面」。
  - iPhone：用 Safari 開啟後，按「分享 → 加入主畫面」。
- **Android 推播建議用 ntfy**：Google Play 搜尋「ntfy」安裝，訂閱和 `NTFY_TOPIC` 相同的主題名稱。主題名稱請取得難猜一點，因為知道名稱的人都能看到你的訊息。
  - 收不到或延遲時，到「設定 → 應用程式 → ntfy → 電池」改成「不受限制」。Android 的省電機制常會擋掉背景通知。
- 每日建議和當沖觸發的**推播**本來就會送到手機（Telegram／ntfy），不開網頁也收得到。

### 零成本組合（全部免費）

| 項目 | 免費方案 | 限制 |
|---|---|---|
| 主機 | Oracle Cloud 永久免費 ARM VM | 熱門區域可能搶不到機器，詳見 deploy/README.md |
| 每日資料 | FinMind 免費會員 | 30 檔股票每天增量更新，請求次數遠低於額度 |
| 當沖即時報價、分 K | 富果「基本用戶」（免費） | WebSocket 最多訂閱 5 檔、日內 API 60 次／分鐘，所以當沖名單預設 5 檔 |
| 推播 | ntfy | 無 |
| 手機私密連線 | Tailscale 個人方案 | 無 |
| 選股 | 規則型量化策略 | 不開 Claude 審查（`+llm` 要付 API 費用） |

富果的方案與額度以[官方定價頁](https://developer.fugle.tw/docs/pricing/)為準。

### 放到雲端主機（不用開電腦）

見 [deploy/README.md](deploy/README.md)：Oracle Cloud 永久免費主機或 US$5 VPS，一行指令就能部署。部署後每天自動推播，手機透過 Tailscale 私密連線。

### 個人設定

不要直接改 `config.yaml`，把要改的項目寫進 `config.local.yaml`（不進 git，更新程式不衝突）：

```yaml
data: {source: finmind}
notify: {channels: [ntfy]}
```

### 指令列（自動化、排程用）

GUI 能做的事，指令列都能做，適合放進排程：

```bash
python run.py cash set 400000          # 建立交割帳
python run.py daily --no-notify        # 產生建議
pytest -q
```

改用真實資料：在 `config.yaml` 設定 `data.source: finmind`，並修改 `data.universe`（股票池）。FinMind token 可選填：`export FINMIND_TOKEN=...`。持股寫在 `portfolio.json`，格式參考 `portfolio.example.json`。

## 0. 資料自動更新

| 資料 | 來源 | 說明 |
|---|---|---|
| 日線、月營收、三大法人、本益比／殖利率 | FinMind（免費，token 選填） | 存在本機快取 `data/cache/`，每天只抓新增部分 |
| 盤中即時報價、1 分 K | 證交所 MIS（免費、非官方）或**富果 MarketData API**（`FUGLE_API_KEY`） | 當沖監控與分 K 資料庫 |

為什麼不全部用券商 API：富果行情 API 沒有月營收和法人資料，基本面、籌碼類策略還是要 FinMind。富果最有價值的是**穩定的即時報價和 1 分 K**。這個專案只讀行情，不需要富果的下單（Trade）API。

```bash
python run.py data update        # 第一次會回補約 1.5 年歷史；之後只抓缺的日期
python run.py data status        # 快取範圍與新鮮度（哪些股票、哪些資料落後）
python run.py auto               # 收盤後一鍵：更新資料 → 每日建議 → 當沖名單 → 存分 K → 推播
```

- `config.yaml` 設成 `data.source: finmind` 後，`daily`、`backtest` 都會讀快取；`auto_update: true` 時會先自動增量更新。
- 新鮮度檢查會比對「現在應該有哪一天的資料」：日線 14:30 之後、法人和本益比 16:00 之後才算當天的資料。資料落後時會寫進報告的資料備註，避免拿舊資料下判斷。
- **分 K 資料庫**：設定 `FUGLE_API_KEY` 後，`auto` 會在收盤後把當沖名單上股票的 1 分 K 存到 `data/kbars/`。累積一段時間後，就能用 `daytrade backtest --bars-dir data/kbars` 拿真實分 K 回測。
- 盤中報價改用富果：在 `config.yaml` 設定 `daytrade.quote_source: fugle`。

**排程（擇一）**

- **GitHub Actions**：`.github/workflows/daily.yml` 可以在每個交易日台北時間 16:47 執行 `auto`。**預設關閉**，因為公開 repo 的執行紀錄任何人都看得到；要啟用請先把 repo 改成 Private，再打開檔案裡的 schedule。
- **自己的電腦（Linux/macOS cron）**：

```bash
47 16 * * 1-5 cd /path/to/my_stock_app_draft && python run.py auto >> output/auto.log 2>&1
```

- **盤中當沖監控**：

```bash
55 8 * * 1-5 cd /path/to/my_stock_app_draft && python run.py daytrade watch >> output/watch.log 2>&1
```

- Windows 可用「工作排程器」設定同樣的指令。

## 1. 選股策略

`python run.py strategies` 可以看完整清單。參數可在 `config.yaml` 的 `strategies:` 區段覆蓋。

| 名稱 | 中文 | 進場 | 出場 |
|---|---|---|---|
| `fundamental` | 基本面轉折 | 營收 YoY 排名 F≥60 且在加速（ACC>0）、MOM≥50、站上 MA20、法人不賣 | MOM<25、連 3 日破 MA60、基本面轉弱 |
| `momentum` | 動能 | MOM≥75、站上 MA60、近 20 日漲幅 < 30%（不追過熱） | MOM<40、連 3 日破 MA60 |
| `trend` | 均線多頭 | MA5 > MA20 > MA60 且 MA60 上彎 | 連 2 日破 MA60、MA20 跌破 MA60 |
| `breakout` | 突破 | 距一年高點 ≤ 3%，且 5 日量 ≥ 60 日量 1.5 倍 | 連 2 日破 MA20、自高點回落 10% |
| `value` | 價值 | PER ≤ 15、PBR ≤ 2、MOM 不低於 30（避開價值陷阱） | PER > 25（評價修復）、MOM < 10 |
| `dividend` | 高股息 | 殖利率 ≥ 5%、波動度不在前 40% | 殖利率 < 3.5% |
| `lowvol` | 低波動 | 60 日波動度最低的 30%、沒有跌破 MA60 | 波動度上升、連 5 日破 MA60 |
| `mean_reversion` | 拉回買進 | 長多格局中 RSI ≤ 35 或碰到布林下軌 | RSI ≥ 60、站回 MA20、持有滿 10 天、停損 8% |
| `chips` | 籌碼 | 法人連買 ≥ 3 天，且 20 日買超力道在前 30% | 法人轉賣（FLOW<30）、連 3 日破 MA60 |
| `multifactor` | 多因子 | 價值、動能、品質、低波動、籌碼五項排名平均 ≥ 60 | 平均 < 40 |

所有策略共用以下規則：

- **硬停損**：收盤比平均成本跌 15%。
- **權重上限**：單檔 12%（2330 為 25%），金融股合計 25%。
- **換股**：持股已滿時，新候選的分數要高出 20 分以上才換。
- **不交易區間**：目標權重與目前權重差 ≤ 3.5% 時不調整。
- **建倉速度**：每天最多新買 5 檔。

用法：

```bash
python run.py daily --strategy momentum
python run.py daily --strategy value+llm                 # 規則選股，買進前讓 Claude 審查（可否決）
python run.py daily --strategy llm                       # Claude 依因子表直接排組合
python run.py daily --strategy custom:my_strategies.momentum_only
python run.py compare-strategies                         # 所有策略同期間回測比較
python run.py compare --variant '{"strategies": {"momentum": {"mom_min": 80}}}'   # 參數變體，多起始日比較
```

**自己寫策略**：繼承 `agent/strategies/base.py` 的 `RuleStrategy`，實作 `score`、`entry_fail`、`exit_reason`，組合建構就會自動套用。也可以直接寫一個 `decide(obs, pf, cfg)` 函式，參考 `my_strategies/momentum_only.py`。

LLM 模式需要設定 `ANTHROPIC_API_KEY`，模型可在 `config.yaml` 的 `llm` 區段調整。程式已開啟 server-side fallback：模型拒答時會自動改用備援模型。LLM 只看得到因子表，不會上網查新聞。

## 2. 現金池（T+2 交割）

台股買進時不需要先有錢，但 T+2 上午必須有足夠餘額，否則就是違約交割。賣出的錢也要到 T+2 才入帳。系統用 `cash_ledger.json` 記錄這些未交割款項。

```bash
python run.py cash set 352000                        # 隨時更新實際餘額；已交割的項目會自動清掉
python run.py cash trade BUY 2330 50 1050            # 記錄實際成交（也會更新 portfolio.json）
python run.py cash event 2026-11-25 45000 --note 薪水  # 未來入金(+)／出金(-)
python run.py cash forecast --days 10                # 未來每天的預估餘額
python run.py cash reserve 20000                     # 安全水位，買進建議不會動用
python run.py cash holidays-update                   # 下載證交所休市日（T+2 才能正確跳過連假）
```

每日建議會自動做到三件事：

1. 計算可用買進額度：T+2 交割日之後，每天預估餘額的最低點，再扣掉安全水位。例如 11/12 要付一筆大額款項，今天就不會建議買超過那天付得出來的金額。
2. 報告中附上「如果照建議執行」的每日現金預測表。
3. 有任何一天餘額低於安全水位時，顯示 🚨 違約交割風險警告。

照建議下單後，用 `python run.py fill output/plan_<日期>.json` 同時更新持股和交割帳；有幾檔沒下的話用 `--skip` 跳過。

## 3. 當沖模組

當沖的資金和風險都與波段組合分開設定，在 `config.yaml` 的 `daytrade:` 區段。

```bash
python run.py daytrade plan                  # 盤前：依前一日收盤挑出觀察名單（可推播）
python run.py daytrade watch                 # 盤中：每 10 秒抓證交所即時報價，觸發時推播
python run.py daytrade backtest --days 60    # 回測（預設用日線合成的分 K）
python run.py daytrade backtest --bars-dir data/kbars   # 用你的真實 1 分 K（檔名 <代號>_<日期>.csv）
python run.py daytrade record 2330 1000 1050 1062       # 記錄實際當沖，淨損益會在 T+2 進交割帳
```

**盤前選股**：同時符合以下條件的股票才會進名單，並依強度給方向。

- 流動性：20 日均成交值 ≥ 2 億。
- 波動：ATR 介於 2%～8%。
- 熱度：前一日量增。
- 方向：收在高檔且站上 MA20 → 做多；收在低檔且跌破 MA20 → 做空。做空需要把 `allow_short` 打開。

**盤中策略**：

- **ORB**（開盤區間突破）：等開盤 15 分鐘形成區間，突破高點時做多；停損在區間低點，最多 0.6 ATR；停利 2R。
- **VWAP**：09:30 之後，價格跌破均價線再站回時進場。

**風控**：

- 每筆最多虧損本金的 0.5%，股數依此計算。
- 單日累計虧損達 1.5% 就停止開新倉。
- 每天最多 3 筆。
- 11:30 後不開新倉，13:20 全部平倉。
- 價格遵守台股的升降單位。

**限制**：

- 即時報價來自證交所 MIS 的非官方 API，約 5 秒更新一次，偶爾會延遲；只用來提醒，不保證價格精確。
- 合成分 K 是隨機漫步，回測虧損是正常的。要評估當沖策略，必須使用真實分 K（可從 FinMind `TaiwanStockKBar` 取得，需贊助會員，或用券商軟體匯出）。
- 現股當沖需要先簽署契約、具備資格；零股能否當沖、能否先賣後買，以券商規定為準。

## 推播設定

先在 `config.yaml` 的 `notify.channels` 填上要用的通道（例如 `[telegram, ntfy]`），再設定對應的環境變數：

| 通道 | 環境變數 |
|---|---|
| telegram | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` |
| discord | `DISCORD_WEBHOOK_URL` |
| ntfy | `NTFY_TOPIC`（可選 `NTFY_SERVER`）。手機裝 ntfy App 並訂閱同一個 topic 即可，免註冊 |
| email | `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`EMAIL_TO` |

設定好之後，用 `python run.py notify-test` 測試。

## 專案結構

```
agent/
  data.py           DemoProvider（模擬）／FinMindProvider（真實），含 OHLC、估值、法人
  datastore.py      本機快取、增量更新、新鮮度檢查
  fugle.py          富果行情：即時報價、1 分 K、歷史 K 線
  factors.py        約 30 個因子：基本面、動能、趨勢、突破、RSI/布林、波動、估值、籌碼
  strategies/       base.py（共用組合建構）、library.py（10 策略）、llm.py、quant.py（相容舊名稱）
  portfolio.py      持股、權重上限、目標權重 → 零股股數
  calendar_tw.py    交易日曆、T+2
  cashflow.py       交割帳、現金預測、可用買進額度
  daytrade/         screener、intraday（ORB/VWAP 模擬）、bars、realtime、backtest
  report.py / notify.py / backtest.py / pipeline.py
my_strategies/      你的自訂策略
run.py              指令入口
app.py              網頁介面（Streamlit，手機可用）
```

`portfolio.json`、`cash_ledger.json`（個人資金資料）、`data/`（快取）和 `output/` 都列在 `.gitignore`，不會上傳；`holidays.json` 可以 commit。
