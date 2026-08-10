"""The case state machine.

    awaiting_intake -> intake_done -> researching -> calling_for_quotes
                    -> quotes_collected -> strategy_ready -> negotiating -> done

Calls run one at a time and chain off their own post-call webhook. That is slower
than parallel dialing but it removes a whole class of webhook races, and with a
single demo phone number there is nothing to parallelise anyway.

Every handler is idempotent: ElevenLabs retries webhooks, and a retry must never
double-record a quote or place a second call.
"""

from __future__ import annotations

import logging
from datetime import date

from . import extraction, research, storage, strategy, voice
from .config import settings

log = logging.getLogger("otto.pipeline")

STATUSES = [
    "awaiting_intake", "intake_done", "researching", "calling_for_quotes",
    "quotes_collected", "strategy_ready", "negotiating", "done",
]


def _money(x: object) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "not stated"


def _vehicle_summary(spec: dict) -> str:
    v = (spec or {}).get("vehicle") or {}
    bits = []
    if v.get("year_min") and v.get("year_max"):
        bits.append(f"{v['year_min']}-{v['year_max']}")
    elif v.get("year_min"):
        bits.append(f"{v['year_min']} or newer")
    bits += [x for x in (v.get("make"), v.get("model"), v.get("trim")) if x]
    summary = " ".join(bits) or "a used car"
    if v.get("max_mileage"):
        summary += f", under {v['max_mileage']:,} miles"
    return summary


def _requirements(spec: dict) -> str:
    musts = (spec or {}).get("must_haves") or []
    return ", ".join(musts) if musts else "nothing beyond the vehicle described"


def _dealer_by_id(dealers: list[dict], dealer_id: str) -> dict | None:
    return next((d for d in dealers if d.get("id") == dealer_id), None)


# --- quote round ------------------------------------------------------------

def _mark_unreached(case_id: str, dealer: dict, reason: str, kind: str = "quotes") -> None:
    did = dealer.get("id", "unknown")
    storage.save_json(case_id, f"{kind}/{did}.json", {
        "dealer_id": did,
        "dealer_name": dealer.get("name", ""),
        "reached": False,
        "outcome": "no_answer",
        "otd_total_usd": None,
        "red_flags": [],
        "evidence": [],
        "notes": reason,
    })
    log.info("%s: dealer unreachable case=%s dealer=%s: %s", kind, case_id, did, reason)


def start_next_quote_call(case_id: str) -> dict:
    """Call the next un-quoted dealer, or close the round."""
    case = storage.read_case(case_id)
    if not case or case.get("status") != "calling_for_quotes":
        return {"skipped": f"status is {case.get('status') if case else None!r}"}

    spec = storage.read_json(case_id, "buyer_spec.json") or {}
    dealers = storage.read_json(case_id, "dealers.json") or []

    for dealer in dealers:
        did = dealer.get("id", "")
        if storage.read_json(case_id, f"quotes/{did}.json") is not None:
            continue  # already has a quote or an unreached marker

        phone = dealer.get("phone", "")
        if not phone:
            _mark_unreached(case_id, dealer, dealer.get("demo_note") or "no phone number")
            continue
        if settings.demo_mode and phone not in settings.demo_target_list:
            _mark_unreached(case_id, dealer, "DEMO_MODE: not a demo target")
            continue

        try:
            resp = voice.place_call(
                kind="quote",
                to_number=phone,
                case_id=case_id,
                dealer_id=did,
                variables={
                    "dealer_name": dealer.get("name", ""),
                    "buyer_name": spec.get("buyer_name") or "the buyer",
                    "vehicle_summary": _vehicle_summary(spec),
                    "requirements": _requirements(spec),
                    "market": (spec.get("constraints") or {}).get("market") or "the Bay Area",
                    "today": date.today().isoformat(),
                },
            )
        except voice.VoiceError as e:
            _mark_unreached(case_id, dealer, f"call failed: {e}")
            continue

        conv = resp.get("conversation_id", "")
        if conv:
            storage.index_conversation(conv, case_id, "quote", did)
        storage.log_event(case_id, "quote_call_placed", dealer_id=did, conversation_id=conv)
        return {"placed": did, "conversation_id": conv}

    storage.set_status(case_id, "quotes_collected")
    log.info("quote round complete case=%s", case_id)
    if settings.auto_advance:
        return advance(case_id)
    return {"done": True, "status": "quotes_collected"}


