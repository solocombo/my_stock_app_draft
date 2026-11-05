import pandas as pd
import pytest

from agent.calendar_tw import settle_date
from agent.cashflow import Ledger
from agent.config import load_config
from agent.daytrade.intraday import round_tick, simulate, tick_size
from agent.daytrade.realtime import Watcher
from agent.daytrade.screener import build_watchlist
from agent.data import DemoProvider
from agent.pipeline import run_day
from agent.portfolio import Portfolio
from agent.strategies.library import ALL


@pytest.fixture(scope="module")
def cfg():
    return load_config("config.yaml")


@pytest.fixture(scope="module")
def md():
    return DemoProvider(n_stocks=40, days=400, end="2026-11-04").load()


# ---------------- T+2
def test_settle_date_skips_weekend_and_holiday():
    assert settle_date("2026-11-05", set()) == pd.Timestamp("2026-11-09")          # 週四 → 下週一
    assert settle_date("2026-11-05", {pd.Timestamp("2026-11-09")}) == pd.Timestamp("2026-11-10")


def test_ledger_budget_and_set_balance(cfg):
    L = Ledger.new(100000, "2026-11-05", holidays=set())
    L.record_trade(cfg, "2026-11-05", "BUY", "2330", 50, 1000)       # 11/09 扣 50,042
    L.record_trade(cfg, "2026-11-06", "SELL", "2454", 20, 1500)      # 11/10 入 29,885
    L.add_event("2026-11-12", -90000, "信用卡")
    fc = L.forecast(start="2026-11-06", days=6).set_index("date")
    assert fc.loc["2026-11-09", "balance"] == 100000 - 50042
    assert fc.loc["2026-11-12", "balance"] == 100000 - 50042 + 29885 - 90000
    # 11/06 下單 → 11/10 交割；之後最低點是 11/12 的 -10,157 → 已經不夠，額度為負
    assert L.buy_budget("2026-11-06") == pytest.approx(-10157)
    L.set_balance(50000, "2026-11-09")
    assert [x["stock_id"] for x in L.d["pending"]] == ["2454"]


def test_daily_respects_t2_budget(md, cfg):
    L = Ledger.new(200000, "2026-11-04", holidays=set())
    L.d["reserve"] = 10000
    plan, *_ = run_day(md, Portfolio(cash=0), cfg, "fundamental", ledger=L)
    spent = sum(o["value"] + o["est_cost"] for o in plan["orders"] if o["side"] == "BUY")
    assert spent <= 190000
    assert not plan["cash"]["shortfalls"]


# ---------------- 策略庫
@pytest.mark.parametrize("strat", ALL, ids=lambda s: s.name)
def test_every_strategy_runs_and_respects_entry(md, cfg, strat):
    from agent.factors import compute_observations
    obs = compute_observations(md, cfg["score_weights"])
    decisions = strat.decide(obs, Portfolio(cash=cfg["capital"]), cfg)
    p = strat.params(cfg)
    o2 = obs.assign(SCORE=strat.score(obs, p))
    for d in decisions:
        assert d.action == "BUY"            # 空手只會有買進
        assert strat.entry_fail(o2.loc[d.stock_id], p) is None
    assert len(decisions) <= cfg["portfolio"]["max_new_buys_per_day"]


def test_mean_reversion_time_exit(md, cfg):
    from agent.factors import compute_observations
    from agent.strategies.library import REGISTRY
    obs = compute_observations(md, cfg["score_weights"])
    sid = obs.index[0]
    pf = Portfolio(cash=0, positions={sid: 10}, avg_cost={sid: float(obs.loc[sid, "close"])},
                   entry_date={sid: "2026-10-01"})
    d = [x for x in REGISTRY["mean_reversion"].decide(obs, pf, cfg) if x.stock_id == sid][0]
    assert d.action == "SELL_ALL"


# ---------------- 當沖
def test_tick_rounding():
    assert tick_size(49.9) == 0.05 and tick_size(1005) == 5
    assert round_tick(101.3, up=True) == 101.5 and round_tick(101.3, up=False) == 101.0


def _bars(prices):
    t = pd.date_range("2026-11-05 09:01", periods=len(prices), freq="min")
    p = pd.Series(prices, dtype=float)
    return pd.DataFrame({"time": t, "open": p, "high": p + 0.1, "low": p - 0.1, "close": p, "volume": 1000})


def test_orb_long_hits_target(cfg):
    prices = [100] * 15 + [100.5, 101, 102, 103, 104, 105] + [105] * 50
    plan = {"stock_id": "X", "side": "long", "atr": 3.0, "strategy": "orb"}
    tr = simulate(_bars(prices), plan, cfg)
    assert tr is not None and tr.exit_reason == "停利" and tr.pnl > 0
    assert tr.shares % cfg["daytrade"]["lot_size"] == 0


def test_orb_long_stopped(cfg):
    prices = [100] * 15 + [100.5, 99.0] + [98] * 30
    tr = simulate(_bars(prices), {"stock_id": "X", "side": "long", "atr": 3.0, "strategy": "orb"}, cfg)
    assert tr.exit_reason == "停損" and tr.pnl < 0


def test_screener_returns_levels(md, cfg):
    wl = build_watchlist(md, cfg)
    for r in wl:
        assert r["side"] in ("long", "short") and r["atr"] > 0


def test_watcher_state_machine(cfg):
    from datetime import datetime
    w = Watcher([{"stock_id": "X", "name": "", "side": "long", "atr": 3.0, "strategy": "orb"}], cfg)
    q = lambda last, h, l, v: {"last": last, "high": h, "low": l, "volume": v}
    assert w.on_quote("X", q(100, 100.5, 99.5, 10), datetime(2026, 11, 5, 9, 5)) == []
    assert "區間" in w.on_quote("X", q(100, 100.5, 99.5, 20), datetime(2026, 11, 5, 9, 15))[0]
    assert "買進" in w.on_quote("X", q(101, 101, 99.5, 30), datetime(2026, 11, 5, 9, 20))[0]
    assert "停利" in w.on_quote("X", q(105, 105, 99.5, 40), datetime(2026, 11, 5, 9, 40))[0]
