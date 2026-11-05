"""持股狀態、權重上限處理、目標權重 → 零股/整張建議單。"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)   # stock_id -> 股數
    avg_cost: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, path, capital):
        p = Path(path)
        if not p.exists():
            return cls(cash=float(capital))
        js = json.loads(p.read_text(encoding="utf-8"))
        return cls(cash=float(js["cash"]), positions={k: float(v) for k, v in js.get("positions", {}).items()},
                   avg_cost={k: float(v) for k, v in js.get("avg_cost", {}).items()})

    def save(self, path):
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def market_value(self, prices: pd.Series) -> float:
        return sum(sh * float(prices.get(s, 0) or 0) for s, sh in self.positions.items())

    def nav(self, prices) -> float:
        return self.cash + self.market_value(prices)

    def weights(self, prices) -> dict[str, float]:
        nav = self.nav(prices)
        return {s: sh * float(prices.get(s, 0) or 0) / nav for s, sh in self.positions.items()} if nav > 0 else {}


@dataclass
class Decision:
    stock_id: str
    action: str                 # BUY | ADD | TRIM | SELL_ALL | HOLD
    target_weight: float
    reason: str
    current_weight: float = 0.0
    source: str = "quant"       # quant | llm | custom
    evidence: dict = field(default_factory=dict)


def cap_weights(raw: dict[str, float], total: float, caps: dict[str, float], fin: set[str],
                fin_max_weight: float) -> dict[str, float]:
    """把 raw 分數正規化成總和 total 的權重，並反覆套用單檔與金融類上限，超出部分重分配。"""
    w = {k: max(v, 1e-9) for k, v in raw.items()}
    fixed: dict[str, float] = {}
    for _ in range(50):
        free = {k: v for k, v in w.items() if k not in fixed}
        remain = total - sum(fixed.values())
        if not free or remain <= 0:
            break
        s = sum(free.values())
        alloc = {k: remain * v / s for k, v in free.items()}
        over = {k: caps[k] for k, a in alloc.items() if a > caps[k] + 1e-12}
        if not over:
            fixed.update(alloc)
            break
        fixed.update(over)
    else:
        fixed.update(alloc)
    out = {k: fixed.get(k, 0.0) for k in w}
    fin_total = sum(v for k, v in out.items() if k in fin)
    if fin_total > fin_max_weight and fin_total > 0:   # 金融類等比例縮減，多出的變現金
        scale = fin_max_weight / fin_total
        out = {k: v * scale if k in fin else v for k, v in out.items()}
    return out


def name_cap(cfg, sid):
    p = cfg["portfolio"]
    return float(p.get("special_caps", {}).get(sid, p["default_cap"]))


@dataclass
class Order:
    stock_id: str
    name: str
    side: str          # BUY | SELL
    shares: int
    price_ref: float
    value: float
    est_cost: float
    odd_lot: bool
    action: str
    reason: str


def _fee(cfg, value):
    c = cfg["costs"]
    return max(c.get("min_fee", 1), value * c["fee_rate"] * c.get("fee_discount", 1.0))


def decisions_to_orders(decisions: list[Decision], pf: Portfolio, obs: pd.DataFrame, cfg) -> list[Order]:
    """目標權重 → 股數。先算賣單（釋出現金），再依現金比例縮放買單。"""
    prices = obs["close"]
    nav = pf.nav(prices)
    lot = int(cfg.get("lot_size", 1))
    min_val = float(cfg.get("min_order_value", 0))
    names = obs["name"].to_dict() if "name" in obs else {}
    sells, buys = [], []
    for d in decisions:
        px = float(prices.get(d.stock_id, float("nan")))
        if not px or math.isnan(px) or d.action == "HOLD":
            continue
        cur_sh = pf.positions.get(d.stock_id, 0.0)
        if d.action == "SELL_ALL":
            tgt_sh = 0
        else:
            tgt_sh = math.floor(d.target_weight * nav / px / lot) * lot
        delta = tgt_sh - cur_sh
        if d.action == "SELL_ALL":
            delta = -cur_sh
        if abs(delta) * px < min_val and d.action != "SELL_ALL":
            continue
        if delta < 0:
            sells.append((d, int(-delta), px))
        elif delta > 0:
            buys.append((d, int(delta), px))

    orders: list[Order] = []
    cash = pf.cash
    for d, sh, px in sells:
        v = sh * px
        cost = _fee(cfg, v) + v * cfg["costs"]["sell_tax"]
        cash += v - cost
        orders.append(Order(d.stock_id, names.get(d.stock_id, ""), "SELL", sh, px, round(v), round(cost), sh % 1000 != 0, d.action, d.reason))

    need = sum(sh * px * (1 + cfg["costs"]["fee_rate"]) for _, sh, px in buys)
    budget = cash - nav * 0.002   # 留一點緩衝給價格跳動
    scale = min(1.0, budget / need) if need > 0 else 1.0
    for d, sh, px in sorted(buys, key=lambda x: -x[0].target_weight):
        sh2 = math.floor(sh * scale / lot) * lot
        v = sh2 * px
        if sh2 <= 0 or v < min_val:
            continue
        orders.append(Order(d.stock_id, names.get(d.stock_id, ""), "BUY", sh2, px, round(v), round(_fee(cfg, v)), sh2 % 1000 != 0, d.action, d.reason))
    return orders


def apply_orders(pf: Portfolio, orders: list[Order], cfg) -> Portfolio:
    """模擬以參考價成交（回測或你手動下單後同步持股用）。"""
    new = Portfolio(pf.cash, dict(pf.positions), dict(pf.avg_cost))
    for o in orders:
        if o.side == "SELL":
            new.positions[o.stock_id] = new.positions.get(o.stock_id, 0) - o.shares
            new.cash += o.value - o.est_cost
            if new.positions[o.stock_id] <= 0:
                new.positions.pop(o.stock_id, None)
                new.avg_cost.pop(o.stock_id, None)
        else:
            old_sh = new.positions.get(o.stock_id, 0)
            old_cost = new.avg_cost.get(o.stock_id, o.price_ref)
            new.positions[o.stock_id] = old_sh + o.shares
            new.avg_cost[o.stock_id] = round((old_sh * old_cost + o.value) / (old_sh + o.shares), 4)
            new.cash -= o.value + o.est_cost
    return new
