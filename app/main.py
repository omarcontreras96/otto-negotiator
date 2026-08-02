"""Otto — used-car negotiation agent. Maritime-hosted brain, ElevenLabs voice.

Maritime contract endpoints (must exist, must behave):
    GET  /health   fast, side-effect-free, 2xx
    POST /chat     natural-language front door, replies within 30s

Everything else is the product surface plus /probe/* (the platform instrument).
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from . import storage
from .config import settings
from .probes import BOOT_ID, router as probe_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("otto")

app = FastAPI(title="Otto", description="Used-car negotiation agent")
app.include_router(probe_router)


@app.on_event("startup")
async def _ensure_data_dir() -> None:
    """Create /data up front so /health can stay side-effect-free and still
    report the truth about writability."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log.info("otto boot=%s data_dir=%s", BOOT_ID, settings.data_dir)


# --- Maritime contract ------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    """Must stay fast and side-effect-free — Maritime polls it to decide liveness."""
    return {"status": "ok", "agent": "otto", "boot_id": BOOT_ID, "config": settings.health()}


@app.post("/chat")
async def chat(request: Request) -> dict:
    """Maritime front door. Also reachable from `maritime chat otto "..."`.

    Deliberately intent-matched rather than LLM-routed: this endpoint has a hard
    30-second budget and is the path Maritime's own health tooling exercises, so
    it must not depend on an upstream model being reachable.
    """
    body = await request.json()
    message = (body.get("message") or "").strip()
    source = body.get("source", "unknown")
    log.info("chat source=%s message=%r", source, message[:200])

    lowered = message.lower()
    case = storage.latest_case()

    if not message:
        return {"response": _greeting()}

    if any(k in lowered for k in ("status", "how is", "where are we", "progress")):
        return {"response": _status_line(case)}

    if any(k in lowered for k in ("report", "recommend", "best deal", "which car")):
        if not case:
            return {"response": "No case yet. Say 'start' to open one."}
        md = storage.read_text(case["case_id"], "report.md")
        return {"response": md or f"No report yet — case is {case.get('status')}."}

    if lowered.startswith("start") or "new case" in lowered:
        case_id = storage.new_case(source=source)
        return {
            "response": (
                f"Opened case {case_id}. Call the intake number to give me the car "
                f"you're shopping for, or POST the spec to /cases/{case_id}/spec."
            )
        }

    return {"response": _greeting() + "\n\n" + _status_line(case)}


def _greeting() -> str:
    return (
        "I'm Otto. I call used-car dealers, collect itemised out-the-door quotes, "
        "negotiate them down with real competing offers, and report back with a "
        "ranked recommendation. I never agree to buy anything.\n"
        "Try: 'status', 'report', or 'start'."
    )


def _status_line(case: dict | None) -> str:
    if not case:
        return "No cases yet."
    cid = case["case_id"]
    quotes = storage.list_json(cid, "quotes")
    negos = storage.list_json(cid, "negotiations")
    reached = [q for q in quotes if q.get("reached")]
    parts = [f"Case {cid} — status: {case.get('status')}."]
    if quotes:
        parts.append(f"{len(reached)}/{len(quotes)} dealers quoted.")
    if reached:
        best = min(
            (q for q in reached if q.get("otd_total_usd")),
            key=lambda q: q["otd_total_usd"],
            default=None,
        )
        if best:
            parts.append(
                f"Best OTD so far: ${best['otd_total_usd']:,.0f} at {best.get('dealer_name')}."
            )
    if negos:
        parts.append(f"{len(negos)} negotiation calls done.")
    return " ".join(parts)


# --- product surface --------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    from .dashboard import render

    return render()


@app.get("/cases")
async def get_cases() -> dict:
    return {"cases": storage.list_cases()}


@app.get("/cases/{case_id}")
async def get_case(case_id: str) -> dict:
    case = storage.read_case(case_id)
    if not case:
        raise HTTPException(404, "no such case")
    return {
        "case": case,
        "buyer_spec": storage.read_json(case_id, "buyer_spec.json"),
        "dealers": storage.read_json(case_id, "dealers.json"),
        "quotes": storage.list_json(case_id, "quotes"),
        "strategy": storage.read_json(case_id, "strategy.json"),
        "negotiations": storage.list_json(case_id, "negotiations"),
    }


@app.get("/cases/{case_id}/report", response_class=PlainTextResponse)
async def get_report(case_id: str) -> str:
    md = storage.read_text(case_id, "report.md")
    if md is None:
        raise HTTPException(404, "no report yet")
    return md
