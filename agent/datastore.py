"""本機資料快取（data/cache/*.csv）＋ 增量更新。

- 第一次：每檔回補歷史（日線約 lookback_days 個交易日、月營收 20 個月、法人 120 天）
- 之後：每檔只從「快取最後日期的隔天」開始抓；已經是最新的就跳過，不浪費 API 額度
- 新增股票到 universe 時自動回補
- 更新完做新鮮度檢查：沒抓到最新交易日的資料會寫進報告的資料備註
  （例如法人資料常在 15:00 後才完整，太早跑會落後一天）
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .calendar_tw import add_trading_days, is_trading_day, load_holidays
from .data import MarketData

TZ = ZoneInfo("Asia/Taipei")

# 資料集 → (日期欄位, 回補天數, 資料就緒時間)
DATASETS = {
    "prices": ("date", None, "14:30"),        # None = 依 data.lookback_days
    "flows": ("date", 120, "16:00"),
    "valuation": ("date", None, "16:00"),
    "revenue": ("month", 620, None),
}


def expected_latest(ready: str, now: datetime, holidays) -> pd.Timestamp:
    """在 now 這個時間點，最新應該有哪一天的資料。"""
    today = pd.Timestamp(now.date())
    if is_trading_day(today, holidays) and now.strftime("%H:%M") >= ready:
        return today
    return add_trading_days(today, -1, holidays)


class DataStore:
    def __init__(self, cfg, root="data/cache", now=None, holidays=None):
        self.cfg = cfg
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.universe = list(cfg["data"]["universe"])
        self.now = now or datetime.now(TZ)
        self.holidays = holidays if holidays is not None else load_holidays()
        self.notes: list[str] = []

    # ---------- 檔案 ----------
    def _path(self, name):
        return self.root / f"{name}.csv"

    def read(self, name) -> pd.DataFrame:
        p = self._path(name)
        if not p.exists():
            return pd.DataFrame()
        df = pd.read_csv(p, dtype={"stock_id": str})
        for col in ("date", "month"):
            if col in df:
                df[col] = pd.to_datetime(df[col])
        return df

    def write(self, name, df):
        df.to_csv(self._path(name), index=False)

    # ---------- 更新 ----------
    def _backfill_start(self, name):
        days = DATASETS[name][1]
        if days is None:
            days = int(self.cfg["data"].get("lookback_days", 400)) * 7 // 5 + 10
        return pd.Timestamp(self.now.date()) - pd.Timedelta(days=days)

    def update(self, provider, datasets=None, log=print) -> dict:
        """增量更新，回傳 {dataset: 抓取筆數}。單檔失敗不中斷，記在 notes。"""
        summary = {}
        info = self.read("info")
        missing_info = set(self.universe) - set(info.get("stock_id", pd.Series(dtype=str)))
        if missing_info:
            try:
                new = provider.fetch_info(self.universe, self.cfg["data"].get("financial_industries", []))
                self.write("info", pd.concat([info, new]).drop_duplicates("stock_id", keep="last"))
            except Exception as e:
                self.notes.append(f"info fetch error: {e}")

        end = pd.Timestamp(self.now.date())
        for name in datasets or DATASETS:
            date_col, _, ready = DATASETS[name]
            old = self.read(name)
            last_by = old.groupby("stock_id")[date_col].max() if not old.empty else pd.Series(dtype="datetime64[ns]")
            target = expected_latest(ready, self.now, self.holidays) if ready else None
            fetch = getattr(provider, f"fetch_{name}")
            parts, n_req = [], 0
            for sid in self.universe:
                last = last_by.get(sid)
                if last is None or pd.isna(last):
                    start = self._backfill_start(name)
                elif name == "revenue":
                    # 營收：上個月（最新可能公布的月份）已有就不用抓；否則從最後一個月重抓（可能有更正）
                    prev_month = pd.Timestamp(self.now.date()).replace(day=1) - pd.DateOffset(months=1)
                    if last >= prev_month:
                        continue
                    start = last
                else:
                    if target is not None and last >= target:
                        continue
                    start = last + pd.Timedelta(days=1)
                try:
                    df = fetch(sid, start, end)
                    n_req += 1
                    if df is not None and not df.empty:
                        parts.append(df)
                except Exception as e:
                    self.notes.append(f"{name} {sid}: {e}")
            if parts:
                new = pd.concat([old] + parts, ignore_index=True)
                new = new.drop_duplicates(["stock_id", date_col], keep="last").sort_values(["stock_id", date_col])
                keep_from = self._backfill_start(name) - pd.Timedelta(days=30)
                new = new[new[date_col] >= keep_from]
                self.write(name, new)
            summary[name] = {"requests": n_req, "rows": int(sum(len(p) for p in parts))}
            log(f"  {name:<10} 請求 {n_req:>3} 次，新增 {summary[name]['rows']} 筆")
        meta = {"updated_at": self.now.isoformat(timespec="seconds"), "summary": summary, "notes": self.notes}
        (self.root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    # ---------- 檢查與載入 ----------
    def freshness(self) -> list[str]:
        out = []
        for name, (date_col, _, ready) in DATASETS.items():
            df = self.read(name)
            if df.empty:
                out.append(f"{name}: 快取是空的")
                continue
            last_by = df.groupby("stock_id")[date_col].max()
            missing = [s for s in self.universe if s not in last_by.index]
            if missing:
                out.append(f"{name}: {len(missing)} 檔沒有資料（{', '.join(missing[:8])}）")
            if ready:
                target = expected_latest(ready, self.now, self.holidays)
                stale = last_by[last_by < target]
                if len(stale):
                    out.append(f"{name} data ends {stale.min().date()} (< {target.date()})：{len(stale)} 檔落後")
        return out

    def status(self) -> pd.DataFrame:
        rows = []
        for name, (date_col, _, _) in DATASETS.items():
            df = self.read(name)
            rows.append({"dataset": name, "rows": len(df), "stocks": df["stock_id"].nunique() if len(df) else 0,
                         "first": str(df[date_col].min().date()) if len(df) else "-",
                         "last": str(df[date_col].max().date()) if len(df) else "-"})
        return pd.DataFrame(rows)

    def load(self) -> MarketData:
        prices = self.read("prices")
        if prices.empty:
            raise RuntimeError("快取沒有價格資料，請先執行 `python run.py data update`")
        prices = prices[prices["stock_id"].isin(self.universe)]
        info = self.read("info")
        if not info.empty:
            info["is_financial"] = info["is_financial"].astype(str).str.lower().isin(["true", "1"])
        else:
            info = pd.DataFrame({"stock_id": self.universe, "name": "", "industry": "", "is_financial": False, "market": "tse"})
        empty = lambda cols: pd.DataFrame(columns=cols)
        rev, fl, val = self.read("revenue"), self.read("flows"), self.read("valuation")
        return MarketData(
            prices=prices,
            revenue=rev if not rev.empty else empty(["month", "stock_id", "revenue"]),
            flows=fl if not fl.empty else empty(["date", "stock_id", "net_buy"]),
            info=info,
            as_of=prices["date"].max(),
            notes=self.notes + self.freshness(),
            valuation=val if not val.empty else empty(["date", "stock_id", "per", "pbr", "dy"]),
        )
