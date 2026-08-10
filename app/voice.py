"""Voice dispatch — the one seam between the pipeline and any voice vendor.

The pipeline calls `place_call(kind, to_number, variables, ...)` and never
learns which vendor dialled. Vapi is primary (it brings its own number);
ElevenLabs remains wired as the fallback, selected automatically when only its
key is present. This seam is why the Twilio week cost a day and not the build.
"""

from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger("otto.voice")


class VoiceError(RuntimeError):
    pass


# ElevenLabs routes by pre-deployed agent id; the mapping lives here so the
# pipeline doesn't carry vendor detail.
_ELEVENLABS_AGENT_IDS = {
    "quote": lambda: settings.elevenlabs_quote_agent_id,
    "nego": lambda: settings.elevenlabs_nego_agent_id,
    "intake": lambda: settings.elevenlabs_intake_agent_id,
}


def place_call(*, kind: str, to_number: str, variables: dict[str, str],
               case_id: str, dealer_id: str = "") -> dict:
    """Place an outbound call. Returns {"conversation_id": ...}.

    Raises VoiceError on any failure, whichever backend is active.
    """
    backend = settings.voice_backend
    if backend == "vapi":
        from . import vapi

        try:
            return vapi.outbound_call(kind=kind, to_number=to_number,
                                      variables=variables, case_id=case_id,
                                      dealer_id=dealer_id)
        except vapi.VoiceError as e:
            raise VoiceError(str(e)) from e

    if backend == "elevenlabs":
        from . import elevenlabs

        agent_id = _ELEVENLABS_AGENT_IDS[kind]()
        dyn = {"case_id": case_id, "agent_type": kind, "dealer_id": dealer_id,
               **variables}
        try:
            return elevenlabs.outbound_call(agent_id=agent_id, to_number=to_number,
                                            dynamic_variables=dyn)
        except elevenlabs.VoiceError as e:
            raise VoiceError(str(e)) from e

    raise VoiceError("no voice backend configured — set VAPI_API_KEY "
                     "(or ELEVENLABS_API_KEY for the fallback)")
