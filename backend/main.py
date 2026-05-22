"""FastAPI entry point — REST + WebSocket for generic debate platform."""
from __future__ import annotations
import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any

from .core.config_store import (
    load_config, update_partial,
    upsert_agent, delete_agent,
    upsert_room, delete_room, get_room, get_agent,
)
from .core.debate_engine import DebateEngine
from .core import db as db

app = FastAPI(title="Debate Agents Party")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"ok": True}


# --- config (LLMs only; agents/rooms have their own endpoints) ---------------

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    safe = json.loads(json.dumps(cfg))
    for c in safe.get("llm_configs", []):
        if c.get("api_key"):
            c["api_key"] = "***" + c["api_key"][-4:]
    return safe


class ConfigPatch(BaseModel):
    llm_configs: list | None = None
    default_llm_id: str | None = None


@app.post("/api/config")
async def set_config(patch: ConfigPatch):
    payload = {k: v for k, v in patch.model_dump(exclude_none=True).items()}
    if "llm_configs" in payload:
        cur_configs = {c["id"]: c for c in load_config().get("llm_configs", []) if c.get("id")}
        for c in payload["llm_configs"]:
            if isinstance(c, dict):
                ak = c.get("api_key", "") or ""
                if ak.startswith("***") and c.get("id") in cur_configs:
                    c["api_key"] = cur_configs[c["id"]].get("api_key", "")
    update_partial(payload)
    return {"ok": True}


# --- agents CRUD -------------------------------------------------------------

@app.get("/api/agents")
async def list_agents():
    return {"agents": load_config().get("agents", [])}


class AgentBody(BaseModel):
    id: str | None = None
    name: str
    emoji: str = "💬"
    color: str = "#888888"
    system: str = ""
    llm_id: str = ""
    is_moderator: bool = False


@app.post("/api/agents")
async def create_agent(body: AgentBody):
    return upsert_agent(body.model_dump(exclude_none=False))


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentBody):
    data = body.model_dump(exclude_none=False)
    data["id"] = agent_id
    # preserve `builtin` flag
    cfg = load_config()
    existing = get_agent(cfg, agent_id)
    if existing:
        data["builtin"] = existing.get("builtin", False)
    return upsert_agent(data)


@app.delete("/api/agents/{agent_id}")
async def remove_agent(agent_id: str):
    if not delete_agent(agent_id):
        raise HTTPException(404, "agent not found")
    return {"ok": True}


# --- rooms CRUD --------------------------------------------------------------

@app.get("/api/rooms")
async def list_rooms():
    return {"rooms": load_config().get("rooms", [])}


@app.get("/api/rooms/{room_id}")
async def fetch_room(room_id: str):
    r = get_room(load_config(), room_id)
    if r is None:
        raise HTTPException(404, "room not found")
    return r


class RoomBody(BaseModel):
    id: str | None = None
    name: str
    topic: str = ""
    moderator_id: str
    agent_ids: list[str] = []
    max_turns: int = 16


@app.post("/api/rooms")
async def create_room(body: RoomBody):
    return upsert_room(body.model_dump(exclude_none=False))


@app.put("/api/rooms/{room_id}")
async def update_room(room_id: str, body: RoomBody):
    data = body.model_dump(exclude_none=False)
    data["id"] = room_id
    return upsert_room(data)


@app.delete("/api/rooms/{room_id}")
async def remove_room(room_id: str):
    if not delete_room(room_id):
        raise HTTPException(404, "room not found")
    db.delete_sessions_for_room(room_id)
    return {"ok": True}


# --- history / sessions -----------------------------------------------------

@app.get("/api/rooms/{room_id}/history")
async def room_history(room_id: str):
    """Return latest session for the room + its messages.
    Frontend uses this to render past debate on room load (no auto-start)."""
    r = get_room(load_config(), room_id)
    if r is None:
        raise HTTPException(404, "room not found")
    sess = db.latest_session_for_room(room_id)
    if sess is None:
        return {"session": None, "messages": [], "active": False}
    msgs = db.get_messages(sess["id"])
    active = room_id in ROOM_ENGINES and not ROOM_ENGINES[room_id].engine.cancelled
    return {"session": sess, "messages": msgs, "active": active}


@app.delete("/api/rooms/{room_id}/history")
async def clear_room_history(room_id: str):
    # only allowed if no active engine
    handle = ROOM_ENGINES.get(room_id)
    if handle and not handle.engine.cancelled:
        raise HTTPException(409, "辩论进行中,无法清空历史")
    db.delete_sessions_for_room(room_id)
    return {"ok": True}


# --- engine registry (one engine per room, many WS subscribers) -------------

class EngineHandle:
    def __init__(self, engine: DebateEngine, runner_task: asyncio.Task):
        self.engine = engine
        self.runner_task = runner_task
        self.subscribers: set[asyncio.Queue] = set()
        self.broadcaster_task: asyncio.Task | None = None


ROOM_ENGINES: dict[str, EngineHandle] = {}


