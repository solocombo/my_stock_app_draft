"""LLM 策略（Claude）。

- decide()：LLM 依因子表直接提出目標組合，程式再套上權重上限與 no-trade band。
- review_decisions()：量化策略的 BUY／ADD 交給 LLM 做風險審查，可 veto（否決）。

需要 `pip install anthropic` 與環境變數 ANTHROPIC_API_KEY。
LLM 只看得到你給它的因子表，沒有連網查新聞；它的理由請當參考，不是事實。
"""
from __future__ import annotations

import json
import os

import pandas as pd

from ..portfolio import Decision, Portfolio, cap_weights, name_cap
from .quant import evidence

_COLS = ["name", "industry", "close", "ma20", "ma60", "ma_break_days", "F", "ACC", "MOM", "FLOW", "S", "rev_month",
         "ret20", "ret60", "rsi14", "vol60", "dist_high250", "vol_ratio", "per", "pbr", "dy", "inst_streak"]


def _client():
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("LLM 策略需要 `pip install anthropic`") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("未設定 ANTHROPIC_API_KEY")
    return anthropic.Anthropic()


def _ask_json(cfg, system: str, user: str, schema: dict) -> dict:
    """以 structured outputs 取得符合 schema 的 JSON。開啟 server-side fallback（被拒答時自動改用備援模型）。"""
    lc = cfg.get("llm", {})
    client = _client()
    resp = client.beta.messages.create(
        model=lc.get("model", "claude-opus-5-5"),
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": lc.get("effort", "medium"),
                       "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"LLM 拒答：{getattr(resp, 'stop_details', None)}")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def _table(obs: pd.DataFrame, ids) -> str:
    sub = obs.loc[list(ids), [c for c in _COLS if c in obs.columns]].copy()
    return sub.round(2).to_json(orient="index", force_ascii=False)


SYSTEM = (
    "你是台股投資組合經理人的助理，只能根據提供的因子表做判斷，不得引用表外資訊或捏造數字。"
    "因子說明：F=基本面分數(0-100, 營收YoY排名)，ACC=營收YoY加速度(百分點, >0代表轉佳)，"
    "MOM=動能分數(0-100)，FLOW=法人買賣超百分位(0-100)，S=綜合分數，ma_break_days=連續收在MA60下天數，"
    "ret20/ret60=報酬率，rsi14，vol60=年化波動，dist_high250=距一年高點，vol_ratio=5日/60日量比，"
    "per/pbr=本益比/股價淨值比，dy=殖利率%，inst_streak=法人連買天數(負=連賣)。"
    "理由要引用表中的具體數值。"
)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "market_view": {"type": "string"},
        "positions": {"type": "array", "items": {
            "type": "object",
            "properties": {"stock_id": {"type": "string"}, "target_weight": {"type": "number"},
                           "reason": {"type": "string"}},
            "required": ["stock_id", "target_weight", "reason"], "additionalProperties": False}},
    },
    "required": ["market_view", "positions"], "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"reviews": {"type": "array", "items": {
        "type": "object",
        "properties": {"stock_id": {"type": "string"}, "verdict": {"type": "string", "enum": ["approve", "veto"]},
                       "reason": {"type": "string"}},
        "required": ["stock_id", "verdict", "reason"], "additionalProperties": False}}},
    "required": ["reviews"], "additionalProperties": False,
}


