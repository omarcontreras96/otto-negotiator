"""Claude client: structured extraction, prose generation, and token streaming.

Extraction runs against call transcripts, which are short and messy; the job is
schema conformance, not eloquence. Tool-use forces valid JSON rather than hoping
for it from a prose response.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from anthropic import Anthropic, AsyncAnthropic

from .config import settings

log = logging.getLogger("otto.llm")

PROMPTS = Path(__file__).parent.parent / "prompts"


class LLMError(RuntimeError):
    pass


def _client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    return Anthropic(api_key=settings.anthropic_api_key)


def load_prompt(name: str) -> str:
    path = PROMPTS / f"{name}.md"
    if not path.exists():
        raise LLMError(f"prompt not found: {path}")
    return path.read_text()


def extract(schema: dict, system: str, user: str, max_tokens: int = 2000) -> dict:
    """Force a schema-valid object out of free text via a single-tool call.

    Returns the tool input verbatim. Raises LLMError if the model declines to
    call the tool, which is the honest failure mode — better than a half-parsed
    dict that silently drops a fee line.
    """
    tool = {
        "name": "record",
        "description": "Record the extracted structured data.",
        "input_schema": schema,
    }
    resp = _client().messages.create(
        model=settings.model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": "record"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record":
            return dict(block.input)
    raise LLMError(f"model did not call the record tool (stop_reason={resp.stop_reason})")


def generate(system: str, user: str, max_tokens: int = 4000) -> str:
    """Plain prose — used for the final report."""
    resp = _client().messages.create(
        model=settings.model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


async def stream_text(messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    """Stream tokens for the OpenAI-compatible custom-LLM probe.

    Takes OpenAI-shaped messages (that is what a voice vendor sends) and maps
    them onto Anthropic's system/messages split.
    """
    if not settings.anthropic_api_key:
        yield "ANTHROPIC_API_KEY is not set"
        return

    system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    convo = [
        {"role": "assistant" if m.get("role") == "assistant" else "user",
         "content": m.get("content", "")}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ] or [{"role": "user", "content": "Hello."}]

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with client.messages.stream(
        model=settings.model,
        max_tokens=1024,
        system=system or "You are a concise voice assistant. Answer in one or two sentences.",
        messages=convo,
    ) as stream:
        async for text in stream.text_stream:
            yield text


def json_schema_from(path_or_dict: str | dict) -> dict:
    """Schemas live next to their prompts as prompts/<name>.schema.json."""
    if isinstance(path_or_dict, dict):
        return path_or_dict
    p = PROMPTS / f"{path_or_dict}.schema.json"
    if not p.exists():
        raise LLMError(f"schema not found: {p}")
    return json.loads(p.read_text())
