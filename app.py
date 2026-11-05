"""網頁介面（手機、電腦都能用）：

    streamlit run app.py                                   # 只有本機能開
    streamlit run app.py --server.address 0.0.0.0          # 同一個 Wi-Fi 的手機也能開 http://<電腦IP>:8501

只產生建議與記帳，不會下單。所有資料存在本機（portfolio.json、cash_ledger.json、output/）。
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

os.chdir(Path(__file__).resolve().parent)

from agent.backtest import compare_strategies  # noqa: E402
from agent.calendar_tw import add_trading_days, load_holidays  # noqa: E402
from agent.cashflow import Ledger, fee, sell_tax  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.data import get_provider  # noqa: E402
from agent.notify import notify  # noqa: E402
from agent.pipeline import run_day  # noqa: E402
from agent.portfolio import Order, Portfolio, apply_orders  # noqa: E402
from agent.report import write_outputs  # noqa: E402
from agent.strategies import list_strategies  # noqa: E402

PORTFOLIO, OUT = "portfolio.json", "output"
st.set_page_config(page_title="我的投資助理", page_icon="📈", layout="centered")
st.markdown("""<style>
  .block-container {padding-top: 1rem; padding-bottom: 3rem; padding-left: 1rem; padding-right: 1rem;}
  h1 {font-size: 1.6rem !important; padding: 0 0 .3rem 0 !important;}
  .stButton button, .stFormSubmitButton button {min-height: 2.8rem;}
  .stats {display: flex; gap: .5rem; margin: .4rem 0 .8rem 0;}
  .stats div {flex: 1; background: rgba(128,128,128,.08); border-radius: .6rem; padding: .5rem .6rem;}
  .stats small {display: block; opacity: .65; font-size: .75rem;}
  .stats b {font-size: 1.1rem; font-variant-numeric: tabular-nums;}
