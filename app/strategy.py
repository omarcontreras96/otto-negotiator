"""Turn collected quotes into a per-dealer negotiation plan.

Contains `competing_disclosure`, which is the single most important function in
this codebase. See its docstring.
"""

from __future__ import annotations

import json
import logging

from . import llm, storage

log = logging.getLogger("otto.strategy")

NO_COMPETING_QUOTE = (
    "No competing quote was captured. You have NO competitor price to cite on this "
    "call. Do not mention, imply, or estimate what any other dealer offered. "
    "Negotiate on days on lot, the fee line items, and the add-ons instead."
)


def _money(x: object) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else ""


def best_competing_quote(case_id: str, exclude_dealer_id: str) -> dict | None:
    """The lowest *evidenced* competitor quote, or None.

    Built only from quote records — never from the strategy's own LLM-authored
    prose, and never from market research. If no dealer actually gave us a
    comparable number, this returns None and the agent is told so explicitly.
    """
    best: dict | None = None
    for q in storage.list_json(case_id, "quotes"):
        if q.get("dealer_id") == exclude_dealer_id:
            continue  # their own price is not leverage against them
        price = q.get("otd_total_usd")
        if not q.get("reached") or not isinstance(price, (int, float)):
            continue
        if best is None or price < best["otd_total_usd"]:
            best = q
    return best


def competing_disclosure(case_id: str, exclude_dealer_id: str, current_otd: object = None) -> str:
    """The one sentence the agent may say about a competitor.

    The amount is embedded *inside* a complete sentence rather than passed as its
    own variable. This is deliberate and was learned the hard way: a template
    reading "(a verified quote of {{competing_quote_total}})" with that variable
    unset produced a sentence asserting a quote existed while showing a blank —
    and the agent filled the blank with the confidential target price.

    One variable that is either a complete true sentence or an explicit denial
    removes that failure mode entirely. There is no code path that yields a bare
    number here.
    """
    comp = best_competing_quote(case_id, exclude_dealer_id)
    if comp is None:
        return NO_COMPETING_QUOTE

    # A competitor's price is only leverage if it undercuts them. Citing a higher
    # rival number ("can you match their $26,000?" to someone quoting $24,000) is
    # nonsense that hands them the win, so treat it as having no leverage at all.
    if isinstance(current_otd, (int, float)) and comp["otd_total_usd"] >= current_otd:
        return NO_COMPETING_QUOTE

    name = comp.get("dealer_name") or "another dealer"
    amount = _money(comp["otd_total_usd"])
    vehicle = comp.get("vehicle_described") or "a comparable vehicle"
    return (
        f"You hold a real, verified out-the-door quote of {amount} from {name} on "
        f"{vehicle}, captured on a recorded call. You may cite this figure and offer "
        f"to have the buyer forward it."
    )


def build(case_id: str) -> dict:
    """Generate strategy.json from the quotes on file."""
    spec = storage.read_json(case_id, "buyer_spec.json") or {}
    quotes = storage.list_json(case_id, "quotes")
    reached = [q for q in quotes if q.get("reached")]

    if not reached:
        result = {
            "market_read": "No dealer was reached, so there is nothing to compare.",
            "shortlist": [],
            "excluded": [
                {"dealer_id": q.get("dealer_id", "?"), "reason": q.get("outcome", "not reached")}
                for q in quotes
            ],
            "per_dealer": [],
        }
        storage.save_json(case_id, "strategy.json", result)
        return result

    from .extraction import redact_private

    system = llm.load_prompt("strategy")
    schema = llm.load_schema("strategy")
    user = (
        "BUYER SPEC (private fields already removed):\n"
        + json.dumps(redact_private(spec), indent=2, default=str)
        + "\n\nQUOTES COLLECTED:\n"
        + json.dumps(quotes, indent=2, default=str)
    )
    result = llm.extract(schema, system, user, max_tokens=3000)
    storage.save_json(case_id, "strategy.json", result)
    storage.log_event(
        case_id, "strategy_built",
        shortlist=len(result.get("shortlist", [])),
        spread=result.get("spread_multiple"),
    )
    log.info("strategy built case=%s shortlist=%s", case_id, result.get("shortlist"))
    return result
