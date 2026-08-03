#!/usr/bin/env python3
"""Run the whole pipeline on canned transcripts — no phone calls, no minutes spent.

ElevenLabs' free tier is 15 minutes total. Debugging extraction, strategy, and
the report over real calls would consume it before the demo. This drives the
same code paths the webhook does, using three dealer transcripts written to
different negotiation styles:

    honda_sf     the stonewaller  — won't quote OTD by phone, pushes you to visit
    royal        the lowballer    — great headline number, fees appear later
    sf_toyota    the upseller     — fair price, aggressive dealer-installed add-ons

Usage:
    python scripts/simulate.py            # full run: quotes -> strategy -> nego -> report
    python scripts/simulate.py --quotes   # stop after the quote round
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="otto-sim-"))
# Never let a simulation reach the telephony layer.
os.environ["DEMO_MODE"] = "true"
os.environ["AUTO_ADVANCE"] = "false"

from app import extraction, report, storage, strategy  # noqa: E402

SPEC = {
    "buyer_name": "Omar",
    "vehicle": {"make": "Honda", "model": "Civic", "year_min": 2020, "year_max": 2022,
                "trim": "EX", "max_mileage": 60000},
    "must_haves": ["clean title", "independent inspection allowed"],
    "nice_to_haves": ["sunroof", "any colour but white"],
    "constraints": {"market": "San Francisco Bay Area", "max_travel_miles": 30,
                    "timeline": "within two weeks", "payment_method": "preapproved",
                    "preapproved": True, "has_trade_in": False},
    "market_reference_usd": 24500,
    # Present on purpose: the simulation asserts this never reaches a dealer prompt.
    "buyer_stated_maximum_usd": 27000,
    "confirmed_by_user": True,
}

QUOTE_CALLS = {
    "honda_sf": ("San Francisco Honda", """
OTTO: Hi, this is an AI assistant calling on behalf of a buyer named Omar. I'm gathering an out-the-door quote on a used 2020 to 2022 Honda Civic EX under 60,000 miles. I can't buy anything, I'm just collecting numbers so Omar can compare a few cars. Are you the right person, or should I hold for someone in sales?
DEALER: You're an AI? Huh. Yeah, I'm in sales, what do you need.
OTTO: Thanks. Just so you know, this call is being recorded — is that alright?
DEALER: Sure, whatever.
OTTO: Appreciate it. Do you have a 2020 to 2022 Civic EX on the lot right now?
DEALER: We've got a 2021 EX, silver, 41,000 miles. Clean Carfax, one owner, came in as a lease return.
OTTO: That's helpful. Out of curiosity, roughly how long has that one been sitting?
DEALER: I'd have to look. A while, maybe two months? It came in over the summer.
OTTO: Understood. Would you allow an independent pre-purchase inspection at a shop of Omar's choosing?
DEALER: Yeah, that's fine, people do that.
OTTO: Great. Before we talk numbers, can you give me the full out-the-door breakdown, itemised? Vehicle price, doc fee, any dealer add-ons, tax, title and registration, each on its own line.
DEALER: I don't do that over the phone. Come in and we'll work up the numbers.
OTTO: I understand. The thing is, Omar is comparing a few cars this week and can only visit the ones with numbers on paper. If you can send the itemised total, this car stays in the running. If not, that's completely fine, and I'll let them know.
DEALER: Look, it's listed at 23,995. I can't give you a total without running it. Come in.
OTTO: Understood, I'll pass that along. Thanks for your time.
"""),
    "royal": ("Royal Motor Sales", """