async def _broadcaster(handle: EngineHandle, room_id: str):
    """Fan out engine.queue events to all subscribed WS queues."""
    try:
        while True:
            evt = await handle.engine.queue.get()
            dead = []
            for q in list(handle.subscribers):
                try:
                    q.put_nowait(evt)
                except Exception:
                    dead.append(q)
            for q in dead:
                handle.subscribers.discard(q)
            if evt.get("type") in ("done", "error"):
                break
    finally:
        # engine finished — drop registration
        if ROOM_ENGINES.get(room_id) is handle:
            ROOM_ENGINES.pop(room_id, None)


def _start_engine(room_id: str, topic_override: str | None = None,
                  session_id: str | None = None) -> EngineHandle:
    queue: asyncio.Queue = asyncio.Queue()
    engine = DebateEngine(room_id=room_id, queue=queue, topic_override=topic_override,
                          session_id=session_id)
    runner = asyncio.create_task(engine.run())
    handle = EngineHandle(engine, runner)
    ROOM_ENGINES[room_id] = handle
    handle.broadcaster_task = asyncio.create_task(_broadcaster(handle, room_id))
    return handle


# --- WebSocket: one connection = one subscriber to a room's engine ----------

@app.websocket("/ws/debate")
async def ws_debate(ws: WebSocket):
    await ws.accept()
    sub_queue: asyncio.Queue = asyncio.Queue()
    bound_room: str | None = None

    def _subscribe(room_id: str):
        nonlocal bound_room
        if bound_room and bound_room in ROOM_ENGINES:
            ROOM_ENGINES[bound_room].subscribers.discard(sub_queue)
        bound_room = room_id
        if room_id in ROOM_ENGINES:
            ROOM_ENGINES[room_id].subscribers.add(sub_queue)

    async def pump():
        while True:
            evt = await sub_queue.get()
            try:
                await ws.send_json(evt)
            except Exception:
                return

    pump_task = asyncio.create_task(pump())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            mtype = msg.get("type")
            room_id = msg.get("room_id") or bound_room

            if mtype == "attach":
                # passive: just subscribe to existing engine if any (for resume after refresh)
                if not room_id:
                    await ws.send_json({"type": "error", "data": {"text": "缺少 room_id"}})
                    continue
                _subscribe(room_id)
                handle = ROOM_ENGINES.get(room_id)
                await ws.send_json({
                    "type": "attached",
                    "data": {
                        "room_id": room_id,
                        "active": bool(handle and not handle.engine.cancelled),
                        "session_id": handle.engine.session_id if handle else None,
                    },
                })

            elif mtype == "start":
                # start NEW debate (new session)
                if not room_id:
                    await ws.send_json({"type": "error", "data": {"text": "缺少 room_id"}})
                    continue
                existing = ROOM_ENGINES.get(room_id)
                if existing and not existing.engine.cancelled:
                    # already running — just subscribe (resume view)
                    _subscribe(room_id)
                    await ws.send_json({
                        "type": "attached",
                        "data": {"room_id": room_id, "active": True,
                                 "session_id": existing.engine.session_id},
                    })
                    continue
                try:
                    handle = _start_engine(room_id, topic_override=msg.get("topic"))
                except Exception as e:
                    await ws.send_json({"type": "error", "data": {"text": str(e)}})
                    continue
                _subscribe(room_id)
                await ws.send_json({
                    "type": "started",
                    "data": {"room_id": room_id, "session_id": handle.engine.session_id},
                })

            elif mtype == "restart":
                # cancel old engine, optionally update topic, start fresh session
                if not room_id:
                    await ws.send_json({"type": "error", "data": {"text": "缺少 room_id"}})
                    continue
                new_topic = (msg.get("topic") or "").strip() or None
                old = ROOM_ENGINES.get(room_id)
                if old and not old.engine.cancelled:
                    old.engine.cancel()
                    try:
                        await asyncio.wait_for(old.runner_task, timeout=2.0)
                    except Exception:
                        pass
                # also clear prior history if requested
                if msg.get("clear_history"):
                    db.delete_sessions_for_room(room_id)
                # update room.topic if provided so future loads pick it up
                if new_topic:
                    r = get_room(load_config(), room_id)
                    if r:
                        r["topic"] = new_topic
                        upsert_room(r)
                try:
                    handle = _start_engine(room_id, topic_override=new_topic)
                except Exception as e:
                    await ws.send_json({"type": "error", "data": {"text": str(e)}})
                    continue
                _subscribe(room_id)
                await ws.send_json({
                    "type": "restarted",
                    "data": {"room_id": room_id, "session_id": handle.engine.session_id},
                })

            elif mtype == "user_message":
                text = (msg.get("text") or "").strip()
                handle = ROOM_ENGINES.get(bound_room or "")
                if handle and text:
                    handle.engine.add_user_message(text)

            elif mtype == "cancel":
                handle = ROOM_ENGINES.get(bound_room or "")
                if handle:
                    handle.engine.cancel()

            elif mtype == "finalize":
                handle = ROOM_ENGINES.get(bound_room or "")
                if handle:
                    handle.engine.request_finalize()

            elif mtype == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "data": {"text": str(e)}})
        except Exception:
            pass
    finally:
        # detach subscriber, but engine keeps running for other viewers / next refresh
        if bound_room and bound_room in ROOM_ENGINES:
            ROOM_ENGINES[bound_room].subscribers.discard(sub_queue)
        pump_task.cancel()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
