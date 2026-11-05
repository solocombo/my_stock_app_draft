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
    prices: pd.DataFrame      # date, stock_id, open, high, low, close, volume
    revenue: pd.DataFrame     # month(Timestamp, 月初), stock_id, revenue
    flows: pd.DataFrame       # date, stock_id, net_buy（三大法人買賣超股數）
    info: pd.DataFrame        # stock_id, name, industry, is_financial, market(tse/otc)
    as_of: pd.Timestamp
    notes: list[str] = field(default_factory=list)
    valuation: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["date", "stock_id", "per", "pbr", "dy"]))

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
            valuation=self.valuation[self.valuation["date"] <= as_of],
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
        p_rows, r_rows, f_rows, info_rows, v_rows = [], [], [], [], []
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
            volume = rng.lognormal(15, 0.5, len(dates)) * (1 + 3 * np.abs(ret) / vol * 0.2)
            prev = np.concatenate([[close[0]], close[:-1]])
            opn = prev * (1 + rng.normal(0, vol * 0.4, len(dates)))
            hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol * 0.5, len(dates))))
            lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol * 0.5, len(dates))))
            p_rows.append(pd.DataFrame({"date": dates, "stock_id": sid, "open": opn.round(2), "high": hi.round(2),
                                        "low": lo.round(2), "close": close.round(2), "volume": volume.round()}))
            # 估值：EPS 跟營收趨勢走，股利 = 配發率 × EPS
            eps = close[0] / rng.uniform(8, 30) * np.exp(trend[month_idx] - trend[month_idx[0]])
            bvps = close[0] / rng.uniform(0.8, 4) * np.linspace(1, 1.08, len(dates))
            payout = rng.uniform(0.3, 0.95)
            v_rows.append(pd.DataFrame({"date": dates, "stock_id": sid, "per": (close / eps).round(2),
                                        "pbr": (close / bvps).round(2), "dy": (payout * eps / close * 100).round(2)}))

            # 法人：與近期報酬弱相關
            net = (pd.Series(ret).rolling(5, min_periods=1).mean().values * 3e7 + rng.normal(0, 2e5, len(dates)))
            f_rows.append(pd.DataFrame({"date": dates, "stock_id": sid, "net_buy": net.round()}))

            info_rows.append((sid, f"模擬股{k + 1:02d}", ind, ind == "金融保險業", "tse"))

        md = MarketData(
            prices=pd.concat(p_rows, ignore_index=True),
            revenue=pd.DataFrame(r_rows, columns=["month", "stock_id", "revenue"]),
            flows=pd.concat(f_rows, ignore_index=True),
            info=pd.DataFrame(info_rows, columns=["stock_id", "name", "industry", "is_financial", "market"]),
            as_of=dates[-1],
            notes=["demo data: 模擬資料，僅供測試流程，不代表任何真實股票"],
            valuation=pd.concat(v_rows, ignore_index=True),
        )
        # 營收只保留已公布的月份（真實資料本來就是如此；回測另外用 slice_to 控制）
        md.revenue = md.revenue[md.revenue["month"] + pd.offsets.MonthBegin(1) + pd.Timedelta(days=9) <= md.as_of]
        return md


# ---------------------------------------------------------------- FinMind

class FinMindProvider:
    """FinMind API v4。每個 fetch_* 回傳已正規化的 DataFrame，供 DataStore 增量快取使用。"""
    URL = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self, token=None, sleep=0.3):
        self.token = token or os.environ.get("FINMIND_TOKEN")
        self.sleep = sleep

    def _get(self, dataset, data_id=None, start=None, end=None):
        params = {"dataset": dataset}
        if data_id:
            params["data_id"] = data_id
        if start:
            params["start_date"] = str(pd.Timestamp(start).date())
        if end:
            params["end_date"] = str(pd.Timestamp(end).date())
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        for attempt in range(4):
            try:
                r = requests.get(self.URL, params=params, headers=headers, timeout=30)
                if r.status_code == 402:
                    raise RuntimeError("FinMind 額度用完（402），請稍後再試或設定 FINMIND_TOKEN")
                r.raise_for_status()
                js = r.json()
                if js.get("status") not in (200, None):
                    raise RuntimeError(js.get("msg"))
                time.sleep(self.sleep)
                return pd.DataFrame(js.get("data", []))
            except RuntimeError:
                raise
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))

    # ---- 各資料集（正規化欄位） ----
    def fetch_info(self, universe, fin_inds):
        info = self._get("TaiwanStockInfo")
        info = info[info["stock_id"].isin(universe)].drop_duplicates("stock_id")
        return pd.DataFrame({
            "stock_id": info["stock_id"], "name": info["stock_name"], "industry": info["industry_category"],
            "is_financial": info["industry_category"].isin(set(fin_inds)),
            "market": info["type"].map({"twse": "tse", "tpex": "otc"}).fillna("tse"),
        })

    def fetch_prices(self, sid, start, end):
        p = self._get("TaiwanStockPrice", sid, start, end)
        if p.empty:
            return p
        return pd.DataFrame({"date": pd.to_datetime(p["date"]), "stock_id": sid,
                             "open": p["open"].astype(float), "high": p["max"].astype(float),
                             "low": p["min"].astype(float), "close": p["close"].astype(float),
                             "volume": p["Trading_Volume"].astype(float)})

    def fetch_revenue(self, sid, start, end):
        r = self._get("TaiwanStockMonthRevenue", sid, start, end)
        if r.empty:
            return r
        m = pd.to_datetime(dict(year=r["revenue_year"], month=r["revenue_month"], day=1))
        return pd.DataFrame({"month": m, "stock_id": sid, "revenue": r["revenue"].astype(float)})

    def fetch_flows(self, sid, start, end):
        f = self._get("TaiwanStockInstitutionalInvestorsBuySell", sid, start, end)
        if f.empty:
            return f
        f["net"] = f["buy"].astype(float) - f["sell"].astype(float)
        g = f.groupby("date", as_index=False)["net"].sum()
        return pd.DataFrame({"date": pd.to_datetime(g["date"]), "stock_id": sid, "net_buy": g["net"]})

    def fetch_valuation(self, sid, start, end):
        v = self._get("TaiwanStockPER", sid, start, end)
        if v.empty:
            return v
        return pd.DataFrame({"date": pd.to_datetime(v["date"]), "stock_id": sid, "per": v["PER"].astype(float),
                             "pbr": v["PBR"].astype(float), "dy": v["dividend_yield"].astype(float)})

    def load(self, cfg) -> MarketData:
        """經由本機快取載入（先增量更新）。"""
        from .datastore import DataStore
        store = DataStore(cfg)
        if cfg["data"].get("auto_update", True):
            store.update(self)
        return store.load()


def get_provider(cfg):
    src = cfg["data"]["source"]
    if src == "demo":
        return DemoProvider()
    if src == "finmind":
        return FinMindProvider()
    raise ValueError(f"unknown data source: {src}")
