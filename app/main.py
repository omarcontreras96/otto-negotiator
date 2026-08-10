"""Otto — used-car negotiation agent. Maritime-hosted brain, ElevenLabs voice.

Maritime contract endpoints (must exist, must behave):
    GET  /health   fast, side-effect-free, 2xx
    POST /chat     natural-language front door, replies within 30s

Everything else is the product surface plus /probe/* (the platform instrument).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from . import elevenlabs, storage
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
async def chat(request: Request, background: BackgroundTasks) -> dict:
    """Maritime front door. Also reachable from `maritime chat otto "..."`.

    Deliberately intent-matched rather than LLM-routed: this endpoint has a hard
    30-second budget and is the path Maritime's own health tooling exercises, so
    it must not depend on an upstream model being reachable.
    """
    body = await request.json()
    message = (body.get("message") or "").strip()
    source = body.get("source", "unknown")
    log.info("chat source=%s message=%r", source, message[:200])
    _chatlog(body)

    # Maritime's platform webhook (POST /api/webhooks/{id}) wakes a sleeping
    # agent and delivers the original payload JSON-stringified into `message`
    # with source="webhook" — measured, docs/maritime-voice-findings.md §3.4.
    # That makes this the sleep-proof path for voice-vendor webhooks: Vapi
    # posts to the platform URL, and a call report arriving here is unwrapped
    # and dispatched exactly as if it had hit /webhooks/vapi directly.
    if source == "webhook":
        try:
            inner = json.loads(message)
        except json.JSONDecodeError:
            inner = None
        if isinstance(inner, dict):
            from . import vapi

            if vapi.is_end_of_call(inner):
                meta = vapi.webhook_meta(inner)
                case_id, agent_type, dealer_id = (
                    meta["case_id"], meta["agent_type"], meta["dealer_id"])
                if not case_id and meta["conversation_id"]:
                    if idx := storage.lookup_conversation(meta["conversation_id"]):
                        case_id = idx["case_id"]
                        agent_type = agent_type or idx["agent"]
                        dealer_id = dealer_id or idx.get("dealer_id", "")
                if not case_id and agent_type in ("intake", ""):
                    case_id = storage.new_case(user_phone=meta["caller_number"],
                                               source="phone")
                    agent_type = "intake"
                if case_id:
                    background.add_task(
                        _handle_call, case_id, agent_type, dealer_id,
                        meta["conversation_id"], vapi.transcript_text(inner))
                    return {"response": f"call report received for {case_id}"}
            return {"response": "webhook payload received"}

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


def _chatlog(body: dict) -> None:
    """Persist every /chat delivery verbatim — probe instrumentation.

    Exists to answer one undocumented question: what does Maritime's
    POST /api/webhooks/{id} actually deliver to a custom container? Read it
    back at GET /probe/chatlog.
    """
    import json as _json
    from datetime import datetime, timezone

    try:
        d = settings.data_dir / "probe"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "chat_log.jsonl").open("a") as f:
            f.write(_json.dumps({
                "at": datetime.now(timezone.utc).isoformat(), "body": body,
            }) + "\n")
    except Exception:  # instrumentation must never break the front door
        log.exception("chatlog write failed")


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


@app.post("/cases")
async def create_case(request: Request) -> dict:
    body = await request.json() if await request.body() else {}
    case_id = storage.new_case(user_phone=body.get("user_phone", ""), source="api")
    if spec := body.get("buyer_spec"):
        storage.save_json(case_id, "buyer_spec.json", spec)
        storage.set_status(case_id, "intake_done")
    return {"case_id": case_id, "status": (storage.read_case(case_id) or {}).get("status")}


@app.post("/debug/call")
async def debug_call(request: Request) -> dict:
    """Place a single test call outside any case: {"kind": "quote", "to_number": "+1..."}.

    Still subject to every demo-mode guard — a non-DEMO_TARGET number is refused
    by the driver itself.
    """
    from . import voice

    body = await request.json()
    kind = body.get("kind", "quote")
    to = body.get("to_number") or (settings.demo_target_list[0]
                                   if settings.demo_target_list else "")
    case_id = storage.new_case(source="debug")
    storage.set_status(case_id, "calling_for_quotes")
    variables = {
        "dealer_name": "Demo Motors", "buyer_name": "Omar",
        "vehicle_summary": "2020-2022 Honda Civic EX, under 60,000 miles",
        "requirements": "clean title", "market": "San Francisco Bay Area",
        "today": __import__("datetime").date.today().isoformat(),
        **(body.get("variables") or {}),
    }
    try:
        resp = voice.place_call(kind=kind, to_number=to, variables=variables,
                                case_id=case_id, dealer_id="debug_dealer")
    except voice.VoiceError as e:
        raise HTTPException(400, str(e))
    storage.index_conversation(resp.get("conversation_id", ""), case_id, kind,
                               "debug_dealer")
    return {"case_id": case_id, **{k: v for k, v in resp.items() if k != "raw"}}


@app.post("/cases/{case_id}/advance")
async def advance_case(case_id: str) -> dict:
    from . import pipeline

    if not storage.read_case(case_id):
        raise HTTPException(404, "no such case")
    return pipeline.advance(case_id)


# --- voice webhooks ---------------------------------------------------------

@app.post("/webhooks/vapi")
async def vapi_webhook(request: Request, background: BackgroundTasks) -> dict:
    """Vapi server webhook — end-of-call-report only; other events are ack'd.

    Routing comes from the query params Otto stamped on each call's server URL
    (?case_id=…&agent=…&dealer_id=…). The metadata echo and the call-id index
    are fallbacks. An inbound intake call arrives with no params at all — a new
    case is opened for it.
    """
    from . import vapi

    payload = await request.json()
    if not vapi.is_end_of_call(payload):
        return {"ok": True, "ignored": (payload.get("message") or {}).get("type")}

    meta = vapi.webhook_meta(payload)
    qp = request.query_params
    case_id = qp.get("case_id") or meta["case_id"]
    agent_type = qp.get("agent") or meta["agent_type"]
    dealer_id = qp.get("dealer_id") or meta["dealer_id"]
    if not case_id and meta["conversation_id"]:
        if idx := storage.lookup_conversation(meta["conversation_id"]):
            case_id = idx["case_id"]
            agent_type = agent_type or idx["agent"]
            dealer_id = dealer_id or idx.get("dealer_id", "")

    transcript = vapi.transcript_text(payload)
    log.info("vapi webhook call=%s case=%s agent=%s dealer=%s ended=%s chars=%d",
             meta["conversation_id"], case_id, agent_type, dealer_id,
             meta["ended_reason"], len(transcript))

    if not case_id:
        if agent_type in ("intake", ""):
            case_id = storage.new_case(user_phone=meta["caller_number"], source="phone")
            agent_type = "intake"
        else:
            return {"ok": False, "reason": "unroutable: no case_id"}

    background.add_task(_handle_call, case_id, agent_type, dealer_id,
                        meta["conversation_id"], transcript)
    return {"ok": True, "case_id": case_id}


@app.post("/webhooks/elevenlabs")
async def elevenlabs_webhook(request: Request, background: BackgroundTasks) -> dict:
    """Post-call webhook — one entrypoint for every agent type.

    Returns 200 immediately and does the work in the background: extraction plus
    placing the next call takes far longer than any sane webhook timeout, and a
    slow 200 just earns a retry and a duplicate.
    """
    raw = await request.body()
    if not _webhook_signature_ok(request, raw):
        raise HTTPException(401, "bad signature")

    payload = json.loads(raw)
    meta = elevenlabs.webhook_meta(payload)
    transcript = elevenlabs.transcript_text(payload)

    # Dynamic variables are the primary routing key; the conversation index is the
    # fallback for when a call was placed before the variables were attached.
    case_id, agent_type, dealer_id = meta["case_id"], meta["agent_type"], meta["dealer_id"]
    if not case_id and meta["conversation_id"]:
        if idx := storage.lookup_conversation(meta["conversation_id"]):
            case_id = idx["case_id"]
            agent_type = agent_type or idx["agent"]
            dealer_id = dealer_id or idx.get("dealer_id", "")

    log.info("webhook conv=%s case=%s agent=%s dealer=%s chars=%d",
             meta["conversation_id"], case_id, agent_type, dealer_id, len(transcript))

    if not case_id:
        # An inbound intake call has no case yet — open one now.
        if agent_type == "intake" or not agent_type:
            case_id = storage.new_case(user_phone=meta["caller_number"], source="phone")
            agent_type = "intake"
        else:
            return {"ok": False, "reason": "unroutable: no case_id"}

    background.add_task(_handle_call, case_id, agent_type, dealer_id,
                        meta["conversation_id"], transcript)
    return {"ok": True, "case_id": case_id}


def _handle_call(case_id: str, agent_type: str, dealer_id: str,
                 conversation_id: str, transcript: str) -> None:
    from . import extraction, pipeline

    try:
        if agent_type == "intake":
            storage.save_text(case_id, f"transcripts/intake_{conversation_id}.txt", transcript)
            spec = extraction.extract_buyer_spec(transcript)
            storage.save_json(case_id, "buyer_spec.json", spec)
            storage.set_status(case_id, "intake_done")
            if settings.auto_advance and spec.get("confirmed_by_user"):
                pipeline.advance(case_id)
            elif not spec.get("confirmed_by_user"):
                log.warning("case=%s spec not confirmed on the call — holding before dialling",
                            case_id)
        elif agent_type == "quote":
            pipeline.handle_quote_result(case_id, dealer_id, conversation_id, transcript)
        elif agent_type == "nego":
            pipeline.handle_nego_result(case_id, dealer_id, conversation_id, transcript)
        else:
            log.warning("unknown agent_type=%r case=%s", agent_type, case_id)
    except Exception:
        log.exception("webhook handling failed case=%s agent=%s", case_id, agent_type)


def _webhook_signature_ok(request: Request, raw: bytes) -> bool:
    """HMAC-verify when a secret is configured; skip when it is not.

    ElevenLabs signs as `t=<unix>,v0=<hex hmac of "t.body">`.
    """
    if not settings.elevenlabs_webhook_secret:
        return True
    header = request.headers.get("elevenlabs-signature", "")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    ts, sig = parts.get("t", ""), parts.get("v0", "")
    if not ts or not sig:
        return False
    expected = hmac.new(
        settings.elevenlabs_webhook_secret.encode(),
        f"{ts}.{raw.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)
