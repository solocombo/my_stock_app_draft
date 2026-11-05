"""盤前選股：用到前一交易日為止的日線，挑出適合當沖的股票並給出方向與關鍵價位。

條件（config.yaml → daytrade）：
- 流動性：20 日均成交值 ≥ min_avg_value（避免滑價）
- 波動：ATR% 介於 atr_pct_min～atr_pct_max（太小賺不到手續費，太大風險失控）
- 熱度：前一日量 ≥ 20 日均量 × min_volume_ratio
- 方向：收在當日高檔且站上 MA20 → 偏多；收在低檔且跌破 MA20 → 偏空（需有先賣後買資格）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .intraday import round_tick


def build_watchlist(md, cfg) -> list[dict]:
    d = cfg["daytrade"]
    rows = []
    names = md.info.set_index("stock_id")
    for sid, g in md.prices.sort_values("date").groupby("stock_id"):
        if len(g) < 25:
            continue
        g = g.tail(60).reset_index(drop=True)
        last = g.iloc[-1]
        c = g["close"]
        tr = pd.concat([g["high"] - g["low"], (g["high"] - c.shift()).abs(), (g["low"] - c.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        atr_pct = atr / last["close"]
        avg_value = float((g["volume"] * c).tail(20).mean())
        vol_ratio = float(last["volume"] / g["volume"].tail(21).head(20).mean())
        rng_ = last["high"] - last["low"]
        close_pos = float((last["close"] - last["low"]) / rng_) if rng_ > 0 else 0.5
        ma20 = float(c.tail(20).mean())
        if avg_value < d["min_avg_value"] or not (d["atr_pct_min"] <= atr_pct <= d["atr_pct_max"]) or vol_ratio < d["min_volume_ratio"]:
            continue
        if close_pos >= 0.6 and last["close"] >= ma20:
            side, strength = "long", close_pos
        elif d.get("allow_short", False) and close_pos <= 0.4 and last["close"] < ma20:
            side, strength = "short", 1 - close_pos
        else:
            continue
        pivot = (last["high"] + last["low"] + last["close"]) / 3
        info = names.loc[sid] if sid in names.index else {}
        rows.append({
            "stock_id": sid, "name": info.get("name", "") if len(info) else "",
            "market": info.get("market", "tse") if len(info) else "tse",
            "side": side, "strategy": d["strategy"],
            "prev_close": float(last["close"]), "prev_high": float(last["high"]), "prev_low": float(last["low"]),
            "pivot": round_tick(pivot, up=True), "atr": round(atr, 2), "atr_pct": round(atr_pct, 4),
            "vol_ratio": round(vol_ratio, 2), "avg_value20": round(avg_value),
            "score": round(float(np.mean([strength, min(vol_ratio / 3, 1), min(atr_pct / d["atr_pct_max"], 1)])) * 100, 1),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows[: d["watchlist_size"]]


def watchlist_markdown(as_of, trade_date, wl, cfg) -> str:
    d = cfg["daytrade"]
    L = [f"# 當沖觀察名單 {trade_date}（依 {as_of} 收盤資料）",
         f"策略：{d['strategy']}｜每筆風險 {d['capital'] * d['risk_per_trade']:,.0f} 元｜單日虧損上限 "
         f"{d['capital'] * d['daily_max_loss']:,.0f} 元｜最多 {d['max_trades_per_day']} 筆｜{d['flat_time']} 前全部平倉", ""]
    if not wl:
        return "\n".join(L + ["今天沒有符合條件的股票，休息也是一種策略。"])
    L += ["| 代號 | 名稱 | 方向 | 昨收 | 昨高 | 昨低 | ATR | 量比 | 分數 |", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    L += [f"| {r['stock_id']} | {r['name']} | {'做多' if r['side'] == 'long' else '做空'} | {r['prev_close']} | {r['prev_high']} | "
          f"{r['prev_low']} | {r['atr']} | {r['vol_ratio']} | {r['score']} |" for r in wl]
    if d["strategy"] == "orb":
        L += ["", f"**進場規則（ORB）**：等開盤 {d['orb_minutes']} 分鐘形成區間；做多在突破區間高點 +1 檔時進場，"
              f"停損設在區間低點（最多 {d.get('max_risk_atr', 0.6)} ATR），停利 {d['rr_target']}R；{d['entry_cutoff']} 後不開新倉。"]
    else:
        L += ["", f"**進場規則（VWAP）**：{d.get('vwap_start', '09:30')} 後，價格跌破 VWAP 再收回其上（紅K）時進場，"
              f"停損設在當日低點下一檔（最多 {d.get('max_risk_atr', 0.6)} ATR），停利 {d['rr_target']}R。"]
    L += ["", "盤中執行 `python run.py daytrade watch`，觸發時會推播精確的價位與股數。",
          "⚠️ 當沖需先簽署當沖契約並具備資格；先賣後買受券源與平盤下限制。本名單僅供參考。"]
    return "\n".join(L)
