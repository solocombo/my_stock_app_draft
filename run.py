#!/usr/bin/env python3
"""個人版 AI 投資組合 Agent。

  python run.py daily                     # 產生今日建議（output/plan_日期.md/.json），並推播
  python run.py daily --strategy quant+llm
  python run.py fill output/plan_2026-11-05.json   # 你手動下單後，把建議單同步進 portfolio.json
  python run.py backtest --start 2025-12-01
  python run.py compare --variant '{"entry": {"mom_min": 60}}'
  python run.py notify-test
"""
import argparse
import json
import sys

import pandas as pd

from agent.backtest import compare_variant, run_backtest
from agent.config import load_config
from agent.data import get_provider
from agent.notify import CHANNELS, notify
from agent.pipeline import run_day
from agent.portfolio import Order, Portfolio, apply_orders
from agent.report import to_markdown, write_outputs


def cmd_daily(a, cfg):
    md = get_provider(cfg).load(cfg)
    pf = Portfolio.load(a.portfolio, cfg["capital"])
    plan, *_ = run_day(md, pf, cfg, a.strategy)
    jp, mp = write_outputs(plan, a.out)
    print(to_markdown(plan))
    print(f"\n已寫入 {jp} 與 {mp}")
    if not a.no_notify:
        for r in notify(plan, cfg):
            print("推播", r)


def cmd_fill(a, cfg):
    plan = json.loads(open(a.plan, encoding="utf-8").read())
    pf = Portfolio.load(a.portfolio, cfg["capital"])
    skip = set(a.skip or [])
    orders = [Order(**o) for o in plan["orders"] if o["stock_id"] not in skip]
    new = apply_orders(pf, orders, cfg)
    new.save(a.portfolio)
    print(f"已更新 {a.portfolio}：現金 {new.cash:,.0f}，持股 {len(new.positions)} 檔")
    print("若實際成交價／股數不同，請直接編輯 portfolio.json。")


def cmd_backtest(a, cfg):
    md = get_provider(cfg).load(cfg)
    start = a.start or md.prices["date"].min() + pd.Timedelta(days=200)
    nav, summ = run_backtest(md, cfg, start, a.end, a.every, a.strategy)
    nav.to_csv(f"{a.out}/backtest_nav.csv")
    print(json.dumps(summ, ensure_ascii=False, indent=2))


def cmd_compare(a, cfg):
    md = get_provider(cfg).load(cfg)
    dates = sorted(md.prices["date"].unique())
    starts = dates[200:-a.length:max(1, (len(dates) - 200 - a.length) // a.n_starts)][: a.n_starts]
    df, verdict = compare_variant(md, cfg, json.loads(a.variant), starts, a.length, a.every)
    print(df.to_string(index=False))
    print(json.dumps(verdict, ensure_ascii=False))
    print("判定：", "變體穩定勝出，可考慮採用" if verdict["pass"] else "未通過（勝率需 ≥ 2/3 且差異中位數 > 0），維持原參數")


def cmd_notify_test(a, cfg):
    fake = {"as_of": "test", "orders": [], "nav": 0, "cash_after_est": 0}
    for ch in cfg.get("notify", {}).get("channels") or list(CHANNELS):
        try:
            CHANNELS[ch]("推播測試", "stock agent 推播測試成功")
            print(ch, "ok")
        except Exception as e:
            print(ch, "failed:", e)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--portfolio", default="portfolio.json")
    p.add_argument("--out", default="output")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily")
    d.add_argument("--strategy")
    d.add_argument("--no-notify", action="store_true")

    f = sub.add_parser("fill")
    f.add_argument("plan")
    f.add_argument("--skip", nargs="*", help="沒有實際下單的股票代號")

    b = sub.add_parser("backtest")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--every", type=int, default=5)
    b.add_argument("--strategy")

    c = sub.add_parser("compare")
    c.add_argument("--variant", required=True, help="JSON，覆蓋 config 的部分參數")
    c.add_argument("--n-starts", type=int, default=6)
    c.add_argument("--length", type=int, default=120)
    c.add_argument("--every", type=int, default=5)

    sub.add_parser("notify-test")
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    import os
    os.makedirs(a.out, exist_ok=True)
    {"daily": cmd_daily, "fill": cmd_fill, "backtest": cmd_backtest,
     "compare": cmd_compare, "notify-test": cmd_notify_test}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
