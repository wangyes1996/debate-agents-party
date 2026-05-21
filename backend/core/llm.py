"""LLM client factory - look up llm_config by id (or default)."""
from __future__ import annotations
from openai import AsyncOpenAI
from .config_store import load_config, find_llm


def get_client(llm_id: str | None = None) -> tuple[AsyncOpenAI, str, str]:
    """Return (client, model_name, llm_name) for the chosen LLM config."""
    cfg = load_config()
    llm = find_llm(cfg, llm_id)
    if not llm:
        raise RuntimeError("No LLM configs defined. Add one on /config page.")
    if not llm.get("api_key"):
        raise RuntimeError(f"LLM '{llm.get('name')}' has no API key. Set it on /config page.")
    if not llm.get("model"):
        raise RuntimeError(f"LLM '{llm.get('name')}' has no model name. Set it on /config page.")
    client = AsyncOpenAI(api_key=llm["api_key"], base_url=llm.get("base_url") or None)
    return client, llm["model"], llm.get("name", "")


async def chat(messages: list[dict], llm_id: str | None = None, temperature: float = 0.7) -> str:
    """Single-shot chat completion."""
    client, model, _ = get_client(llm_id)
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


async def chat_stream(messages: list[dict], llm_id: str | None = None, temperature: float = 0.7):
    """Streaming chat completion - yields token chunks."""
    client, model, _ = get_client(llm_id)
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta
