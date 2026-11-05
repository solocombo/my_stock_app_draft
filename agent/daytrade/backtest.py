"""當沖回測：每天用「前一日為止」的日線選股，用當日分 K 模擬。"""
from __future__ import annotations

import pandas as pd

from .bars import load_csv_bars, synth_bars
from .intraday import run_session
from .screener import build_watchlist


def run_daytrade_backtest(md, cfg, days=60, bars_dir=None, seed=11):
    dates = sorted(md.prices["date"].unique())[-days:]
    px = md.prices.set_index(["date", "stock_id"])
    trades = []
    for k, d in enumerate(dates):
        prev = md.prices["date"][md.prices["date"] < d].max()
        if pd.isna(prev):
            continue
        wl = build_watchlist(md.slice_to(prev), cfg)
        bars = {}
        for p in wl:
            sid = p["stock_id"]
            if bars_dir:
                b = load_csv_bars(bars_dir, sid, d)
            else:
                r = px.loc[(d, sid)]
                b = synth_bars(d, r["open"], r["high"], r["low"], r["close"], r["volume"], seed=seed + k * 1000 + sum(map(ord, sid)))
            if b is not None:
                bars[sid] = b
        trades += [t.dict() for t in run_session(bars, wl, cfg)]
    df = pd.DataFrame(trades)
    return df, summarize(df, len(dates))


def summarize(df, n_days):
    if df.empty:
        return {"days": n_days, "trades": 0}
    daily = df.groupby("date")["pnl"].sum()
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    return {
        "days": n_days, "trades": len(df), "trade_days": int(daily.size),
        "win_rate": round(float((df["pnl"] > 0).mean()), 3),
        "total_pnl": int(df["pnl"].sum()), "total_cost": int(df["cost"].sum()),
        "avg_win": int(wins["pnl"].mean()) if len(wins) else 0,
        "avg_loss": int(losses["pnl"].mean()) if len(losses) else 0,
        "profit_factor": round(float(wins["pnl"].sum() / -losses["pnl"].sum()), 2) if len(losses) and losses["pnl"].sum() < 0 else None,
        "worst_day": int(daily.min()), "best_day": int(daily.max()),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }
