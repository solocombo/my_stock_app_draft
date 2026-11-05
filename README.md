# my_stock_app — 個人版 AI 投資組合 Agent

參考「玉山 Agent 基金經理人」的作法：每天收盤後，Agent 依 **觀察值 → 推論 → 決策** 產出一份可追溯的台股投資組合建議，再推播給你。

- **只給建議，不會自動下單**：沒有串接券商 API，下單由你自己操作。
- **支援零股**：`lot_size: 1` 時以股為單位計算；設成 `1000` 就只建議整張。
- **策略可插拔**：Python 量化規則、LLM（Claude）、兩者混用，或自己寫策略。
- **推播**：Telegram／Discord／ntfy／Email，可同時啟用多個通道。

> ⚠️ 本程式僅供個人研究與參考，不構成投資建議。模擬資料的回測結果不代表真實績效。

## 快速開始

```bash
pip install -r requirements.txt
python run.py daily --no-notify          # 先用模擬資料（demo）跑一次
pytest -q
```

輸出在 `output/plan_<日期>.md`（給人看）與 `.json`（完整紀錄：每檔的決策、理由、因子數值、建議股數）。

### 換成真實台股資料

1. `config.yaml` 把 `data.source` 改成 `finmind`，並把 `data.universe` 改成你想追蹤的股票。
2. （選填）到 FinMind 註冊並設定 `export FINMIND_TOKEN=...`，免費額度會比較高。
3. 把你的持股寫進 `portfolio.json`，格式參考 `portfolio.example.json`（股數可以是零股）。

## 每日流程

```
資料 (agent/data.py) ─► 因子 (agent/factors.py) ─► 策略 (agent/strategies/) ─► 股數 (agent/portfolio.py) ─► 報告與推播
```

```bash
python run.py daily                                   # 產生建議並推播
python run.py fill output/plan_2026-11-05.json        # 照建議下單後，同步到 portfolio.json
python run.py fill output/plan_2026-11-05.json --skip 2454   # 有幾檔沒下的話就跳過
```

若實際成交價或股數與建議不同，請直接修改 `portfolio.json`。

## 因子（量化策略 `quant`）

| 因子 | 意義 |
|---|---|
| F | 基本面分數 0–100：近 3 個月營收 YoY 的排名，加上近 6 個月 YoY 為正的月數 |
| ACC | 營收加速度（百分點）＝近 3 個月平均 YoY − 前 3 個月平均 YoY；> 0 代表基本面轉折向上 |
| MOM | 動能分數 0–100：20／60／120 日報酬加權後的排名 |
| FLOW | 籌碼百分位：近 20 日三大法人買賣超 ÷ 近 20 日成交量 |
| S | 綜合分數 ＝ 0.4F ＋ 0.3MOM ＋ 0.2FLOW ＋ 0.1ACC排名 |

**規則**（參數都在 `config.yaml`）：

- **出場**：MOM < 25、連續 3 日收在 MA60 之下，或基本面轉弱時，建議全部賣出。
- **進場**：F ≥ 60、ACC > 0、MOM ≥ 50、收盤 ≥ MA20、FLOW ≥ 20，而且還沒跌破 MA60。
- **換股**：持股已滿時，候選股的 S 要比最弱的持股高 20 以上才換。
- **權重**：依 S² 分配。單檔上限 12%（2330 為 25%），金融股合計不超過 25%，保留 5% 現金。
- **防止過度交易**：目標權重與目前權重相差不到 3.5% 時不調整。每天最多新買 5 檔，可分批建倉。單筆金額低於 1,000 元的建議不列出，避免零股手續費吃掉報酬。

## 策略選擇

```bash
python run.py daily --strategy quant        # 純量化（預設）
python run.py daily --strategy quant+llm    # 量化選股，買進／加碼前交給 Claude 審查（可否決）
python run.py daily --strategy llm          # Claude 依因子表直接排組合，程式再套上限與區間
python run.py daily --strategy custom:my_strategies.momentum_only
```

LLM 模式需要 `ANTHROPIC_API_KEY`，預設模型是 `claude-opus-5-5`，可在 `config.yaml` 的 `llm` 區段調整模型、思考強度和你的投資偏好。另外有兩點要注意：

- 程式會開啟 server-side fallback：模型拒答時，API 會自動改用備援模型。
- LLM 只看得到因子表，不會上網查新聞；它寫的理由只能當參考。LLM 呼叫失敗時，`quant+llm` 會照常輸出量化結果，並在 evidence 裡記錄失敗原因。

**自己寫策略**：在 `my_strategies/` 新增檔案，實作 `decide(obs, pf, cfg) -> list[Decision]`。零股股數換算、報告、推播、回測都共用同一套程式，可以參考 `my_strategies/momentum_only.py`。

## 回測與參數比較

```bash
python run.py backtest --start 2025-12-01 --every 5
python run.py compare --variant '{"entry": {"mom_min": 60}}' --n-starts 6
```

`compare` 會從多個起始日分別回測原參數和變體。只有變體勝率 ≥ 2/3、而且報酬差的中位數 > 0，才判定為通過，避免因為某一段行情就改參數。回測以當日收盤價成交，含手續費與證交稅，不含滑價、漲跌停和除權息。

## 推播設定

先在 `config.yaml` 的 `notify.channels` 填上要用的通道（例如 `[telegram, ntfy]`），再設定對應的環境變數：

| 通道 | 環境變數 |
|---|---|
| telegram | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` |
| discord | `DISCORD_WEBHOOK_URL` |
| ntfy | `NTFY_TOPIC`（選填 `NTFY_SERVER`）。手機裝 ntfy App 並訂閱同一個 topic 即可，免註冊 |
| email | `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`EMAIL_TO`（Gmail 請使用應用程式密碼） |

設定好之後，用 `python run.py notify-test` 測試。

## 自動排程（GitHub Actions）

`.github/workflows/daily.yml` 會在每個交易日台北時間 18:07 執行，並推播建議。要在 repo 的 Secrets 設定 `PORTFOLIO_JSON`（`portfolio.json` 的內容），以及推播用的金鑰。`portfolio.json` 預設列在 `.gitignore`，不會上傳，以免公開 repo 外洩你的持股。

## 專案結構

```
agent/
  data.py         DemoProvider（模擬）／FinMindProvider（真實）
  factors.py      F、ACC、MOM、FLOW、MA20/60、ma_break_days
  strategies/     quant.py、llm.py、策略註冊
  portfolio.py    持股、權重上限、目標權重 → 零股股數
  report.py       JSON + Markdown 報告
  notify.py       推播
  backtest.py     回測與多起始日比較
my_strategies/    你的自訂策略
run.py            指令入口
```
