"""盤中監控：輪詢證交所 MIS 即時報價（免費、非官方 API，約 5 秒更新、偶有延遲），
價格觸發計畫時推播。不會下單。

狀態：waiting（等區間形成）→ armed（等突破）→ in_position（等停損／停利／平倉）→ done
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from ..notify import send_all
from .intraday import position_size, round_tick, tick_size

TZ = ZoneInfo("Asia/Taipei")
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def fetch_quotes(stock_ids: list[str]) -> dict[str, dict]:
    chans = "|".join(f"{m}_{s}.tw" for s in stock_ids for m in ("tse", "otc"))
    r = requests.get(MIS_URL, params={"ex_ch": chans, "json": 1, "delay": 0}, timeout=10,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp"})
    r.raise_for_status()
    out = {}
    for q in r.json().get("msgArray", []):
        def num(k):
            try:
                return float(str(q.get(k, "")).split("_")[0])
            except ValueError:
                return None
        last = num("z") or num("b") or num("a")   # 沒成交時用最佳買／賣價
        out[q["c"]] = {"last": last, "open": num("o"), "high": num("h"), "low": num("l"),
                       "prev_close": num("y"), "volume": num("v")}
    return out


class Watcher:
    def __init__(self, plans: list[dict], cfg):
        self.cfg, self.d = cfg, cfg["daytrade"]
        self.state = {p["stock_id"]: {"plan": p, "status": "waiting", "orh": None, "orl": None,
                                      "pv": 0.0, "vol": 0.0, "last_vol": None, "below_vwap": False} for p in plans}
        self.trades_opened = 0
        self.realized = 0.0

    def _orb_end(self):
        m = 9 * 60 + self.d["orb_minutes"]
        return f"{m // 60:02d}:{m % 60:02d}"

    def on_quote(self, sid, q, now: datetime) -> list[str]:
        st = self.state.get(sid)
        if not st or q.get("last") is None or st["status"] == "done":
            return []
        p, d, hhmm, px = st["plan"], self.d, now.strftime("%H:%M"), q["last"]
        side, alerts = p["side"], []
        # VWAP 近似：用每次輪詢的成交量增量 × 最新價
        if q.get("volume") is not None:
            if st["last_vol"] is not None and q["volume"] > st["last_vol"]:
                dv = q["volume"] - st["last_vol"]
                st["pv"] += px * dv
                st["vol"] += dv
            st["last_vol"] = q["volume"]
        vwap = st["pv"] / st["vol"] if st["vol"] else None

        if st["status"] == "waiting":
            if p["strategy"] == "orb" and hhmm >= self._orb_end():
                st["orh"], st["orl"] = q["high"], q["low"]
                st["status"] = "armed"
                alerts.append(f"{sid} 開盤區間形成：高 {st['orh']}／低 {st['orl']}，"
                              f"{'突破 ' + str(round_tick(st['orh'] + tick_size(st['orh']), True)) + ' 做多' if side == 'long' else '跌破 ' + str(round_tick(st['orl'] - tick_size(st['orl']), False)) + ' 做空'}")
            elif p["strategy"] == "vwap" and hhmm >= d.get("vwap_start", "09:30"):
                st["status"] = "armed"
            return alerts

        if st["status"] == "armed":
            if hhmm > d["entry_cutoff"]:
                st["status"] = "done"
                return [f"{sid} 已過 {d['entry_cutoff']} 未觸發，今日放棄"]
            if self.trades_opened >= d["max_trades_per_day"] or self.realized <= -d["capital"] * d["daily_max_loss"]:
                return []
            entry = stop = None
            max_risk = p["atr"] * d.get("max_risk_atr", 0.6)
            if p["strategy"] == "orb":
                if side == "long" and px > st["orh"]:
                    entry, stop = px, round_tick(max(st["orl"], px - max_risk), False)
                elif side == "short" and px < st["orl"]:
                    entry, stop = px, round_tick(min(st["orh"], px + max_risk), True)
            elif vwap:
                if side == "long":
                    if px < vwap:
                        st["below_vwap"] = True
                    elif st["below_vwap"] and px > vwap:
                        entry, stop = px, round_tick(max(q["low"] - tick_size(px), px - max_risk), False)
                else:
                    if px > vwap:
                        st["below_vwap"] = True
                    elif st["below_vwap"] and px < vwap:
                        entry, stop = px, round_tick(min(q["high"] + tick_size(px), px + max_risk), True)
            if entry is None:
                return []
            r = abs(entry - stop)
            target = entry + d["rr_target"] * r if side == "long" else entry - d["rr_target"] * r
            shares = position_size(entry, stop, d)
            if shares <= 0:
                st["status"] = "done"
                return [f"{sid} 觸發但風險金額不足一個單位，略過"]
            st.update(status="in_position", entry=entry, stop=stop, target=round_tick(target, side == "long"), shares=shares)
            self.trades_opened += 1
            return [f"🔔 {sid} {p.get('name', '')} {'做多買進' if side == 'long' else '做空先賣'} 約 {entry}，"
                    f"{shares:,} 股；停損 {stop}，停利 {st['target']}（{d['flat_time']} 前平倉）。"
                    f"成交後建議立刻在券商 App 掛停損停利（OCO／觸價）單，不用盯盤也會自動出場"]

        if st["status"] == "in_position":
            sign = 1 if side == "long" else -1
            reason = None
            if (px - st["stop"]) * sign <= 0:
                reason = "停損"
            elif (px - st["target"]) * sign >= 0:
                reason = "停利"
            elif hhmm >= d["flat_time"]:
                reason = "時間到強制平倉"
            if reason:
                pnl = (px - st["entry"]) * st["shares"] * sign
                self.realized += pnl
                st["status"] = "done"
                return [f"🔔 {sid} {reason}：{'賣出' if side == 'long' else '買回'} {st['shares']:,} 股 約 {px}，"
                        f"毛損益約 {pnl:+,.0f}（今日累計 {self.realized:+,.0f}）"]
        return []


def get_quote_fetcher(cfg, ids=None):
    """回傳 (fetch(ids) -> quotes, tick 秒數, 關閉函式)。"""
    src = cfg["daytrade"].get("quote_source", "twse_mis")
    if src == "fugle_ws":
        from ..fugle import FugleStream
        stream = FugleStream(ids or [])
        stream.connect()

        def fetch(ids_):
            stream.ensure_connected()
            return stream.latest(ids_)
        return fetch, 1, stream.close
    if src == "fugle":
        from ..fugle import _client, fetch_quotes as fugle_quotes
        client = _client()
        return (lambda ids_: fugle_quotes(ids_, client)), None, lambda: None
    return fetch_quotes, None, lambda: None


def watch(plans, cfg, interval=10, dry_run=False):
    src = cfg["daytrade"].get("quote_source", "twse_mis")
    limit = cfg["daytrade"].get("fugle_ws_max_subscriptions", 5)
    if src == "fugle_ws" and len(plans) > limit:
        print(f"⚠️ 富果 WebSocket 方案上限 {limit} 檔，只監控名單前 {limit} 檔（分數最高）")
        plans = plans[:limit]
    w = Watcher(plans, cfg)
    ids = [p["stock_id"] for p in plans]
    fetch, tick, close = get_quote_fetcher(cfg, ids)
    interval = tick or interval
    print(f"監控 {', '.join(ids)}，報價來源 {src}，每 {interval} 秒檢查；Ctrl+C 結束")
    try:
        while True:
            now = datetime.now(TZ)
            if now.strftime("%H:%M") > "13:30":
                print("已收盤，結束監控")
                break
            if now.strftime("%H:%M") < "09:00":
                time.sleep(interval)
                continue
            try:
                quotes = fetch(ids)
            except Exception as e:
                print("報價失敗：", e)
                time.sleep(interval)
                continue
            for sid in ids:
                for msg in w.on_quote(sid, quotes.get(sid, {}), now):
                    print(now.strftime("%H:%M:%S"), msg)
                    if not dry_run:
                        send_all("當沖提醒", msg, cfg)
            time.sleep(interval)
    finally:
        close()
