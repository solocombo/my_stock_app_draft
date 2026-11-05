"""因子計算：每檔股票一列 observation，所有策略共用。

基本面   F（0-100 營收成長排名）、ACC（營收 YoY 加速度，百分點）
動能     MOM（0-100）、ret5/20/60/120/250
趨勢     ma5/20/60、ma60_slope、ma_break_days（連續收在 MA60 下）、ma20_break_days
突破     dist_high250（距 250 日高點 %）、vol_ratio（5 日量 / 60 日量）、dd20（距 20 日高點回落 %）
擺盪     rsi14、bb_pct（布林 %B，0=下軌、1=上軌）
風險     vol60（年化波動）、atr14_pct
估值     per、pbr、dy（殖利率 %）
籌碼     FLOW（0-100）、inst_streak（法人連買天數，負數 = 連賣）
流動性   avg_value20（20 日均成交值）
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _pct_rank(s: pd.Series) -> pd.Series:
    return (s.rank(pct=True) * 100).round(1)


def _streak(mask: np.ndarray) -> int:
    n = 0
    for b in mask[::-1]:
        if not b:
            break
        n += 1
    return n


def _rsi(c: pd.Series, n=14) -> float:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up.iloc[-1] / dn.iloc[-1] if dn.iloc[-1] > 0 else np.inf
    return float(100 - 100 / (1 + rs))


def _tech(prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sid, g in prices.sort_values("date").groupby("stock_id"):
        g = g.reset_index(drop=True)
        c = g["close"]
        if len(c) < 121:
            rows.append({"stock_id": sid, "close": c.iloc[-1] if len(c) else np.nan, "tech_na": True})
            continue
        ma5, ma20, ma60 = (c.rolling(n).mean() for n in (5, 20, 60))
        hi = g["high"] if "high" in g else c
        lo = g["low"] if "low" in g else c
        tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        sd20 = c.rolling(20).std()
        last = len(c) - 1
        n250 = min(250, len(c))
        rows.append({
            "stock_id": sid,
            "close": round(float(c.iloc[-1]), 2),
            "ma5": round(float(ma5.iloc[-1]), 2),
            "ma20": round(float(ma20.iloc[-1]), 2),
            "ma60": round(float(ma60.iloc[-1]), 2),
            "ma60_slope": float(ma60.iloc[-1] / ma60.iloc[-21] - 1),
            "ma_break_days": _streak((c < ma60).to_numpy()),
            "ma20_break_days": _streak((c < ma20).to_numpy()),
            "ret5": float(c.iloc[-1] / c.iloc[-6] - 1),
            "ret20": float(c.iloc[-1] / c.iloc[-21] - 1),
            "ret60": float(c.iloc[-1] / c.iloc[-61] - 1),
            "ret120": float(c.iloc[-1] / c.iloc[-121] - 1),
            "ret250": float(c.iloc[-1] / c.iloc[-n250] - 1),
            "dist_high250": float(c.iloc[-1] / hi.tail(n250).max() - 1),
            "dd20": float(c.iloc[-1] / c.tail(20).max() - 1),
            "vol_ratio": float(g["volume"].tail(5).mean() / g["volume"].tail(60).mean()),
            "rsi14": round(_rsi(c), 1),
            "bb_pct": float((c.iloc[-1] - (ma20.iloc[-1] - 2 * sd20.iloc[-1])) / (4 * sd20.iloc[-1])) if sd20.iloc[-1] > 0 else 0.5,
            "vol60": float(c.pct_change().tail(60).std() * np.sqrt(252)),
            "atr14_pct": float(tr.tail(14).mean() / c.iloc[last]),
            "avg_vol20": float(g["volume"].tail(20).mean()),
            "avg_value20": float((g["volume"] * c).tail(20).mean()),
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
    return pd.DataFrame(rows, columns=["stock_id", "rev_month", "yoy3", "yoy3_prev", "yoy_pos6", "fund_na"])


def _flow(flows: pd.DataFrame) -> pd.DataFrame:
    if flows.empty:
        return pd.DataFrame(columns=["stock_id", "net20", "inst_streak"])
    rows = []
    for sid, g in flows.sort_values("date").groupby("stock_id"):
        n = g["net_buy"].to_numpy()
        buy, sell = _streak(n > 0), _streak(n < 0)
        rows.append({"stock_id": sid, "net20": float(n[-20:].sum()), "inst_streak": buy if buy else -sell})
    return pd.DataFrame(rows)


def _valuation(val: pd.DataFrame) -> pd.DataFrame:
    if val is None or val.empty:
        return pd.DataFrame(columns=["stock_id", "per", "pbr", "dy"])
    return val.sort_values("date").groupby("stock_id")[["per", "pbr", "dy"]].last().reset_index()


def compute_observations(md, weights) -> pd.DataFrame:
    df = (_tech(md.prices)
          .merge(_fundamental(md.revenue), on="stock_id", how="left")
          .merge(_flow(md.flows), on="stock_id", how="left")
          .merge(_valuation(getattr(md, "valuation", None)), on="stock_id", how="left")
          .merge(md.info, on="stock_id", how="left"))
    df["fund_na"] = df["fund_na"].astype("boolean").fillna(True).astype(bool)
    df["tech_na"] = df["tech_na"].astype(bool)

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
    out = df.set_index("stock_id")
    out.attrs["as_of"] = md.as_of
    return out
