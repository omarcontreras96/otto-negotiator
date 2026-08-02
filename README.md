# Otto — used-car negotiation agent

Calls used-car dealers, collects itemised out-the-door quotes, negotiates them
down with real competing offers, and reports back a ranked recommendation.
Otto is never authorised to buy a car.

**Brain:** Maritime (serverless container, persistent `/data`)
**Voice:** ElevenLabs Agents (telephony, STT/TTS, turn-taking)
**Reasoning:** Claude

Built at SundAI SF Hack, 2 Aug 2026. Doubles as a measured evaluation of
Maritime as a host for voice-agent workloads — see `docs/maritime-voice-findings.md`.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health` | Maritime liveness + config snapshot |
| `POST /chat` | Maritime front door (natural language) |
| `GET /` | dashboard |
| `GET /cases/{id}` | full case state |
| `GET /cases/{id}/report` | ranked recommendation |
| `/probe/*` | platform instrumentation (see `app/probes.py`) |

## Safety

`DEMO_MODE=true` routes every outbound call to a number in `DEMO_TARGETS` while
keeping the real dealer's name and address on the record. No real dealership is
dialed unless demo mode is explicitly turned off.
