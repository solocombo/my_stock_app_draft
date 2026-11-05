#!/usr/bin/env python3
"""個人版 AI 投資組合 Agent（只產生建議，不下單）。

波段組合
  python run.py daily [--strategy momentum]         產生明日建議（含 T+2 現金預測）並推播
  python run.py fill output/plan_<日期>.json         照建議下單後，同步持股與交割帳
  python run.py strategies                          列出所有選股策略
  python run.py backtest [--strategy value]         單一策略回測
  python run.py compare-strategies                  所有策略同期間比較
  python run.py compare --variant '{"entry": {"mom_min": 60}}'   參數變體多起始日比較

現金池（T+2）
  python run.py cash set 350000 [--date 2026-11-05] 更新交割帳戶實際餘額（隨時可改）
  python run.py cash trade BUY 2330 50 1050 [--date ...] [--daytrade]   記錄實際成交
  python run.py cash event 2026-11-10 50000 --note 薪水    未來入金(+)／出金(-)
  python run.py cash forecast [--days 10]           未來每日預估餘額
  python run.py cash holidays-update                下載證交所休市日

當沖
  python run.py daytrade plan                       盤前觀察名單（依前一日收盤）
  python run.py daytrade watch [--dry-run]          盤中監控並推播觸發
  python run.py daytrade backtest [--days 60] [--bars-dir data/kbars]
  python run.py daytrade record 2330 1000 1050 1062 [--side long]   記錄實際當沖（寫入交割帳）
"""
import argparse
import json
import os
import sys
from datetime import date

import pandas as pd

from agent.backtest import compare_strategies, compare_variant, run_backtest
from agent.calendar_tw import add_trading_days, load_holidays, update_holidays
from agent.cashflow import Ledger
from agent.config import load_config
from agent.data import get_provider
from agent.notify import CHANNELS, notify, send_all
from agent.pipeline import run_day
from agent.portfolio import Order, Portfolio, apply_orders
from agent.report import to_markdown, write_outputs
from agent.strategies import list_strategies


def _ledger(cfg):
    return Ledger.load()


# ------------------------------------------------------------------ 波段
def cmd_daily(a, cfg):
    md = get_provider(cfg).load(cfg)
    pf = Portfolio.load(a.portfolio, cfg["capital"])
    led = _ledger(cfg)
    plan, *_ = run_day(md, pf, cfg, a.strategy, ledger=led)
    jp, mp = write_outputs(plan, a.out)
    print(to_markdown(plan))
    if led is None:
        print("\n（提示：尚未建立交割帳，執行 `python run.py cash set <餘額>` 後即可依 T+2 計算可用資金）")
    print(f"\n已寫入 {jp} 與 {mp}")
    if not a.no_notify:
        for r in notify(plan, cfg):
            print("推播", r)


def cmd_fill(a, cfg):
    plan = json.loads(open(a.plan, encoding="utf-8").read())
    pf = Portfolio.load(a.portfolio, cfg["capital"])
    skip = set(a.skip or [])
    orders = [Order(**o) for o in plan["orders"] if o["stock_id"] not in skip]
    trade_date = a.date or plan.get("trade_date") or plan["as_of"]
    new = apply_orders(pf, orders, cfg, trade_date=trade_date)
    led = _ledger(cfg)
    if led is not None:
        for o in orders:
            led.record_trade(cfg, trade_date, o.side, o.stock_id, o.shares, o.price_ref, note="fill")
        led.save()
        new.cash = led.total_cash
        print(f"已寫入交割帳：{len(orders)} 筆，交割日 {led.d['pending'][-1]['settle_date'] if orders else '-'}")
    new.save(a.portfolio)
    print(f"已更新 {a.portfolio}：持股 {len(new.positions)} 檔")
    print("實際成交價／股數不同時，請改用 `cash trade` 逐筆記錄，或直接編輯 portfolio.json 與 cash_ledger.json。")


def cmd_strategies(a, cfg):
    for n, label, desc in list_strategies():
        print(f"{n:<15} {label:<6} {desc}")
    print("\n另有：llm、<名稱>+llm（例如 momentum+llm）、custom:模組")


def _default_start(md):
    return md.prices["date"].min() + pd.Timedelta(days=200)


def cmd_backtest(a, cfg):
    md = get_provider(cfg).load(cfg)
    nav, summ = run_backtest(md, cfg, a.start or _default_start(md), a.end, a.every, a.strategy)
    nav.to_csv(f"{a.out}/backtest_nav.csv")
    print(json.dumps(summ, ensure_ascii=False, indent=2))


def cmd_compare_strategies(a, cfg):
    md = get_provider(cfg).load(cfg)
    names = a.names or [n for n, *_ in list_strategies()]
    df = compare_strategies(md, cfg, names, a.start or _default_start(md), a.end, a.every)
    df.to_csv(f"{a.out}/compare_strategies.csv", index=False)
    print(df.to_string(index=False))
    if cfg["data"]["source"] == "demo":
        print("\n⚠️ 模擬資料的結果只能驗證程式流程，不代表策略在真實市場的優劣。")


