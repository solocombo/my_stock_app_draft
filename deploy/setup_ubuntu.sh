#!/usr/bin/env bash
# 雲端主機一鍵部署（Ubuntu 22.04／24.04，x86 或 ARM 皆可）
#
#   git clone https://github.com/solocombo/my_stock_app_draft.git
#   cd my_stock_app_draft && sudo bash deploy/setup_ubuntu.sh
#
# 會做的事：
#   1. 時區設為台北、安裝 Python
#   2. 建立 .venv 並安裝套件
#   3. 建立 .env（放 API 金鑰，只有你能讀）
#   4. 安裝 systemd 服務：
#        stock-gui        網頁介面（只聽本機 127.0.0.1:8501，不對外開放）
#        stock-auto       交易日 16:47 自動：更新資料 → 建議 → 當沖名單 → 推播
#        stock-watch      交易日 08:55 啟動盤中當沖監控（13:30 自動結束）
#   5. 安裝 Tailscale，用 https://<主機名>.<你的 tailnet>.ts.net 從手機私密連線
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "請用 sudo 執行：sudo bash deploy/setup_ubuntu.sh"; exit 1; fi
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$APP_DIR")}"
echo "== 專案：$APP_DIR，執行身分：$RUN_USER"

echo "== 1/5 時區與系統套件"
timedatectl set-timezone Asia/Taipei
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl >/dev/null

echo "== 2/5 Python 套件"
sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "== 3/5 金鑰檔 .env"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/deploy/env.example" "$APP_DIR/.env"
  chown "$RUN_USER" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"

echo "== 4/5 systemd 服務與排程"
PY="$APP_DIR/.venv/bin/python"
unit() {  # unit <name> <content>
  printf '%s\n' "$2" > "/etc/systemd/system/$1"
}
COMMON="User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=PYTHONUNBUFFERED=1"

unit stock-gui.service "[Unit]
Description=Stock agent web GUI
After=network-online.target
[Service]
$COMMON
ExecStart=$PY -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target"

unit stock-auto.service "[Unit]
Description=Stock agent daily run (data update, plan, notify)
[Service]
Type=oneshot
$COMMON
ExecStart=$PY run.py auto"

unit stock-auto.timer "[Unit]
Description=Run stock-auto on weekdays after market data is ready
[Timer]
OnCalendar=Mon..Fri *-*-* 16:47:00 Asia/Taipei
Persistent=true
[Install]
WantedBy=timers.target"

unit stock-watch.service "[Unit]
Description=Stock agent intraday day-trade watcher
[Service]
$COMMON
ExecStart=$PY run.py daytrade watch
RuntimeMaxSec=6h"

unit stock-watch.timer "[Unit]
Description=Start day-trade watcher before market open
[Timer]
OnCalendar=Mon..Fri *-*-* 08:55:00 Asia/Taipei
[Install]
WantedBy=timers.target"

systemctl daemon-reload
systemctl enable --now stock-gui.service stock-auto.timer
if sudo -u "$RUN_USER" bash -c "cd '$APP_DIR' && $PY -c 'from agent.config import load_config; import sys; sys.exit(0 if load_config()[\"daytrade\"].get(\"watch_enabled\") else 1)'"; then
  systemctl enable --now stock-watch.timer
else
  echo "   （當沖盤中監控預設關閉；要開啟：sudo systemctl enable --now stock-watch.timer）"
fi

echo "== 5/5 Tailscale（私密連線，不用對外開任何連接埠）"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if ! tailscale status >/dev/null 2>&1; then
  echo ">> 接下來會顯示一個登入網址，用瀏覽器打開並登入（跟手機用同一個帳號）"
  tailscale up
fi
tailscale serve --bg 8501 || echo "!! tailscale serve 失敗：請依畫面提示到 Tailscale 後台開啟 HTTPS，再執行 sudo tailscale serve --bg 8501"

echo
echo "完成 ✅"
echo "  手機（裝好 Tailscale 並登入同帳號）開啟："
tailscale serve status 2>/dev/null | grep -o 'https://[^ ]*' | head -1 || true
echo "  下一步：編輯金鑰  nano $APP_DIR/.env   之後執行  sudo systemctl restart stock-gui"
echo "  查看狀態：bash deploy/status.sh"
