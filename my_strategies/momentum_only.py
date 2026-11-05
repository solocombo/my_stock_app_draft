"""自訂策略範例：純動能，買 MOM 最高的前 N 檔、等權重。

使用方式：config.yaml 設 strategy: "custom:my_strategies.momentum_only"
或 python run.py daily --strategy custom:my_strategies.momentum_only

只要寫一個 decide(obs, pf, cfg) -> list[Decision]，其餘（零股股數、報告、推播、回測）都共用。
"""
from agent.portfolio import Decision

TOP_N = 10


def decide(obs, pf, cfg):
    cur = pf.weights(obs["close"])
    picks = list(obs["MOM"].dropna().sort_values(ascending=False).head(TOP_N).index)
    w = (1 - cfg["portfolio"]["cash_target"]) / len(picks)
    out = [Decision(s, "SELL_ALL", 0, "跌出動能前 N 名", cur.get(s, 0), "custom") for s in pf.positions if s not in picks]
    for s in picks:
        c = cur.get(s, 0)
        if c and abs(w - c) <= cfg["portfolio"]["no_trade_band"]:
            out.append(Decision(s, "HOLD", c, "動能前 N 名，權重差在區間內", c, "custom"))
        else:
            act = "BUY" if not c else ("ADD" if w > c else "TRIM")
            out.append(Decision(s, act, w, f"MOM {obs.loc[s, 'MOM']:.0f}，動能前 {TOP_N} 名", c, "custom"))
    return out
