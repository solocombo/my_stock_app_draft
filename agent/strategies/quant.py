"""量化規則策略：基本面轉折（F、ACC）＋ 動能（MOM、MA）＋ 籌碼（FLOW）。

流程：
1. 持股檢查出場條件 → SELL_ALL
2. 篩選新買進候選（全部門檻都要過）
3. 補足空位；檔數已滿時，候選 S 高出最弱持股 ≥ swap_margin 才替換
4. 依 S 分配目標權重（含單檔／金融上限）
5. 目標與現況差距 > no_trade_band 才 ADD／TRIM，否則 HOLD
"""
from __future__ import annotations

import math

import pandas as pd

from ..portfolio import Decision, Portfolio, cap_weights, name_cap


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def evidence(row) -> dict:
    keys = ["close", "ma20", "ma60", "ma_break_days", "F", "ACC", "MOM", "FLOW", "S", "rev_month"]
    return {k: _num(row.get(k)) for k in keys if k in row}


def exit_reason(row, cfg) -> str | None:
    ex = cfg["exit"]
    if pd.isna(row.get("MOM")):
        return None
    if row["MOM"] < ex["mom_stop"]:
        return f"MOM {row['MOM']:.0f} < {ex['mom_stop']}，動能停損"
    if row.get("ma_break_days", 0) >= ex["ma60_break_days"]:
        return f"連續 {int(row['ma_break_days'])} 日收在 MA60（{row['ma60']}）之下，技術停損"
    if not row.get("fund_na", True):
        if row["F"] < ex["f_exit"]:
            return f"F {row['F']:.0f} < {ex['f_exit']}，基本面轉弱"
        if row["ACC"] <= 0 and row["F"] < cfg["entry"]["f_min"]:
            return f"ACC {row['ACC']:.2f} ≤ 0 且 F {row['F']:.0f} 未達進場標準，基本面轉折結束"
    return None


def entry_fail(row, cfg) -> str | None:
    en = cfg["entry"]
    if row.get("tech_na") or row.get("fund_na"):
        return "資料不足"
    if row["F"] < en["f_min"]:
        return f"F {row['F']:.0f} < {en['f_min']}"
    if not row["ACC"] > en["acc_min_exclusive"]:
        return f"ACC {row['ACC']:.2f} ≤ {en['acc_min_exclusive']}"
    if row["MOM"] < en["mom_min"]:
        return f"MOM {row['MOM']:.0f} < {en['mom_min']}"
    if en.get("require_close_above_ma20") and not row["above_ma20"]:
        return f"收盤 {row['close']} < MA20 {row['ma20']}"
    if pd.isna(row.get("FLOW")) or row["FLOW"] < en["flow_pct_min"]:
        return f"FLOW {row.get('FLOW')} < {en['flow_pct_min']}"
    if row.get("ma_break_days", 0) > 0:
        return "收在 MA60 下"
    return None


def decide(obs: pd.DataFrame, pf: Portfolio, cfg) -> list[Decision]:
    pc = cfg["portfolio"]
    cur_w = pf.weights(obs["close"])
    decisions: list[Decision] = []

    keep = []
    for sid in pf.positions:
        if sid not in obs.index:
            decisions.append(Decision(sid, "HOLD", cur_w.get(sid, 0), "無最新資料，暫不動作", cur_w.get(sid, 0)))
            continue
        row = obs.loc[sid]
        r = exit_reason(row, cfg)
        if r:
            decisions.append(Decision(sid, "SELL_ALL", 0.0, r, cur_w.get(sid, 0), evidence=evidence(row)))
        else:
            keep.append(sid)

    cands = []
    for sid, row in obs.iterrows():
        if sid in pf.positions:
            continue
        if entry_fail(row, cfg) is None:
            cands.append(sid)
    cands.sort(key=lambda s: -obs.loc[s, "S"])

    # 補位
    fin = set(obs.index[obs["is_financial"].fillna(False).astype(bool)])
    slots = pc["target_names"] - len(keep)
    new = []
    for sid in cands:
        if len(new) >= min(max(slots, 0), pc["max_new_buys_per_day"]):
            break
        if sid in fin and len([s for s in keep + new if s in fin]) >= pc["financial_max_names"]:
            continue
        new.append(sid)

    # 替換：檔數已滿時
    swaps = {}
    rest = [s for s in cands if s not in new]
    budget = pc["max_new_buys_per_day"] - len(new)
    for sid in rest:
        if budget <= 0 or not keep:
            break
        weakest = min(keep, key=lambda s: obs.loc[s, "S"] if not pd.isna(obs.loc[s, "S"]) else -1)
        if obs.loc[sid, "S"] - obs.loc[weakest, "S"] >= pc["swap_margin"]:
            keep.remove(weakest)
            new.append(sid)
            swaps[weakest] = sid
            budget -= 1
    for out_sid, in_sid in swaps.items():
        row = obs.loc[out_sid]
        decisions.append(Decision(out_sid, "SELL_ALL", 0.0,
                                  f"換股：{in_sid} S {obs.loc[in_sid, 'S']:.0f} 比本檔 S {row['S']:.0f} 高 ≥ {pc['swap_margin']}",
                                  cur_w.get(out_sid, 0), evidence=evidence(row)))

    names = keep + new
    if not names:
        return decisions
    raw = {s: max(float(obs.loc[s, "S"]), 1.0) ** 2 for s in names}   # S 平方：分數高的權重多一點
    caps = {s: name_cap(cfg, s) for s in names}
    tgt = cap_weights(raw, 1 - pc["cash_target"], caps, fin, pc["financial_max_weight"])

    band = pc["no_trade_band"]
    for sid in keep:
        row, t, c = obs.loc[sid], tgt[sid], cur_w.get(sid, 0)
        gap = t - c
        ev = evidence(row) | {"target_weight": round(t, 4), "current_weight": round(c, 4)}
        if gap > band:
            decisions.append(Decision(sid, "ADD", t, f"續抱並加碼：目標 {t:.1%} 比現況 {c:.1%} 高 {gap:.1%} > {band:.1%}", c, evidence=ev))
        elif gap < -band:
            decisions.append(Decision(sid, "TRIM", t, f"減碼：目標 {t:.1%} 比現況 {c:.1%} 低 {-gap:.1%} > {band:.1%}", c, evidence=ev))
        elif c > caps[sid] + 0.005:   # 漲多超過上限 0.5% 以上才砍回上限，避免為了零頭交易
            decisions.append(Decision(sid, "TRIM", caps[sid], f"減碼：現況 {c:.1%} 超過單檔上限 {caps[sid]:.1%}", c, evidence=ev))
        else:
            decisions.append(Decision(sid, "HOLD", c, f"續抱：目標與現況差 {gap:+.1%}，在 ±{band:.1%} 內不動", c, evidence=ev))
    for sid in new:
        row = obs.loc[sid]
        decisions.append(Decision(
            sid, "BUY", tgt[sid],
            f"新買進：F {row['F']:.0f}、ACC {row['ACC']:+.2f}pp、MOM {row['MOM']:.0f}、FLOW {row['FLOW']:.0f}，S {row['S']:.0f}",
            0.0, evidence=evidence(row) | {"target_weight": round(tgt[sid], 4)}))
    return decisions