OTTO: Hi, this is an AI assistant calling on behalf of a buyer named Omar. I'm gathering an out-the-door quote on a used Honda Civic EX, 2020 to 2022, under 60,000 miles. I'm not able to complete a purchase — just collecting numbers. Are you the right person?
DEALER: Sure, I can help. We've got a 2020 Civic EX right now.
OTTO: Is that on the lot and available today?
DEALER: It is. 2020 EX, 52,000 miles, dark grey.
OTTO: And the title status — clean, or is it branded in any way?
DEALER: Clean title. It was a fleet vehicle, so a couple of small dings, nothing structural.
OTTO: Got it. Roughly how long has it been on the lot?
DEALER: Oh, that one's been here since March. Almost five months. We've already knocked twelve hundred off it.
OTTO: That's useful, thank you. Would you allow an independent pre-purchase inspection?
DEALER: Absolutely, no problem.
OTTO: Great. Can you give me the full out-the-door breakdown, itemised? Vehicle price, doc fee, any add-ons, tax, title and registration.
DEALER: Sure. Vehicle is 20,400. Doc fee is 85 — that's the California cap. Then there's a dealer prep of 495, tax works out to about 1,836, title and reg is 385. So call it 23,201 out the door.
OTTO: Thank you, that's exactly what I needed. Just to confirm, the 495 dealer prep — is that reconditioning work already done?
DEALER: Yeah, detail, safety inspection, that kind of thing.
OTTO: Understood. And is that price contingent on financing through you?
DEALER: It's better if you finance with us but no, that's the price.
OTTO: Perfect. Can you send that itemised in writing with the stock number and VIN?
DEALER: I can text it over, sure.
OTTO: Much appreciated. Thanks for your time.
"""),
    "sf_toyota": ("San Francisco Toyota", """
OTTO: Hi, this is an AI assistant calling on behalf of a buyer named Omar. I'm collecting an out-the-door quote on a used Honda Civic EX. Are you the right person to speak with?
DEALER: We're a Toyota store but we take trades, we've got a 2022 Civic EX on the used lot. Am I talking to a robot right now?
OTTO: Yes, I'm an AI assistant working for a real buyer. Omar is real and is ready to move quickly on the right car.
DEALER: Ha. Alright. What do you want to know.
OTTO: Is it on the lot today, and what's the mileage?
DEALER: It's here. 2022 EX, 28,000 miles. Clean title, no accidents, we've got the Carfax.
OTTO: How long has it been on the lot?
DEALER: Came in about three weeks ago. It's a fresh unit, we don't need to discount it.
OTTO: Understood. Would you allow an independent pre-purchase inspection?
DEALER: Sure.
OTTO: Great. Can you give me the itemised out-the-door total? Vehicle price, doc fee, any dealer add-ons, tax, title and registration.
DEALER: Vehicle's 24,900. Doc fee 85. Now, all our used cars come with the protection package — that's paint sealant and fabric protection, 895, and VIN etching at 295. Those are already on the car. Tax is 2,241, title and reg 385. Out the door you're at 28,801.
OTTO: Thank you. To be clear on two of those — the paint sealant and the VIN etching, at 1,190 combined. Omar didn't request those. Are they removable from the price?
DEALER: They're already installed, we can't un-install them.
OTTO: Understood, I'll note that. And what monthly payment range were you going to suggest?
DEALER: Well what were you looking to be at monthly?
OTTO: Omar isn't shopping payments, only the out-the-door total. Thank you for the itemisation — can you send that in writing?
DEALER: I'll email it.
OTTO: Appreciated. Thanks for your time.
"""),
}

NEGO_CALLS = {
    "royal": """
OTTO: Hi, this is Otto — the AI assistant calling back for Omar about the 2020 Civic EX. I've now got out-the-door numbers from a few stores and Omar would like to give you a chance at it. Do you have two minutes?
DEALER: Go ahead.
OTTO: Thanks. Your total came to 23,201 out the door. Given that car has been on your lot since March — nearly five months — and you've already come down twelve hundred, Omar is at 21,400 out the door.
DEALER: That's too low. I can't do that.
OTTO: I understand there's a floor. What's the closest you can get?
DEALER: Let me talk to my manager. ... Okay, I can do 22,700.
OTTO: I appreciate you checking. Two things on the breakdown — the 495 dealer prep is reconditioning work that was done before Omar existed as a buyer, and it's already reflected in a full retail asking price. If that comes off, and we're at 22,200, Omar can move this week.
DEALER: I can take the prep off. That puts us at 22,706 — call it 22,700.
OTTO: So the prep comes off but the total is the same. Let's do it properly: 23,201 minus the 495 prep is 22,706. Omar is at 22,200, and that's with the inspection staying as a contingency.
DEALER: ... Alright. 22,400 and that's genuinely where I stop.
OTTO: That works. Can you send that to me in writing, itemised, with the stock number and VIN on it, noting it's valid through the end of the week and contingent on an independent inspection? Omar will take it from there.
DEALER: I'll text it over tonight.
OTTO: Thank you, appreciate you working with me on it.
""",
    "sf_toyota": """
