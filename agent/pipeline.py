"""每日流程：資料 → 因子 → 策略 → 建議單（受 T+2 現金池限制）→ 報告。"""
from __future__ import annotations

from dataclasses import replace

from .calendar_tw import add_trading_days, load_holidays, settle_date
from .cashflow import orders_as_pending
from .config import config_sha256
from .factors import compute_observations
from .portfolio import decisions_to_orders
from .report import build_plan
from .strategies import get_strategy


def run_day(md, pf, cfg, strategy_name=None, ledger=None):
    strategy_name = strategy_name or cfg["strategy"]
    holidays = ledger.holidays if ledger else load_holidays()
    trade_date = add_trading_days(md.as_of, 1, holidays)       # 收盤後產生 → 下一個交易日下單
    notes = list(md.notes)

    budget = None
    if ledger is not None:
        pf = replace(pf, cash=ledger.total_cash)                 # 淨值用「餘額 + 未交割淨額」
        budget = ledger.buy_budget(trade_date)
        if not holidays:
            notes.append("尚未設定休市日（holidays.json），T+2 只排除週末；連假前後請注意")

    obs = compute_observations(md, cfg["score_weights"])
    decisions = get_strategy(strategy_name)(obs, pf, cfg)
    orders = decisions_to_orders(decisions, pf, obs, cfg, cash_budget=budget)
    na = int(obs["tech_na"].sum())
    if na:
        notes.append(f"技術面資料不足 {na} 檔")
    plan = build_plan(md.as_of, cfg, config_sha256(cfg), strategy_name, obs, decisions, orders, pf, notes)
    plan["trade_date"] = str(trade_date.date())

    if ledger is not None:
        extra = orders_as_pending(orders, cfg, trade_date, holidays)
        fc = ledger.forecast(start=trade_date, days=8, extra=extra)
        plan["cash"] = {
            "bank_balance": ledger.d["bank_balance"], "balance_date": ledger.d["balance_date"],
            "pending_net": round(ledger.pending_net), "buy_budget": round(budget),
            "settle_date": str(settle_date(trade_date, holidays).date()),
            "forecast_if_executed": fc.to_dict(orient="records"),
            "shortfalls": ledger.shortfalls(extra=extra, start=trade_date).to_dict(orient="records"),
        }
    return plan, obs, decisions, orders
