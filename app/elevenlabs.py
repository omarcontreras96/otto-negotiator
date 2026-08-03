"""Thin client over the ElevenLabs Agents outbound-call API.

    POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call
    headers: xi-api-key
    body:    { agent_id, agent_phone_number_id, to_number,
               conversation_initiation_client_data: { dynamic_variables: {...} } }
    returns: { success, message, conversation_id, callSid }

Custom dynamic variables echo back in the post-call webhook under
data.conversation_initiation_client_data.dynamic_variables, which is how a
finished call is routed back to its case.
"""

from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("otto.voice")

API_BASE = "https://api.elevenlabs.io"
OUTBOUND_PATH = "/v1/convai/twilio/outbound-call"


class VoiceError(RuntimeError):
    pass


def outbound_call(
    *,
    agent_id: str,
    to_number: str,
    dynamic_variables: dict[str, str] | None = None,
    call_recording_enabled: bool = True,
    timeout: float = 30.0,
) -> dict:
    """Place an outbound call. Raises VoiceError on any non-success."""
    if not settings.elevenlabs_api_key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    if not settings.elevenlabs_phone_number_id:
        raise VoiceError("ELEVENLABS_PHONE_NUMBER_ID is not set")
    if not agent_id:
        raise VoiceError("no agent_id — is the agent deployed and its id in .env?")
    if not to_number:
        raise VoiceError("no destination number")

    # Belt and braces on top of the pipeline's own check: in demo mode this
    # client refuses to dial anything that is not an explicit demo target, so
    # no code path anywhere can reach a real dealership by accident.
    if settings.demo_mode and to_number not in settings.demo_target_list:
        raise VoiceError(
            f"refusing to dial {to_number}: DEMO_MODE is on and it is not a DEMO_TARGET"
        )

    body: dict = {
        "agent_id": agent_id,
        "agent_phone_number_id": settings.elevenlabs_phone_number_id,
        "to_number": to_number,
        "call_recording_enabled": call_recording_enabled,
    }
    if dynamic_variables:
        body["conversation_initiation_client_data"] = {
            "dynamic_variables": {k: str(v) for k, v in dynamic_variables.items()},
        }

    resp = httpx.post(
        f"{API_BASE}{OUTBOUND_PATH}",
        headers={"xi-api-key": settings.elevenlabs_api_key,
                 "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if resp.status_code >= 300:
        raise VoiceError(f"outbound-call failed [{resp.status_code}]: {resp.text[:400]}")
    data = resp.json()
    if data.get("success") is False:
        raise VoiceError(f"outbound-call not successful: {data}")
    log.info("placed call agent=%s to=%s conv=%s", agent_id, to_number,
             data.get("conversation_id"))
    return data


def transcript_text(payload: dict) -> str:
    """Flatten an ElevenLabs post-call webhook transcript into plain text.

    The payload carries a turn list; extraction reads far better from a rendered
    dialogue than from raw JSON.
    """
    data = payload.get("data", payload)
    turns = data.get("transcript") or []
    if isinstance(turns, str):
        return turns
    lines = []
    for t in turns:
        role = t.get("role") or t.get("speaker") or "?"
        text = (t.get("message") or t.get("text") or "").strip()
        if text:
            lines.append(f"{'OTTO' if role in ('agent', 'assistant') else 'DEALER'}: {text}")
    return "\n".join(lines)


def webhook_meta(payload: dict) -> dict:
    """Pull routing info out of a post-call webhook."""
    data = payload.get("data", payload)
    init = data.get("conversation_initiation_client_data") or {}
    dyn = init.get("dynamic_variables") or {}
    return {
        "conversation_id": data.get("conversation_id") or "",
        "agent_id": data.get("agent_id") or "",
        "case_id": dyn.get("case_id") or "",
        "agent_type": dyn.get("agent_type") or "",
        "dealer_id": dyn.get("dealer_id") or "",
        "caller_number": (data.get("metadata") or {}).get("caller_number", ""),
    }
