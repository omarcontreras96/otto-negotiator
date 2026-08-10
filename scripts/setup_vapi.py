#!/usr/bin/env python3
"""One-shot Vapi setup: verify the key, get a free US number, wire inbound intake.

    python scripts/setup_vapi.py             # do everything
    python scripts/setup_vapi.py --check     # just verify the key and list state

Outbound needs nothing pre-created (transient assistants), so this script's job
is only: a phone number id in .env, and — for the inbound intake flow — a
persistent intake assistant attached to that number.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
ENV = ROOT / ".env"
API = "https://api.vapi.ai"


def h(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--area-code", default="415")
    args = ap.parse_args()

    load_dotenv(ENV)
    key = os.getenv("VAPI_API_KEY", "").strip()
    if not key:
        sys.exit("VAPI_API_KEY is not set in .env — dashboard.vapi.ai → Settings → API Keys "
                 "(use the PRIVATE key, not the public one)")

    # 1. key valid?
    r = httpx.get(f"{API}/assistant", headers=h(key), params={"limit": 5}, timeout=25)
    if r.status_code >= 300:
        sys.exit(f"key check failed [{r.status_code}]: {r.text[:300]} — "
                 "is this the PRIVATE key?")
    print(f"✓ key valid ({len(r.json())} assistants on the account)")

    # 2. numbers on the account
    r = httpx.get(f"{API}/phone-number", headers=h(key), timeout=25)
    r.raise_for_status()
    numbers = r.json()
    for n in numbers:
        print(f"  number: {n.get('number') or n.get('sipUri', '?')}  "
              f"provider={n.get('provider')}  id={n.get('id')}")

    if args.check:
        return

    # 3. ensure a number exists
    if numbers:
        number = numbers[0]
    else:
        print(f"requesting a free Vapi number (area {args.area_code}) ...")
        r = httpx.post(f"{API}/phone-number", headers=h(key),
                       json={"provider": "vapi",
                             "numberDesiredAreaCode": args.area_code},
                       timeout=40)
        if r.status_code >= 300:
            sys.exit(
                f"could not create a number via API [{r.status_code}]: {r.text[:300]}\n"
                "Create it in the dashboard instead: Phone Numbers → Create → "
                "Free Vapi Number, then re-run this script."
            )
        number = r.json()
        print(f"✓ created {number.get('number')}  id={number.get('id')}")

    set_key(str(ENV), "VAPI_PHONE_NUMBER_ID", number["id"])
    print(f"✓ VAPI_PHONE_NUMBER_ID={number['id']} written to .env")

    # 4. persistent intake assistant for inbound calls
    base_url = os.getenv("BASE_URL", "").strip().rstrip("/")
    if not base_url:
        print("\n! BASE_URL is empty — skipping inbound wiring. Run scripts/dev.sh "
              "(or deploy) to get a public URL, then re-run this script.")
        return

    from app import vapi as vapi_mod

    assistant_cfg = vapi_mod.build_assistant(
        "intake", {}, f"{base_url}/webhooks/vapi")
    existing_id = os.getenv("VAPI_INTAKE_ASSISTANT_ID", "").strip()
    if existing_id:
        r = httpx.patch(f"{API}/assistant/{existing_id}", headers=h(key),
                        json=assistant_cfg, timeout=30)
        action = "updated"
    else:
        r = httpx.post(f"{API}/assistant", headers=h(key), json=assistant_cfg,
                       timeout=30)
        action = "created"
    if r.status_code >= 300:
        sys.exit(f"intake assistant {action} failed [{r.status_code}]: {r.text[:300]}")
    assistant_id = r.json()["id"]
    set_key(str(ENV), "VAPI_INTAKE_ASSISTANT_ID", assistant_id)
    print(f"✓ intake assistant {action}: {assistant_id}")

    r = httpx.patch(f"{API}/phone-number/{number['id']}", headers=h(key),
                    json={"assistantId": assistant_id}, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"attach failed [{r.status_code}]: {r.text[:300]}")
    print(f"✓ inbound calls to {number.get('number')} now reach the intake agent")
    print("\nDone. Call the number to talk to Otto; outbound needs nothing else.")


if __name__ == "__main__":
    main()
