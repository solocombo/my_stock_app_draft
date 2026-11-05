#!/usr/bin/env bash
# 查看服務狀態、下次排程時間、最近的執行紀錄
systemctl --no-pager --lines=0 status stock-gui.service | head -3
echo
systemctl --no-pager list-timers 'stock-*'
echo
echo "== 最近一次每日執行（stock-auto）"
journalctl -u stock-auto.service -n 25 --no-pager
