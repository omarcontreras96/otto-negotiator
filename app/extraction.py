"""Transcript -> structured record.

Every extraction is schema-forced (see llm.extract) rather than parsed from prose.
The schemas deliberately allow null everywhere a number might be missing, because
the alternative — a model helpfully inventing a plausible total — is the exact
failure this product exists to catch in dealers.
"""

from __future__ import annotations

import logging

from . import llm

log = logging.getLogger("otto.extraction")


def _run(name: str, transcript: str, context: str = "") -> dict:
    system = llm.load_prompt(name)
    schema = llm.load_schema(name)
    user = f"{context}\n\n--- TRANSCRIPT ---\n{transcript}".strip()
    return llm.extract(schema, system, user)


def extract_buyer_spec(transcript: str) -> dict:
    return _run("extract_buyer_spec", transcript)


def extract_quote(transcript: str, dealer_name: str = "", vehicle: str = "") -> dict:
    ctx = f"Dealer called: {dealer_name}\nVehicle the buyer is shopping for: {vehicle}"
    return _run("extract_quote", transcript, ctx)


def extract_outcome(transcript: str, dealer_name: str = "", opening_otd: object = None) -> dict:
    ctx = (
        f"Dealer called: {dealer_name}\n"
        f"Their out-the-door total before this call: {opening_otd}"
    )
    return _run("extract_outcome", transcript, ctx)


def redact_private(spec: dict) -> dict:
    """Strip anything a dealer-facing agent must never see.

    The buyer's stated maximum lives in exactly one field so it can be removed by
    name here, rather than hoping it never surfaces inside free-text notes. Any
    prompt variable built for a dealer call is built from this copy, never the
    raw spec.
    """
    safe = {k: v for k, v in (spec or {}).items() if k != "buyer_stated_maximum_usd"}
    return safe
