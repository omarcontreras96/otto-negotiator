"""Build the call list: which lots to phone about this car.

Two sources, in order of preference:
  1. Google Places (real names, addresses, and *real* phone numbers) when a key is set
  2. a curated seed list of genuine SF-area dealerships, used when it is not

The seed list carries real business names and addresses but **no phone numbers** —
inventing a phone number is exactly the kind of plausible-looking fabrication that
gets a real stranger cold-called by a robot. Without Places, the only numbers Otto
will ever dial are the DEMO_TARGETS you explicitly configured.
"""

from __future__ import annotations

import logging
import re

import httpx

from . import storage
from .config import settings

log = logging.getLogger("otto.research")

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# Real dealerships, San Francisco and the immediate peninsula. Van Ness is the
# city's historic Auto Row and is a 5-minute walk from 150 Van Ness.
SF_DEALER_SEEDS = [
    {"name": "San Francisco Honda", "address": "1395 Van Ness Ave, San Francisco, CA 94109",
     "area": "Van Ness Auto Row"},
    {"name": "Royal Motor Sales", "address": "280 S Van Ness Ave, San Francisco, CA 94103",
     "area": "South Van Ness"},
    {"name": "Volvo Cars San Francisco", "address": "285 S Van Ness Ave, San Francisco, CA 94103",
     "area": "South Van Ness"},
    {"name": "San Francisco Toyota", "address": "3800 Geary Blvd, San Francisco, CA 94118",
     "area": "Richmond"},
    {"name": "Mazda San Francisco", "address": "280 S Van Ness Ave, San Francisco, CA 94103",
     "area": "South Van Ness"},
    {"name": "San Francisco Bay Autos", "address": "San Francisco, CA",
     "area": "independent"},
    {"name": "City Toyota", "address": "500 E Market St, Daly City, CA 94014",
     "area": "Daly City"},
    {"name": "Serramonte Auto Plaza", "address": "Serramonte Blvd, Colma, CA 94014",
     "area": "Colma"},
    {"name": "Melody Toyota", "address": "1000 El Camino Real, San Bruno, CA 94066",
     "area": "San Bruno"},
    {"name": "Autobahn Motors", "address": "700 Island Pkwy, Belmont, CA 94002",
     "area": "Belmont"},
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32]


def _query(spec: dict) -> str:
    v = spec.get("vehicle", {}) if spec else {}
    make = v.get("make") or ""
    model = v.get("model") or ""
    if make or model:
        return f"used {make} {model} dealership near San Francisco, CA".strip()
    return "used car dealership in San Francisco, CA"


def _from_places(spec: dict, limit: int) -> list[dict]:
    resp = httpx.post(
        PLACES_URL,
        headers={
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": (
                "places.displayName,places.formattedAddress,"
                "places.nationalPhoneNumber,places.rating,places.userRatingCount"
            ),
            "Content-Type": "application/json",
        },
        json={"textQuery": _query(spec), "maxResultCount": limit,
              "locationBias": {"circle": {
                  "center": {"latitude": 37.7749, "longitude": -122.4194},
                  "radius": 25000.0}}},
        timeout=20.0,
    )
    resp.raise_for_status()
    out = []
    for p in resp.json().get("places", []):
        name = (p.get("displayName") or {}).get("text", "")
        if not name:
            continue
        out.append({
            "id": _slug(name),
            "name": name,
            "address": p.get("formattedAddress", ""),
            "phone": _e164(p.get("nationalPhoneNumber", "")),
            "rating": p.get("rating"),
            "review_count": p.get("userRatingCount"),
            "source": "google_places",
        })
    return out


def _e164(national: str) -> str:
    digits = re.sub(r"\D", "", national or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return ""


def _from_seeds(limit: int) -> list[dict]:
    return [
        {"id": _slug(d["name"]), "name": d["name"], "address": d["address"],
         "area": d["area"], "phone": "", "source": "seed_list"}
        for d in SF_DEALER_SEEDS[:limit]
    ]


def _apply_demo_routing(dealers: list[dict]) -> list[dict]:
    """Point every call at a DEMO_TARGET while keeping the real business on record.

    The dealer's real name and address stay in the case file — the report and the
    dashboard show genuine SF lots — but `phone` is replaced, so demo mode cannot
    dial a real dealership even if the call list came straight from Places.
    """
    targets = settings.demo_target_list
    if not targets:
        for d in dealers:
            d["phone"] = ""
            d["demo_note"] = "DEMO_MODE on but DEMO_TARGETS is empty — not callable"
        return dealers
    for i, d in enumerate(dealers):
        d["real_phone_withheld"] = bool(d.get("phone"))
        d["phone"] = targets[i % len(targets)]
        d["demo_note"] = "DEMO_MODE: routed to a demo target, not the real dealership"
    return dealers


def build_call_list(case_id: str, limit: int = 5) -> dict:
    """Resolve the call list for a case and persist it as dealers.json."""
    spec = storage.read_json(case_id, "buyer_spec.json") or {}

    dealers: list[dict] = []
    if settings.google_places_api_key:
        try:
            dealers = _from_places(spec, limit)
            log.info("places returned %d dealers for case=%s", len(dealers), case_id)
        except Exception as e:
            log.warning("places lookup failed (%s) — falling back to seed list", e)

    if not dealers:
        dealers = _from_seeds(limit)

    if settings.demo_mode:
        dealers = _apply_demo_routing(dealers)

    storage.save_json(case_id, "dealers.json", dealers)
    storage.log_event(
        case_id, "call_list_built",
        count=len(dealers),
        source=dealers[0]["source"] if dealers else None,
        demo_mode=settings.demo_mode,
    )
    return {"count": len(dealers), "dealers": dealers}
