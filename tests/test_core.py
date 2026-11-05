import pandas as pd
import pytest

from agent.config import load_config
from agent.data import DemoProvider
from agent.factors import compute_observations
from agent.pipeline import run_day
from agent.portfolio import Decision, Portfolio, apply_orders, cap_weights, decisions_to_orders
from agent.strategies.quant import exit_reason


@pytest.fixture(scope="module")
def cfg():
    return load_config("config.yaml")


@pytest.fixture(scope="module")
def md():
    return DemoProvider(n_stocks=40, days=400, end="2026-11-04").load()


def test_cap_weights_respects_caps():
    w = cap_weights({"a": 100, "b": 1, "c": 1}, 0.9, {"a": 0.2, "b": 0.5, "c": 0.5}, set(), 1.0)
    assert w["a"] == pytest.approx(0.2)
    assert sum(w.values()) == pytest.approx(0.9)


def test_financial_cap():
    w = cap_weights({"f1": 1, "f2": 1, "x": 1}, 0.9, {k: 0.5 for k in ("f1", "f2", "x")}, {"f1", "f2"}, 0.2)
    assert w["f1"] + w["f2"] == pytest.approx(0.2)


def test_exit_rules(cfg):
    row = pd.Series({"MOM": 20, "ma_break_days": 0, "fund_na": True})
    assert "MOM" in exit_reason(row, cfg)
    row = pd.Series({"MOM": 60, "ma_break_days": 3, "ma60": 100, "fund_na": True})
    assert "MA60" in exit_reason(row, cfg)
    row = pd.Series({"MOM": 60, "ma_break_days": 2, "ma60": 100, "fund_na": True})
    assert exit_reason(row, cfg) is None


def test_odd_lot_orders(cfg):
    obs = pd.DataFrame({"close": [850.0], "name": ["X"]}, index=["2330"])
    pf = Portfolio(cash=100000)
    orders = decisions_to_orders([Decision("2330", "BUY", 0.25, "t")], pf, obs, cfg)
    assert orders[0].shares == 29 and orders[0].odd_lot  # 25000/850 = 29 股零股


def test_daily_pipeline_runs_and_respects_constraints(md, cfg):
    pf = Portfolio(cash=cfg["capital"])
    plan, obs, decisions, orders = run_day(md, pf, cfg, "quant")
    assert plan["orders"], "空手時應產生買進建議"
    assert plan["cash_after_est"] >= 0
    new = apply_orders(pf, orders, cfg)
    w = new.weights(obs["close"])
    assert len(new.positions) <= cfg["portfolio"]["target_names"]
    assert max(w.values()) <= cfg["portfolio"]["default_cap"] + 0.01
    for d in decisions:
        if d.action == "BUY":
            r = obs.loc[d.stock_id]
            assert r["F"] >= cfg["entry"]["f_min"] and r["ACC"] > 0 and r["MOM"] >= cfg["entry"]["mom_min"]


def test_no_lookahead(md):
    sub = md.slice_to("2026-06-01")
    assert sub.prices["date"].max() <= pd.Timestamp("2026-06-01")
    # 2026-05 營收要到 6/10 才公布
    assert sub.revenue["month"].max() < pd.Timestamp("2026-05-01")


def test_custom_strategy(md, cfg):
    plan, *_ = run_day(md, Portfolio(cash=cfg["capital"]), cfg, "custom:my_strategies.momentum_only")
    assert len(plan["orders"]) == 10


def test_llm_review_veto_with_mock(md, cfg, monkeypatch):
    from agent.strategies import llm

    def fake_ask(cfg_, system, user, schema):
        import json, re
        ids = re.findall(r'"stock_id": "(\w+)"', user)
        return {"reviews": [{"stock_id": s, "verdict": "veto" if i == 0 else "approve", "reason": "測試"}
                            for i, s in enumerate(ids)]}

    monkeypatch.setattr(llm, "_ask_json", fake_ask)
    plan, obs, decisions, orders = run_day(md, Portfolio(cash=cfg["capital"]), cfg, "quant+llm")
    base, *_ = run_day(md, Portfolio(cash=cfg["capital"]), cfg, "quant")
    assert len(plan["orders"]) == len(base["orders"]) - 1
