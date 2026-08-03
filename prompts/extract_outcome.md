# Extract the outcome of a negotiation call

You are reading a transcript of Otto negotiating with a dealership that had already quoted
a price. Produce the structured outcome.

## Rules

**`final_otd_usd` is the out-the-door total at the end of this call**, as actually stated.
Null if no number was restated. Never inferred from a discount percentage, never computed.

**`price_moved` is the central claim of this product, so hold it to a high bar.** It is
true only if the transcript shows the dealer stating a better number or granting a
concession *during this call*. A dealer saying "I could probably do a bit better" is not
movement. A dealer saying "I can do 24,800 instead of 25,400" is.

**`what_moved_them`** should name the lever visible in the transcript — the days-on-lot
point, the competing quote, a specific fee challenge, an add-on removal. If the dealer
moved without Otto applying a lever, say so plainly.

**`agent_fabricated_leverage` is an audit field, not a performance metric.** Set it true
if Otto cited any competing offer, deadline, or fact that it was not given — including
implying other dealers' prices vaguely ("others are lower") when it held no competing
quote. Judge strictly against the transcript. A true value here is a serious finding and
must be reported honestly; never soften it because the call otherwise went well.

**Evidence is required** for the final number and for any claimed movement. Quote the
transcript verbatim and briefly.
