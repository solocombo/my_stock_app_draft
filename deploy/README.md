# 部署到雲端主機

放到雲端之後，電腦不用開著。每個交易日 16:47 主機會自動產生建議並推播，手機隨時可以打開網頁介面。

- **不需要資料庫**：資料都是專案資料夾裡的檔案。
- **不需要開放任何連接埠**：手機透過 Tailscale 私密連線，只有你的裝置看得到。

## 1. 準備一台 Ubuntu 主機（擇一）

**A. Oracle Cloud 永久免費（推薦，免費）**

1. 到 cloud.oracle.com 註冊。需要信用卡驗證，不會扣款。
2. 「Compute → Instances → Create instance」：
   - Image 選 **Ubuntu 24.04**。
   - Shape 選 **Ampere（VM.Standard.A1.Flex）**，1～2 OCPU、6～12 GB 記憶體就很夠，都在免費額度內。
   - 下載 SSH 私鑰，建立完成後記下 Public IP。
3. 如果顯示「Out of capacity」，代表這個區域的免費機器被搶光了：可以換一個 Availability Domain、過幾小時再試，或改用 B。

**B. 付費 VPS（最省事，約 US$5／月）**

Vultr、Linode（Akamai）、DigitalOcean 等都可以。機房選**東京**（離台灣近），規格選 1 vCPU／1～2 GB，系統選 Ubuntu 24.04。

## 2. 連線到主機

Windows 的 PowerShell 內建 ssh：

```powershell
ssh -i 下載的私鑰檔 ubuntu@主機的PublicIP
```

VPS 通常是 `root@IP`，用密碼登入。

## 3. 一鍵安裝

在主機上執行：

```bash
git clone https://github.com/solocombo/my_stock_app_draft.git
```

```bash
cd my_stock_app_draft && sudo bash deploy/setup_ubuntu.sh
```

如果 repo 是私人的，`git clone` 時會要求帳號密碼。密碼欄請貼 GitHub 的 **Personal Access Token**：到 GitHub「Settings → Developer settings → Fine-grained tokens」建立，權限只給這個 repo 的 Contents: Read-only。

安裝過程中會出現一個 Tailscale 登入網址，用瀏覽器打開並登入。**要跟手機用同一個帳號。**

## 4. 填金鑰與個人設定

金鑰放在 `.env`，這個檔案只有你能讀，也不會進 git：

```bash
nano .env
```

個人設定寫在 `config.local.yaml`。只寫要改的項目就好，更新程式時不會衝突：

```bash
nano config.local.yaml
```

範例：

```yaml
data: {source: finmind}
notify: {channels: [ntfy]}
daytrade: {quote_source: fugle_ws, watch_enabled: true}   # 要做當沖才需要
```

存檔後重啟網頁介面：

```bash
sudo systemctl restart stock-gui
```

最後下載休市日，讓 T+2 和排程能正確跳過連假：

```bash
.venv/bin/python run.py cash holidays-update
```

## 5. 手機連線（Android）

1. Google Play 安裝 **Tailscale**，登入同一個帳號。
2. 打開安裝完成時顯示的網址：`https://<主機名>.<你的tailnet>.ts.net`。
3. Chrome「⋮ → 加到主畫面」，之後就像 App 一樣使用。

如果安裝時 `tailscale serve` 失敗，通常是 tailnet 還沒開啟 HTTPS。到 Tailscale 管理後台的「DNS」頁面，開啟 MagicDNS 和 HTTPS Certificates，再執行：

```bash
sudo tailscale serve --bg 8501
```

## 自動排程

| 服務 | 時間 | 內容 |
|---|---|---|
| `stock-gui` | 一直開著 | 網頁介面，當掉會自動重啟 |
| `stock-auto.timer` | 交易日 16:47 | 更新資料 → 每日建議 → 當沖名單 → 存分 K → 推播 |
| `stock-watch.timer` | 交易日 08:55（預設關閉） | 盤中當沖監控，13:30 自動結束 |

- **休市日會自動跳過**，前提是先跑過 `holidays-update`。
- **開啟盤中監控**：

```bash
sudo systemctl enable --now stock-watch.timer
```

- 想立刻跑一次每日流程（不用等到 16:47）：

```bash
sudo systemctl start stock-auto
```

## 日常維護

查看服務狀態、下次執行時間、最近一次的紀錄：

```bash
bash deploy/status.sh
```

更新到 GitHub 最新版。你的持股、交割帳、`.env`、資料快取都不會被動到：

```bash
bash deploy/update.sh
```

備份持股、交割帳、設定和金鑰：

```bash
bash deploy/backup.sh
```

備份檔建議下載回自己電腦保存。在**自己電腦**的 PowerShell 執行：

```powershell
scp -i 私鑰檔 ubuntu@主機IP:~/stock_backup_*.tar.gz .
```

## 當沖放在雲端

**主機在日本或韓國，對當沖的影響可以忽略。** 到台灣的網路延遲約 30～60 毫秒，而整條流程裡比這慢得多的環節是：

- 報價更新
- 推播送到手機（1～3 秒）
- 你打開券商 App 下單（10 秒以上）

真正要調整的是下面兩件事。

1. **報價來源改用富果 WebSocket**：
   - 在 `config.local.yaml` 設定 `daytrade: {quote_source: fugle_ws, watch_enabled: true}`，並在 `.env` 填 `FUGLE_API_KEY`。
   - 為什麼不用證交所 MIS：它是非官方 API，可能擋海外 IP。
   - WebSocket 的好處：有新報價伺服器就推送，程式每秒檢查一次；不吃 REST 額度；斷線會自動重連。
2. **用券商的停損停利單縮短反應時間**：
   - 收到進場推播、下單成交之後，立刻在券商 App 掛**停損停利（OCO）**或**觸價單**，價位就照推播訊息裡寫的。
   - 出場就交給券商主機自動執行，不用一直盯盤，也不受手機通知延遲影響。

把 `stock-watch` 開啟後，每個交易日 08:55 會自動啟動盤中監控，13:30 收盤自動結束：

```bash
sudo systemctl enable --now stock-watch.timer
```

開始實際交易前，建議先用乾跑模式觀察一天。乾跑只在主機上印出訊號，不會推播：

```bash
.venv/bin/python run.py daytrade watch --dry-run
```

## 注意事項
- **記憶體只有 1 GB 的主機**，跑「策略比較」可能會很慢。可以加 swap：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

- **不要用防火牆把 8501 埠開放到公網**。網頁介面沒有登入機制，請一律透過 Tailscale 連線。
