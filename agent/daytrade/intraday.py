"""當沖策略與逐筆模擬。價格單位遵守台股升降單位（tick）。"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd


def tick_size(price: float) -> float:
    for limit, t in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price < limit:
            return t
    return 5.0


def round_tick(price: float, up: bool) -> float:
    t = tick_size(price)
    n = price / t
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * t, 2)


def position_size(entry, stop, dcfg) -> int:
    """依每筆風險金額與單筆部位上限計算股數。"""
    risk = dcfg["capital"] * dcfg["risk_per_trade"]
    per_share = abs(entry - stop)
    if per_share <= 0:
        return 0
    lot = int(dcfg.get("lot_size", 1000))
    sh = min(risk / per_share, dcfg["max_position_value"] / entry)
    return int(math.floor(sh / lot) * lot)


def trade_cost(entry, exit_, shares, side, cfg):
    c, d = cfg["costs"], cfg["daytrade"]
    fee = lambda v: max(c.get("min_fee", 1), math.floor(v * c["fee_rate"] * c.get("fee_discount", 1.0)))
    buy_px, sell_px = (entry, exit_) if side == "long" else (exit_, entry)
    return fee(buy_px * shares) + fee(sell_px * shares) + math.floor(sell_px * shares * d["tax"])


@dataclass
class DayTrade:
    date: str
    stock_id: str
    side: str
    strategy: str
    entry_time: str
    entry: float
    stop: float
    target: float
    shares: int
    exit_time: str = ""
    exit: float = 0.0
    exit_reason: str = ""
    gross: float = 0.0
    cost: float = 0.0
    pnl: float = 0.0

    def dict(self):
        return asdict(self)


def _hhmm(ts) -> str:
    return pd.Timestamp(ts).strftime("%H:%M")


def _vwap(bars: pd.DataFrame) -> pd.Series:
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    return (typical * bars["volume"]).cumsum() / bars["volume"].cumsum()


def find_entry(bars: pd.DataFrame, plan: dict, dcfg) -> dict | None:
    """在分 K 中找第一個符合策略的進場點。回傳 {i, time, entry, stop, target} 或 None。"""
    side, atr = plan["side"], plan["atr"]
    cutoff = dcfg["entry_cutoff"]
    max_risk = atr * dcfg.get("max_risk_atr", 0.6)
    strat = plan.get("strategy", dcfg["strategy"])
    t = bars["time"].map(_hhmm)

    if strat == "orb":
        n = dcfg["orb_minutes"]
        orb = bars.iloc[:n]
        orh, orl = orb["high"].max(), orb["low"].min()
        for i in range(n, len(bars)):
            if t.iloc[i] > cutoff:
                break
            b = bars.iloc[i]
            if side == "long" and b["high"] > orh:
                entry = round_tick(max(orh + tick_size(orh), b["open"]), up=True)
                stop = round_tick(max(orl, entry - max_risk), up=False)
            elif side == "short" and b["low"] < orl:
                entry = round_tick(min(orl - tick_size(orl), b["open"]), up=False)
                stop = round_tick(min(orh, entry + max_risk), up=True)
            else:
                continue
            return _with_target(i, t.iloc[i], entry, stop, side, dcfg)
        return None

    if strat == "vwap":
        vw = _vwap(bars)
        start = dcfg.get("vwap_start", "09:30")
        for i in range(1, len(bars)):
            if t.iloc[i] < start:
                continue
            if t.iloc[i] > cutoff:
                break
            prev, b = bars.iloc[i - 1], bars.iloc[i]
            if side == "long" and prev["low"] < vw.iloc[i - 1] and b["close"] > vw.iloc[i] and b["close"] > b["open"]:
                entry = round_tick(b["close"], up=True)
                stop = round_tick(max(bars["low"].iloc[: i + 1].min() - tick_size(entry), entry - max_risk), up=False)
            elif side == "short" and prev["high"] > vw.iloc[i - 1] and b["close"] < vw.iloc[i] and b["close"] < b["open"]:
                entry = round_tick(b["close"], up=False)
                stop = round_tick(min(bars["high"].iloc[: i + 1].max() + tick_size(entry), entry + max_risk), up=True)
            else:
                continue
            if abs(entry - stop) < tick_size(entry):
                continue
            # 收盤價成交 → 實際進場在下一根
            return _with_target(i + 1 if i + 1 < len(bars) else i, t.iloc[i], entry, stop, side, dcfg)
        return None
    raise ValueError(f"unknown daytrade strategy {strat}")


def _with_target(i, time, entry, stop, side, dcfg):
    r = abs(entry - stop)
    target = entry + dcfg["rr_target"] * r if side == "long" else entry - dcfg["rr_target"] * r
    return {"i": i, "time": time, "entry": entry, "stop": stop,
            "target": round_tick(target, up=(side == "long"))}


def simulate(bars: pd.DataFrame, plan: dict, cfg) -> DayTrade | None:
    """單檔單日模擬。同一根 K 同時碰到停損與停利時，保守地當作先停損。"""
    dcfg = cfg["daytrade"]
    bars = bars.sort_values("time").reset_index(drop=True)
    e = find_entry(bars, plan, dcfg)
    if not e:
        return None
    side = plan["side"]
    sh = position_size(e["entry"], e["stop"], dcfg)
    if sh <= 0:
        return None
    tr = DayTrade(str(pd.Timestamp(bars["time"].iloc[0]).date()), plan["stock_id"], side,
                  plan.get("strategy", dcfg["strategy"]), e["time"], e["entry"], e["stop"], e["target"], sh)
    flat = dcfg["flat_time"]
    for i in range(e["i"], len(bars)):
        b = bars.iloc[i]
        hh = _hhmm(b["time"])
        hit_stop = b["low"] <= tr.stop if side == "long" else b["high"] >= tr.stop
        hit_tgt = b["high"] >= tr.target if side == "long" else b["low"] <= tr.target
        if hit_stop:
            tr.exit, tr.exit_reason = tr.stop, "停損"
        elif hit_tgt:
            tr.exit, tr.exit_reason = tr.target, "停利"
        elif hh >= flat or i == len(bars) - 1:
            tr.exit, tr.exit_reason = float(b["close"]), "收盤前強制平倉"
        else:
            continue
        tr.exit_time = hh
        break
    sign = 1 if side == "long" else -1
    tr.gross = round((tr.exit - tr.entry) * sh * sign)
    tr.cost = trade_cost(tr.entry, tr.exit, sh, side, cfg)
    tr.pnl = tr.gross - tr.cost
    return tr


def run_session(bars_by_stock: dict, plans: list[dict], cfg) -> list[DayTrade]:
    """一天的當沖：依觸發時間排序，套用每日最多筆數與單日虧損上限。"""
    dcfg = cfg["daytrade"]
    cands = []
    for p in plans:
        bars = bars_by_stock.get(p["stock_id"])
        if bars is None or bars.empty:
            continue
        tr = simulate(bars, p, cfg)
        if tr:
            cands.append(tr)
    cands.sort(key=lambda x: x.entry_time)
    done = []
    max_loss = dcfg["capital"] * dcfg["daily_max_loss"]
    for tr in cands:
        if len(done) >= dcfg["max_trades_per_day"]:
            break
        realized = sum(d.pnl for d in done if d.exit_time <= tr.entry_time)
        if realized <= -max_loss:      # 單日虧損達上限 → 當天停止開新倉
            break
        done.append(tr)
    return done
