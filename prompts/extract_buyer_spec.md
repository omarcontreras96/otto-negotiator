# Extract the buyer spec from an intake call transcript

You are reading a transcript of a call between Otto and a car buyer. Produce the
structured spec that will be reused verbatim on every dealer call.

## Rules

**Record only what the buyer said.** Null every field they did not address. Do not infer a
year range from a model, a mileage ceiling from a budget, or requirements from what buyers
"usually" want. An invented requirement makes quotes incomparable in a way nobody catches
until the report.

**Separate `must_haves` from `nice_to_haves` carefully.** `must_haves` is a hard filter and
is never traded away in negotiation; `nice_to_haves` are the only things Otto may concede
for savings. When the buyer is ambiguous, it is a nice-to-have — wrongly hardening a
preference costs the buyer money, while wrongly softening one is caught at the recommendation.

**`buyer_stated_maximum_usd` is private and load-bearing.** Fill it *only* if the buyer
volunteered a ceiling. It exists so that downstream code can guard one known field rather
than hope a number never appears in prose. It is never sent to a dealer-facing agent.
`market_reference_usd` is a different thing entirely: what comparable cars are *listed* at.

**Never put a private figure in `notes`.** If the buyer said "I can stretch to $28k",
that belongs in `buyer_stated_maximum_usd` and nowhere else.

**`confirmed_by_user`** is true only if the buyer heard a read-back summary and agreed to
it. If the call ended without confirmation, it is false and the spec is not call-ready.
