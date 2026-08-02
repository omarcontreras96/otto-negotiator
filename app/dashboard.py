"""Single-file dashboard. No build step, no CDN — it has to render inside the
container with nothing but stdlib, and a bundler is not worth the deploy risk."""

from __future__ import annotations

import html

from . import storage
from .config import settings

CSS = """
:root{color-scheme:light dark;--fg:#111;--dim:#666;--line:#e3e3e3;--bg:#fff;--accent:#0b6;--warn:#c40}
@media(prefers-color-scheme:dark){:root{--fg:#e8e8e8;--dim:#999;--line:#2a2a2a;--bg:#111}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem;background:var(--bg);color:var(--fg);
  font:15px/1.55 ui-sans-serif,-apple-system,system-ui,sans-serif;max-width:60rem;margin-inline:auto}
h1{font-size:1.4rem;margin:0 0 .25rem}
h2{font-size:1rem;margin:2rem 0 .5rem;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.sub{color:var(--dim);margin:0 0 1.5rem}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:.1rem .5rem;border:1px solid var(--line);border-radius:99px;font-size:12px}
.win{color:var(--accent);font-weight:600}
.flag{color:var(--warn)}
.empty{color:var(--dim);font-style:italic}
.wrap{overflow-x:auto}
code{font-size:13px}
"""


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _money(x) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "—"


def render() -> str:
    cases = storage.list_cases()
    case = cases[0] if cases else None
    body = [f"<h1>Otto</h1><p class='sub'>Used-car negotiation agent · "
            f"{'DEMO MODE — no real dealership is dialed' if settings.demo_mode else 'LIVE'}</p>"]

    if not case:
        body.append("<p class='empty'>No cases yet. Call the intake number, or "
                    "<code>POST /cases</code> to open one.</p>")
        return _page("".join(body))

    cid = case["case_id"]
    spec = storage.read_json(cid, "buyer_spec.json") or {}
    quotes = storage.list_json(cid, "quotes")
    negos = {n.get("dealer_id"): n for n in storage.list_json(cid, "negotiations")}

    body.append(
        f"<p><span class='pill'>{_esc(cid)}</span> "
        f"<span class='pill'>{_esc(case.get('status'))}</span></p>"
    )

    if spec:
        v = spec.get("vehicle", {})
        body.append("<h2>Looking for</h2><p>")
        body.append(
            " ".join(
                filter(
                    None,
                    [
                        _esc(v.get("year_min")) and f"{_esc(v.get('year_min'))}+",
                        _esc(v.get("make")),
                        _esc(v.get("model")),
                        _esc(v.get("trim")),
                    ],
                )
            )
            or "<span class='empty'>unspecified</span>"
        )
        if spec.get("target_otd_usd"):
            body.append(f" · target {_money(spec['target_otd_usd'])} out the door")
        body.append("</p>")

    # Quotes, ranked by the best number we actually hold for each dealer.
    body.append("<h2>Quotes</h2>")
    if not quotes:
        body.append("<p class='empty'>No calls completed yet.</p>")
    else:
        rows = []
        for q in quotes:
            n = negos.get(q.get("dealer_id"), {})
            final = n.get("final_otd_usd")
            initial = q.get("otd_total_usd")
            moved = (
                initial - final
                if isinstance(initial, (int, float)) and isinstance(final, (int, float))
                else None
            )
            rows.append((final if final is not None else initial, q, n, initial, final, moved))
        rows.sort(key=lambda r: (r[0] is None, r[0]))

        body.append(
            "<div class='wrap'><table><tr><th>Dealer</th><th>Vehicle</th>"
            "<th class='num'>First OTD</th><th class='num'>After negotiation</th>"
            "<th class='num'>Moved</th><th>Outcome</th></tr>"
        )
        for i, (_, q, n, initial, final, moved) in enumerate(rows):
            best = " win" if i == 0 and initial is not None else ""
            flags = q.get("red_flags") or []
            body.append(
                f"<tr><td class='{best.strip()}'>{_esc(q.get('dealer_name'))}"
                + (f"<br><span class='flag'>⚑ {_esc(', '.join(flags))}</span>" if flags else "")
                + f"</td><td>{_esc(q.get('vehicle_described') or '—')}</td>"
                f"<td class='num'>{_money(initial)}</td>"
                f"<td class='num'>{_money(final)}</td>"
                f"<td class='num'>{('−' + _money(moved)[1:]) if moved else '—'}</td>"
                f"<td>{_esc(n.get('outcome') or q.get('outcome') or '—')}</td></tr>"
            )
        body.append("</table></div>")

    if storage.read_text(cid, "report.md"):
        body.append(f"<h2>Report</h2><p><a href='/cases/{_esc(cid)}/report'>report.md</a></p>")

    return _page("".join(body))


def _page(inner: str) -> str:
    return f"<!doctype html><meta charset=utf-8><title>Otto</title><style>{CSS}</style>{inner}"
