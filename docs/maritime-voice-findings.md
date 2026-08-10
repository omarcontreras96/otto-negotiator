# Can voice-agent startups host on Maritime?

**A measured evaluation, run while building a real voice-agent product on the platform.**

Author: Omar Contreras · SundAI SF Hack, 2 Aug 2026
Subject under test: Maritime (maritime.sh), CLI 1.5.1 → 1.7.0, Free/base project tier
Workload: **Otto**, a used-car negotiation agent — ElevenLabs Agents for voice, Maritime for the brain.

> **Why this is worth writing down.** Voice is the hardest hosting workload there is: it is
> latency-bound, stateful, long-lived, and it fails *audibly*. If Maritime can host it, it can host
> nearly anything. This doc records what actually happened, with timestamps, rather than what the
> docs promise.

---

## 0. The question, decomposed

"Can a voice-agent startup host on Maritime?" is really four questions, because a voice product has
four surfaces and only some of them are latency-critical:

| # | Surface | Maritime's role | Latency budget | Status |
|---|---|---|---|---|
| D1 | **Orchestrator** — decides who to call, holds case state, receives post-call webhooks | the whole backend | seconds; off the critical path | see §3 |
| D2 | **Mid-call tool** — agent calls out during the conversation (`log_quote`, `get_concession`) | tool endpoint | **<500 ms invisible, ~1 s tolerable** | see §3 |
| D3 | **Custom LLM** — Maritime serves an OpenAI-compatible `/chat/completions`; it *is* the agent's mind | model endpoint | **<500 ms to first token** | see §3 |
| D4 | **Media** — terminating the audio socket itself | websocket host | ~20 ms/frame | see §3 |

D1 is table stakes. D2 is the interesting middle. D3 is the deep integration Maritime would want to
own. D4 is almost certainly not Maritime's job and shouldn't be.

---

## 1. Findings log

### F-1 · Private GitHub repos fail to build, with a misleading error 🔴

Deploying from a **private** repo fails. The surfaced error is:

```
Deploy failed: Docker BuildKit CLI build failed with exit code 1
```

The real cause is only visible in `maritime history <agent> --json`:

```
remote: Write access to repository not granted.
fatal: unable to access 'https://github.com/…/otto-negotiator.git/': 403
ERROR: failed to solve: failed to read dockerfile: failed to load cache key
```

Maritime injects a GitHub credential that lacks read access to private repos. Making the repo public
resolved it immediately.

**Impact:** high. No startup ships from a public repo. This alone blocks the ICP until a GitHub App
install or deploy-key flow exists.
**Cheap fix:** surface the 403 in the CLI error rather than the generic BuildKit exit code, and
document the private-repo path.

### F-2 · `maritime deploy` after a failed first build tries to *pull* a nonexistent image 🟠

After F-1's build failure, a plain `maritime deploy otto` skipped building and went straight to pull:

```
Pulling image: maritime-agent-4c44633a...
Error: pull access denied for maritime-agent-4c44633a, repository does not exist
```

It never retried the build from the repo. Recovering required the explicit form:
`maritime deploy otto --source github --repo <url> --branch main`.

**Impact:** medium. The natural retry after any first-deploy failure is a dead end, and the error
points at a registry problem rather than at the original build failure.

### F-3 · Micro-VM launch failed platform-wide for ~5 hours: `fc-manager 500 for /vms` 🔴 **RESOLVED**

Observed 2026-08-02, ~14:47 PDT until ~19:50 PDT — roughly five hours during which no agent
could start. Recovered without action on our side; the same deploy command then succeeded. The Docker build succeeds **completely** — image built,
pushed to `ghcr.io/maritime-sh/maritime-agent-builds`, runtime image confirmed ready — and then the
micro-VM will not start:

```
Target runtime image ready (maritime-agent-4c44633a:69406727).
Starting container...
Injecting 10 env var(s): [...]
Error: fc-manager 500 for /vms: Internal Server Error
```

**Isolated to the platform, not the image.** A brand-new agent from the stock `zeroclaw` template —
no custom code involved at all — fails identically:

