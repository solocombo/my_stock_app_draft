"""簡易回測：每隔 N 個交易日跑一次策略，以當日收盤價成交（含手續費與證交稅）。

用來比較參數變體，不是精確模擬（沒有滑價、漲跌停、除權息調整）。
多起始日比較：同一組參數從不同日期開始跑，看變體是否穩定勝出，避免只是運氣。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import deep_merge
from .factors import compute_observations
from .portfolio import Portfolio, apply_orders, decisions_to_orders
from .strategies import get_strategy


def run_backtest(md, cfg, start, end=None, rebalance_every=5, strategy_name=None, obs_cache=None):
    strat = get_strategy(strategy_name or cfg["strategy"])
    dates = sorted(md.prices["date"].unique())
    dates = [d for d in dates if d >= pd.Timestamp(start) and (end is None or d <= pd.Timestamp(end))]
    pf = Portfolio(cash=float(cfg["capital"]))
    px_wide = md.prices.pivot(index="date", columns="stock_id", values="close")
    nav_rows, n_trades = [], 0
    for i, d in enumerate(dates):
        if i % rebalance_every == 0:
            if obs_cache is not None and d in obs_cache:
                obs = obs_cache[d]
            else:
                obs = compute_observations(md.slice_to(d), cfg["score_weights"])
                if obs_cache is not None:
                    obs_cache[d] = obs
            orders = decisions_to_orders(strat(obs, pf, cfg), pf, obs, cfg)
            pf = apply_orders(pf, orders, cfg, trade_date=d)
            n_trades += len(orders)
        nav_rows.append((d, pf.nav(px_wide.loc[d]), len(pf.positions)))
    nav = pd.DataFrame(nav_rows, columns=["date", "nav", "n_names"]).set_index("date")
    bench = px_wide.loc[dates].pct_change().mean(axis=1).fillna(0).add(1).cumprod() * cfg["capital"]
    nav["benchmark_equal_weight"] = bench
    return nav, summarize(nav, n_trades)


def summarize(nav, n_trades=0):
    r = nav["nav"].pct_change().dropna()
    years = max(len(r) / 252, 1e-9)
    total = nav["nav"].iloc[-1] / nav["nav"].iloc[0] - 1
    b = nav["benchmark_equal_weight"]
    return {
        "total_return": round(float(total), 4),
        "cagr": round(float((1 + total) ** (1 / years) - 1), 4),
        "vol": round(float(r.std() * np.sqrt(252)), 4),
        "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2) if r.std() > 0 else None,
        "max_drawdown": round(float((nav["nav"] / nav["nav"].cummax() - 1).min()), 4),
        "benchmark_return": round(float(b.iloc[-1] / b.iloc[0] - 1), 4),
        "trades": n_trades,
        "avg_names": round(float(nav["n_names"].mean()), 1),
    }


def compare_variant(md, cfg, variant: dict, starts, length_days=120, rebalance_every=5):
    """base vs variant 在多個起始日比較總報酬。回傳每段差異與「勝率、中位數差」。"""
    vcfg = deep_merge(cfg, variant)
    dates = sorted(md.prices["date"].unique())
    rows = []
    for s in starts:
        idx = dates.index(min(dates, key=lambda d: abs(d - pd.Timestamp(s))))
        e = dates[min(idx + length_days, len(dates) - 1)]
        _, a = run_backtest(md, cfg, dates[idx], e, rebalance_every)
        _, b = run_backtest(md, vcfg, dates[idx], e, rebalance_every)
        rows.append({"start": str(pd.Timestamp(dates[idx]).date()), "base": a["total_return"],
                     "variant": b["total_return"], "diff": round(b["total_return"] - a["total_return"], 4)})
    df = pd.DataFrame(rows)
    verdict = {"win_rate": round(float((df["diff"] > 0).mean()), 2), "median_diff": float(df["diff"].median())}
    verdict["pass"] = verdict["win_rate"] >= 2 / 3 and verdict["median_diff"] > 0
    return df, verdict


def compare_strategies(md, cfg, names, start, end=None, rebalance_every=5):
    """同一段期間、同一份因子快取，比較多個策略。"""
    cache, rows = {}, []
    for n in names:
        _, s = run_backtest(md, cfg, start, end, rebalance_every, n, obs_cache=cache)
        rows.append({"strategy": n, **s})
    return pd.DataFrame(rows).sort_values("sharpe", ascending=False, na_position="last")
