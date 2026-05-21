"""Config persistence - JSON file under backend/data/config.json."""
from __future__ import annotations
import json
import os
from pathlib import Path
from threading import Lock

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_CONFIG_FILE = _DATA_DIR / "config.json"
_LOCK = Lock()

DEFAULT_CONFIG = {
    "active_provider": "openai",
    "providers": {
        "openai": {
            "api_key": "",
            "base_url": "",
            "model": "gpt-4o-mini",
        },
        "anthropic": {
            "api_key": "",
            "base_url": "",
            "model": "claude-3-5-sonnet-20241022",
        },
        "deepseek": {
            "api_key": "",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
        "volcengine": {
            "api_key": "",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "",
        },
    },
    "agents": {
        "enabled_roles": ["bull", "bear", "tech", "news", "risk"],
        "max_rounds": 3,
        "user_can_interrupt": True,
    },
    "data_source": {
        "primary": "binance",  # binance | coingecko | yfinance
        "symbol": "BTCUSDT",
    },
}


def load_config() -> dict:
    with _LOCK:
        if not _CONFIG_FILE.exists():
            _CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
            return dict(DEFAULT_CONFIG)
        try:
            data = json.loads(_CONFIG_FILE.read_text())
            # merge defaults for missing keys
            for k, v in DEFAULT_CONFIG.items():
                data.setdefault(k, v)
            return data
        except Exception:
            return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> dict:
    with _LOCK:
        # merge with existing to avoid losing keys
        cur = load_config()
        cur.update(cfg)
        _CONFIG_FILE.write_text(json.dumps(cur, indent=2))
        return cur


def update_partial(patch: dict) -> dict:
    """Deep-ish merge a partial config patch into current config."""
    cur = load_config()
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cur.get(k), dict):
            cur[k].update(v)
        else:
            cur[k] = v
    return save_config(cur)
