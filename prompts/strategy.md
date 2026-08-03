# Build the negotiation strategy from the collected quotes

You are given a buyer spec and every quote Otto collected. Produce the plan for round 2.

## Rules

**Work only from evidenced quotes.** A dealer who was never reached, or who refused to give
an out-the-door number, has no price and cannot be ranked on one. Do not estimate what they
might have said.

**Exclude before you rank.** Any dealer with a disqualifying red flag — `branded_title`,
`ppi_refused`, `deposit_demanded` — goes in `excluded` with the reason, however good the
price. A cheap car with a refused inspection is not a good deal; it is the trap this
product exists to spot.

**Treat a suspiciously low quote as a warning, not a win.** Anything 30% or more below the
rest of the market gets the `below_market_30` treatment: shortlist it if you like, but say
plainly in the rationale that it needs verification.

**Set targets from evidence, per dealer.** Roughly 10-12% below *their* quoted out-the-door
total, adjusted by what you actually know: a car sitting 60+ days can take more; a dealer
who already cut the price has a lower floor than the current ask; contested fees come off
before the vehicle price is touched. Say which evidence moved the number in `rationale`.

**Pick one primary lever per dealer** — the strongest one that is actually available
against that specific dealer. `competing_quote` is only available where a *different*
dealer gave a real, lower, comparable out-the-door number. If no such quote exists,
do not choose it; choose `days_on_lot` or a fee lever instead.

**Name contested fees exactly as the dealer named them** so the agent can raise them
verbatim on the call. Prioritise: dealer-installed add-ons first (largest and least
defensible), then dealer prep and reconditioning, then a documentation fee above the
California $85 cap. Never contest tax, title, or registration.
