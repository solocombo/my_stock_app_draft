#!/usr/bin/env bash
# 備份個人資料（持股、交割帳、設定、金鑰）成一個壓縮檔，下載到自己電腦保存：
#   bash deploy/backup.sh
#   （在自己電腦）scp <使用者>@<主機>:~/stock_backup_*.tar.gz .
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
out=~/stock_backup_$(date +%Y%m%d_%H%M).tar.gz
tar czf "$out" --ignore-failed-read portfolio.json cash_ledger.json config.yaml holidays.json .env 2>/dev/null || true
chmod 600 "$out"
echo "已備份到 $out（內含金鑰，請妥善保存）"
