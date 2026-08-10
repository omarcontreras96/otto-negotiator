"""Vapi voice driver.

Replaces the ElevenLabs+Twilio pair after Twilio's fraud queue held both trial
accounts for a week (docs/maritime-voice-findings.md §3b). Vapi supplies the
phone number itself — no telephony vendor, no import step, no policy queue.

Design differences from the ElevenLabs driver, both deliberate:

  * **Transient assistants.** Each outbound call carries its full assistant
    config in the request body — prompt already rendered, server URL already
    set. There is no deploy step, no dashboard state, and no dynamic-variable
    registry to drift out of sync with the pipeline: git is the only source of
    truth for prompts.
  * **Routing via the server URL.** Each call's webhook URL carries
    ?case_id=…&agent=…&dealer_id=… — the payload doesn't need to echo anything
    back for us to route it. The call-id index remains as a fallback.
"""

from __future__ import annotations

import logging
import re

import httpx

from .config import settings
from .llm import load_prompt

log = logging.getLogger("otto.vapi")

API_BASE = "https://api.vapi.ai"

# kind -> prompt file
PROMPT_FILES = {
    "intake": "intake_agent",
    "quote": "quote_agent",
    "nego": "nego_agent",
}


class VoiceError(RuntimeError):
    pass


def _split_prompt(name: str) -> tuple[str, str]:
    """(first_message, system_prompt) from a prompts/*.md file."""
    text = load_prompt(name)
    first = re.search(r"^## First message\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    system = re.search(r"^## System prompt\s*\n(.*?)(?=\Z)", text, re.S | re.M)
    if not first or not system:
        raise VoiceError(f"{name}: needs '## First message' and '## System prompt'")
    return first.group(1).strip(), system.group(1).strip()


def render(text: str, variables: dict[str, str]) -> str:
    """Substitute {{var}} placeholders server-side.

    Unknown placeholders are replaced with an empty string and logged — a
    literal '{{target_otd}}' in a live prompt is worse than a blank, but a
    blank is still a bug, so it must be visible in the logs.
    """
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in variables:
            return str(variables[key])
        log.warning("prompt placeholder {{%s}} had no value — substituted empty", key)
        return ""

    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.vapi_api_key}",
            "Content-Type": "application/json"}


def build_assistant(kind: str, variables: dict[str, str],
                    webhook_url: str | None) -> dict:
    """A transient assistant for one call, prompt fully rendered."""
    first, system = _split_prompt(PROMPT_FILES[kind])
    assistant: dict = {
        "name": f"Otto {kind}",
        "firstMessage": render(first, variables),
        "model": {
            "provider": settings.vapi_model_provider,
            "model": settings.vapi_model,
            "messages": [{"role": "system", "content": render(system, variables)}],
            # Quote agent records numbers; nego improvises against objections.
            "temperature": 0.25 if kind == "quote" else 0.4,
        },
        "voice": {"provider": settings.vapi_voice_provider,
                  "voiceId": settings.vapi_voice_id},
        # Dealership calls should never run long; intake can breathe a bit.
        "maxDurationSeconds": 900 if kind == "intake" else 600,
    }
    if webhook_url:
        assistant["server"] = {"url": webhook_url}
        assistant["serverMessages"] = ["end-of-call-report"]
    return assistant


