"""現金池／交割帳（T+2）。

台股買進不需要先有錢，但 T+2 上午 10 點前交割帳戶必須有足夠餘額，否則就是違約交割；
賣出的錢也要 T+2 才會入帳。所以「今天能買多少」要看的是未來每一天的帳戶餘額。

cash_ledger.json：
{
  "bank_balance": 300000,          # 你最後一次確認的交割帳戶餘額
  "balance_date": "2026-11-05",    # 那筆餘額是哪一天（已包含當天及以前交割的款項）
  "reserve": 10000,                # 永遠不動用的安全水位
  "pending": [ {trade_date, settle_date, stock_id, side, shares, price, fee, tax, net, daytrade, note} ],
  "events":  [ {date, amount, note} ]   # 未來的入金(+)／出金(-)，例如薪水、信用卡扣款
}

用法：隨時用 `cash set` 更新實際餘額，已交割的項目會自動移除，不會重複計算。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from .calendar_tw import is_trading_day, load_holidays, settle_date, trading_days

LEDGER_FILE = Path("cash_ledger.json")


def fee(cfg, value):
    c = cfg["costs"]
    return max(c.get("min_fee", 1), math.floor(value * c["fee_rate"] * c.get("fee_discount", 1.0)))


def sell_tax(cfg, value, daytrade=False):
    rate = cfg.get("daytrade", {}).get("tax", 0.0015) if daytrade else cfg["costs"]["sell_tax"]
    return math.floor(value * rate)


class Ledger:
    def __init__(self, data: dict, holidays=None):
        self.d = data
        self.d.setdefault("pending", [])
        self.d.setdefault("events", [])
        self.d.setdefault("reserve", 0)
        self.holidays = holidays if holidays is not None else load_holidays()

    # ---------- 讀寫 ----------
    @classmethod
    def load(cls, path=LEDGER_FILE, holidays=None):
        p = Path(path)
        if not p.exists():
            return None
        return cls(json.loads(p.read_text(encoding="utf-8")), holidays)

    @classmethod
    def new(cls, balance, date, holidays=None):
        return cls({"bank_balance": float(balance), "balance_date": str(pd.Timestamp(date).date())}, holidays)

    def save(self, path=LEDGER_FILE):
        self.d["pending"].sort(key=lambda x: (x["settle_date"], x["trade_date"]))
        self.d["events"].sort(key=lambda x: x["date"])
        Path(path).write_text(json.dumps(self.d, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 更新 ----------
    def set_balance(self, amount, date):
        """你查到的實際餘額。date 當天（含）以前應交割的款項都視為已反映在餘額裡。"""
        d = str(pd.Timestamp(date).date())
        self.d["bank_balance"] = float(amount)
        self.d["balance_date"] = d
        self.d["pending"] = [x for x in self.d["pending"] if x["settle_date"] > d]
        self.d["events"] = [x for x in self.d["events"] if x["date"] > d]

    def add_event(self, date, amount, note=""):
        self.d["events"].append({"date": str(pd.Timestamp(date).date()), "amount": float(amount), "note": note})

    def record_trade(self, cfg, trade_date, side, stock_id, shares, price, daytrade=False, note=""):
        value = shares * price
        f = fee(cfg, value)
        tax = sell_tax(cfg, value, daytrade) if side == "SELL" else 0
        net = (value - f - tax) if side == "SELL" else -(value + f)
        item = {"trade_date": str(pd.Timestamp(trade_date).date()),
                "settle_date": str(settle_date(trade_date, self.holidays).date()),
                "stock_id": stock_id, "side": side, "shares": shares, "price": price,
                "fee": f, "tax": tax, "net": round(net), "daytrade": daytrade, "note": note}
        self.d["pending"].append(item)
        return item

    # ---------- 查詢 ----------
    @property
    def pending_net(self) -> float:
        return sum(x["net"] for x in self.d["pending"])

    @property
    def total_cash(self) -> float:
        """餘額 + 所有未交割淨額（不含未來入出金），用來算淨值。"""
        return self.d["bank_balance"] + self.pending_net

    def _effective(self, d) -> pd.Timestamp:
        """款項落在假日時，順延到下一個交易日。"""
        d = pd.Timestamp(d).normalize()
        while not is_trading_day(d, self.holidays):
            d += pd.Timedelta(days=1)
        return d

    def forecast(self, start=None, days=10, extra: list[dict] | None = None) -> pd.DataFrame:
        """未來交易日的預估帳戶餘額（每天交割後）。extra：尚未下單的建議（同 pending 格式），用來試算。"""
        base = pd.Timestamp(self.d["balance_date"]).normalize()
        start = max(pd.Timestamp(start or pd.Timestamp.today()).normalize(), base + pd.Timedelta(days=1))
        items = [(x["settle_date"], x["net"], f"{'買' if x['side'] == 'BUY' else '賣'}{x['stock_id']} {x['shares']}股"
                  + ("(當沖)" if x.get("daytrade") else "")) for x in self.d["pending"] + (extra or [])]
        items += [(x["date"], x["amount"], x["note"] or ("入金" if x["amount"] > 0 else "出金")) for x in self.d["events"]]
        items = [(self._effective(d), amt, label) for d, amt, label in items]
        items = [i for i in items if i[0] > base]

        bal = self.d["bank_balance"] + sum(amt for d, amt, _ in items if d < start)
        end = max([i[0] for i in items] + [start])
        rows = []
        for d in trading_days(start, end, self.holidays):
            todays = [i for i in items if i[0] == d]
            inflow = sum(i[1] for i in todays if i[1] > 0)
            outflow = sum(i[1] for i in todays if i[1] < 0)
            bal += inflow + outflow
            rows.append({"date": str(d.date()), "in": round(inflow), "out": round(outflow),
                         "balance": round(bal), "items": "；".join(i[2] for i in todays)})
        d = rows and pd.Timestamp(rows[-1]["date"]) or start - pd.Timedelta(days=1)
        while len(rows) < days:     # 補滿天數，方便看
            d = self._effective(d + pd.Timedelta(days=1))
            rows.append({"date": str(d.date()), "in": 0, "out": 0, "balance": round(bal), "items": ""})
        return pd.DataFrame(rows, columns=["date", "in", "out", "balance", "items"])

    def buy_budget(self, trade_date) -> float:
        """今天下單的買進淨額上限：T+2 交割日之後每一天的餘額最低點，再扣掉安全水位。"""
        t2 = str(settle_date(trade_date, self.holidays).date())
        fc = self.forecast(start=pd.Timestamp(self.d["balance_date"]) + pd.Timedelta(days=1), days=30)
        after = fc[fc["date"] >= t2]["balance"]
        floor = after.min() if len(after) else self.total_cash
        return float(floor) - self.d.get("reserve", 0)

    def shortfalls(self, extra=None, start=None) -> pd.DataFrame:
        fc = self.forecast(start=start, days=30, extra=extra)
        return fc[fc["balance"] < self.d.get("reserve", 0)]


def orders_as_pending(orders, cfg, trade_date, holidays) -> list[dict]:
    """把建議單轉成交割項目，讓 forecast 試算「如果照做」的現金曲線。"""
    sd = str(settle_date(trade_date, holidays).date())
    out = []
    for o in orders:
        tax = sell_tax(cfg, o.value) if o.side == "SELL" else 0
        f = fee(cfg, o.value)
        net = (o.value - f - tax) if o.side == "SELL" else -(o.value + f)
        out.append({"trade_date": str(pd.Timestamp(trade_date).date()), "settle_date": sd, "stock_id": o.stock_id,
                    "side": o.side, "shares": o.shares, "net": round(net)})
    return out
