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
pip install -r requirements.txt
python run.py cash set 400000          # 建立交割帳（填你目前的交割帳戶餘額）
python run.py daily --no-notify        # 用模擬資料跑一次
pytest -q
```

改用真實資料：在 `config.yaml` 設定 `data.source: finmind`，並修改 `data.universe`（股票池）。FinMind token 可選填：`export FINMIND_TOKEN=...`。持股寫在 `portfolio.json`，格式參考 `portfolio.example.json`。

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

設定好之後，用 `python run.py notify-test` 測試。GitHub Actions 排程設定在 `.github/workflows/daily.yml`，每個交易日 18:07 執行。

## 專案結構

```
agent/
  data.py           DemoProvider（模擬）／FinMindProvider（真實），含 OHLC、估值、法人
  factors.py        約 30 個因子：基本面、動能、趨勢、突破、RSI/布林、波動、估值、籌碼
  strategies/       base.py（共用組合建構）、library.py（10 策略）、llm.py、quant.py（相容舊名稱）
  portfolio.py      持股、權重上限、目標權重 → 零股股數
  calendar_tw.py    交易日曆、T+2
  cashflow.py       交割帳、現金預測、可用買進額度
  daytrade/         screener、intraday（ORB/VWAP 模擬）、bars、realtime、backtest
  report.py / notify.py / backtest.py / pipeline.py
my_strategies/      你的自訂策略
run.py              指令入口
```

`portfolio.json`、`cash_ledger.json`（個人資金資料）和 `output/` 都列在 `.gitignore`，不會上傳；`holidays.json` 可以 commit。
