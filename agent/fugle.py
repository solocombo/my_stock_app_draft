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


class FugleStream:
    """富果 WebSocket 即時行情（aggregates 頻道：開高低收、成交量，與 REST quote 同格式）。

    伺服器有更新就推送，比輪詢即時，也不會吃 REST 額度。斷線時 latest() 會回傳舊資料，
    watch 迴圈會呼叫 ensure_connected() 自動重連。
    """

    def __init__(self, stock_ids, client_factory=None, log=print):
        self.ids = list(stock_ids)
        self.quotes: dict[str, dict] = {}
        self.connected = False
        self.log = log
        self._factory = client_factory or self._default_factory
        self._ws = None

    @staticmethod
    def _default_factory():
        try:
            from fugle_marketdata import WebSocketClient
        except ImportError as e:
            raise RuntimeError("請先 `pip install fugle-marketdata`") from e
        key = os.environ.get("FUGLE_API_KEY")
        if not key:
            raise RuntimeError("未設定 FUGLE_API_KEY")
        return WebSocketClient(api_key=key).stock

    def _on_message(self, raw):
        import json
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if msg.get("event") not in ("data", "snapshot"):
            return
        d = msg.get("data") or {}
        sid = d.get("symbol")
        if not sid:
            return
        if "price" in d and "lastPrice" not in d:      # trades 頻道格式
            q = self.quotes.get(sid, {}).copy()
            q["last"] = d["price"]
            q["volume"] = d.get("volume", q.get("volume"))
            q["high"] = max(q.get("high") or d["price"], d["price"])
            q["low"] = min(q.get("low") or d["price"], d["price"])
            self.quotes[sid] = q
        else:
            self.quotes[sid] = _norm_quote(d)

    def _on_disconnect(self, *args):
        self.connected = False
        self.log(f"富果 WebSocket 斷線：{args}")

    def connect(self):
        self._ws = self._factory()
        self._ws.on("message", self._on_message)
        self._ws.on("disconnect", self._on_disconnect)
        self._ws.connect()
        self._ws.subscribe({"channel": "aggregates", "symbols": self.ids})
        self.connected = True
        self.log(f"富果 WebSocket 已連線，訂閱 {', '.join(self.ids)}")

    def ensure_connected(self):
        if not self.connected:
            try:
                self.connect()
            except Exception as e:
                self.log(f"富果 WebSocket 連線失敗：{e}")

    def latest(self, ids=None) -> dict[str, dict]:
        return {k: v for k, v in self.quotes.items() if ids is None or k in ids}

    def close(self):
        if self._ws is not None:
            try:
                self._ws.disconnect()
            except Exception:
                pass