```
maritime create fc-probe --template zeroclaw
→ status: error
→ Error: fc-manager 500 for /vms: Internal Server Error
```

Ruled out along the way:
- not the private repo (F-1 was a separate, earlier failure; the build now succeeds)
- not `VOLUME ["/data"]` in the Dockerfile (removed; identical failure) — though see F-4
- not the custom container contract (a stock template fails the same way)

**Repro for the team:** `maritime create <anything> --template zeroclaw`, watch
`maritime history <agent> --json`.

### F-4 · Do not declare `VOLUME ["/data"]` in your Dockerfile ⚪️

Not the cause of F-3, but worth documenting: Maritime mounts its own persistent volume at `/data`,
so an image-level `VOLUME` directive is redundant and risks conflicting with the mount. Removed from
Otto's Dockerfile. Worth an explicit line in the custom-container docs, which currently say only
"persist under /data".

### F-5 · The CLI's `--port` default contradicts the docs ⚪️

`maritime create --help` says `--port <n>  Port your app listens on (exposed publicly; default 8080)`.
The custom-container docs say the opposite about 8080:

> "NEVER hardcode 8080, it collides with the VM port forwarder"

These can be reconciled (`--port` is what the router targets; the warning is about what your process
binds) but a developer reading both will bind the wrong port. Deploying with `--port 18789` set
`exposedPort: 18789` cleanly, matching the injected `PORT`.

### F-6 · Injected environment, observed 📗

Useful and undocumented — a custom container receives exactly these 10:

```
__MARITIME_EXPOSED_PORT   PORT
OPENAI_API_KEY            OPENAI_BASE_URL          (Maritime's metered LLM proxy)
MARITIME_AGENT_ID         MARITIME_BACKEND_URL     MARITIME_INTERNAL_TOKEN
MARITIME_TUNNEL_EDGE_URL  MARITIME_TUNNEL_TOKEN    MARITIME_COMPANION_URL
```

Note there is **no `ANTHROPIC_API_KEY`** and no way to opt the proxy into Anthropic — a team standardised
on Claude brings its own key and pays twice for the privilege of ignoring the bundled proxy.

### F-7 · Public URL format 📗

`--public` yields `https://api.maritime.sh/a/{agent-uuid}` — a path on the shared API host, not a
per-agent subdomain. Consequences worth knowing before you design around it: no custom domain in the
free path, everything is same-origin with the control plane, and the agent UUID is the public
identifier.

---

## 2. Build-time observations

The build pipeline itself is genuinely good, and this is a real selling point:

- On-demand EC2 BuildKit worker, discovered and ready in ~10 s
- Cold build of a FastAPI + Anthropic image: **~15 s** (`pip install` of 24 packages in 6.9 s)
- Warm rebuild seeded from the registry cache: **~6 s**, layer-delta push in 3 s
- Full `git push` → image-in-registry cycle: **under 30 seconds**

For comparison, that is faster than most teams' CI. If F-3 is transient, the developer loop here is
a strength worth marketing.

---

## 3. Latency measurements — COLLECTED

`fc-manager` recovered ~19:50 PDT. Otto deployed clean and every probe ran. Measured from a
laptop in San Francisco; the build worker reports `us-east-1`, so the agent is very likely
East Coast. All figures are end-to-end from the client, which is what a voice vendor's
webhook or tool call actually experiences.

### 3.1 Warm request latency

| | ms |
|---|---:|
| min | 279 |
| **p50** | **291** |
| p90 | 381 |
| max | 395 |
| **`api.maritime.sh/health` (control plane, same machine)** | **93** |

**Maritime's tunnel adds ~200 ms over the network floor** to every request. That is the
number to internalise: it is not geography, because the control-plane baseline travels the
same path.

### 3.2 Sleep and wake — the decisive result

Three separate behaviours, and the middle one is the problem:

