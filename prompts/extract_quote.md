# Extract a structured quote from a dealer call transcript

You are reading the transcript of a phone call between Otto (an AI assistant collecting
used-car prices) and a car dealership. Produce the structured record.

## Rules

**Record only what was actually said.** If a figure was never stated, the field is null.
Never infer a total from partial line items, never round, never fill a gap with a typical
value. A null is a correct answer; an invented number corrupts every comparison downstream
and is the specific failure this product exists to prevent.

**`otd_total_usd` is the out-the-door total** — everything the buyer pays to drive away.
If the dealer gave only a vehicle price, put that in `line_items.vehicle_price` and leave
`otd_total_usd` null. They are not the same number and must never be conflated.

**`reached` is false** when nobody was reached, the call went to voicemail, or the
transcript is empty — regardless of what else appears.

**Quote the dealer for evidence.** `evidence` holds short verbatim fragments from the
transcript supporting the numbers, so the final report can cite what was actually said.

**Outcome** is exactly one of:
- `quote_received` — an out-the-door number was given
- `otd_refused` — they would not give an out-the-door price by phone
- `callback_promised` — they committed to calling back
- `no_answer` — nobody reached
- `declined` — they would not engage
- `stalled` — the call ended without any of the above

**Red flags** — include an entry only where the transcript supports it:
- `branded_title` — salvage, rebuilt, lemon buyback, or otherwise branded
- `ppi_refused` — they would not allow an independent pre-purchase inspection
- `otd_refused` — no out-the-door number by phone
- `financing_contingent` — the price depends on dealer financing or a bundled product
- `payment_framing` — they steered to monthly payment instead of the total
- `deposit_demanded` — a deposit was required to hold the car or honour a price
- `addons_preinstalled` — dealer-installed extras already on the car

**Add-ons and fees.** Put every dealer-retained charge in `line_items.dealer_addons` with
its name and amount as stated. Note that California caps the documentation fee at $85 — if
a higher doc fee was quoted, record the real figure and set `doc_fee_over_ca_cap` true.
