"""富果 MarketData API（行情，不含下單）。需要 `pip install fugle-marketdata` 與環境變數 FUGLE_API_KEY。

用途：
- 盤中即時報價（當沖監控，比證交所 MIS 穩定）
- 當日 1 分 K（收盤後存檔，累積成真實分 K 資料庫給當沖回測）
- 歷史 K 線

回應欄位依富果 API v1.0 文件解析；若富果改版，只需要修改本檔的 _norm_* 函式。
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def _client():
    try:
        from fugle_marketdata import RestClient
    except ImportError as e:
        raise RuntimeError("請先 `pip install fugle-marketdata`") from e
    key = os.environ.get("FUGLE_API_KEY")
    if not key:
        raise RuntimeError("未設定 FUGLE_API_KEY（到 developer.fugle.tw 申請行情 API 金鑰）")
    return RestClient(api_key=key)


def _norm_quote(q: dict) -> dict:
    last_trade = q.get("lastTrade") or {}
    total = q.get("total") or {}
    last = q.get("lastPrice") or q.get("closePrice") or last_trade.get("price")
    if last is None:   # 尚未成交：用最佳買價
        bids = q.get("bids") or []
        last = bids[0].get("price") if bids else None
    return {"last": last, "open": q.get("openPrice"), "high": q.get("highPrice"), "low": q.get("lowPrice"),
            "prev_close": q.get("previousClose") or q.get("referencePrice"),
            "volume": total.get("tradeVolume")}


def _norm_candles(js: dict) -> pd.DataFrame:
    data = js.get("data") or []
    if not data:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["date"]).dt.tz_localize(None) if "T" in str(df["date"].iloc[0]) else pd.to_datetime(df["date"])
    return df[["time", "open", "high", "low", "close", "volume"]].sort_values("time").reset_index(drop=True)


def fetch_quotes(stock_ids: list[str], client=None) -> dict[str, dict]:
    c = client or _client()
    out = {}
    for sid in stock_ids:
        try:
            out[sid] = _norm_quote(c.stock.intraday.quote(symbol=sid))
        except Exception:
            continue
    return out


def intraday_candles(sid, timeframe=1, client=None) -> pd.DataFrame:
    c = client or _client()
    return _norm_candles(c.stock.intraday.candles(symbol=sid, timeframe=str(timeframe)))


def historical_candles(sid, start, end, timeframe="D", client=None) -> pd.DataFrame:
    c = client or _client()
    js = c.stock.historical.candles(symbol=sid, from_=str(pd.Timestamp(start).date()),
                                    to=str(pd.Timestamp(end).date()), timeframe=timeframe)
    return _norm_candles(js)


def save_today_bars(stock_ids, out_dir="data/kbars", client=None, log=print) -> list[Path]:
    """收盤後存當日 1 分 K → data/kbars/<代號>_<日期>.csv（daytrade backtest --bars-dir 直接可用）。"""
    c = client or _client()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    saved = []
    for sid in stock_ids:
        try:
            df = intraday_candles(sid, 1, c)
        except Exception as e:
            log(f"  {sid}: 失敗 {e}")
            continue
        if df.empty:
            continue
        p = Path(out_dir) / f"{sid}_{df['time'].iloc[0].date()}.csv"
        df.to_csv(p, index=False)
        saved.append(p)
    log(f"  已存 {len(saved)} 檔分 K 到 {out_dir}/")
    return saved
