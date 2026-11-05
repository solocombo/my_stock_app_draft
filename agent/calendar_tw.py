"""台股交易日曆：週一到週五，扣除休市日。

休市日存在 holidays.json（["2026-01-01", ...]）。用 `python run.py cash holidays-update`
從證交所 OpenAPI 下載；沒有設定時只排除週末，T+2 日期在連假前後可能不準，報告會提示。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import requests

HOLIDAY_FILE = Path("holidays.json")
TWSE_HOLIDAY_API = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"


def load_holidays(path=HOLIDAY_FILE) -> set[pd.Timestamp]:
    p = Path(path)
    if not p.exists():
        return set()
    return {pd.Timestamp(d).normalize() for d in json.loads(p.read_text(encoding="utf-8"))}


def is_trading_day(d, holidays) -> bool:
    d = pd.Timestamp(d).normalize()
    return d.weekday() < 5 and d not in holidays


def add_trading_days(d, n, holidays) -> pd.Timestamp:
    d = pd.Timestamp(d).normalize()
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        d += pd.Timedelta(days=step)
        if is_trading_day(d, holidays):
            left -= 1
    return d


def trading_days(start, end, holidays) -> list[pd.Timestamp]:
    return [d for d in pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize())
            if is_trading_day(d, holidays)]


def settle_date(trade_date, holidays, t=2) -> pd.Timestamp:
    return add_trading_days(trade_date, t, holidays)


def _parse_twse_date(s: str) -> date | None:
    s = str(s).strip().replace("/", "").replace("-", "")
    if len(s) == 7:          # 民國年 1150101
        return date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
    if len(s) == 8:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return None


def update_holidays(path=HOLIDAY_FILE) -> list[str]:
    """下載證交所休市日。注意：清單中「開始交易／最後交易日」等非休市項目會被排除。"""
    rows = requests.get(TWSE_HOLIDAY_API, timeout=30).json()
    days = set(load_holidays(path))
    for r in rows:
        name = str(r.get("Name", ""))
        if any(k in name for k in ("開始交易", "最後交易", "交易日")):
            continue
        d = _parse_twse_date(r.get("Date", ""))
        if d and d.weekday() < 5:
            days.add(pd.Timestamp(d))
    out = sorted(str(d.date()) for d in days)
    Path(path).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
