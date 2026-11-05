"""每日流程：資料 → 因子 → 策略 → 建議單 → 報告。"""
from __future__ import annotations

from .config import config_sha256
from .factors import compute_observations
from .portfolio import decisions_to_orders
from .report import build_plan
from .strategies import get_strategy


def run_day(md, pf, cfg, strategy_name=None):
    strategy_name = strategy_name or cfg["strategy"]
    obs = compute_observations(md, cfg["score_weights"])
    decisions = get_strategy(strategy_name)(obs, pf, cfg)
    orders = decisions_to_orders(decisions, pf, obs, cfg)
    notes = list(md.notes)
    na = int(obs["tech_na"].sum())
    if na:
        notes.append(f"技術面資料不足 {na} 檔")
    plan = build_plan(md.as_of, cfg, config_sha256(cfg), strategy_name, obs, decisions, orders, pf, notes)
    return plan, obs, decisions, orders
