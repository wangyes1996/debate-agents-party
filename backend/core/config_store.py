"""Config persistence — schema v3 (generic debate platform).

Schema (v3):
{
  "schema_version": 3,
  "llm_configs": [{id, name, api_key, base_url, model}, ...],
  "default_llm_id": "<llm id>",
  "agents": [
    {
      "id": "<stable string, used in [NEXT: <id>] tokens>",
      "name": "现实主义者",
      "emoji": "🧱",
      "color": "#94a3b8",
      "system": "<system prompt body>",
      "llm_id": "" | "<llm id>",   # "" = use default_llm_id
      "is_moderator": false,
      "builtin": true               # came from seed presets (still editable/deletable)
    }, ...
  ],
  "rooms": [
    {
      "id": "<uuid>",
      "name": "AI 会取代程序员吗?",
      "topic": "<the actual debate question>",
      "moderator_id": "moderator",   # must be an agent id with is_moderator=true
      "agent_ids": ["realist", "idealist", ...],  # non-moderator participants
      "max_turns": 16,
      "created_at": <unix ts>
    }, ...
  ]
}

Migration v2 → v3:
- Preserves llm_configs and default_llm_id as-is.
- Seeds the generic agent roster (moderator + 9 personas).
- Creates a "默认辩论室" using the previously-enabled crypto roles IF those
  role names happen to still exist as seed agents; otherwise uses the new
  generic default room.
"""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from threading import Lock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_FILE = _PROJECT_ROOT / "config.json"
_LOCK = Lock()

