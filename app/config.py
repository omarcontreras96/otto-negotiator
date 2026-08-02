"""Environment-backed settings.

On Maritime these come from `maritime env set otto KEY=value` (encrypted by default).
Locally they come from a .env file.
"""

from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- storage -----------------------------------------------------------
    # /data is the only path that survives sleep/wake on Maritime. Locally we
    # fall back to ./data so you can run without root.
    @cached_property
    def data_dir(self) -> Path:
        raw = os.getenv("DATA_DIR")
        if raw:
            return Path(raw)
        maritime = Path("/data")
        if maritime.is_dir() and os.access(maritime, os.W_OK):
            return maritime
        return Path.cwd() / "data"

    # --- voice layer (ElevenLabs Agents) -----------------------------------
    elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_phone_number_id = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")
    elevenlabs_intake_agent_id = os.getenv("ELEVENLABS_INTAKE_AGENT_ID", "")
    elevenlabs_quote_agent_id = os.getenv("ELEVENLABS_QUOTE_AGENT_ID", "")
    elevenlabs_nego_agent_id = os.getenv("ELEVENLABS_NEGO_AGENT_ID", "")
    elevenlabs_report_agent_id = os.getenv("ELEVENLABS_REPORT_AGENT_ID", "")
    # Optional: when set, post-call webhooks are HMAC-verified.
    elevenlabs_webhook_secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")

    # --- reasoning ---------------------------------------------------------
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    # Extraction and strategy are structured-output tasks on short transcripts;
    # Sonnet is the right cost/latency point. Override for the report if desired.
    model = os.getenv("OTTO_MODEL", "claude-sonnet-5")

    # --- research ----------------------------------------------------------
    google_places_api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")

    # --- orchestrator ------------------------------------------------------
    # Public HTTPS base for this service (the Maritime public URL, or an ngrok
    # tunnel in dev). ElevenLabs posts call results here.
    base_url = os.getenv("BASE_URL", "").rstrip("/")

    # --- demo safety -------------------------------------------------------
    # When on, NO real dealership is ever dialed: every outbound call is routed
    # to a DEMO_TARGET while the real dealer name/address is kept in the record,
    # so the demo shows real SF lots without calling them.
    demo_mode = _bool("DEMO_MODE", True)
    auto_advance = _bool("AUTO_ADVANCE", True)

    @property
    def demo_target_list(self) -> list[str]:
        return [n.strip() for n in os.getenv("DEMO_TARGETS", "").split(",") if n.strip()]

    @property
    def voice_configured(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_phone_number_id)

    def health(self) -> dict:
        """Config snapshot for GET /health — booleans only, never key material."""
        return {
            "data_dir": str(self.data_dir),
            "data_dir_writable": os.access(self.data_dir, os.W_OK)
            if self.data_dir.exists()
            else False,
            "voice_configured": self.voice_configured,
            "anthropic_configured": bool(self.anthropic_api_key),
            "places_configured": bool(self.google_places_api_key),
            "base_url_set": bool(self.base_url),
            "demo_mode": self.demo_mode,
            "demo_targets": len(self.demo_target_list),
            "agents": {
                "intake": bool(self.elevenlabs_intake_agent_id),
                "quote": bool(self.elevenlabs_quote_agent_id),
                "nego": bool(self.elevenlabs_nego_agent_id),
                "report": bool(self.elevenlabs_report_agent_id),
            },
        }


settings = Settings()
