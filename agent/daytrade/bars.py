"""分 K 來源。

- synth_bars：由日線 OHLC 合成 1 分 K（布朗橋），只用來驗證流程，沒有真實的盤中規律。
- load_csv_bars：讀你自己準備的分 K，檔名 <stock_id>_<YYYY-MM-DD>.csv，欄位 time,open,high,low,close,volume
- FinMind TaiwanStockKBar（需贊助會員）：fetch_finmind_kbar
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests


def synth_bars(day: pd.Timestamp, o, h, l, c, volume, seed=0, minutes=270) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    w = np.concatenate([[0], np.cumsum(rng.normal(0, 1, minutes))])
    t = np.linspace(0, 1, minutes + 1)
    bridge = w - t * w[-1]
    path = o + (c - o) * t
    span = bridge.max() - bridge.min()
    room = (h - l) - abs(c - o)
    path = path + (bridge * (max(room, 0) / span if span > 0 else 0))
    # 平移／壓縮到 [l, h]
    lo_p, hi_p = path.min(), path.max()
    if hi_p - lo_p > 0:
        path = l + (path - lo_p) * (h - l) / (hi_p - lo_p)
    path[0], path[-1] = min(max(o, l), h), min(max(c, l), h)
    times = pd.date_range(pd.Timestamp(day).normalize() + pd.Timedelta(hours=9, minutes=1), periods=minutes, freq="min")
    op, cl = path[:-1], path[1:]
    noise = np.abs(rng.normal(0, (h - l) * 0.01, minutes))
    hi = np.minimum(np.maximum(op, cl) + noise, h)
    lo = np.maximum(np.minimum(op, cl) - noise, l)
    u = 1 + 2 * (np.linspace(-1, 1, minutes) ** 2)          # 開盤、收盤量大
    vol = np.maximum(1, (volume * u / u.sum()).round())
    return pd.DataFrame({"time": times, "open": op.round(2), "high": hi.round(2), "low": lo.round(2),
                         "close": cl.round(2), "volume": vol})


def load_csv_bars(bars_dir, stock_id, day) -> pd.DataFrame | None:
    p = Path(bars_dir) / f"{stock_id}_{pd.Timestamp(day).date()}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    t = df["time"].astype(str)
    if not t.str.contains("-").any():          # 只有 HH:MM 時補上日期
        t = str(pd.Timestamp(day).date()) + " " + t
    df["time"] = pd.to_datetime(t)
    return df[["time", "open", "high", "low", "close", "volume"]]


def fetch_finmind_kbar(stock_id, day, token=None) -> pd.DataFrame | None:
    token = token or os.environ.get("FINMIND_TOKEN")
    r = requests.get("https://api.finmindtrade.com/api/v4/data",
                     params={"dataset": "TaiwanStockKBar", "data_id": stock_id, "start_date": str(pd.Timestamp(day).date())},
                     headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        return None
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["date"] + " " + df["minute"])
    return df.rename(columns={"max": "high", "min": "low"})[["time", "open", "high", "low", "close", "volume"]]
