"""Config persistence - JSON file under backend/data/config.json.

Schema (v2):
  llm_configs: [{id, name, api_key, base_url, model}, ...]
  default_llm_id: <id of one llm_config>
  agents.role_llm: {role_key: <llm_config id> or ""}  ("" = use default)
  agents.enabled_roles, max_rounds, user_can_interrupt
  data_source: {primary, symbol}
"""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from threading import Lock

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_CONFIG_FILE = _DATA_DIR / "config.json"
_LOCK = Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _starter_configs() -> list[dict]:
    """A few preset templates the user can edit; api_key is empty so they're inert."""
    return [
        {
            "id": _new_id(),
            "name": "OpenAI · gpt-4o-mini",
            "api_key": "",
            "base_url": "",
            "model": "gpt-4o-mini",
        },
        {
            "id": _new_id(),
            "name": "DeepSeek · deepseek-chat",
            "api_key": "",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
        {
            "id": _new_id(),
            "name": "火山方舟 · Doubao",
            "api_key": "",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "",
        },
    ]


def _build_default() -> dict:
    starter = _starter_configs()
    return {
        "llm_configs": starter,
        "default_llm_id": starter[0]["id"],
        "agents": {
            "enabled_roles": ["bull", "bear", "tech", "news", "risk"],
            "max_rounds": 3,
            "user_can_interrupt": True,
            "role_llm": {
                "moderator": "",
                "bull": "",
                "bear": "",
                "tech": "",
                "news": "",
                "risk": "",
            },
        },
        "data_source": {
            "primary": "binance",
            "symbol": "BTCUSDT",
        },
    }


def _migrate_v1_to_v2(old: dict) -> dict:
    """Convert old {providers: {name: {...}}, active_provider} to new list-based schema."""
    configs: list[dict] = []
    name_to_id: dict[str, str] = {}
    for pname, pcfg in (old.get("providers") or {}).items():
        cid = _new_id()
        name_to_id[pname] = cid
        configs.append(
            {
                "id": cid,
                "name": pname,
                "api_key": pcfg.get("api_key", "") or "",
                "base_url": pcfg.get("base_url", "") or "",
                "model": pcfg.get("model", "") or "",
            }
        )
    if not configs:
        configs = _starter_configs()
    default_id = name_to_id.get(old.get("active_provider", ""), configs[0]["id"])

    agents = dict(old.get("agents") or {})
    agents.setdefault("enabled_roles", ["bull", "bear", "tech", "news", "risk"])
    agents.setdefault("max_rounds", 3)
    agents.setdefault("user_can_interrupt", True)
    # remap any old role_llm provider names to ids
    old_role_llm = agents.get("role_llm") or {}
    new_role_llm = {}
    for r, val in old_role_llm.items():
        new_role_llm[r] = name_to_id.get(val, "") if val else ""
    for r in ["moderator", "bull", "bear", "tech", "news", "risk"]:
        new_role_llm.setdefault(r, "")
    agents["role_llm"] = new_role_llm

    return {
        "llm_configs": configs,
        "default_llm_id": default_id,
        "agents": agents,
        "data_source": old.get("data_source") or {"primary": "binance", "symbol": "BTCUSDT"},
    }


def _ensure_v2(data: dict) -> dict:
    """If we see v1 schema (providers dict), migrate."""
    if "providers" in data and "llm_configs" not in data:
        return _migrate_v1_to_v2(data)
    # fill any missing top-level keys
    default = _build_default()
    for k, v in default.items():
        if k not in data:
            data[k] = v
        elif isinstance(v, dict) and isinstance(data[k], dict):
            for sk, sv in v.items():
                data[k].setdefault(sk, sv)
    # ensure ids unique + every config has all fields
    for c in data["llm_configs"]:
        c.setdefault("id", _new_id())
        c.setdefault("name", "untitled")
        c.setdefault("api_key", "")
        c.setdefault("base_url", "")
        c.setdefault("model", "")
    return data


def load_config() -> dict:
    with _LOCK:
        if not _CONFIG_FILE.exists():
            data = _build_default()
            _CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            return data
        try:
            raw = json.loads(_CONFIG_FILE.read_text())
            data = _ensure_v2(raw)
            # persist migration result
            if data is not raw:
                _CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            return data
        except Exception:
            return _build_default()


def save_config(cfg: dict) -> dict:
    # load OUTSIDE the lock to avoid re-entrant deadlock
    cur = load_config()
    cur.update(cfg)
    cur = _ensure_v2(cur)
    with _LOCK:
        _CONFIG_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False))
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


def find_llm(cfg: dict, llm_id: str | None) -> dict | None:
    """Return the llm_config dict matching id, or default."""
    configs = cfg.get("llm_configs", [])
    if llm_id:
        for c in configs:
            if c.get("id") == llm_id:
                return c
    # fall back to default
    did = cfg.get("default_llm_id")
    if did:
        for c in configs:
            if c.get("id") == did:
                return c
    return configs[0] if configs else None


def new_llm_id() -> str:
    return _new_id()