def outbound_call(*, kind: str, to_number: str, variables: dict[str, str],
                  case_id: str, dealer_id: str = "", timeout: float = 30.0) -> dict:
    """Place an outbound call. Returns {"conversation_id": <vapi call id>}."""
    if not settings.vapi_api_key:
        raise VoiceError("VAPI_API_KEY is not set")
    if not settings.vapi_phone_number_id:
        raise VoiceError("VAPI_PHONE_NUMBER_ID is not set")
    if not to_number:
        raise VoiceError("no destination number")

    # Same belt-and-braces as the ElevenLabs driver: demo mode refuses to dial
    # anything that is not an explicit demo target, whatever the caller passed.
    if settings.demo_mode and to_number not in settings.demo_target_list:
        raise VoiceError(
            f"refusing to dial {to_number}: DEMO_MODE is on and it is not a DEMO_TARGET")

    webhook = None
    if settings.base_url:
        webhook = (f"{settings.base_url}/webhooks/vapi"
                   f"?case_id={case_id}&agent={kind}&dealer_id={dealer_id}")
    else:
        log.warning("BASE_URL not set — call results will not come back to Otto")

    body = {
        "assistant": build_assistant(kind, variables, webhook),
        "phoneNumberId": settings.vapi_phone_number_id,
        "customer": {"number": to_number},
        "metadata": {"case_id": case_id, "agent_type": kind, "dealer_id": dealer_id},
    }
    resp = httpx.post(f"{API_BASE}/call", headers=_headers(), json=body, timeout=timeout)
    if resp.status_code >= 300:
        raise VoiceError(f"create-call failed [{resp.status_code}]: {resp.text[:400]}")
    data = resp.json()
    call_id = data.get("id", "")
    log.info("placed vapi call kind=%s to=%s call=%s", kind, to_number, call_id)
    return {"conversation_id": call_id, "raw": data}


# --- API-side call retrieval (reconciliation) --------------------------------

def list_recent_calls(limit: int = 20) -> list[dict]:
    """Recent calls straight from Vapi's API — the pull side of reconciliation."""
    r = httpx.get(f"{API_BASE}/call", headers=_headers(),
                  params={"limit": limit}, timeout=25)
    r.raise_for_status()
    return r.json()


def call_to_report(call: dict) -> dict:
    """Normalise an API call object to the same shape webhook_meta produces.

    Webhooks push `message.call` / `message.artifact`; the API returns the call
    with `artifact` inline. Reconciliation exists because a webhook can be lost
    (a free quick-tunnel died mid-demo and swallowed one on 2026-08-09) — the
    API copy is authoritative and always there.
    """
    meta = call.get("metadata") or {}
    inbound = call.get("type") == "inboundPhoneCall"
    return {
        "conversation_id": call.get("id", ""),
        "case_id": meta.get("case_id", ""),
        "agent_type": meta.get("agent_type", "") or ("intake" if inbound else ""),
        "dealer_id": meta.get("dealer_id", ""),
        "caller_number": (call.get("customer") or {}).get("number", ""),
        "ended_reason": call.get("endedReason", ""),
        "status": call.get("status", ""),
        "transcript": transcript_text({"message": {"artifact": call.get("artifact") or {}}}),
    }


# --- webhook parsing ---------------------------------------------------------

def is_end_of_call(payload: dict) -> bool:
    return (payload.get("message") or {}).get("type") == "end-of-call-report"


def webhook_meta(payload: dict) -> dict:
    """Routing info from an end-of-call-report. Query params take precedence
    (handled in main.py); this covers the metadata echo and the call id."""
    msg = payload.get("message") or {}
    call = msg.get("call") or {}
    meta = call.get("metadata") or {}
    customer = call.get("customer") or {}
    return {
        "conversation_id": call.get("id", ""),
        "case_id": meta.get("case_id", ""),
        "agent_type": meta.get("agent_type", ""),
        "dealer_id": meta.get("dealer_id", ""),
        "caller_number": customer.get("number", ""),
        "ended_reason": msg.get("endedReason", ""),
    }


def transcript_text(payload: dict) -> str:
    """Flatten the artifact into OTTO:/DEALER: dialogue for extraction."""
    artifact = (payload.get("message") or {}).get("artifact") or {}
    msgs = artifact.get("messages") or []
    lines = []
    for m in msgs:
        role = m.get("role", "")
        text = (m.get("message") or m.get("content") or "").strip()
        if not text or role == "system":
            continue
        speaker = "OTTO" if role in ("assistant", "bot") else "DEALER"
        lines.append(f"{speaker}: {text}")
    if lines:
        return "\n".join(lines)
    # Fall back to the pre-rendered transcript string ("AI: … User: …").
    raw = artifact.get("transcript") or ""
    return raw.replace("AI:", "OTTO:").replace("User:", "DEALER:")
