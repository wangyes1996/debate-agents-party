"""FastAPI entry point - REST + WebSocket."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .core.config_store import load_config, save_config, update_partial, DEFAULT_CONFIG
from .core.debate_engine import DebateEngine
from .api.market import fetch_market, format_market_summary
from .agents.personas import PERSONAS

app = FastAPI(title="Debate Agents Party")

# CORS - frontend on :3000 talks to backend on :8000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only - tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/personas")
async def personas():
    """List all available agent roles for the frontend config UI."""
    return {
        k: {"name": v["name"], "emoji": v["emoji"], "color": v["color"]}
        for k, v in PERSONAS.items()
        if k != "moderator"
    }


@app.get("/api/config")
async def get_config():
    cfg = load_config()
    # mask api keys for transit
    safe = json.loads(json.dumps(cfg))
    for p in safe.get("providers", {}).values():
        if p.get("api_key"):
            p["api_key"] = "***" + p["api_key"][-4:]
    return safe


class ConfigPatch(BaseModel):
    active_provider: str | None = None
    providers: dict | None = None
    agents: dict | None = None
    data_source: dict | None = None


@app.post("/api/config")
async def set_config(patch: ConfigPatch):
    payload = {k: v for k, v in patch.model_dump(exclude_none=True).items()}
    # never overwrite api_key with masked placeholder
    if "providers" in payload:
        cur = load_config().get("providers", {})
        for pname, pcfg in payload["providers"].items():
            if isinstance(pcfg, dict) and pcfg.get("api_key", "").startswith("***"):
                pcfg["api_key"] = cur.get(pname, {}).get("api_key", "")
    update_partial(payload)
    return {"ok": True}


@app.get("/api/market")
async def market():
    cfg = load_config().get("data_source", {})
    m = await fetch_market(cfg.get("primary", "binance"), cfg.get("symbol", "BTCUSDT"))
    return {"market": m, "summary": format_market_summary(m)}


# --- WebSocket: one connection = one debate room ---

@app.websocket("/ws/debate")
async def ws_debate(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue()
    engine: DebateEngine | None = None
    runner_task: asyncio.Task | None = None

    async def pump():
        """Forward queue events to the websocket."""
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
                topic = msg.get("topic", "分析最新的 BTC 行情")
                if engine and not engine.cancelled:
                    await ws.send_json({"type": "error", "data": {"text": "已经有一场辩论在进行中"}})
                    continue
                engine = DebateEngine(topic=topic, queue=queue)
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


# Allow `python -m backend.main`
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
