"""產出每日建議：JSON（機器可讀、可追溯）+ Markdown（給人看、推播用）。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

DISCLAIMER = "⚠️ 本建議由程式依規則與模型產生，僅供個人參考，不構成投資建議；請自行判斷後手動下單。"


def build_plan(as_of, cfg, cfg_sha, strategy, obs, decisions, orders, pf, notes):
    prices = obs["close"]
    nav = pf.nav(prices)
    buy_v = sum(o.value + o.est_cost for o in orders if o.side == "BUY")
    sell_v = sum(o.value - o.est_cost for o in orders if o.side == "SELL")
    plan = {
        "as_of": str(as_of.date()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": strategy,
        "config_sha256": cfg_sha,
        "data_source": cfg["data"]["source"],
        "nav": round(nav),
        "cash_before": round(pf.cash),
        "cash_after_est": round(pf.cash + sell_v - buy_v),
        "decisions": [asdict(d) for d in decisions if not d.stock_id.startswith("_")],
        "market_notes": [d.reason for d in decisions if d.stock_id.startswith("_")],
        "orders": [asdict(o) for o in orders],
        "data_notes": notes,
        "disclaimer": DISCLAIMER,
    }
    plan["sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return plan


def to_markdown(plan, short=False) -> str:
    L = [f"# 每日投資組合建議 {plan['as_of']}",
         f"策略：`{plan['strategy']}`｜資料：{plan['data_source']}｜淨值約 {plan['nav']:,} 元",
         f"現金：{plan['cash_before']:,} → 預估 {plan['cash_after_est']:,} 元", ""]
    orders = plan["orders"]
    if orders:
        L.append("## 建議下單")
        L.append("| 動作 | 代號 | 名稱 | 股數 | 參考價 | 金額 | 理由 |")
        L.append("|---|---|---|---:|---:|---:|---|")
        for o in sorted(orders, key=lambda o: o["side"] != "SELL"):
            tag = "賣" if o["side"] == "SELL" else "買"
            lot = "（零股）" if o["odd_lot"] else ""
            L.append(f"| {tag} {o['action']} | {o['stock_id']} | {o['name']} | {o['shares']:,}{lot} | {o['price_ref']} | {o['value']:,} | {o['reason']} |")
    else:
        L.append("## 今日無買賣建議")
    for n in plan.get("market_notes", []):
        L += ["", n]
    if not short:
        holds = [d for d in plan["decisions"] if d["action"] == "HOLD"]
        if holds:
            L += ["", "## 續抱", "| 代號 | 現況權重 | 理由 |", "|---|---:|---|"]
            L += [f"| {d['stock_id']} | {d['current_weight']:.1%} | {d['reason']} |" for d in holds]
        watch = [d for d in plan["decisions"]
                 if d["action"] == "HOLD" and d["evidence"].get("ma_break_days", 0) in (1, 2)]
        if watch:
            L += ["", "## 停損預警（已連續收在 MA60 下）"]
            L += [f"- {d['stock_id']}：{d['evidence']['ma_break_days']} 日，收盤 {d['evidence']['close']}，MA60 {d['evidence']['ma60']}" for d in watch]
        if plan["data_notes"]:
            L += ["", "## 資料備註"] + [f"- {n}" for n in plan["data_notes"][:20]]
    L += ["", DISCLAIMER]
    return "\n".join(L)


def write_outputs(plan, out_dir="output"):
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    jp = d / f"plan_{plan['as_of']}.json"
    mp = d / f"plan_{plan['as_of']}.md"
    jp.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    mp.write_text(to_markdown(plan), encoding="utf-8")
    return jp, mp
