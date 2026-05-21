"""LLM client factory - supports multi-provider via OpenAI-compatible API."""
from __future__ import annotations
import os
from openai import AsyncOpenAI
from .config_store import load_config


def _provider_settings(provider: str) -> dict:
    """Return base_url + api_key + model for a provider."""
    cfg = load_config()
    p = cfg.get("providers", {}).get(provider, {})
    return {
        "api_key": p.get("api_key") or os.getenv(f"{provider.upper()}_API_KEY", ""),
        "base_url": p.get("base_url"),
        "model": p.get("model", "gpt-4o-mini"),
    }


def get_client(provider: str | None = None) -> tuple[AsyncOpenAI, str]:
    """Return (client, model_name) for the configured provider."""
    cfg = load_config()
    provider = provider or cfg.get("active_provider", "openai")
    s = _provider_settings(provider)
    if not s["api_key"]:
        raise RuntimeError(f"No API key configured for provider '{provider}'. Set it on /config page.")
    client = AsyncOpenAI(api_key=s["api_key"], base_url=s["base_url"] or None)
    return client, s["model"]


async def chat(messages: list[dict], provider: str | None = None, temperature: float = 0.7) -> str:
    """Single-shot chat completion."""
    client, model = get_client(provider)
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


async def chat_stream(messages: list[dict], provider: str | None = None, temperature: float = 0.7):
    """Streaming chat completion - yields token chunks."""
    client, model = get_client(provider)
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
