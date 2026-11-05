"""選股策略庫：市面上常見的 10 種做法。參數都可以在 config.yaml 的 strategies.<名稱> 覆蓋。

| 名稱            | 中文       | 核心邏輯 |
|-----------------|------------|----------|
| fundamental     | 基本面轉折 | 營收成長且加速 + 動能 + 籌碼（原本的 quant） |
| momentum        | 動能       | 中期漲幅強勢股，避開短線過熱 |
| trend           | 均線多頭   | MA5 > MA20 > MA60 且 MA60 上彎 |
| breakout        | 突破       | 接近 250 日新高且量增 |
| value           | 價值       | 低本益比、低股價淨值比，避開持續下跌的價值陷阱 |
| dividend        | 高股息     | 高殖利率 + 低波動 |
| lowvol          | 低波動     | 波動度最低的一群，且沒有跌破長期均線 |
| mean_reversion  | 拉回買進   | 長多格局中 RSI 超賣，反彈或持有到期出場 |
| chips           | 籌碼       | 法人連續買超 |
| multifactor     | 多因子     | 價值、動能、品質、低波動、籌碼排名平均 |
"""
from __future__ import annotations

import pandas as pd

from .base import RuleStrategy, rank


class Fundamental(RuleStrategy):
    name, label = "fundamental", "基本面轉折"
    description = "營收 YoY 為正且在加速（ACC>0），配合動能與法人買超"
    defaults = {"f_min": 60, "acc_min": 0, "mom_min": 50, "flow_min": 20, "mom_stop": 25, "ma60_break": 3, "f_exit": 40}
    evidence_keys = ["F", "ACC", "MOM", "FLOW", "rev_month"]

    def score(self, obs, p):
        return obs["S"]

    def entry_fail(self, row, p):
        if row["fund_na"]:
            return "無營收資料"
        if row["F"] < p["f_min"]:
            return f"F {row['F']:.0f} < {p['f_min']}"
        if not row["ACC"] > p["acc_min"]:
            return f"ACC {row['ACC']:.2f} ≤ {p['acc_min']}"
        if row["MOM"] < p["mom_min"]:
            return f"MOM {row['MOM']:.0f} < {p['mom_min']}"
        if row["close"] < row["ma20"]:
            return "收盤 < MA20"
        if pd.isna(row.get("FLOW")) or row["FLOW"] < p["flow_min"]:
            return f"FLOW < {p['flow_min']}"
        if row["ma_break_days"] > 0:
            return "收在 MA60 下"
        return None

    def exit_reason(self, row, p, held):
        if row["MOM"] < p["mom_stop"]:
            return f"MOM {row['MOM']:.0f} < {p['mom_stop']}，動能停損"
        if row["ma_break_days"] >= p["ma60_break"]:
            return f"連續 {int(row['ma_break_days'])} 日收在 MA60（{row['ma60']}）之下，技術停損"
        if not row["fund_na"]:
            if row["F"] < p["f_exit"]:
                return f"F {row['F']:.0f} < {p['f_exit']}，基本面轉弱"
            if row["ACC"] <= 0 and row["F"] < p["f_min"]:
                return f"ACC {row['ACC']:.2f} ≤ 0 且 F {row['F']:.0f} 未達進場標準，轉折結束"
        return None


class Momentum(RuleStrategy):
    name, label = "momentum", "動能"
    description = "20/60/120 日報酬加權排名前段，排除近 20 日已大漲（過熱）的股票"
    defaults = {"mom_min": 75, "max_ret20": 0.30, "mom_exit": 40, "ma60_break": 3}
    evidence_keys = ["MOM", "ret20", "ret60", "ret120"]

    def score(self, obs, p):
        return 0.6 * obs["MOM"] + 0.4 * rank(obs["ret250"])

    def entry_fail(self, row, p):
        if row["MOM"] < p["mom_min"]:
            return "動能不足"
        if row["ret20"] > p["max_ret20"]:
            return f"近 20 日已漲 {row['ret20']:.0%}，過熱"
        if row["close"] < row["ma60"]:
            return "收在 MA60 下"
        return None

    def exit_reason(self, row, p, held):
        if row["MOM"] < p["mom_exit"]:
            return f"MOM {row['MOM']:.0f} < {p['mom_exit']}，動能轉弱"
        if row["ma_break_days"] >= p["ma60_break"]:
            return f"連續 {int(row['ma_break_days'])} 日收在 MA60 下"
        return None


