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
    return {"ok": True}


# --- WebSocket: one connection = one debate ---------------------------------

@app.websocket("/ws/debate")
async def ws_debate(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue()
    engine: DebateEngine | None = None
    runner_task: asyncio.Task | None = None

    async def pump():
        while True:
            evt = await queue.get()
            try:
                await ws.send_json(evt)
            except Exception:
                return
            if evt.get("type") in ("done", "error"):
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
            if mtype == "start":
                room_id = msg.get("room_id")
                topic_override = msg.get("topic")  # optional override
                if not room_id:
                    await ws.send_json({"type": "error", "data": {"text": "缺少 room_id"}})
                    continue
                if engine and not engine.cancelled:
                    await ws.send_json({"type": "error", "data": {"text": "已经有一场辩论在进行中"}})
                    continue
                try:
                    engine = DebateEngine(room_id=room_id, queue=queue, topic_override=topic_override)
                except Exception as e:
                    await ws.send_json({"type": "error", "data": {"text": str(e)}})
                    continue
                runner_task = asyncio.create_task(engine.run())
            elif mtype == "user_message":
                text = (msg.get("text") or "").strip()
                if engine and text:
                    engine.add_user_message(text)
            elif mtype == "cancel":
                if engine:
                    engine.cancel()
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
        if engine:
            engine.cancel()
        if runner_task and not runner_task.done():
            runner_task.cancel()
        pump_task.cancel()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