OTTO: Hi, this is Otto, the AI assistant calling back for Omar about the 2022 Civic EX. I've got out-the-door numbers from a few stores now. Do you have a couple of minutes?
DEALER: Sure.
OTTO: Your total came to 28,801. I now hold a verified out-the-door quote of 22,400 from Royal Motor Sales on a 2020 Civic EX, captured on a recorded call. I know yours is a newer car with lower miles, so I'm not asking you to match that. What I am asking about is the 1,190 of paint sealant and VIN etching that Omar didn't request.
DEALER: I told you, those are already installed.
OTTO: Understood. Then rather than removing the items, take the equivalent off the vehicle price and we're at the same place. Omar is at 27,400 out the door.
DEALER: I can do 28,200.
OTTO: Omar didn't consent to those add-ons in writing. Either the charge comes off or the vehicle price adjusts — those are the same thing to the buyer. At 27,600 with the inspection as a contingency, this is a live deal today.
DEALER: Best I can do is 27,900, and I'll throw in the first service.
OTTO: I'll take that back to Omar. Can you put 27,900 in writing, itemised, with the free first service noted, and confirm the independent inspection is fine?
DEALER: Yeah, I'll email it.
OTTO: Thank you.
""",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotes", action="store_true", help="stop after the quote round")
    args = ap.parse_args()

    case_id = storage.new_case(source="simulation")
    storage.save_json(case_id, "buyer_spec.json", SPEC)
    storage.save_json(case_id, "dealers.json", [
        {"id": k, "name": v[0], "address": "San Francisco, CA", "phone": "", "source": "simulation"}
        for k, v in QUOTE_CALLS.items()
    ])
    print(f"case {case_id}  (data in {storage.case_dir(case_id)})\n")

    print("── quote round ──")
    for did, (name, transcript) in QUOTE_CALLS.items():
        storage.save_text(case_id, f"transcripts/{did}_sim.txt", transcript)
        q = extraction.extract_quote(transcript, name, "2020-2022 Honda Civic EX")
        storage.save_json(case_id, f"quotes/{did}.json",
                          {"dealer_id": did, "dealer_name": name,
                           "conversation_id": "sim", **q})
        otd = q.get("otd_total_usd")
        print(f"  {name:26s} otd={otd if otd else '—':>9}  outcome={q.get('outcome'):16s} "
              f"days={q.get('days_on_lot')}  flags={','.join(q.get('red_flags') or []) or '—'}")

    if args.quotes:
        return

    print("\n── strategy ──")
    plan = strategy.build(case_id)
    print(f"  {plan.get('market_read')}")
    print(f"  shortlist: {plan.get('shortlist')}")
    for p in plan.get("per_dealer", []):
        print(f"    {p['dealer_id']:12s} target={p.get('target_otd_usd')} "
              f"lever={p.get('primary_lever')} fees={p.get('contested_fees')}")

    print("\n── leverage check (what each dealer would actually hear) ──")
    for did in QUOTE_CALLS:
        q = storage.read_json(case_id, f"quotes/{did}.json") or {}
        line = strategy.competing_disclosure(case_id, did, q.get("otd_total_usd"))
        print(f"  {did:12s} {line[:110]}")
        assert "27000" not in line and "27,000" not in line, \
            "LEAK: the buyer's private maximum reached a dealer-facing string"

    print("\n── negotiation round ──")
    for did, transcript in NEGO_CALLS.items():
        name = QUOTE_CALLS[did][0]
        q = storage.read_json(case_id, f"quotes/{did}.json") or {}
        storage.save_text(case_id, f"transcripts/{did}_nego_sim.txt", transcript)
        o = extraction.extract_outcome(transcript, name, q.get("otd_total_usd"))
        storage.save_json(case_id, f"negotiations/{did}.json",
                          {"dealer_id": did, "dealer_name": name,
                           "conversation_id": "sim",
                           "opening_otd_usd": q.get("otd_total_usd"), **o})
        print(f"  {name:26s} final={o.get('final_otd_usd')}  moved={o.get('price_moved')} "
              f"({o.get('movement_usd')})  via={o.get('what_moved_them')}")
        if o.get("agent_fabricated_leverage"):
            print(f"    ⚠ HONESTY FLAG raised on {did}")

    print("\n── report ──")
    md = report.generate(case_id)
    print(md)


if __name__ == "__main__":
    main()