| Behaviour | Result |
|---|---|
| `GET` the public URL while asleep | **HTTP 503 `"Agent is starting or asleep. Try again in a moment."`** — polled every 500 ms for **240 s** and it never woke. HTTP traffic to the public URL does **not** wake an agent. **Re-confirmed a week later** (2026-08-09): 69 consecutive 503s over 150 s. |
| `POST /api/webhooks/{agent_id}` while asleep | **HTTP 200 in 1.47 s**, wakes it (re-test a week later: 200 in 1.7 s) |
| public URL after that webhook | serving **0.38 s** later |

**Total cold wake ≈ 1.85 s**, and only via the webhook endpoint. The platform's own log
puts the snapshot resume itself at **1,196 ms** (`Agent woken (snapshot, 1196ms)`), so the
resume dominates and the delivery plumbing adds ~0.5 s.

### 3.3 Snapshot/resume — confirmed, and better than documented

| | before sleep | after wake |
|---|---|---|
| `boot_id` | `1638ed398b574be8` | `1638ed398b574be8` ✅ same |
| process | — | same pid |
| monotonic uptime | — | 613.197 s |
| wall uptime | — | 613.197 s |
| **apparent sleep** | — | **0.0 s** |

The process is genuinely resumed, not restarted — in-process state survives.
`/data` persisted across the cycle: the record written before sleep read back intact.

**Week-long durability (added 2026-08-09).** The same agent was left asleep for **7 days**
and woken: `boot_id` still `1638ed398b574be8` — the *same process*, resumed after a week —
and `/data` intact byte-for-byte. Wake was as fast as after a 10-minute nap. This is the
sleep/wake economics claim proven at its most demanding: an agent that costs ~nothing for a
week and resumes mid-thought in ~1.2 s.

**Clock correction to the earlier reading.** After the 7-day sleep, monotonic uptime =
wall uptime = ~603,000 s (≈7 days). So guest clocks are **advanced in lockstep on resume**,
not frozen (at short sleeps the two interpretations are indistinguishable, which is how the
first reading went wrong). Practical consequence: `wall − monotonic` drift is always zero,
so **a process cannot detect from inside that it slept**. Re-establish long-lived
connections on error, never on a clock heuristic — the docs' advice is right, and now we
know why.

### 3.4 Arbitrary routes and websockets

**Arbitrary routes work.** Every path served on the public URL, GET and POST:
`/probe/echo`, `/probe/uptime`, `/cases`, `/`, `POST /probe/echo`, `POST /chat` — all 200.
A voice vendor's webhook can point straight at a Maritime agent. No shim needed.

**Webhook delivery contract (established 2026-08-09).** What `POST /api/webhooks/{id}`
actually hands the container: the entire original body, JSON-serialised as a string, inside
a `/chat` call —

```json
{"message": "{\"message\": \"...\", \"custom_field\": \"does-this-arrive\"}",
 "source": "webhook", "webhook": true}
```

Custom fields survive intact. A custom container needs ~5 lines of routing
(`if source == "webhook": json.loads(message)`) and an ElevenLabs/Vapi post-call webhook
reaches a **sleeping** agent with no shim service. Combined with 3.2 this yields the one
integration rule that must be in the docs: **webhooks → `api.maritime.sh/api/webhooks/{id}`;
the public app URL 503s while asleep and never wakes.**

**WebSockets: blocked at the edge (added 2026-08-09).** A `wss://` upgrade against the
public URL is rejected **HTTP 403** before reaching the container, whose WS endpoint works
fine locally. Media termination (D4) is therefore confirmed impossible — no Twilio Media
Streams, no WebSocket custom-LLM transport. SSE over plain HTTP streams fine, so the
HTTP-streaming variant of D3 is unaffected.

Public URL format: `https://api.maritime.sh/a/{agent-uuid}`.

### 3.5 Bundled LLM proxy

Works, and it is a genuine cost story. `maritime_proxy_available: true` in-container, with
`OPENAI_API_KEY` + `OPENAI_BASE_URL` injected. A real streamed completion through it
returned in **1.43 s**. Otto's entire reasoning path — extraction, strategy, report — runs
on it with no key of our own and no metered LLM bill.

Transport-only SSE (`?echo=1`, no model call) delivered its first byte in **0.39 s**. That
is the floor for a custom-LLM integration: ~390 ms before the model has done any work.