def cmd_compare(a, cfg):
    md = get_provider(cfg).load(cfg)
    dates = sorted(md.prices["date"].unique())
    starts = dates[200:-a.length:max(1, (len(dates) - 200 - a.length) // a.n_starts)][: a.n_starts]
    df, verdict = compare_variant(md, cfg, json.loads(a.variant), starts, a.length, a.every)
    print(df.to_string(index=False))
    print(json.dumps(verdict, ensure_ascii=False))
    print("判定：", "變體穩定勝出，可考慮採用" if verdict["pass"] else "未通過（勝率需 ≥ 2/3 且差異中位數 > 0），維持原參數")


# ------------------------------------------------------------------ 現金池
def _print_forecast(led, days):
    fc = led.forecast(start=pd.Timestamp.today(), days=days)
    print(f"帳戶餘額 {led.d['bank_balance']:,.0f}（{led.d['balance_date']}）｜未交割淨額 {led.pending_net:+,.0f}"
          f"｜安全水位 {led.d.get('reserve', 0):,.0f}")
    print(f"今天下單可用買進額度（T+2 = {add_trading_days(pd.Timestamp.today(), 2, led.holidays).date()}）："
          f"{led.buy_budget(pd.Timestamp.today()):,.0f}")
    print(fc.to_string(index=False))
    low = fc[fc["balance"] < led.d.get("reserve", 0)]
    if len(low):
        print("\n🚨 違約交割風險：", "、".join(f"{r.date} 預估 {r.balance:,}" for r in low.itertuples()))
    if not led.holidays:
        print("\n（尚未下載休市日，T+2 只排除週末。執行 `python run.py cash holidays-update`）")


def cmd_cash(a, cfg):
    led = _ledger(cfg)
    if a.cash_cmd == "holidays-update":
        days = update_holidays()
        print(f"已更新 holidays.json，共 {len(days)} 天；最近：{', '.join(d for d in days if d >= str(date.today()))[:200]}")
        return
    if a.cash_cmd == "set":
        if led is None:
            led = Ledger.new(a.amount, a.date or date.today())
            led.d["reserve"] = cfg.get("cash", {}).get("reserve", 0)
        else:
            led.set_balance(a.amount, a.date or date.today())
        led.save()
        print("已更新餘額。")
        _print_forecast(led, 10)
        return
    if led is None:
        sys.exit("尚未建立交割帳：先執行 `python run.py cash set <目前交割帳戶餘額>`")
    if a.cash_cmd == "trade":
        item = led.record_trade(cfg, a.date or date.today(), a.side.upper(), a.stock_id, a.shares, a.price, a.daytrade)
        led.save()
        pf = Portfolio.load(a.portfolio, cfg["capital"])
        if not a.daytrade:
            o = Order(a.stock_id, "", a.side.upper(), a.shares, a.price, round(a.shares * a.price), item["fee"] + item["tax"],
                      a.shares % 1000 != 0, "MANUAL", "手動記錄")
            pf = apply_orders(pf, [o], cfg, trade_date=a.date or date.today())
            pf.cash = led.total_cash
            pf.save(a.portfolio)
        print(f"已記錄：{item['side']} {a.stock_id} {a.shares} 股 @ {a.price}，淨額 {item['net']:+,}，交割日 {item['settle_date']}")
    elif a.cash_cmd == "event":
        led.add_event(a.date_, a.amount, a.note or "")
        led.save()
        print("已加入。")
    elif a.cash_cmd == "reserve":
        led.d["reserve"] = a.amount
        led.save()
    _print_forecast(led, a.days if a.cash_cmd == "forecast" else 10)


# ------------------------------------------------------------------ 當沖
def cmd_daytrade(a, cfg):
    from agent.daytrade.backtest import run_daytrade_backtest
    from agent.daytrade.screener import build_watchlist, watchlist_markdown

    if a.dt_cmd == "plan":
        md = get_provider(cfg).load(cfg)
        wl = build_watchlist(md, cfg)
        td = add_trading_days(md.as_of, 1, load_holidays())
        md_text = watchlist_markdown(md.as_of.date(), td.date(), wl, cfg)
        path = f"{a.out}/daytrade_plan_{td.date()}.json"
        json.dump({"as_of": str(md.as_of.date()), "trade_date": str(td.date()), "watchlist": wl},
                  open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        open(path.replace(".json", ".md"), "w", encoding="utf-8").write(md_text)
        print(md_text, f"\n\n已寫入 {path}")
        if not a.no_notify and wl:
            body = "\n".join(f"{r['stock_id']} {r['name']} {'多' if r['side'] == 'long' else '空'} 昨收{r['prev_close']} ATR{r['atr']}" for r in wl)
            for r in send_all(f"當沖名單 {td.date()}", body, cfg):
                print("推播", r)
    elif a.dt_cmd == "watch":
        from agent.daytrade.realtime import watch
        path = a.plan or sorted(p for p in os.listdir(a.out) if p.startswith("daytrade_plan_") and p.endswith(".json"))[-1]
        path = path if os.path.sep in path else os.path.join(a.out, path)
        plans = json.load(open(path, encoding="utf-8"))["watchlist"]
        watch(plans, cfg, a.interval, a.dry_run)
    elif a.dt_cmd == "backtest":
        md = get_provider(cfg).load(cfg)
        df, summ = run_daytrade_backtest(md, cfg, a.days, a.bars_dir)
        df.to_csv(f"{a.out}/daytrade_trades.csv", index=False)
        print(json.dumps(summ, ensure_ascii=False, indent=2))
        if not a.bars_dir:
            print("\n⚠️ 使用由日線合成的分 K，沒有真實盤中規律；請匯入真實分 K（--bars-dir）再評估策略。")
    elif a.dt_cmd == "record":
        led = _ledger(cfg)
        if led is None:
            sys.exit("尚未建立交割帳：先執行 `python run.py cash set <餘額>`")
        d = a.date or date.today()
        buy_px, sell_px = (a.entry, a.exit) if a.side == "long" else (a.exit, a.entry)
        b = led.record_trade(cfg, d, "BUY", a.stock_id, a.shares, buy_px, daytrade=True, note="當沖")
        s = led.record_trade(cfg, d, "SELL", a.stock_id, a.shares, sell_px, daytrade=True, note="當沖")
        led.save()
        print(f"當沖 {a.stock_id} 淨損益 {b['net'] + s['net']:+,}，於 {s['settle_date']} 交割")


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
    f.add_argument("--date", help="實際下單日（預設為建議單的 trade_date）")

    sub.add_parser("strategies")

    for name in ("backtest", "compare-strategies"):
        b = sub.add_parser(name)
        b.add_argument("--start")
        b.add_argument("--end")
        b.add_argument("--every", type=int, default=5)
        if name == "backtest":
            b.add_argument("--strategy")
        else:
            b.add_argument("--names", nargs="*")

    c = sub.add_parser("compare")
    c.add_argument("--variant", required=True, help="JSON，覆蓋 config 的部分參數")
    c.add_argument("--n-starts", type=int, default=6)
    c.add_argument("--length", type=int, default=120)
    c.add_argument("--every", type=int, default=5)

    cash = sub.add_parser("cash").add_subparsers(dest="cash_cmd", required=True)
    cs = cash.add_parser("set")
    cs.add_argument("amount", type=float)
    cs.add_argument("--date")
    ct = cash.add_parser("trade")
    ct.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    ct.add_argument("stock_id")
    ct.add_argument("shares", type=int)
    ct.add_argument("price", type=float)
    ct.add_argument("--date")
    ct.add_argument("--daytrade", action="store_true")
    ce = cash.add_parser("event")
    ce.add_argument("date_", metavar="date")
    ce.add_argument("amount", type=float)
    ce.add_argument("--note")
    cr = cash.add_parser("reserve")
    cr.add_argument("amount", type=float)
    cf = cash.add_parser("forecast")
    cf.add_argument("--days", type=int, default=10)
    cash.add_parser("holidays-update")

    dt = sub.add_parser("daytrade").add_subparsers(dest="dt_cmd", required=True)
    dp = dt.add_parser("plan")
    dp.add_argument("--no-notify", action="store_true")
    dw = dt.add_parser("watch")
    dw.add_argument("--plan")
    dw.add_argument("--interval", type=int, default=10)
    dw.add_argument("--dry-run", action="store_true")
    db = dt.add_parser("backtest")
    db.add_argument("--days", type=int, default=60)
    db.add_argument("--bars-dir")
    dr = dt.add_parser("record")
    dr.add_argument("stock_id")
    dr.add_argument("shares", type=int)
    dr.add_argument("entry", type=float)
    dr.add_argument("exit", type=float)
    dr.add_argument("--side", choices=["long", "short"], default="long")
    dr.add_argument("--date")

    sub.add_parser("notify-test")

    a = p.parse_args(argv)
    cfg = load_config(a.config)
    os.makedirs(a.out, exist_ok=True)
    if a.cmd == "notify-test":
        for ch in cfg.get("notify", {}).get("channels") or list(CHANNELS):
            try:
                CHANNELS[ch]("推播測試", "stock agent 推播測試成功")
                print(ch, "ok")
            except Exception as e:
                print(ch, "failed:", e)
        return
    {"daily": cmd_daily, "fill": cmd_fill, "strategies": cmd_strategies, "backtest": cmd_backtest,
     "compare-strategies": cmd_compare_strategies, "compare": cmd_compare, "cash": cmd_cash,
     "daytrade": cmd_daytrade}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