def handle_quote_result(case_id: str, dealer_id: str, conversation_id: str, transcript: str) -> None:
    """Record a finished quote call, then place the next one."""
    if not dealer_id:
        log.warning("quote webhook with no dealer_id case=%s — cannot record", case_id)
        return

    if storage.read_json(case_id, f"quotes/{dealer_id}.json") is not None:
        log.info("quote already recorded case=%s dealer=%s (webhook retry)", case_id, dealer_id)
    else:
        dealers = storage.read_json(case_id, "dealers.json") or []
        dealer = _dealer_by_id(dealers, dealer_id) or {"id": dealer_id, "name": ""}
        spec = storage.read_json(case_id, "buyer_spec.json") or {}

        if not transcript.strip():
            _mark_unreached(case_id, dealer, "empty transcript / no answer")
        else:
            storage.save_text(case_id, f"transcripts/{dealer_id}_{conversation_id}.txt", transcript)
            try:
                quote = extraction.extract_quote(
                    transcript, dealer.get("name", ""), _vehicle_summary(spec)
                )
            except Exception as e:
                log.exception("quote extraction failed case=%s dealer=%s", case_id, dealer_id)
                _mark_unreached(case_id, dealer, f"extraction failed: {e}")
            else:
                storage.save_json(case_id, f"quotes/{dealer_id}.json", {
                    "dealer_id": dealer_id,
                    "dealer_name": dealer.get("name", ""),
                    "dealer_address": dealer.get("address", ""),
                    "conversation_id": conversation_id,
                    "transcript_path": f"transcripts/{dealer_id}_{conversation_id}.txt",
                    **quote,
                })
                log.info("quote recorded case=%s dealer=%s otd=%s flags=%s",
                         case_id, dealer_id, quote.get("otd_total_usd"), quote.get("red_flags"))

    start_next_quote_call(case_id)


# --- negotiation round ------------------------------------------------------

def start_next_nego_call(case_id: str) -> dict:
    """Call the next shortlisted dealer back, or produce the report."""
    case = storage.read_case(case_id)
    if not case or case.get("status") not in ("strategy_ready", "negotiating"):
        return {"skipped": f"status is {case.get('status') if case else None!r}"}

    plan = storage.read_json(case_id, "strategy.json") or {}
    per_dealer = {p.get("dealer_id"): p for p in plan.get("per_dealer", [])}
    shortlist = plan.get("shortlist") or list(per_dealer.keys())
    dealers = storage.read_json(case_id, "dealers.json") or []
    spec = storage.read_json(case_id, "buyer_spec.json") or {}

    for did in shortlist:
        if storage.read_json(case_id, f"negotiations/{did}.json") is not None:
            continue
        dealer = _dealer_by_id(dealers, did) or {"id": did, "name": ""}
        quote = storage.read_json(case_id, f"quotes/{did}.json") or {}
        p = per_dealer.get(did, {})
        current = p.get("current_otd_usd") or quote.get("otd_total_usd")

        phone = dealer.get("phone", "")
        if not phone or (settings.demo_mode and phone not in settings.demo_target_list):
            _mark_unreached(case_id, dealer, "not callable in demo mode", kind="negotiations")
            continue

        try:
            resp = voice.place_call(
                kind="nego",
                to_number=phone,
                case_id=case_id,
                dealer_id=did,
                variables={
                    "dealer_name": dealer.get("name", ""),
                    "buyer_name": spec.get("buyer_name") or "the buyer",
                    "vehicle_summary": quote.get("vehicle_described") or _vehicle_summary(spec),
                    "today": date.today().isoformat(),
                    "current_otd": _money(current),
                    "current_offer": _money(p.get("opening_offer_usd")),
                    "target_otd": _money(p.get("target_otd_usd")),
                    "prior_quote_summary": _prior_quote_summary(quote),
                    "days_on_lot": str(quote.get("days_on_lot") or "not stated"),
                    "contested_fees": ", ".join(p.get("contested_fees") or [])
                                      or "none identified",
                    "red_flags": ", ".join(quote.get("red_flags") or []) or "none",
                    # A complete true sentence, or an explicit denial. Never a bare number.
                    "competing_quote_disclosure": strategy.competing_disclosure(
                        case_id, did, current
                    ),
                },
            )
        except voice.VoiceError as e:
            _mark_unreached(case_id, dealer, f"call failed: {e}", kind="negotiations")
            continue

        conv = resp.get("conversation_id", "")
        if conv:
            storage.index_conversation(conv, case_id, "nego", did)
        storage.set_status(case_id, "negotiating")
        storage.log_event(case_id, "nego_call_placed", dealer_id=did, conversation_id=conv)
        return {"placed": did, "conversation_id": conv}

    # Shortlist exhausted -> report.
    from . import report
    md = report.generate(case_id)
    storage.set_status(case_id, "done")
    log.info("negotiations complete case=%s -> done", case_id)
    return {"done": True, "status": "done", "report_chars": len(md)}


