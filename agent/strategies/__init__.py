"""策略註冊。每個策略都是可呼叫物件：

    decide(obs: pd.DataFrame, pf: Portfolio, cfg: dict) -> list[Decision]

名稱：
  library 內建：fundamental, momentum, trend, breakout, value, dividend, lowvol, mean_reversion, chips, multifactor
  quant                 = fundamental（舊名稱）
  llm                   Claude 直接排組合
  <任一策略>+llm         先跑規則策略，買進／加碼前交給 Claude 審查，例如 momentum+llm
  custom:模組[:函式]     自訂 Python 策略，例如 custom:my_strategies.momentum_only
"""
import importlib

from .library import ALL, REGISTRY


def get_strategy(name: str):
    if name == "quant":
        name = "fundamental"
    if name in REGISTRY:
        return REGISTRY[name]
    if name == "llm":
        from .llm import decide as llm_decide
        return llm_decide
    if name.endswith("+llm"):
        from .llm import review_decisions
        base = get_strategy(name[: -len("+llm")])

        def combo(obs, pf, cfg):
            return review_decisions(base(obs, pf, cfg), obs, pf, cfg)
        return combo
    if name.startswith("custom:"):
        mod_name, _, fn = name[len("custom:"):].partition(":")
        return getattr(importlib.import_module(mod_name), fn or "decide")
    raise ValueError(f"unknown strategy: {name}（可用：{', '.join(REGISTRY)}, llm, <名稱>+llm, custom:模組）")


def list_strategies():
    return [(s.name, s.label, s.description) for s in ALL]
