"""策略註冊。每個策略都是一個函式：

    decide(obs: pd.DataFrame, pf: Portfolio, cfg: dict) -> list[Decision]

obs 是 factors.compute_observations 的輸出（index = stock_id）。
自訂策略：在任意模組寫一個 decide 函式，config 填 strategy: "custom:模組路徑"
（例如 custom:my_strategies.momentum_only），或 "custom:模組:函式名"。
"""
import importlib

from .quant import decide as quant_decide


def get_strategy(name: str):
    if name == "quant":
        return quant_decide
    if name == "llm":
        from .llm import decide as llm_decide
        return llm_decide
    if name == "quant+llm":
        from .llm import review_decisions

        def combo(obs, pf, cfg):
            return review_decisions(quant_decide(obs, pf, cfg), obs, pf, cfg)
        return combo
    if name.startswith("custom:"):
        target = name[len("custom:"):]
        mod_name, _, fn = target.partition(":")
        mod = importlib.import_module(mod_name)
        return getattr(mod, fn or "decide")
    raise ValueError(f"unknown strategy: {name}")