### 3.6 Verdict per integration depth

| Depth | Budget | Measured | Verdict |
|---|---|---|---|
| **D1 Orchestrator** | seconds | 291 ms warm · 1.85 s cold via webhook | ✅ **Yes.** Arbitrary routes work, webhooks wake reliably, `/data` persists. This is a clean fit. |
| **D2 Mid-call tool** | <500 ms invisible, ~1 s tolerable | 291 ms warm (p90 381) · **1.85 s if asleep** | ⚠️ **Only with sleep disabled.** Warm is tolerable; a cold start is 1.85 s of silence mid-sentence. Requires `--idle 0`. |
| **D3 Custom LLM** | <500 ms to first token | **390 ms transport alone**, before the model | ❌ **No.** The tunnel overhead consumes the entire first-token budget. |
| **D4 Media** | ~20 ms/frame | **WS upgrade rejected, HTTP 403 at the edge** | ❌ **Confirmed impossible** — and shouldn't be Maritime's job anyway. |

---

## 3b. The other half of the stack also blocked us — and that is the real lesson

Maritime was not the only vendor that stopped this build. The telephony layer did too, and
the two failures together are the actual finding.

**Twilio blocked phone-number provisioning on both trial accounts**, at the policy layer:

| Call | Account 1 `AC0882f…` | Account 2 `ACdf9a7f…` |
|---|---|---|
| `GET /Accounts/{sid}.json` | 200 active, Trial | 200 active, Trial |
| `GET /IncomingPhoneNumbers.json` | 200 — **zero numbers** | 200 — **zero numbers** |
| `GET /AvailablePhoneNumbers/US/Local` | **401 policy evaluation failed** | **401 policy evaluation failed** |
| `GET /OutgoingCallerIds.json` | **401** | — |

Auth was valid throughout; reading the account worked. Only provisioning was refused. The
number shown as "Twilio trial number" in the console's Voice playground turned out to be
Twilio's own demo number for the guided tour, not a number owned by either account — so
ElevenLabs was being asked to import a number that did not exist, and surfaced Twilio's
policy error as an opaque HTTP 401.

**Time lost to this: roughly 90 minutes**, across two Twilio accounts, an ElevenLabs import
dialog that reused a stale stored credential without saying so, and a console that
displayed a number the account did not own.

### Why this belongs in a Maritime GTM doc

A voice-agent startup's first day involves **three vendors minimum** — telephony, voice
platform, and hosting — and *any one of them* can end the evaluation. Today, two of three
did, independently, for unrelated reasons. Neither failure was the developer's fault and
neither produced an error message that named its own cause.

That reframes what Maritime is competing on. The pitch is not "cheaper than Railway"; it is
**"the one part of your stack that does not stop you on day one."** For a workload where
the developer is already fighting a telephony vendor's fraud queue and a voice platform's
credential cache, the hosting layer's job is to be the boring, working part. Today it was
not: the private-repo failure (F-1) and the launch outage (F-3) put Maritime in the same
category as the vendors that blocked us.

The corollary for qualifying the ICP: **ask a prospect where they are in their telephony
setup before demoing.** A team that already has working PSTN and a voice vendor is a
qualified lead with a real hosting problem. A team that does not is still weeks from
caring what their agent runs on.

---

## 4. Read for GTM

### The one-line answer

**Yes — voice-agent startups can host on Maritime, for the agent's brain and state, with
sleep disabled.** Not for the model endpoint, and not for the audio.

### The three things to change

**1. Stop selling sleep to this segment. Sell resume.**
The pricing page leads with sleep-when-idle. For a voice startup that is a *liability*: a
sleeping agent returns **503**, and HTTP traffic to the public URL never wakes it — I polled
for four minutes. A voice vendor posting a call result to a sleeping agent loses the result.

But the underlying tech is genuinely impressive and under-sold: the process is **snapshotted
and resumed with zero clock drift** and in-process state intact. Lead with *that* — "your
agent keeps its memory across restarts" — and position always-on as the default tier for
latency-sensitive workloads rather than an upsell. Charging $20 to turn off the headline
feature is the wrong shape for this ICP.

