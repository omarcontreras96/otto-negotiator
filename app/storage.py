"""Flat-file case store rooted at /data (the only durable path on Maritime).

Layout:
    /data/cases/{case_id}/case.json              status + metadata
                         /buyer_spec.json        the confirmed job spec
                         /dealers.json           the call list
                         /quotes/{dealer_id}.json
                         /negotiations/{dealer_id}.json
                         /strategy.json
                         /report.md
                         /transcripts/{dealer_id}_{conv_id}.txt
    /data/index/conversations.json               conv_id -> {case_id, agent, dealer_id}

Writes are atomic (tmp + rename) because Maritime can snapshot the VM mid-write.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    p = settings.data_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def case_dir(case_id: str) -> Path:
    d = root() / "cases" / case_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write(path: Path, text: str) -> None:
    """tmp + rename. A snapshot taken mid-write must never leave a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(text)
    tmp.replace(path)


# --- generic json ----------------------------------------------------------

def save_json(case_id: str, rel: str, data: Any) -> None:
    _atomic_write(case_dir(case_id) / rel, json.dumps(data, indent=2, default=str))


def read_json(case_id: str, rel: str) -> Any | None:
    path = case_dir(case_id) / rel
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def save_text(case_id: str, rel: str, text: str) -> None:
    _atomic_write(case_dir(case_id) / rel, text)


def read_text(case_id: str, rel: str) -> str | None:
    path = case_dir(case_id) / rel
    return path.read_text() if path.exists() else None


def list_json(case_id: str, subdir: str) -> list[dict]:
    d = case_dir(case_id) / subdir
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


# --- cases -----------------------------------------------------------------

def new_case(user_phone: str = "", source: str = "api") -> str:
    case_id = f"c_{uuid.uuid4().hex[:10]}"
    save_json(
        case_id,
        "case.json",
        {
            "case_id": case_id,
            "status": "awaiting_intake",
            "user_phone": user_phone,
            "source": source,
            "created_at": _now(),
            "updated_at": _now(),
            "events": [{"at": _now(), "event": "case_created", "source": source}],
        },
    )
    return case_id


def read_case(case_id: str) -> dict | None:
    return read_json(case_id, "case.json")


def update_case(case_id: str, **fields) -> dict:
    case = read_case(case_id) or {"case_id": case_id, "events": []}
    case.update(fields)
    case["updated_at"] = _now()
    save_json(case_id, "case.json", case)
    return case


def set_status(case_id: str, status: str) -> dict:
    case = read_case(case_id) or {"case_id": case_id, "events": []}
    prev = case.get("status")
    case["status"] = status
    case["updated_at"] = _now()
    case.setdefault("events", []).append(
        {"at": _now(), "event": "status", "from": prev, "to": status}
    )
    save_json(case_id, "case.json", case)
    return case


def log_event(case_id: str, event: str, **detail) -> None:
    case = read_case(case_id)
    if not case:
        return
    case.setdefault("events", []).append({"at": _now(), "event": event, **detail})
    case["updated_at"] = _now()
    save_json(case_id, "case.json", case)


def list_cases() -> list[dict]:
    d = root() / "cases"
    if not d.exists():
        return []
    cases = []
    for p in sorted(d.iterdir(), reverse=True):
        if p.is_dir():
            c = read_case(p.name)
            if c:
                cases.append(c)
    return cases


def latest_case() -> dict | None:
    """Most recently updated case — what `POST /chat` talks about by default."""
    cases = list_cases()
    return max(cases, key=lambda c: c.get("updated_at", ""), default=None)


# --- conversation index ----------------------------------------------------
# ElevenLabs post-call webhooks identify a call by conversation_id. We keep our
# own index so a webhook can be routed back to its case even if the dynamic
# variables are missing from the payload.

def _index_path() -> Path:
    d = root() / "index"
    d.mkdir(parents=True, exist_ok=True)
    return d / "conversations.json"


def index_conversation(conv_id: str, case_id: str, agent: str, dealer_id: str = "") -> None:
    path = _index_path()
    idx = json.loads(path.read_text()) if path.exists() else {}
    idx[conv_id] = {"case_id": case_id, "agent": agent, "dealer_id": dealer_id, "at": _now()}
    _atomic_write(path, json.dumps(idx, indent=2))


def lookup_conversation(conv_id: str) -> dict | None:
    path = _index_path()
    if not path.exists():
        return None
    return json.loads(path.read_text()).get(conv_id)
