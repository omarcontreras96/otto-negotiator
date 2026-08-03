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

### F-3 · Micro-VM launch is failing platform-wide: `fc-manager 500 for /vms` 🔴 **BLOCKING**

Observed 2026-08-02, ~14:47–14:58 PDT. The Docker build succeeds **completely** — image built,
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

## 3. Latency measurements

**Not yet collected — blocked by F-3.** No micro-VM has started, so there is nothing to measure.

The instrument is written and deployed-ready (`app/probes.py`), and will produce:

| Probe | Measures | Decides |
|---|---|---|
| `GET /probe/echo` | arbitrary routes reachable on the public URL? | can a voice vendor's webhook point at Maritime directly (D1) |
| `WS /probe/ws` | websocket termination + per-frame RTT | is media streaming possible at all (D4) |
| `GET /probe/uptime` | `boot_id` stability + wall-vs-monotonic drift across sleep | is it really snapshot/resume, and how long did it sleep (D2) |
| `GET /probe/ping` | warm RTT, and first-request-after-sleep RTT | **the number**: can Maritime sit on a call's critical path (D2) |
| `POST /probe/v1/chat/completions` | time-to-first-token, `?echo=1` isolating platform from model | viability of the deep integration (D3) |
| `POST /probe/capture` | what `POST /api/webhooks/{id}` actually delivers to a container | webhook shim needed or not (D1) |

---

## 4. Provisional read for GTM

Held loosely until §3 has numbers.

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

- [ ] F-3 resolved → collect every §3 measurement
- [ ] Cold-start wake latency after a real idle-sleep cycle (the headline number)
- [ ] Confirm `/data` survives sleep/wake with a real payload (`/probe/captures`)
- [ ] Establish what `POST /api/webhooks/{id}` delivers to a custom container
- [ ] Test ElevenLabs post-call webhook → Maritime public URL, end to end
- [ ] Ask the team: is an Anthropic-backed LLM proxy on the roadmap (F-6)?
</content>