**2. The ~200 ms tunnel tax is the ceiling on how deep the integration can go.**
291 ms warm against a 93 ms network floor means Maritime cannot be the model endpoint — 390 ms
of transport lands before the first token. If owning that deeper layer matters commercially,
the tunnel is the thing to optimise. If it does not, D1/D2 is still a real and sufficient
wedge.

**3. Fix F-1 before any voice startup trials the platform.**
Private repos failing to build, reported as a generic BuildKit error, ends an evaluation on
day one. No startup ships from a public repo.

### What is genuinely strong

- **Build pipeline** — `git push` → image in registry in **under 30 s**; warm rebuilds ~6 s.
  Faster than most teams' CI, and a legitimate headline.
- **Snapshot/resume** — verified, zero drift, same pid. Real engineering.
- **Bundled LLM proxy** — Otto's whole reasoning path ran on it for no extra cost. For a
  seed-stage team burning model spend, "hosting includes inference" is a sharper hook than
  $1/agent.
- **Arbitrary routes + wake-on-webhook** — the two things the D1 integration needs, both work.

### Qualifying question for the sales motion

**Ask where the prospect is on telephony before demoing.** A team with working PSTN and a
voice vendor has a real hosting problem you can solve today. A team without one is still
weeks from caring what their agent runs on — as this build demonstrated, at length (§3b).

---

## 4b. Original provisional read (written before the measurements, kept honest)

**The honest positioning is "Maritime hosts the agent's brain, not its ears."** Nothing in the
platform competes with ElevenLabs, Vapi, or Retell — and that is a feature, not a gap. Those vendors
own telephony, STT, TTS, and turn-taking; none of them wants to be your backend, and all of their
customers need one. That backend is stateful, bursty, idle most of the time, and currently sitting on
a Railway or Fly instance billed 24/7 to serve a few webhooks an hour. **That is a real and
well-shaped wedge**, and the sleep/wake economics are genuinely better for it.

Where the story gets harder is depth. Every step from D1 toward D3 buys tighter lock-in and a bigger
share of the workload, but each one also moves Maritime onto the critical path of a live phone call,
where a cold start is not a slow page load — it is dead air in a stranger's ear. The pricing page
sells sleep-when-idle as the headline benefit; for this ICP, sleeping is the thing they will pay to
turn off. If wake latency is materially above ~300 ms, the honest sell to a voice startup is
"always-on tier, $20/agent, and you still win on the developer loop" rather than "your agent sleeps
when idle."

**What today's session already says, regardless of the latency numbers:** the three failures above
(private repos, failed-deploy recovery, and a platform-wide launch outage) were all hit inside the
first hour by a developer following the documented happy path. For a design partner in a hackathon
that is a war story. For a voice startup evaluating hosts on a Tuesday, F-1 alone ends the trial
before latency ever gets measured.

---

## 5. Open items

- [x] F-3 resolved → every §3 measurement collected
- [x] Cold-start wake latency — **1.85 s**, and only via the webhook endpoint
- [x] `/data` survives sleep/wake — confirmed
- [x] `POST /api/webhooks/{id}` returns the agent object and wakes the VM in 1.47 s
- [x] Payload delivery confirmed (2026-08-09): full body arrives at `/chat`, stringified in
      `message`, with `source: "webhook"` — contract in §3.4
- [x] Websockets measured (2026-08-09): **403 at the edge** — §3.4, D4 impossible
- [x] Week-long sleep survival (2026-08-09): same process resumed after 7 days, `/data`
      intact — §3.3
- [ ] Test ElevenLabs post-call webhook → Maritime webhook URL, end to end (blocked: no
      phone number, §3b — Twilio policy hold on both trial accounts)
- [ ] Ask the team: is an Anthropic-backed LLM proxy on the roadmap (F-6)?
- [ ] Ask the team: is public-URL wake-on-request intended behaviour or a gap? The docs'
      "wake on the next message" reads as if it should wake (§3.2)
</content>
