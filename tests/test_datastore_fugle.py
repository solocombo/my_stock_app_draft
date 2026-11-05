from datetime import datetime

import pandas as pd

from agent.config import load_config
from agent.datastore import DataStore, expected_latest
from agent.fugle import _norm_candles, _norm_quote


class FakeFinMind:
    """模擬 FinMind：每檔每個交易日都有資料，記錄被請求的區間。"""

    def __init__(self, last_day):
        self.last_day = pd.Timestamp(last_day)
        self.calls = []

    def fetch_info(self, universe, fin):
        return pd.DataFrame({"stock_id": universe, "name": universe, "industry": "半導體業",
                             "is_financial": False, "market": "tse"})

    def _days(self, start, end):
        return pd.bdate_range(start, min(pd.Timestamp(end), self.last_day))

    def fetch_prices(self, sid, start, end):
        self.calls.append(("prices", sid, pd.Timestamp(start)))
        d = self._days(start, end)
        return pd.DataFrame({"date": d, "stock_id": sid, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6})

    def fetch_flows(self, sid, start, end):
        d = self._days(start, end)
        return pd.DataFrame({"date": d, "stock_id": sid, "net_buy": 1000.0})

    def fetch_valuation(self, sid, start, end):
        d = self._days(start, end)
        return pd.DataFrame({"date": d, "stock_id": sid, "per": 15.0, "pbr": 2.0, "dy": 3.0})

    def fetch_revenue(self, sid, start, end):
        m = pd.date_range(pd.Timestamp(start).replace(day=1), "2026-10-01", freq="MS")
        return pd.DataFrame({"month": m, "stock_id": sid, "revenue": 1e9})


def _cfg():
    cfg = load_config("config.yaml")
    cfg["data"]["universe"] = ["1111", "2222"]
    return cfg


def test_expected_latest():
    tz = datetime(2026, 11, 5, 15, 0).astimezone().tzinfo
    assert expected_latest("14:30", datetime(2026, 11, 5, 15, 0), set()) == pd.Timestamp("2026-11-05")
    assert expected_latest("16:00", datetime(2026, 11, 5, 15, 0), set()) == pd.Timestamp("2026-11-04")
    assert expected_latest("14:30", datetime(2026, 11, 8, 10, 0), set()) == pd.Timestamp("2026-11-06")   # 週日 → 週五


def test_incremental_update(tmp_path):
    cfg = _cfg()
    # 第一次：11/04 晚上，回補
    fm = FakeFinMind("2026-11-04")
    s1 = DataStore(cfg, tmp_path, now=datetime(2026, 11, 4, 20, 0), holidays=set())
    s1.update(fm, log=lambda *_: None)
    assert len([c for c in fm.calls if c[0] == "prices"]) == 2
    md = s1.load()
    assert md.as_of == pd.Timestamp("2026-11-04") and not [n for n in md.notes if "落後" in n]

    # 同一天再跑：已是最新 → 不再請求價格
    fm.calls.clear()
    DataStore(cfg, tmp_path, now=datetime(2026, 11, 4, 21, 0), holidays=set()).update(fm, log=lambda *_: None)
    assert not [c for c in fm.calls if c[0] == "prices"]

    # 隔天收盤後：只從 11/05 開始抓
    fm2 = FakeFinMind("2026-11-05")
    s3 = DataStore(cfg, tmp_path, now=datetime(2026, 11, 5, 20, 0), holidays=set())
    s3.update(fm2, log=lambda *_: None)
    assert {c[2] for c in fm2.calls} == {pd.Timestamp("2026-11-05")}
    prices = s3.read("prices")
    assert prices.groupby("stock_id")["date"].max().eq(pd.Timestamp("2026-11-05")).all()
    assert not prices.duplicated(["stock_id", "date"]).any()


def test_freshness_flags_stale_data(tmp_path):
    cfg = _cfg()
    DataStore(cfg, tmp_path, now=datetime(2026, 11, 4, 20, 0), holidays=set()).update(FakeFinMind("2026-11-04"), log=lambda *_: None)
    # 資料源只到 11/04，但現在是 11/05 晚上 → 應提示落後
    s = DataStore(cfg, tmp_path, now=datetime(2026, 11, 5, 20, 0), holidays=set())
    s.update(FakeFinMind("2026-11-04"), log=lambda *_: None)
    assert any("prices data ends 2026-11-04" in n for n in s.freshness())


def test_fugle_normalizers():
    q = _norm_quote({"symbol": "2330", "openPrice": 1000, "highPrice": 1010, "lowPrice": 995, "lastPrice": 1005,
                     "previousClose": 998, "total": {"tradeVolume": 12345}})
    assert q == {"last": 1005, "open": 1000, "high": 1010, "low": 995, "prev_close": 998, "volume": 12345}
    q2 = _norm_quote({"bids": [{"price": 50.1, "size": 3}], "total": {}})
    assert q2["last"] == 50.1
    df = _norm_candles({"data": [
        {"date": "2026-11-05T09:01:00.000+08:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "average": 1.2},
        {"date": "2026-11-05T09:00:00.000+08:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5, "average": 1}]})
    assert list(df["time"].dt.strftime("%H:%M")) == ["09:00", "09:01"]


class FakeWS:
    """模擬富果 WebSocketClient.stock：記錄訂閱，可手動推送訊息與斷線。"""
    instances = []

    def __init__(self):
        self.handlers, self.subs = {}, []
        FakeWS.instances.append(self)

    def on(self, ev, fn):
        self.handlers[ev] = fn

    def connect(self):
        pass

    def subscribe(self, params):
        self.subs.append(params)

    def disconnect(self):
        self.handlers["disconnect"](1000, "bye")

    def push(self, data, event="data"):
        import json
        self.handlers["message"](json.dumps({"event": event, "channel": "aggregates", "data": data}))


def test_fugle_stream_push_and_reconnect():
    from datetime import datetime
    from agent.daytrade.realtime import Watcher
    from agent.fugle import FugleStream

    FakeWS.instances.clear()
    s = FugleStream(["X"], client_factory=FakeWS, log=lambda *_: None)
    s.connect()
    ws = FakeWS.instances[-1]
    assert ws.subs == [{"channel": "aggregates", "symbols": ["X"]}]

    cfg = load_config("config.yaml")
    w = Watcher([{"stock_id": "X", "name": "", "side": "long", "atr": 3.0, "strategy": "orb"}], cfg)
    agg = lambda last, h, v: {"symbol": "X", "openPrice": 100, "highPrice": h, "lowPrice": 99.5, "lastPrice": last,
                              "previousClose": 99, "total": {"tradeVolume": v}}
    ws.push(agg(100, 100.5, 10))
    assert w.on_quote("X", s.latest()["X"], datetime(2026, 11, 5, 9, 15))[0].startswith("X 開盤區間")
    ws.push(agg(101, 101, 20))
    msg = w.on_quote("X", s.latest()["X"], datetime(2026, 11, 5, 9, 16))[0]
    assert "買進" in msg and "OCO" in msg

    ws.push({"symbol": "X", "price": 101.5, "volume": 25}, event="snapshot")   # trades 格式也能吃
    assert s.latest()["X"]["last"] == 101.5 and s.latest()["X"]["high"] == 101.5

    ws.disconnect()
    assert not s.connected
    s.ensure_connected()                       # 自動重連並重新訂閱
    assert s.connected and len(FakeWS.instances) == 2 and FakeWS.instances[-1].subs
