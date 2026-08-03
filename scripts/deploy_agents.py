#!/usr/bin/env python3
"""Create or update Otto's ElevenLabs agents from the prompt files in git.

Prompts are the product here, so they live in version control and get pushed to
ElevenLabs — not edited in a dashboard and lost. Each prompts/{type}_agent.md
supplies two sections:

    ## First message     -> conversation_config.agent.first_message
    ## System prompt     -> conversation_config.agent.prompt.prompt

Every {{placeholder}} found in the prose is auto-registered as a dynamic variable
so ElevenLabs will accept it at call time.

Usage:
    python scripts/deploy_agents.py                 # create or update all three
    python scripts/deploy_agents.py --agent quote   # just one
    python scripts/deploy_agents.py --list          # show what exists already
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).parent.parent
PROMPTS = ROOT / "prompts"
ENV_PATH = ROOT / ".env"
API = "https://api.elevenlabs.io/v1/convai"

AGENTS = {
    "intake": {
        "file": "intake_agent.md",
        "name": "Otto — intake",
        "env": "ELEVENLABS_INTAKE_AGENT_ID",
        "temperature": 0.4,
    },
    "quote": {
        "file": "quote_agent.md",
        "name": "Otto — quote gatherer",
        "env": "ELEVENLABS_QUOTE_AGENT_ID",
        # Low: this agent asks a fixed set of questions and records numbers.
        "temperature": 0.25,
    },
    "nego": {
        "file": "nego_agent.md",
        "name": "Otto — negotiator",
        "env": "ELEVENLABS_NEGO_AGENT_ID",
        # Slightly higher: it has to improvise against objections, but not so high
        # that it starts improvising facts.
        "temperature": 0.35,
    },
}

# claude-sonnet-4 is ElevenLabs' strongest listed Claude option for agents and
# holds instructions ("never state a budget") better than the faster models.
LLM = os.getenv("ELEVENLABS_AGENT_LLM", "claude-sonnet-4")


def parse_prompt(path: Path) -> tuple[str, str]:
    """Split a prompt file into (first_message, system_prompt)."""
    text = path.read_text()
    first = re.search(r"^## First message\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    system = re.search(r"^## System prompt\s*\n(.*?)(?=\Z)", text, re.S | re.M)
    if not first or not system:
        sys.exit(f"{path.name}: needs both '## First message' and '## System prompt'")
    return first.group(1).strip(), system.group(1).strip()


def placeholders(*texts: str) -> dict[str, str]:
    """Every {{var}} in the prose, registered with an empty default."""
    found: set[str] = set()
    for t in texts:
        found |= set(re.findall(r"\{\{(\w+)\}\}", t))
    return {v: "" for v in sorted(found)}


def build_config(kind: str) -> dict:
    meta = AGENTS[kind]
    first, system = parse_prompt(PROMPTS / meta["file"])
    return {
        "name": meta["name"],
        "conversation_config": {
            "agent": {
                "first_message": first,
                "language": "en",
                "prompt": {
                    "prompt": system,
                    "llm": LLM,
                    "temperature": meta["temperature"],
                },
            },
            "tts": {
                # flash = lowest latency, which matters more than timbre on a
                # dealership call where the other party is impatient.
                "model_id": "eleven_flash_v2",
                "stability": 0.5,
                "similarity_boost": 0.8,
                "speed": 1.0,
            },
            "asr": {"quality": "high"},
            "turn": {
                # Dealers interrupt and multitask; a short timeout makes Otto
                # step on them, a long one makes it feel dead.
                "turn_timeout": 8,
                "mode": "turn",
            },
            "conversation": {
                "max_duration_seconds": 600,
                "client_events": ["audio", "interruption", "user_transcript",
                                  "agent_response"],
            },
        },
        "platform_settings": {
            "overrides": {
                "conversation_config_override": {
                    "agent": {"prompt": {"prompt": True}, "first_message": True},
                },
            },
        },
        "dynamic_variables": {"dynamic_variable_placeholders": placeholders(first, system)},
    }


def headers(key: str) -> dict:
    return {"xi-api-key": key, "Content-Type": "application/json"}


def list_agents(key: str) -> list[dict]:
    r = httpx.get(f"{API}/agents", headers=headers(key), timeout=30)
    r.raise_for_status()
    return r.json().get("agents", [])


def deploy(kind: str, key: str) -> str:
    meta = AGENTS[kind]
    cfg = build_config(kind)
    existing = os.getenv(meta["env"], "").strip()

    if existing:
        r = httpx.patch(f"{API}/agents/{existing}", headers=headers(key),
                        json=cfg, timeout=60)
        if r.status_code < 300:
            print(f"  updated  {kind:7s} {existing}  ({len(cfg['conversation_config']['agent']['prompt']['prompt']):,} chars)")
            return existing
        print(f"  ! update failed for {kind} [{r.status_code}]: {r.text[:300]}")
        print(f"    falling back to creating a new agent")

    r = httpx.post(f"{API}/agents/create", headers=headers(key), json=cfg, timeout=60)
    if r.status_code >= 300:
        sys.exit(f"  ✗ create failed for {kind} [{r.status_code}]: {r.text[:600]}")
    agent_id = r.json().get("agent_id", "")
    print(f"  created  {kind:7s} {agent_id}")
    set_key(str(ENV_PATH), meta["env"], agent_id)
    return agent_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=list(AGENTS), help="deploy only this one")
    ap.add_argument("--list", action="store_true", help="list agents on the account")
    args = ap.parse_args()

    load_dotenv(ENV_PATH)
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        sys.exit("ELEVENLABS_API_KEY is not set in .env")

    if args.list:
        for a in list_agents(key):
            print(f"  {a.get('agent_id')}  {a.get('name')}")
        return

    print(f"Deploying agents (llm={LLM}) ...")
    for kind in ([args.agent] if args.agent else list(AGENTS)):
        deploy(kind, key)

    print("\nAgent ids written to .env.")
    print("Remaining manual step: set the post-call webhook URL in the ElevenLabs")
    print("dashboard (Settings -> Webhooks) to  <public-url>/webhooks/elevenlabs")


if __name__ == "__main__":
    main()
