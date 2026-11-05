"""因子計算：每檔股票產生一列 observation。

F    基本面分數 0-100：近 3 月營收 YoY 與近 6 月 YoY 為正的月數，在股票池內排名
ACC  營收加速度（百分點）：近 3 月平均 YoY − 前 3 月平均 YoY。> 0 代表「基本面轉折向上」
MOM  動能分數 0-100：20/60/120 日報酬加權後排名
FLOW 籌碼百分位 0-100：近 20 日法人買賣超 / 近 20 日均量
技術面：MA20、MA60、ma_break_days（連續收在 MA60 下的天數）
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _pct_rank(s: pd.Series) -> pd.Series:
    return (s.rank(pct=True) * 100).round(1)


def _tech(prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sid, g in prices.sort_values("date").groupby("stock_id"):
        c = g["close"].reset_index(drop=True)
        if len(c) < 121:
            rows.append({"stock_id": sid, "close": c.iloc[-1] if len(c) else np.nan, "tech_na": True})
            continue
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()
        below = (c < ma60).to_numpy()
        breaks = 0
        for b in below[::-1]:
            if not b:
                break
            breaks += 1
        rows.append({
            "stock_id": sid,
            "close": round(float(c.iloc[-1]), 2),
            "ma20": round(float(ma20.iloc[-1]), 2),
            "ma60": round(float(ma60.iloc[-1]), 2),
            "ma_break_days": breaks,
            "ret20": float(c.iloc[-1] / c.iloc[-21] - 1),
            "ret60": float(c.iloc[-1] / c.iloc[-61] - 1),
            "ret120": float(c.iloc[-1] / c.iloc[-121] - 1),
            "avg_vol20": float(g["volume"].tail(20).mean()),
            "tech_na": False,
        })
    return pd.DataFrame(rows)


def _fundamental(revenue: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sid, g in revenue.sort_values("month").groupby("stock_id"):
        s = g.set_index("month")["revenue"]
        yoy = (s / s.shift(12) - 1).dropna()
        if len(yoy) < 6:
            rows.append({"stock_id": sid, "fund_na": True})
            continue
        rows.append({
            "stock_id": sid,
            "rev_month": yoy.index[-1].strftime("%Y-%m"),
            "yoy3": float(yoy.iloc[-3:].mean()),
            "yoy3_prev": float(yoy.iloc[-6:-3].mean()),
            "yoy_pos6": int((yoy.iloc[-6:] > 0).sum()),
            "fund_na": False,
        })
    return pd.DataFrame(rows)


def _flow(flows: pd.DataFrame, as_of) -> pd.DataFrame:
    if flows.empty:
        return pd.DataFrame(columns=["stock_id", "net20", "flow_date"])
    f = flows.sort_values("date")
    out = f.groupby("stock_id").agg(net20=("net_buy", lambda x: x.tail(20).sum()), flow_date=("date", "max")).reset_index()
    return out


def compute_observations(md, weights) -> pd.DataFrame:
    tech = _tech(md.prices)
    fund = _fundamental(md.revenue)
    flow = _flow(md.flows, md.as_of)
    df = tech.merge(fund, on="stock_id", how="left").merge(flow, on="stock_id", how="left")
    df = df.merge(md.info, on="stock_id", how="left")
    df["fund_na"] = df["fund_na"].astype("boolean").fillna(True).astype(bool)

    ok = ~df["tech_na"]
    mom_raw = 0.2 * df["ret20"] + 0.5 * df["ret60"] + 0.3 * df["ret120"]
    df.loc[ok, "MOM"] = _pct_rank(mom_raw[ok])

    fok = ~df["fund_na"]
    df.loc[fok, "ACC"] = ((df["yoy3"] - df["yoy3_prev"]) * 100).round(2)
    f_raw = 0.7 * df["yoy3"].rank(pct=True) + 0.3 * df["yoy_pos6"].rank(pct=True)
    df.loc[fok, "F"] = _pct_rank(f_raw[fok])
    df.loc[fok, "ACC_score"] = _pct_rank(df.loc[fok, "ACC"])

    flow_ratio = df["net20"] / (df["avg_vol20"] * 20)
    fl = flow_ratio.notna() & ok
    df.loc[fl, "FLOW"] = _pct_rank(flow_ratio[fl])

    w = weights
    df["S"] = (w["F"] * df["F"] + w["MOM"] * df["MOM"] + w["FLOW"] * df["FLOW"].fillna(50)
               + w["ACC"] * df["ACC_score"]).round(1)
    df["above_ma20"] = df["close"] >= df["ma20"]
    return df.set_index("stock_id")