def decide(obs: pd.DataFrame, pf: Portfolio, cfg) -> list[Decision]:
    pc = cfg["portfolio"]
    cur_w = pf.weights(obs["close"])
    pool = list(obs["S"].dropna().sort_values(ascending=False).head(40).index)
    ids = list(dict.fromkeys(list(pf.positions) + pool))
    ids = [s for s in ids if s in obs.index]
    user = (
        f"投資偏好：{cfg.get('llm', {}).get('guidance', '')}\n"
        f"限制：最多 {pc['target_names']} 檔、至少 {pc['min_names']} 檔；單檔上限 {pc['default_cap']:.0%}"
        f"（個別上限 {pc.get('special_caps', {})}）；金融股合計 ≤ {pc['financial_max_weight']:.0%}；"
        f"保留現金約 {pc['cash_target']:.0%}。避免無理由的頻繁換股。\n"
        f"目前持股權重：{json.dumps({k: round(v, 4) for k, v in cur_w.items()})}\n"
        f"因子表：{_table(obs, ids)}\n"
        "請提出新的目標組合（target_weight 為小數，總和 ≤ 1 − 現金比率）。不在清單中的持股代表全部賣出。"
    )
    plan = _ask_json(cfg, SYSTEM, user, PLAN_SCHEMA)

    llm_w = {p["stock_id"]: max(0.0, float(p["target_weight"])) for p in plan["positions"] if p["stock_id"] in obs.index}
    reasons = {p["stock_id"]: p["reason"] for p in plan["positions"]}
    fin = set(obs.index[obs["is_financial"].fillna(False).astype(bool)])
    total = min(sum(llm_w.values()), 1 - pc["cash_target"]) if llm_w else 0
    tgt = cap_weights(llm_w, total, {s: name_cap(cfg, s) for s in llm_w}, fin, pc["financial_max_weight"]) if llm_w else {}

    out, band = [], pc["no_trade_band"]
    for sid in pf.positions:
        if sid not in tgt:
            out.append(Decision(sid, "SELL_ALL", 0, "LLM 未納入組合", cur_w.get(sid, 0), "llm",
                                evidence(obs.loc[sid]) if sid in obs.index else {}))
    for sid, t in tgt.items():
        c = cur_w.get(sid, 0)
        ev = evidence(obs.loc[sid]) | {"target_weight": round(t, 4)}
        if c == 0:
            act = "BUY"
        elif abs(t - c) <= band:
            act, t = "HOLD", c
        else:
            act = "ADD" if t > c else "TRIM"
        out.append(Decision(sid, act, t, f"[LLM] {reasons.get(sid, '')}", c, "llm", ev))
    out.append(Decision("_MARKET", "HOLD", 0, f"[LLM market view] {plan['market_view']}", 0, "llm"))
    return out


def review_decisions(decisions: list[Decision], obs: pd.DataFrame, pf: Portfolio, cfg) -> list[Decision]:
    targets = [d for d in decisions if d.action in ("BUY", "ADD")]
    if not targets:
        return decisions
    user = (
        f"投資偏好：{cfg.get('llm', {}).get('guidance', '')}\n"
        "以下是量化規則挑出的買進／加碼建議。請逐檔審查風險（例如數據之間互相矛盾、短期漲幅過大、過度集中同產業），"
        "只有在有明確理由時才 veto，並引用數值。\n"
        f"建議：{json.dumps([{'stock_id': d.stock_id, 'action': d.action, 'target_weight': round(d.target_weight, 4)} for d in targets], ensure_ascii=False)}\n"
        f"因子表：{_table(obs, [d.stock_id for d in targets])}"
    )
    try:
        res = _ask_json(cfg, SYSTEM, user, REVIEW_SCHEMA)
    except Exception as e:  # LLM 失敗時不阻擋量化結果，但要留下紀錄
        for d in targets:
            d.evidence["llm_review"] = f"failed: {e}"
        return decisions
    verdict = {r["stock_id"]: r for r in res["reviews"]}
    out = []
    for d in decisions:
        r = verdict.get(d.stock_id)
        if d in targets and r:
            d.evidence["llm_review"] = r["verdict"]
            if r["verdict"] == "veto":
                keep_w = d.current_weight
                out.append(Decision(d.stock_id, "HOLD", keep_w, f"LLM 否決{d.action}：{r['reason']}（原建議：{d.reason}）",
                                    keep_w, "llm", d.evidence))
                continue
            d.reason += f"｜LLM 審查通過：{r['reason']}"
        out.append(d)
    return [d for d in out if not (d.action == "HOLD" and d.current_weight == 0 and d.stock_id not in pf.positions)]