SCHEMA_VERSION = 3


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _starter_llm_configs() -> list[dict]:
    return [
        {"id": _new_id(), "name": "OpenAI · gpt-4o-mini", "api_key": "", "base_url": "", "model": "gpt-4o-mini"},
        {"id": _new_id(), "name": "DeepSeek · deepseek-chat", "api_key": "", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    ]


def _seed_agents() -> list[dict]:
    """Import seed personas lazily so this module stays import-safe."""
    from ..agents.personas import SEED_AGENTS
    out = []
    for a in SEED_AGENTS:
        out.append({
            "id": a["id"],
            "name": a["name"],
            "emoji": a["emoji"],
            "color": a["color"],
            "system": a["system"],
            "llm_id": "",
            "is_moderator": bool(a.get("is_moderator", False)),
            "builtin": True,
        })
    return out


def _seed_default_room(agents: list[dict]) -> dict:
    from ..agents.personas import DEFAULT_ROOM
    agent_ids_available = {a["id"] for a in agents}
    moderator_id = next((a["id"] for a in agents if a.get("is_moderator")), agents[0]["id"] if agents else "")
    picked = [aid for aid in DEFAULT_ROOM["agent_ids"] if aid in agent_ids_available]
    if not picked:
        picked = [a["id"] for a in agents if not a.get("is_moderator")][:5]
    return {
        "id": _new_id(),
        "name": DEFAULT_ROOM["name"],
        "topic": DEFAULT_ROOM["topic"],
        "moderator_id": DEFAULT_ROOM.get("moderator_id", moderator_id) if DEFAULT_ROOM.get("moderator_id") in agent_ids_available else moderator_id,
        "agent_ids": picked,
        "max_turns": DEFAULT_ROOM.get("max_turns", 16),
        "created_at": time.time(),
    }


def _build_default() -> dict:
    llms = _starter_llm_configs()
    agents = _seed_agents()
    return {
        "schema_version": SCHEMA_VERSION,
        "llm_configs": llms,
        "default_llm_id": llms[0]["id"],
        "agents": agents,
        "rooms": [_seed_default_room(agents)],
    }


def _migrate_v2_to_v3(old: dict) -> dict:
    """Old schema had agents={enabled_roles, max_rounds, role_llm, ...} and no rooms."""
    llms = old.get("llm_configs") or _starter_llm_configs()
    default_llm_id = old.get("default_llm_id") or (llms[0]["id"] if llms else "")

    agents = _seed_agents()
    # The user may have had per-role LLM overrides under the old role names
    # (bull/bear/tech/news/risk). Those crypto roles don't exist in the new
    # seed roster, so the overrides naturally lapse — that's fine. We could
    # try to map them, but the user is rebuilding for a generic platform.
    room = _seed_default_room(agents)
    # Carry over max_rounds as a rough turn budget
    try:
        max_rounds = int((old.get("agents") or {}).get("max_rounds", 3))
        room["max_turns"] = max(8, (len(room["agent_ids"]) + 1) * max_rounds)
    except Exception:
        pass

    return {
        "schema_version": SCHEMA_VERSION,
        "llm_configs": llms,
        "default_llm_id": default_llm_id,
        "agents": agents,
        "rooms": [room],
    }


def _ensure_v3(data: dict) -> dict:
    if data.get("schema_version") == SCHEMA_VERSION and "agents" in data and isinstance(data["agents"], list) and "rooms" in data:
        # Already v3 — just patch any missing fields
        data.setdefault("llm_configs", [])
        data.setdefault("default_llm_id", data["llm_configs"][0]["id"] if data["llm_configs"] else "")
        data.setdefault("rooms", [])
        for a in data["agents"]:
            a.setdefault("llm_id", "")
            a.setdefault("is_moderator", False)
            a.setdefault("builtin", False)
            a.setdefault("color", "#888")
            a.setdefault("emoji", "💬")
            a.setdefault("system", "")
        for r in data["rooms"]:
            r.setdefault("max_turns", 16)
            r.setdefault("agent_ids", [])
            r.setdefault("created_at", time.time())
        return data
    # v2 detection: list-based llm_configs but dict-shaped agents
    if isinstance(data.get("agents"), dict) or "rooms" not in data:
        return _migrate_v2_to_v3(data)
    # unknown shape → start fresh, but preserve LLMs if possible
    fresh = _build_default()
    if isinstance(data.get("llm_configs"), list) and data["llm_configs"]:
        fresh["llm_configs"] = data["llm_configs"]
        fresh["default_llm_id"] = data.get("default_llm_id") or data["llm_configs"][0]["id"]
    return fresh


def load_config() -> dict:
    with _LOCK:
        if not _CONFIG_FILE.exists():
            data = _build_default()
            _CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            return data
        try:
            raw = json.loads(_CONFIG_FILE.read_text())
        except Exception:
            return _build_default()
    # do migration outside lock (it may touch other things)
    data = _ensure_v3(raw)
    if data is not raw:
        with _LOCK:
            _CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data


def save_config(cfg: dict) -> dict:
    cfg = _ensure_v3(cfg)
    with _LOCK:
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return cfg


def update_partial(patch: dict) -> dict:
    """Shallow merge a partial patch into current config (top-level keys only)."""
    cur = load_config()
    for k, v in patch.items():
        cur[k] = v
    return save_config(cur)


# ----- agent helpers ---------------------------------------------------------

def get_agent(cfg: dict, agent_id: str) -> dict | None:
    for a in cfg.get("agents", []):
        if a.get("id") == agent_id:
            return a
    return None


def upsert_agent(agent: dict) -> dict:
    cfg = load_config()
    aid = agent.get("id") or _new_id()
    agent["id"] = aid
    existing = [a for a in cfg["agents"] if a["id"] == aid]
    if existing:
        existing[0].update(agent)
    else:
        agent.setdefault("llm_id", "")
        agent.setdefault("is_moderator", False)
        agent.setdefault("builtin", False)
        cfg["agents"].append(agent)
    save_config(cfg)
    return agent


def delete_agent(agent_id: str) -> bool:
    cfg = load_config()
    before = len(cfg["agents"])
    cfg["agents"] = [a for a in cfg["agents"] if a["id"] != agent_id]
    # also strip from rooms
    for r in cfg.get("rooms", []):
        r["agent_ids"] = [x for x in r.get("agent_ids", []) if x != agent_id]
        if r.get("moderator_id") == agent_id:
            # pick another moderator if available
            r["moderator_id"] = next((a["id"] for a in cfg["agents"] if a.get("is_moderator")), "")
    save_config(cfg)
    return len(cfg["agents"]) < before


# ----- room helpers ----------------------------------------------------------

def get_room(cfg: dict, room_id: str) -> dict | None:
    for r in cfg.get("rooms", []):
        if r.get("id") == room_id:
            return r
    return None


def upsert_room(room: dict) -> dict:
    cfg = load_config()
    rid = room.get("id") or _new_id()
    room["id"] = rid
    room.setdefault("created_at", time.time())
    room.setdefault("max_turns", 16)
    existing = [r for r in cfg["rooms"] if r["id"] == rid]
    if existing:
        existing[0].update(room)
    else:
        cfg["rooms"].append(room)
    save_config(cfg)
    return room


def delete_room(room_id: str) -> bool:
    cfg = load_config()
    before = len(cfg["rooms"])
    cfg["rooms"] = [r for r in cfg["rooms"] if r["id"] != room_id]
    save_config(cfg)
    return len(cfg["rooms"]) < before


# ----- llm helpers -----------------------------------------------------------

def find_llm(cfg: dict, llm_id: str | None) -> dict | None:
    configs = cfg.get("llm_configs", [])
    if llm_id:
        for c in configs:
            if c.get("id") == llm_id:
                return c
    did = cfg.get("default_llm_id")
    if did:
        for c in configs:
            if c.get("id") == did:
                return c
    return configs[0] if configs else None


def new_id() -> str:
    return _new_id()
