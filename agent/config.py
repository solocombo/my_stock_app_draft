import copy
import hashlib
import json
from pathlib import Path

import yaml


def load_config(path="config.yaml", overrides=None):
    """讀 config.yaml，再疊上同目錄的 config.local.yaml（你的個人設定，不進 git，更新程式時不會衝突）。"""
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    local = Path(path).with_name("config.local.yaml")
    if local.exists():
        cfg = deep_merge(cfg, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def deep_merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config_sha256(cfg):
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()
