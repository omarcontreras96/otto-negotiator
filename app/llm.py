"""Model access with two interchangeable backends.

Otto's reasoning is all short, structured work over call transcripts — extraction,
strategy, report prose. It does not need a specific vendor, so it takes whichever
backend is available and prefers the free one:

  1. **Maritime's bundled proxy** (default when hosted). Maritime injects
     OPENAI_API_KEY + OPENAI_BASE_URL into every container, and per its own docs
     "messages, tokens, and awake seconds never change the hosting bill". Running
     here costs nothing beyond the flat agent price.
  2. **Anthropic direct** (default locally, or force with OTTO_LLM_BACKEND=anthropic).

Both paths force schema-valid JSON through tool calling rather than parsing prose,
because a half-parsed extraction silently drops a fee line and that is exactly the
failure this product exists to prevent.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from .config import settings

log = logging.getLogger("otto.llm")

PROMPTS = Path(__file__).parent.parent / "prompts"


class LLMError(RuntimeError):
    pass


def backend() -> str:
    """Which backend is in play. Explicit override wins, then Maritime, then Anthropic."""
    forced = os.getenv("OTTO_LLM_BACKEND", "").strip().lower()
    if forced in ("openai", "maritime", "anthropic"):
        return "openai" if forced == "maritime" else forced
    if settings.openai_base_url and settings.openai_api_key:
        return "openai"
    if settings.anthropic_api_key:
        return "anthropic"
    raise LLMError(
        "no model backend configured — set ANTHROPIC_API_KEY, or run on Maritime "
        "where OPENAI_API_KEY/OPENAI_BASE_URL are injected"
    )


def load_prompt(name: str) -> str:
    path = PROMPTS / f"{name}.md"
    if not path.exists():
        raise LLMError(f"prompt not found: {path}")
    return path.read_text()


def load_schema(name: str) -> dict:
    path = PROMPTS / f"{name}.schema.json"
    if not path.exists():
        raise LLMError(f"schema not found: {path}")
    return json.loads(path.read_text())


# --- structured extraction --------------------------------------------------

def extract(schema: dict, system: str, user: str, max_tokens: int = 2000) -> dict:
    """Force a schema-valid object out of free text. Raises if the model declines."""
    if backend() == "openai":
        return _extract_openai(schema, system, user, max_tokens)
    return _extract_anthropic(schema, system, user, max_tokens)


def _extract_anthropic(schema: dict, system: str, user: str, max_tokens: int) -> dict:
    from anthropic import Anthropic

    resp = Anthropic(api_key=settings.anthropic_api_key).messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        tools=[{
            "name": "record",
            "description": "Record the extracted structured data.",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": "record"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record":
            return dict(block.input)
    raise LLMError(f"model did not call the record tool (stop={resp.stop_reason})")


def _extract_openai(schema: dict, system: str, user: str, max_tokens: int) -> dict:
    body = {
        "model": settings.openai_model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "record",
                "description": "Record the extracted structured data.",
                "parameters": schema,
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": "record"}},
    }
    data = _openai_post("/chat/completions", body)
    try:
        calls = data["choices"][0]["message"]["tool_calls"]
        return json.loads(calls[0]["function"]["arguments"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise LLMError(f"could not read a tool call from the response: {e}") from e


# --- prose ------------------------------------------------------------------

def generate(system: str, user: str, max_tokens: int = 4000) -> str:
    """Plain prose — the final report."""
    if backend() == "openai":
        data = _openai_post("/chat/completions", {
            "model": settings.openai_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        })
        return data["choices"][0]["message"]["content"] or ""

    from anthropic import Anthropic

    resp = Anthropic(api_key=settings.anthropic_api_key).messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


# --- streaming (custom-LLM probe) -------------------------------------------

async def stream_text(messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    """Stream tokens for the OpenAI-compatible probe endpoint.

    Takes OpenAI-shaped messages, because that is what a voice vendor sends.
    """
    try:
        which = backend()
    except LLMError as e:
        yield str(e)
        return

    if which == "openai":
        async for piece in _stream_openai(messages):
            yield piece
        return

    from anthropic import AsyncAnthropic

    system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    convo = [
        {"role": "assistant" if m.get("role") == "assistant" else "user",
         "content": m.get("content", "")}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ] or [{"role": "user", "content": "Hello."}]

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with client.messages.stream(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=system or "You are a concise voice assistant. Answer in one or two sentences.",
        messages=convo,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def _stream_openai(messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    body = {
        "model": settings.openai_model,
        "messages": messages,
        "max_tokens": 1024,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{settings.openai_base_url}/chat/completions",
            headers=_openai_headers(),
            json=body,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    return
                try:
                    delta = json.loads(payload)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if content := delta.get("content"):
                    yield content


# --- transport --------------------------------------------------------------

def _openai_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def _openai_post(path: str, body: dict, timeout: float = 90.0) -> dict:
    url = f"{settings.openai_base_url}{path}"
    resp = httpx.post(url, headers=_openai_headers(), json=body, timeout=timeout)
    if resp.status_code >= 300:
        raise LLMError(f"{path} failed [{resp.status_code}]: {resp.text[:400]}")
    return resp.json()
