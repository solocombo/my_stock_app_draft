"""資料層：統一輸出 MarketData，策略不需要知道資料從哪來。

- DemoProvider：模擬資料（可離線、可重現），用來開發與回測流程。
- FinMindProvider：真實台股資料（FinMind API v4）。FINMIND_TOKEN 選填，有 token 額度較高。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests


@dataclass
class MarketData:
    prices: pd.DataFrame      # date, stock_id, close, volume
    revenue: pd.DataFrame     # month(Timestamp, 月初), stock_id, revenue
    flows: pd.DataFrame       # date, stock_id, net_buy（三大法人買賣超股數）
    info: pd.DataFrame        # stock_id, name, industry, is_financial
    as_of: pd.Timestamp
    notes: list[str] = field(default_factory=list)

    def slice_to(self, as_of) -> "MarketData":
        """回測用：只保留 as_of（含）以前看得到的資料，避免偷看未來。"""
        as_of = pd.Timestamp(as_of)
        # 月營收於次月 10 日前公布：as_of 當下只看得到「公布日 ≤ as_of」的月份
        rev_visible = self.revenue[self.revenue["month"] + pd.offsets.MonthBegin(1) + pd.Timedelta(days=9) <= as_of]
        return MarketData(
            prices=self.prices[self.prices["date"] <= as_of],
            revenue=rev_visible,
            flows=self.flows[self.flows["date"] <= as_of],
            info=self.info,
            as_of=as_of,
            notes=list(self.notes),
        )


# ---------------------------------------------------------------- demo

class DemoProvider:
    """產生 n 檔模擬股票；部分股票有「營收加速 → 股價走強」的關聯，讓策略有東西可抓。"""

    def __init__(self, n_stocks=60, days=600, seed=7, end=None):
        self.n, self.days, self.seed = n_stocks, days, seed
        self.end = pd.Timestamp(end or date.today()).normalize()

    def load(self, cfg=None) -> MarketData:
        rng = np.random.default_rng(self.seed)
        dates = pd.bdate_range(end=self.end, periods=self.days)
        ids = [f"D{i:03d}" for i in range(1, self.n + 1)]
        industries = ["半導體業", "電子零組件業", "電腦及週邊設備業", "金融保險業", "航運業", "塑膠工業", "食品工業", "通信網路業"]

        months = pd.date_range(dates[0] - pd.offsets.MonthBegin(14), dates[-1], freq="MS")
        p_rows, r_rows, f_rows, info_rows = [], [], [], []
        for k, sid in enumerate(ids):
            ind = industries[k % len(industries)]
            # 營收：基期 × 季節性 × 隨機趨勢（趨勢會帶動股價漂移）
            trend = np.cumsum(rng.normal(0, 0.03, len(months)))
            base = rng.uniform(5e8, 5e10)
            season = 1 + 0.05 * np.sin(np.arange(len(months)) / 12 * 2 * np.pi)
            rev = base * np.exp(trend) * season * rng.lognormal(0, 0.04, len(months))
            r_rows += [(m, sid, float(v)) for m, v in zip(months, rev)]

            # 股價：漂移跟營收趨勢（落後 1 個月）連動 + 雜訊
            month_idx = np.searchsorted(months.values, dates.values, side="right") - 2
            month_idx = np.clip(month_idx, 1, len(months) - 1)
            drift = (trend[month_idx] - trend[month_idx - 1]) * 0.08
            vol = rng.uniform(0.012, 0.028)
            ret = drift + rng.normal(0.0002, vol, len(dates))
            close = rng.uniform(20, 900) * np.exp(np.cumsum(ret))
            volume = rng.lognormal(15, 0.5, len(dates))
            p_rows.append(pd.DataFrame({"date": dates, "stock_id": sid, "close": close.round(2), "volume": volume.round()}))

            # 法人：與近期報酬弱相關
            net = (pd.Series(ret).rolling(5, min_periods=1).mean().values * 3e7 + rng.normal(0, 2e5, len(dates)))
            f_rows.append(pd.DataFrame({"date": dates, "stock_id": sid, "net_buy": net.round()}))

            info_rows.append((sid, f"模擬股{k + 1:02d}", ind, ind == "金融保險業"))

        return MarketData(
            prices=pd.concat(p_rows, ignore_index=True),
            revenue=pd.DataFrame(r_rows, columns=["month", "stock_id", "revenue"]),
            flows=pd.concat(f_rows, ignore_index=True),
            info=pd.DataFrame(info_rows, columns=["stock_id", "name", "industry", "is_financial"]),
            as_of=dates[-1],
            notes=["demo data: 模擬資料，僅供測試流程，不代表任何真實股票"],
        )


# ---------------------------------------------------------------- FinMind

class FinMindProvider:
    URL = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self, token=None, sleep=0.3):
        self.token = token or os.environ.get("FINMIND_TOKEN")
        self.sleep = sleep

    def _get(self, dataset, data_id=None, start=None, end=None):
        params = {"dataset": dataset}
        if data_id:
            params["data_id"] = data_id
        if start:
            params["start_date"] = str(start)
        if end:
            params["end_date"] = str(end)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        for attempt in range(4):
            try:
                r = requests.get(self.URL, params=params, headers=headers, timeout=30)
                r.raise_for_status()
                js = r.json()
                if js.get("status") not in (200, None):
                    raise RuntimeError(js.get("msg"))
                time.sleep(self.sleep)
                return pd.DataFrame(js.get("data", []))
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))

    def load(self, cfg) -> MarketData:
        dcfg = cfg["data"]
        universe = dcfg["universe"]
        end = date.today()
        start = end - timedelta(days=int(dcfg.get("lookback_days", 400)) * 7 // 5)
        rev_start = end - timedelta(days=550)
        notes, prices, revs, flows = [], [], [], []

        info = self._get("TaiwanStockInfo")
        info = info[info["stock_id"].isin(universe)].drop_duplicates("stock_id")
        fin_inds = set(dcfg.get("financial_industries", []))
        info = pd.DataFrame({
            "stock_id": info["stock_id"],
            "name": info["stock_name"],
            "industry": info["industry_category"],
            "is_financial": info["industry_category"].isin(fin_inds),
        })

        for sid in universe:
            try:
                p = self._get("TaiwanStockPrice", sid, start, end)
                if p.empty:
                    notes.append(f"{sid}: price missing")
                    continue
                prices.append(pd.DataFrame({"date": pd.to_datetime(p["date"]), "stock_id": sid,
                                            "close": p["close"].astype(float), "volume": p["Trading_Volume"].astype(float)}))
                r = self._get("TaiwanStockMonthRevenue", sid, rev_start, end)
                if not r.empty:
                    m = pd.to_datetime(dict(year=r["revenue_year"], month=r["revenue_month"], day=1))
                    revs.append(pd.DataFrame({"month": m, "stock_id": sid, "revenue": r["revenue"].astype(float)}))
                else:
                    notes.append(f"{sid}: revenue missing")
                f = self._get("TaiwanStockInstitutionalInvestorsBuySell", sid, end - timedelta(days=60), end)
                if not f.empty:
                    f["net"] = f["buy"].astype(float) - f["sell"].astype(float)
                    g = f.groupby("date", as_index=False)["net"].sum()
                    flows.append(pd.DataFrame({"date": pd.to_datetime(g["date"]), "stock_id": sid, "net_buy": g["net"]}))
                else:
                    notes.append(f"{sid}: institutional missing")
            except Exception as e:  # 單檔失敗不中斷整批
                notes.append(f"{sid}: fetch error {e}")

        prices = pd.concat(prices, ignore_index=True)
        as_of = prices["date"].max()
        fl = pd.concat(flows, ignore_index=True) if flows else pd.DataFrame(columns=["date", "stock_id", "net_buy"])
        if not fl.empty and fl["date"].max() < as_of:
            notes.append(f"institutional data ends {fl['date'].max().date()} (< as_of {as_of.date()})")
        return MarketData(
            prices=prices,
            revenue=pd.concat(revs, ignore_index=True) if revs else pd.DataFrame(columns=["month", "stock_id", "revenue"]),
            flows=fl, info=info, as_of=as_of, notes=notes,
        )


def get_provider(cfg):
    src = cfg["data"]["source"]
    if src == "demo":
        return DemoProvider()
    if src == "finmind":
        return FinMindProvider()
    raise ValueError(f"unknown data source: {src}")
