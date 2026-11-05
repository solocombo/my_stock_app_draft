"""相容舊名稱：quant = library.Fundamental（基本面轉折）。"""
from __future__ import annotations

from .library import REGISTRY

_F = REGISTRY["fundamental"]


def evidence(row) -> dict:
    return _F.evidence(row)


def decide(obs, pf, cfg):
    return _F.decide(obs, pf, cfg)
