"""The ranked recommendation the buyer actually reads."""

from __future__ import annotations

import json
import logging

from . import llm, storage

log = logging.getLogger("otto.report")


def _money(x: object) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "—"


def build_facts(case_id: str) -> dict:
    """Everything the report may cite, assembled in code rather than by the model.

    The model writes prose over these facts; it never sources its own numbers.
    """
    spec = storage.read_json(case_id, "buyer_spec.json") or {}
    quotes = {q["dealer_id"]: q for q in storage.list_json(case_id, "quotes") if q.get("dealer_id")}
    negos = {n["dealer_id"]: n for n in storage.list_json(case_id, "negotiations") if n.get("dealer_id")}
    plan = storage.read_json(case_id, "strategy.json") or {}

    rows = []
    for did, q in quotes.items():
        n = negos.get(did, {})
        first = q.get("otd_total_usd")
        final = n.get("final_otd_usd")
        best = final if isinstance(final, (int, float)) else first
        rows.append({
            "dealer_id": did,
            "dealer_name": q.get("dealer_name") or did,
            "address": q.get("dealer_address", ""),
            "vehicle": q.get("vehicle_described"),
            "reached": q.get("reached", False),
            "first_otd_usd": first,
            "final_otd_usd": final,
            "best_otd_usd": best,
            "moved_usd": (first - final) if isinstance(first, (int, float))
                          and isinstance(final, (int, float)) else None,
            "what_moved_them": n.get("what_moved_them"),
            "price_moved": n.get("price_moved", False),
            "concessions_won": n.get("concessions_won", []),
            "fees_removed": n.get("fees_removed", []),
            "red_flags": q.get("red_flags", []),
            "days_on_lot": q.get("days_on_lot"),
            "ppi_permitted": q.get("ppi_permitted"),
            "quote_outcome": q.get("outcome"),
            "nego_outcome": n.get("outcome"),
            "evidence": (q.get("evidence") or []) + (n.get("evidence") or []),
            "honesty_flag": n.get("agent_fabricated_leverage", False),
        })

    priced = [r for r in rows if isinstance(r["best_otd_usd"], (int, float))]
    priced.sort(key=lambda r: r["best_otd_usd"])
    unpriced = [r for r in rows if r not in priced]

    total_moved = sum(r["moved_usd"] or 0 for r in rows)
    return {
        "case_id": case_id,
        "buyer_spec": spec,
        "market_read": plan.get("market_read"),
        "ranked": priced,
        "no_price": unpriced,
        "dealers_called": len(rows),
        "dealers_reached": sum(1 for r in rows if r["reached"]),
        "dealers_with_otd": len(priced),
        "total_negotiated_saving_usd": total_moved or None,
        "spread_usd": (priced[-1]["best_otd_usd"] - priced[0]["best_otd_usd"])
                      if len(priced) > 1 else None,
        "honesty_violations": [r["dealer_id"] for r in rows if r["honesty_flag"]],
    }


def generate(case_id: str) -> str:
    """Write report.md. Falls back to a plain table if the model is unavailable —
    a report that exists beats a beautiful one that failed to render."""
    facts = build_facts(case_id)
    try:
        md = llm.generate(
            llm.load_prompt("report"),
            "FACTS (every number you may cite is here; invent nothing):\n"
            + json.dumps(facts, indent=2, default=str),
            max_tokens=3000,
        )
    except Exception as e:
        log.warning("report generation failed (%s) — writing the fallback table", e)
        md = _fallback(facts)

    storage.save_text(case_id, "report.md", md)
    storage.log_event(case_id, "report_written", chars=len(md))
    return md


def _fallback(facts: dict) -> str:
    lines = [
        f"# Otto report — case {facts['case_id']}",
        "",
        f"Called {facts['dealers_called']} dealers · reached {facts['dealers_reached']} · "
        f"{facts['dealers_with_otd']} gave an out-the-door number.",
        "",
        "| Dealer | First OTD | After negotiation | Moved | Flags |",
        "|---|---:|---:|---:|---|",
    ]
    for r in facts["ranked"]:
        lines.append(
            f"| {r['dealer_name']} | {_money(r['first_otd_usd'])} | "
            f"{_money(r['final_otd_usd'])} | {_money(r['moved_usd'])} | "
            f"{', '.join(r['red_flags']) or '—'} |"
        )
    for r in facts["no_price"]:
        lines.append(f"| {r['dealer_name']} | — | — | — | {r['quote_outcome'] or 'no price'} |")

    if facts["ranked"]:
        best = facts["ranked"][0]
        lines += ["", f"**Best out-the-door: {_money(best['best_otd_usd'])} at "
                      f"{best['dealer_name']}.**"]
    lines += [
        "",
        "Still yours to do: get the quote in writing and itemised, book an independent "
        "pre-purchase inspection, and read the buyer's order line by line against the "
        "agreed number before signing.",
    ]
    return "\n".join(lines)