</style>""", unsafe_allow_html=True)


def stats(*items):
    """手機上也並排的小數字卡（st.metric 在窄螢幕會一個一行）。"""
    cells = "".join(f"<div><small>{k}</small><b>{v}</b></div>" for k, v in items)
    st.markdown(f'<div class="stats">{cells}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------- 共用
@st.cache_data(ttl=600, show_spinner="載入行情資料…")
def load_market(_cfg_key: str):
    cfg = load_config()
    return get_provider(cfg).load(cfg)


def cfg():
    return load_config()


def market():
    c = cfg()
    return load_market(json.dumps(c["data"], sort_keys=True))


def money(x):
    return f"{x:,.0f}"


def side_label(s):
    return "🟢 買" if s == "BUY" else "🔴 賣"


MONEY_COLS = {c: st.column_config.NumberColumn(format="localized") for c in ("入帳", "扣款", "預估餘額", "市值", "淨額")}

STRATS = {n: f"{label}（{n}）" for n, label, _ in list_strategies()}

st.title("📈 我的投資助理")
c0 = cfg()
if c0["data"]["source"] == "demo":
    st.caption("ℹ️ 目前是模擬資料（config.yaml → data.source: demo），改成 finmind 使用真實台股。")

tab_today, tab_hold, tab_cash, tab_strat, tab_dt = st.tabs(["今日建議", "持股", "現金池", "策略比較", "當沖"])

# ---------------------------------------------------------------- 今日建議
with tab_today:
    names = list(STRATS)
    default = c0["strategy"] if c0["strategy"] in STRATS else "fundamental"
    strat = st.selectbox("選股策略", names, index=names.index(default), format_func=STRATS.get)
    use_llm = st.toggle("買進前讓 Claude 審查（需 ANTHROPIC_API_KEY）", value=False)
    if st.button("產生建議", type="primary", width="stretch"):
        md = market()
        pf = Portfolio.load(PORTFOLIO, c0["capital"])
        with st.spinner("計算中…"):
            plan, *_ = run_day(md, pf, c0, strat + ("+llm" if use_llm else ""), ledger=Ledger.load())
        write_outputs(plan, OUT)
        st.session_state["plan"] = plan

    plan = st.session_state.get("plan")
    if plan is None:
        files = sorted(Path(OUT).glob("plan_*.json")) if Path(OUT).exists() else []
        if files:
            plan = json.loads(files[-1].read_text(encoding="utf-8"))
            st.caption(f"顯示最近一次：{files[-1].name}")

    if plan:
        stats(("淨值", money(plan["nav"])), ("建議", f"{len(plan['orders'])} 筆"),
              ("下單日", plan.get("trade_date", plan["as_of"])[5:]))
        cash = plan.get("cash")
        if cash and cash["shortfalls"]:
            st.error("🚨 違約交割風險：" + "、".join(f"{r['date']} 預估 {money(r['balance'])}" for r in cash["shortfalls"]))
        if not plan["orders"]:
            st.success("今天沒有買賣建議，持股續抱。")
        else:
            st.subheader("建議下單")
            st.caption("勾選你實際有下單的項目，可修改成交股數與價格，再按「記錄成交」。")
            df = pd.DataFrame([{
                "下單": True, "動作": side_label(o["side"]), "代號": o["stock_id"], "名稱": o["name"],
                "股數": o["shares"], "價格": o["price_ref"], "理由": o["reason"], "_side": o["side"], "_action": o["action"],
            } for o in sorted(plan["orders"], key=lambda o: o["side"] != "SELL")])
            edited = st.data_editor(
                df, hide_index=True, width="stretch",
                column_order=["下單", "動作", "代號", "名稱", "股數", "價格", "理由"],
                disabled=["動作", "代號", "名稱", "理由"],
                column_config={"股數": st.column_config.NumberColumn(step=1, min_value=0),
                               "價格": st.column_config.NumberColumn(format="%.2f"),
                               "理由": st.column_config.TextColumn(width="large")})
            if st.button("✅ 記錄成交（更新持股與交割帳）", width="stretch"):
                td = plan.get("trade_date") or plan["as_of"]
                rows = edited[edited["下單"] & (edited["股數"] > 0)]
                orders = []
                for _, r in rows.iterrows():
                    v = round(r["股數"] * r["價格"])
                    cost = fee(c0, v) + (sell_tax(c0, v) if r["_side"] == "SELL" else 0)
                    orders.append(Order(r["代號"], r["名稱"], r["_side"], int(r["股數"]), float(r["價格"]), v, cost,
                                        int(r["股數"]) % 1000 != 0, r["_action"], "GUI"))
                pf = apply_orders(Portfolio.load(PORTFOLIO, c0["capital"]), orders, c0, trade_date=td)
                led = Ledger.load()
                if led:
                    for o in orders:
                        led.record_trade(c0, td, o.side, o.stock_id, o.shares, o.price_ref, note="GUI")
                    led.save()
                    pf.cash = led.total_cash
                pf.save(PORTFOLIO)
                st.success(f"已記錄 {len(orders)} 筆" + ("，並寫入交割帳" if led else "（尚未建立交割帳，請到「現金池」設定）"))
        if cash:
            with st.expander(f"現金預測（可用買進額度 {money(cash['buy_budget'])}）", expanded=bool(cash["shortfalls"])):
                fc = pd.DataFrame(cash["forecast_if_executed"])
                st.dataframe(fc.rename(columns={"date": "日期", "in": "入帳", "out": "扣款", "balance": "預估餘額", "items": "項目"}),
                             hide_index=True, width="stretch", column_config=MONEY_COLS)
        holds = [d for d in plan["decisions"] if d["action"] == "HOLD"]
        if holds:
            with st.expander(f"續抱 {len(holds)} 檔"):
                st.dataframe(pd.DataFrame([{"代號": d["stock_id"], "權重": f"{d['current_weight']:.1%}", "理由": d["reason"]} for d in holds]),
                             hide_index=True, width="stretch")
        for n in plan.get("data_notes", []):
            st.caption("⚠️ " + n)
        if st.button("📲 推播這份建議", width="stretch"):
            st.write(notify(plan, c0) or ["尚未在 config.yaml 設定 notify.channels"])
        st.caption(plan["disclaimer"])

# ---------------------------------------------------------------- 持股
with tab_hold:
    pf = Portfolio.load(PORTFOLIO, c0["capital"])
    if not pf.positions:
        st.info("目前沒有持股。可以在「今日建議」記錄成交，或在下方手動新增。")
    else:
        md = market()
        last = md.prices.sort_values("date").groupby("stock_id")["close"].last()
        names_ = md.info.set_index("stock_id")["name"].to_dict() if "name" in md.info else {}
        rows = []
        for sid, sh in pf.positions.items():
            px, cost = float(last.get(sid, float("nan"))), pf.avg_cost.get(sid, float("nan"))
            rows.append({"代號": sid, "名稱": names_.get(sid, ""), "股數": int(sh), "成本": round(cost, 2), "現價": px,
                         "市值": round(sh * px), "損益%": round((px / cost - 1) * 100, 1) if cost else None,
                         "買進日": pf.entry_date.get(sid, "")})
        df = pd.DataFrame(rows).sort_values("市值", ascending=False)
        led = Ledger.load()
        cash_now = led.total_cash if led else pf.cash
        stats(("股票市值", money(df["市值"].sum())), ("現金（含未交割）", money(cash_now)))
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={**MONEY_COLS, "損益%": st.column_config.NumberColumn(format="%+.1f%%")})
    with st.expander("手動新增／修改持股"):
        with st.form("edit_pos"):
            sid = st.text_input("股票代號")
            sh = st.number_input("股數（0 = 刪除）", min_value=0, step=1)
            cost = st.number_input("平均成本", min_value=0.0, step=0.5)
            if st.form_submit_button("儲存", width="stretch") and sid:
                pf = Portfolio.load(PORTFOLIO, c0["capital"])
                if sh == 0:
                    pf.positions.pop(sid, None), pf.avg_cost.pop(sid, None), pf.entry_date.pop(sid, None)
                else:
                    pf.positions[sid], pf.avg_cost[sid] = float(sh), float(cost)
                    pf.entry_date.setdefault(sid, str(date.today()))
                pf.save(PORTFOLIO)
                st.success("已儲存")
                st.rerun()

# ---------------------------------------------------------------- 現金池
with tab_cash:
    led = Ledger.load()
    with st.form("set_bal"):
        st.markdown("**更新交割帳戶餘額**（查銀行 App 後隨時更新）")
        bal = st.number_input("目前餘額", min_value=0, step=1000, value=int(led.d["bank_balance"]) if led else 0)
        bdate = st.date_input("餘額日期", value=date.today())
        if st.form_submit_button("更新餘額", type="primary", width="stretch"):
            if led is None:
                led = Ledger.new(bal, bdate)
                led.d["reserve"] = c0.get("cash", {}).get("reserve", 0)
            else:
                led.set_balance(bal, bdate)
            led.save()
            st.success("已更新")
    if led is None:
        st.info("先輸入目前的交割帳戶餘額，就能預測 T+2 現金。")
    else:
        today = pd.Timestamp.today()
        budget = led.buy_budget(today)
        stats(("帳戶餘額", money(led.d["bank_balance"])), ("未交割", f"{led.pending_net:+,.0f}"), ("今天可買", money(budget)))
        fc = led.forecast(start=today, days=10)
        low = fc[fc["balance"] < led.d.get("reserve", 0)]
        if len(low):
            st.error("🚨 違約交割風險：" + "、".join(f"{r.date} 預估 {money(r.balance)}" for r in low.itertuples()))
        st.line_chart(fc.set_index("date")["balance"], height=200)
        st.dataframe(fc.rename(columns={"date": "日期", "in": "入帳", "out": "扣款", "balance": "預估餘額", "items": "項目"}),
                     hide_index=True, width="stretch", column_config=MONEY_COLS)
        if not led.holidays:
            st.caption("尚未下載休市日，T+2 只排除週末。電腦上執行 `python run.py cash holidays-update`。")
        with st.expander("記錄一筆成交（手動）"):
            with st.form("trade"):
                side = st.radio("方向", ["BUY", "SELL"], horizontal=True, format_func=side_label)
                sid = st.text_input("代號")
                sh = st.number_input("股數", min_value=1, step=1)
                px = st.number_input("成交價", min_value=0.01, step=0.5)
                td = st.date_input("成交日", value=date.today())
                if st.form_submit_button("記錄", width="stretch") and sid:
                    item = led.record_trade(c0, td, side, sid, int(sh), float(px))
                    led.save()
                    o = Order(sid, "", side, int(sh), float(px), round(sh * px), item["fee"] + item["tax"], sh % 1000 != 0, "MANUAL", "GUI")
                    pf = apply_orders(Portfolio.load(PORTFOLIO, c0["capital"]), [o], c0, trade_date=td)
                    pf.cash = led.total_cash
                    pf.save(PORTFOLIO)
                    st.success(f"淨額 {item['net']:+,}，{item['settle_date']} 交割")
                    st.rerun()
        with st.expander("未來入金／出金（薪水、帳單）"):
            with st.form("event"):
                ed = st.date_input("日期", value=date.today())
                amt = st.number_input("金額（入金為正、出金為負）", step=1000, value=0)
                note = st.text_input("備註")
                if st.form_submit_button("加入", width="stretch") and amt:
                    led.add_event(ed, amt, note)
                    led.save()
                    st.rerun()
        if led.d["pending"]:
            with st.expander(f"未交割明細 {len(led.d['pending'])} 筆"):
                st.dataframe(pd.DataFrame(led.d["pending"])[["trade_date", "settle_date", "stock_id", "side", "shares", "price", "net"]],
                             hide_index=True, width="stretch")

# ---------------------------------------------------------------- 策略比較
with tab_strat:
    st.caption("同一段期間、同一份資料回測各策略（每 5 個交易日調整一次）。")
    picks = st.multiselect("策略", list(STRATS), default=list(STRATS)[:5], format_func=STRATS.get)
    if st.button("開始比較", type="primary", width="stretch") and picks:
        md = market()
        start = md.prices["date"].min() + pd.Timedelta(days=200)
        with st.spinner(f"回測 {len(picks)} 個策略，約需 {len(picks) * 6} 秒…"):
            st.session_state["cmp"] = compare_strategies(md, c0, picks, start)
    if "cmp" in st.session_state:
        df = st.session_state["cmp"].copy()
        show = pd.DataFrame({
            "策略": df["strategy"].map(lambda n: STRATS.get(n, n)),
            "總報酬%": (df["total_return"] * 100).round(1), "年化%": (df["cagr"] * 100).round(1),
            "最大回撤%": (df["max_drawdown"] * 100).round(1), "夏普": df["sharpe"],
            "交易次數": df["trades"], "平均檔數": df["avg_names"]})
        st.dataframe(show, hide_index=True, width="stretch")
        st.caption(f"同期等權重基準：{df['benchmark_return'].iloc[0] * 100:.1f}%")
        if c0["data"]["source"] == "demo":
            st.warning("模擬資料的結果只能驗證流程，不代表真實市場表現。")
    with st.expander("策略說明"):
        for n, label, desc in list_strategies():
            st.markdown(f"**{label}**（`{n}`）：{desc}")

# ---------------------------------------------------------------- 當沖
with tab_dt:
    from agent.daytrade.screener import build_watchlist
    d = c0["daytrade"]
    st.caption(f"策略 {d['strategy'].upper()}｜每筆風險 {money(d['capital'] * d['risk_per_trade'])}｜單日虧損上限 "
               f"{money(d['capital'] * d['daily_max_loss'])}｜{d['flat_time']} 前平倉")
    if st.button("產生明日觀察名單", type="primary", width="stretch"):
        md = market()
        wl = build_watchlist(md, c0)
        td = add_trading_days(md.as_of, 1, load_holidays())
        Path(OUT).mkdir(exist_ok=True)
        Path(OUT, f"daytrade_plan_{td.date()}.json").write_text(
            json.dumps({"as_of": str(md.as_of.date()), "trade_date": str(td.date()), "watchlist": wl}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        st.session_state["wl"] = (str(td.date()), wl)
    if "wl" in st.session_state:
        td, wl = st.session_state["wl"]
        st.subheader(f"{td} 觀察名單")
        if not wl:
            st.info("沒有符合條件的股票。")
        else:
            st.dataframe(pd.DataFrame([{"代號": r["stock_id"], "名稱": r["name"], "方向": "做多" if r["side"] == "long" else "做空",
                                        "昨收": r["prev_close"], "昨高": r["prev_high"], "昨低": r["prev_low"],
                                        "ATR": r["atr"], "量比": r["vol_ratio"]} for r in wl]),
                         hide_index=True, width="stretch")
            st.caption("盤中觸發提醒請在電腦執行 `python run.py daytrade watch`，訊號會推播到手機。")
    led = Ledger.load()
    with st.expander("記錄一筆當沖（寫入交割帳）"):
        if led is None:
            st.info("請先到「現金池」設定餘額。")
        else:
            with st.form("dt_rec"):
                sid = st.text_input("代號")
                sh = st.number_input("股數", min_value=1, step=1000, value=1000)
                side = st.radio("方向", ["long", "short"], horizontal=True, format_func=lambda s: "先買後賣" if s == "long" else "先賣後買")
                e = st.number_input("進場價", min_value=0.01, step=0.5)
                x = st.number_input("出場價", min_value=0.01, step=0.5)
                if st.form_submit_button("記錄", width="stretch") and sid:
                    bp, sp = (e, x) if side == "long" else (x, e)
                    b1 = led.record_trade(c0, date.today(), "BUY", sid, int(sh), bp, daytrade=True, note="當沖")
                    s1 = led.record_trade(c0, date.today(), "SELL", sid, int(sh), sp, daytrade=True, note="當沖")
                    led.save()
                    st.success(f"淨損益 {b1['net'] + s1['net']:+,}，{s1['settle_date']} 交割")