def _prior_quote_summary(quote: dict) -> str:
    """What this dealer already told us — evidenced, so safe to restate to them."""
    bits = []
    li = quote.get("line_items") or {}
    if li.get("vehicle_price"):
        bits.append(f"vehicle {_money(li['vehicle_price'])}")
    if li.get("doc_fee"):
        bits.append(f"doc fee {_money(li['doc_fee'])}")
    for a in (li.get("dealer_addons") or [])[:3]:
        bits.append(f"{a.get('name')} {_money(a.get('amount_usd'))}")
    if not quote.get("otd_itemized_received"):
        bits.append("they never itemised it")
    return "; ".join(bits) or "no itemisation captured"


def handle_nego_result(case_id: str, dealer_id: str, conversation_id: str, transcript: str) -> None:
    if not dealer_id:
        log.warning("nego webhook with no dealer_id case=%s", case_id)
        return

    if storage.read_json(case_id, f"negotiations/{dealer_id}.json") is not None:
        log.info("nego already recorded case=%s dealer=%s (webhook retry)", case_id, dealer_id)
    else:
        dealers = storage.read_json(case_id, "dealers.json") or []
        dealer = _dealer_by_id(dealers, dealer_id) or {"id": dealer_id, "name": ""}
        quote = storage.read_json(case_id, f"quotes/{dealer_id}.json") or {}

        if not transcript.strip():
            _mark_unreached(case_id, dealer, "empty transcript / no answer", kind="negotiations")
        else:
            storage.save_text(case_id, f"transcripts/{dealer_id}_{conversation_id}.txt", transcript)
            try:
                outcome = extraction.extract_outcome(
                    transcript, dealer.get("name", ""), quote.get("otd_total_usd")
                )
            except Exception as e:
                log.exception("nego extraction failed case=%s dealer=%s", case_id, dealer_id)
                _mark_unreached(case_id, dealer, f"extraction failed: {e}", kind="negotiations")
            else:
                storage.save_json(case_id, f"negotiations/{dealer_id}.json", {
                    "dealer_id": dealer_id,
                    "dealer_name": dealer.get("name", ""),
                    "conversation_id": conversation_id,
                    "transcript_path": f"transcripts/{dealer_id}_{conversation_id}.txt",
                    "opening_otd_usd": quote.get("otd_total_usd"),
                    **outcome,
                })
                if outcome.get("agent_fabricated_leverage"):
                    # Loud on purpose: this is the honesty guarantee failing, and it
                    # must never be discovered later in a demo.
                    log.error(
                        "HONESTY VIOLATION case=%s dealer=%s — agent cited leverage it "
                        "did not hold. Review %s",
                        case_id, dealer_id, f"transcripts/{dealer_id}_{conversation_id}.txt",
                    )
                    storage.log_event(case_id, "honesty_violation", dealer_id=dealer_id)
                log.info("nego recorded case=%s dealer=%s moved=%s final=%s",
                         case_id, dealer_id, outcome.get("price_moved"),
                         outcome.get("final_otd_usd"))

    start_next_nego_call(case_id)


# --- dispatch ---------------------------------------------------------------

def advance(case_id: str) -> dict:
    """Run whatever comes next for this case. Safe to call repeatedly."""
    case = storage.read_case(case_id)
    if not case:
        return {"error": "no such case"}
    status = case.get("status")

    if status == "intake_done":
        storage.set_status(case_id, "researching")
        research.build_call_list(case_id)
        storage.set_status(case_id, "calling_for_quotes")
        return start_next_quote_call(case_id)

    if status == "researching":
        research.build_call_list(case_id)
        storage.set_status(case_id, "calling_for_quotes")
        return start_next_quote_call(case_id)

    if status == "calling_for_quotes":
        return start_next_quote_call(case_id)

    if status == "quotes_collected":
        strategy.build(case_id)
        storage.set_status(case_id, "strategy_ready")
        return start_next_nego_call(case_id)

    if status in ("strategy_ready", "negotiating"):
        return start_next_nego_call(case_id)

    return {"status": status, "note": "nothing to advance"}
