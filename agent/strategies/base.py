"""規則型策略的共用骨架。

子類別只需定義：
  label          中文名稱
  defaults       參數預設值（可在 config.yaml 的 strategies.<name> 覆蓋）
  evidence_keys  報告中要列出的因子
  score(obs, p)          → Series，0-100，越高越想買
  entry_fail(row, p)     → None 表示可進場，否則回傳不符合的原因
  exit_reason(row, p, held_days) → None 表示續抱，否則回傳出場原因

組合建構（補位、換股、權重上限、no-trade band、硬停損）由 decide() 統一處理。
"""
from __future__ import annotations

import math

import pandas as pd

from ..portfolio import Decision, Portfolio, cap_weights, name_cap


def rank(s: pd.Series, ascending=True) -> pd.Series:
    """0-100 百分位排名；ascending=False 代表數值越小分數越高。"""
    r = s.rank(pct=True, ascending=ascending) * 100
    return r


def fmt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NA"
    if isinstance(v, float):
        return f"{v:.2f}" if abs(v) < 10 else f"{v:.0f}"
    return str(v)


class RuleStrategy:
    name = "base"
    label = "基底"
    description = ""
    defaults: dict = {}
    evidence_keys = ["close", "ma20", "ma60"]

    # ---- 子類別覆寫 ----
    def score(self, obs: pd.DataFrame, p: dict) -> pd.Series:
        raise NotImplementedError

    def entry_fail(self, row, p) -> str | None:
        return None

    def exit_reason(self, row, p, held_days) -> str | None:
        return None

    # ---- 共用 ----
    def params(self, cfg) -> dict:
        return {**self.defaults, **(cfg.get("strategies", {}) or {}).get(self.name, {})}

    def evidence(self, row) -> dict:
        out = {}
        for k in dict.fromkeys(self.evidence_keys + ["close", "ma60", "ma_break_days", "SCORE"]):
            v = row.get(k)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                out[k] = round(float(v), 4) if isinstance(v, (float, int)) and not isinstance(v, bool) else v
        return out

    def describe(self, row) -> str:
        return "、".join(f"{k} {fmt(row.get(k))}" for k in self.evidence_keys if k not in ("close", "ma20", "ma60"))

    def __call__(self, obs, pf, cfg):
        return self.decide(obs, pf, cfg)

    def decide(self, obs: pd.DataFrame, pf: Portfolio, cfg) -> list[Decision]:
        p = self.params(cfg)
        pc = cfg["portfolio"]
        as_of = obs.attrs.get("as_of")
        obs = obs.copy()
        obs["SCORE"] = self.score(obs, p).round(1)
        cur_w = pf.weights(obs["close"])
        decisions: list[Decision] = []
        stop = p.get("stop_loss", pc.get("stop_loss", 0.15))

        keep = []
        for sid in pf.positions:
            if sid not in obs.index or obs.loc[sid, "tech_na"]:
                decisions.append(Decision(sid, "HOLD", cur_w.get(sid, 0), "無足夠資料，暫不動作", cur_w.get(sid, 0), self.name))
                continue
            row = obs.loc[sid]
            held = None
            if as_of is not None and sid in pf.entry_date:
                held = len(pd.bdate_range(pf.entry_date[sid], as_of)) - 1
            r = None
            cost = pf.avg_cost.get(sid)
            if cost and stop and row["close"] <= cost * (1 - stop):
                r = f"硬停損：收盤 {row['close']} 比成本 {cost:.2f} 跌 {row['close'] / cost - 1:.1%}（上限 -{stop:.0%}）"
            r = r or self.exit_reason(row, p, held)
            if r:
                decisions.append(Decision(sid, "SELL_ALL", 0.0, r, cur_w.get(sid, 0), self.name, self.evidence(row)))
            else:
                keep.append(sid)

        cands = [sid for sid, row in obs.iterrows()
                 if sid not in pf.positions and not row["tech_na"] and not pd.isna(row["SCORE"])
                 and self.entry_fail(row, p) is None]
        cands.sort(key=lambda s: -obs.loc[s, "SCORE"])

        fin = set(obs.index[obs["is_financial"].fillna(False).astype(bool)])
        slots = pc["target_names"] - len(keep)
        new: list[str] = []
        for sid in cands:
            if len(new) >= min(max(slots, 0), pc["max_new_buys_per_day"]):
                break
            if sid in fin and len([s for s in keep + new if s in fin]) >= pc["financial_max_names"]:
                continue
            new.append(sid)

        swaps = {}
        budget = pc["max_new_buys_per_day"] - len(new)
        for sid in [s for s in cands if s not in new]:
            if budget <= 0 or not keep:
                break
            weakest = min(keep, key=lambda s: obs.loc[s, "SCORE"] if not pd.isna(obs.loc[s, "SCORE"]) else -1)
            ws = obs.loc[weakest, "SCORE"]
            ws = -1 if pd.isna(ws) else ws
            if obs.loc[sid, "SCORE"] - ws >= pc["swap_margin"]:
                keep.remove(weakest)
                new.append(sid)
                swaps[weakest] = sid
                budget -= 1
        for out_sid, in_sid in swaps.items():
            decisions.append(Decision(out_sid, "SELL_ALL", 0.0,
                                      f"換股：{in_sid} 分數 {obs.loc[in_sid, 'SCORE']:.0f} 比本檔 {fmt(obs.loc[out_sid, 'SCORE'])} 高 ≥ {pc['swap_margin']}",
                                      cur_w.get(out_sid, 0), self.name, self.evidence(obs.loc[out_sid])))

        names = keep + new
        if not names:
            return decisions
        raw = {s: max(float(obs.loc[s, "SCORE"]) if not pd.isna(obs.loc[s, "SCORE"]) else 1.0, 1.0) ** 2 for s in names}
        caps = {s: name_cap(cfg, s) for s in names}
        tgt = cap_weights(raw, 1 - pc["cash_target"], caps, fin, pc["financial_max_weight"])

        band = pc["no_trade_band"]
        for sid in keep:
            row, t, c = obs.loc[sid], tgt[sid], cur_w.get(sid, 0)
            gap = t - c
            ev = self.evidence(row) | {"target_weight": round(t, 4), "current_weight": round(c, 4)}
            if gap > band:
                decisions.append(Decision(sid, "ADD", t, f"續抱並加碼：目標 {t:.1%} 比現況 {c:.1%} 高 {gap:.1%} > {band:.1%}", c, self.name, ev))
            elif gap < -band:
                decisions.append(Decision(sid, "TRIM", t, f"減碼：目標 {t:.1%} 比現況 {c:.1%} 低 {-gap:.1%} > {band:.1%}", c, self.name, ev))
            elif c > caps[sid] + 0.005:
                decisions.append(Decision(sid, "TRIM", caps[sid], f"減碼：現況 {c:.1%} 超過單檔上限 {caps[sid]:.1%}", c, self.name, ev))
            else:
                decisions.append(Decision(sid, "HOLD", c, f"續抱：目標與現況差 {gap:+.1%}，在 ±{band:.1%} 內不動", c, self.name, ev))
        for sid in new:
            row = obs.loc[sid]
            decisions.append(Decision(sid, "BUY", tgt[sid], f"新買進［{self.label}］：{self.describe(row)}，分數 {row['SCORE']:.0f}",
                                      0.0, self.name, self.evidence(row) | {"target_weight": round(tgt[sid], 4)}))
        return decisions