class Trend(RuleStrategy):
    name, label = "trend", "均線多頭"
    description = "MA5 > MA20 > MA60 多頭排列、MA60 上彎；跌破 MA60 或均線死叉出場"
    defaults = {"min_slope": 0.0, "ma60_break": 2}
    evidence_keys = ["ma5", "ma20", "ma60", "ma60_slope", "ret60"]

    def score(self, obs, p):
        return 0.5 * rank(obs["ma60_slope"]) + 0.5 * rank(obs["ret60"])

    def entry_fail(self, row, p):
        if not (row["ma5"] > row["ma20"] > row["ma60"]):
            return "非多頭排列"
        if row["ma60_slope"] <= p["min_slope"]:
            return "MA60 未上彎"
        if row["close"] < row["ma20"]:
            return "收盤 < MA20"
        return None

    def exit_reason(self, row, p, held):
        if row["ma_break_days"] >= p["ma60_break"]:
            return f"連續 {int(row['ma_break_days'])} 日收在 MA60 下"
        if row["ma20"] < row["ma60"]:
            return "MA20 跌破 MA60（死亡交叉）"
        return None


class Breakout(RuleStrategy):
    name, label = "breakout", "突破"
    description = "收盤距 250 日高點 ≤ 3% 且 5 日均量 ≥ 60 日均量 1.5 倍；跌破 MA20 兩天或自高點回落 10% 出場"
    defaults = {"near_high": -0.03, "vol_ratio_min": 1.5, "ma20_break": 2, "trail": -0.10}
    evidence_keys = ["dist_high250", "vol_ratio", "ret20", "dd20"]

    def score(self, obs, p):
        return 0.5 * rank(obs["vol_ratio"]) + 0.5 * rank(obs["dist_high250"])

    def entry_fail(self, row, p):
        if row["dist_high250"] < p["near_high"]:
            return f"距高點 {row['dist_high250']:.1%}"
        if row["vol_ratio"] < p["vol_ratio_min"]:
            return f"量比 {row['vol_ratio']:.2f} 不足"
        if row["close"] < row["ma20"]:
            return "收盤 < MA20"
        return None

    def exit_reason(self, row, p, held):
        if row["ma20_break_days"] >= p["ma20_break"]:
            return f"連續 {int(row['ma20_break_days'])} 日收在 MA20 下，突破失敗"
        if row["dd20"] <= p["trail"]:
            return f"自 20 日高點回落 {row['dd20']:.1%}，移動停利"
        return None


class Value(RuleStrategy):
    name, label = "value", "價值"
    description = "本益比、股價淨值比在股票池內偏低；MOM 太低（持續下跌）的不買，避免價值陷阱"
    defaults = {"per_max": 15, "pbr_max": 2.0, "mom_floor": 30, "per_exit": 25, "mom_exit": 10}
    evidence_keys = ["per", "pbr", "dy", "MOM"]

    def score(self, obs, p):
        per = obs["per"].where(obs["per"] > 0)
        return 0.5 * rank(per, ascending=False) + 0.5 * rank(obs["pbr"], ascending=False)

    def entry_fail(self, row, p):
        if pd.isna(row.get("per")) or row["per"] <= 0 or row["per"] > p["per_max"]:
            return f"PER {row.get('per')} 不符"
        if row["pbr"] > p["pbr_max"]:
            return f"PBR {row['pbr']} > {p['pbr_max']}"
        if row["MOM"] < p["mom_floor"]:
            return f"MOM {row['MOM']:.0f} 太低，可能是價值陷阱"
        return None

    def exit_reason(self, row, p, held):
        if not pd.isna(row.get("per")) and row["per"] > p["per_exit"]:
            return f"PER {row['per']:.1f} > {p['per_exit']}，評價已修復"
        if row["MOM"] < p["mom_exit"]:
            return f"MOM {row['MOM']:.0f}，股價持續轉弱"
        return None


class Dividend(RuleStrategy):
    name, label = "dividend", "高股息"
    description = "殖利率 ≥ 5% 且波動度不高；殖利率掉到 3.5% 以下出場"
    defaults = {"dy_min": 5.0, "vol_pct_max": 60, "dy_exit": 3.5, "stop_loss": 0.15}
    evidence_keys = ["dy", "per", "vol60"]

    def score(self, obs, p):
        return 0.7 * rank(obs["dy"]) + 0.3 * rank(obs["vol60"], ascending=False)

    def entry_fail(self, row, p):
        if pd.isna(row.get("dy")) or row["dy"] < p["dy_min"]:
            return "殖利率不足"
        if pd.isna(row.get("per")) or row["per"] <= 0:
            return "虧損公司"
        return None

    def decide(self, obs, pf, cfg):   # 波動度門檻要用股票池百分位
        obs = obs.copy()
        vp = obs["vol60"].rank(pct=True) * 100
        obs.loc[vp > self.params(cfg)["vol_pct_max"], "dy"] = obs["dy"].where(obs.index.isin(list(pf.positions)))
        return super().decide(obs, pf, cfg)

    def exit_reason(self, row, p, held):
        if not pd.isna(row.get("dy")) and row["dy"] < p["dy_exit"]:
            return f"殖利率 {row['dy']:.2f}% < {p['dy_exit']}%"
        return None


