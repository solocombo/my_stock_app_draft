#!/usr/bin/env bash
# 更新程式到 GitHub 最新版並重啟（你的持股、交割帳、.env、data/ 不會被動到）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart stock-gui
echo "已更新到 $(git log -1 --format='%h %s')"