class LowVol(RuleStrategy):
    name, label = "lowvol", "低波動"
    description = "60 日波動度最低的 30%，且收盤在 MA60 附近之上"
    defaults = {"vol_pct_max": 30, "vol_pct_exit": 60, "ma60_break": 5}
    evidence_keys = ["vol60", "ret60"]

    def score(self, obs, p):
        return rank(obs["vol60"], ascending=False)

    def entry_fail(self, row, p):
        if row["SCORE"] < 100 - p["vol_pct_max"]:
            return "波動度不夠低"
        if row["close"] < row["ma60"] * 0.97:
            return "跌破 MA60"
        return None

    def exit_reason(self, row, p, held):
        if row.get("SCORE", 100) < 100 - p["vol_pct_exit"]:
            return f"波動度上升到股票池前 {100 - row['SCORE']:.0f}%"
        if row["ma_break_days"] >= p["ma60_break"]:
            return f"連續 {int(row['ma_break_days'])} 日收在 MA60 下"
        return None


class MeanReversion(RuleStrategy):
    name, label = "mean_reversion", "拉回買進"
    description = "長多（收盤 > MA60 且 MA60 上彎）中 RSI ≤ 35 或跌到布林下軌；RSI ≥ 60、站回 MA20 或持有 10 天出場"
    defaults = {"rsi_max": 35, "bb_max": 0.05, "rsi_exit": 60, "max_days": 10, "stop_loss": 0.08}
    evidence_keys = ["rsi14", "bb_pct", "ret5", "ma60_slope"]

    def score(self, obs, p):
        return rank(obs["rsi14"], ascending=False)

    def entry_fail(self, row, p):
        if row["close"] < row["ma60"] or row["ma60_slope"] <= 0:
            return "非長多格局"
        if not (row["rsi14"] <= p["rsi_max"] or row["bb_pct"] <= p["bb_max"]):
            return "未超賣"
        return None

    def exit_reason(self, row, p, held):
        if row["rsi14"] >= p["rsi_exit"]:
            return f"RSI {row['rsi14']:.0f} ≥ {p['rsi_exit']}，反彈完成"
        if row["close"] >= row["ma20"] and row["rsi14"] >= 50:
            return "站回 MA20，均值回歸完成"
        if held is not None and held >= p["max_days"]:
            return f"已持有 {held} 個交易日，達上限 {p['max_days']}"
        return None


class Chips(RuleStrategy):
    name, label = "chips", "籌碼"
    description = "三大法人連續買超 ≥ 3 天，且 20 日買超力道在前 30%"
    defaults = {"streak_min": 3, "flow_min": 70, "flow_exit": 30, "ma60_break": 3}
    evidence_keys = ["FLOW", "inst_streak", "ret20"]

    def score(self, obs, p):
        return 0.6 * obs["FLOW"] + 0.4 * rank(obs["inst_streak"])

    def entry_fail(self, row, p):
        if pd.isna(row.get("FLOW")) or row["FLOW"] < p["flow_min"]:
            return "籌碼力道不足"
        if pd.isna(row.get("inst_streak")) or row["inst_streak"] < p["streak_min"]:
            return "法人未連買"
        if row["close"] < row["ma20"]:
            return "收盤 < MA20"
        return None

    def exit_reason(self, row, p, held):
        if not pd.isna(row.get("FLOW")) and row["FLOW"] < p["flow_exit"]:
            return f"FLOW {row['FLOW']:.0f} < {p['flow_exit']}，法人轉賣"
        if row["ma_break_days"] >= p["ma60_break"]:
            return f"連續 {int(row['ma_break_days'])} 日收在 MA60 下"
        return None


class MultiFactor(RuleStrategy):
    name, label = "multifactor", "多因子"
    description = "價值（低 PER/PBR）、動能、品質（營收成長 F）、低波動、籌碼五項排名平均"
    defaults = {"score_min": 60, "score_exit": 40, "weights": {"value": 1, "momentum": 1, "quality": 1, "lowvol": 1, "flow": 1}}
    evidence_keys = ["per", "pbr", "MOM", "F", "vol60", "FLOW"]

    def score(self, obs, p):
        w = p["weights"]
        per = obs["per"].where(obs["per"] > 0)
        parts = {
            "value": (rank(per, ascending=False) + rank(obs["pbr"], ascending=False)) / 2,
            "momentum": obs["MOM"],
            "quality": obs["F"],
            "lowvol": rank(obs["vol60"], ascending=False),
            "flow": obs["FLOW"],
        }
        num = sum(w[k] * v.fillna(50) for k, v in parts.items())
        return num / sum(w.values())

    def entry_fail(self, row, p):
        if row["SCORE"] < p["score_min"]:
            return "綜合分數不足"
        if row["close"] < row["ma60"] * 0.95:
            return "跌破 MA60 過多"
        return None

    def exit_reason(self, row, p, held):
        if row["SCORE"] < p["score_exit"]:
            return f"綜合分數 {row['SCORE']:.0f} < {p['score_exit']}"
        return None


ALL = [Fundamental(), Momentum(), Trend(), Breakout(), Value(), Dividend(), LowVol(), MeanReversion(), Chips(), MultiFactor()]
REGISTRY = {s.name: s for s in ALL}
