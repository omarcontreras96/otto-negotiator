"""Platform probes — the GTM instrument.

Maritime's docs leave four things undocumented that decide whether a voice-agent
company can host here. Each probe below answers exactly one of them, and the
answers go into docs/maritime-voice-findings.md.

  P1  arbitrary routes   Is anything beyond /health + /chat reachable on the
                         public URL? Voice vendors POST call results to a URL you
                         choose; if only /chat is routable, every integration has
                         to be tunnelled through one endpoint.
  P2  websockets         Can a socket be terminated here? Decides whether media
                         streaming (Twilio Media Streams, ElevenLabs custom-LLM
                         streaming) is even on the table.
  P3  snapshot/resume    Docs say the process is "snapshotted and resumed, not
                         restarted". If true, in-process state survives sleep and
                         the only cost of sleeping is wake latency. Proven by
                         comparing monotonic uptime against wall-clock drift.
  P4  latency            Warm round-trip and cold-start wake. The number that
                         decides whether Maritime can sit on a call's critical
                         path at all.

Probes are read-only, cheap, and safe to leave deployed.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .config import settings

router = APIRouter(prefix="/probe", tags=["probe"])

# Captured at import, i.e. at process start. If the process is snapshotted and
# resumed, these survive a sleep/wake cycle; if it is restarted, they reset.
BOOT_MONOTONIC = time.monotonic()
BOOT_WALL = time.time()
BOOT_ID = os.urandom(8).hex()

_CAPTURE_MAX = 50


# --- P1: arbitrary route reachability --------------------------------------

@router.get("/echo")
async def echo(request: Request) -> dict:
    """If this responds on the public URL, arbitrary GET routes are reachable."""
    return {
        "probe": "P1-arbitrary-route",
        "ok": True,
        "path": request.url.path,
        "query": dict(request.query_params),
        "headers_seen": sorted(request.headers.keys()),
        "boot_id": BOOT_ID,
    }


@router.post("/echo")
async def echo_post(request: Request) -> dict:
    """Same, for POST — the method voice vendors actually use for webhooks."""
    raw = await request.body()
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    return {
        "probe": "P1-arbitrary-route-post",
        "ok": True,
        "bytes": len(raw),
        "parsed_json": parsed is not None,
        "boot_id": BOOT_ID,
    }


# --- P2: websocket support --------------------------------------------------

@router.websocket("/ws")
async def ws_echo(websocket: WebSocket) -> None:
    """Echo socket. Connecting at all is the finding; RTT per frame is the bonus.

    A voice media stream is ~50 frames/sec of 20ms audio, so per-frame overhead
    matters as much as connect success.
    """
    await websocket.accept()
    await websocket.send_json({"probe": "P2-websocket", "ok": True, "boot_id": BOOT_ID})
    try:
        while True:
            msg = await websocket.receive_text()
            await websocket.send_json({"echo": msg, "server_ns": time.perf_counter_ns()})
    except WebSocketDisconnect:
        return


# --- P3: snapshot/resume vs restart ----------------------------------------

@router.get("/uptime")
async def uptime() -> dict:
    """Distinguish snapshot/resume from restart, and measure how long we slept.

    monotonic_uptime counts only time the process was *running*. wall_uptime
    counts real elapsed time. If the process is resumed from a snapshot, both
    survive the sleep but wall_uptime jumps ahead of monotonic_uptime by roughly
    the sleep duration. If it is restarted, boot_id changes and both reset.

    Read boot_id first: a stable boot_id across a sleep/wake cycle IS the proof.
    """
    mono = time.monotonic() - BOOT_MONOTONIC
    wall = time.time() - BOOT_WALL
    return {
        "probe": "P3-snapshot-resume",
        "boot_id": BOOT_ID,
        "boot_wall_utc": datetime.fromtimestamp(BOOT_WALL, timezone.utc).isoformat(),
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "monotonic_uptime_s": round(mono, 3),
        "wall_uptime_s": round(wall, 3),
        # Time the process existed but was not scheduled — i.e. slept.
        "apparent_sleep_s": round(wall - mono, 3),
        "pid": os.getpid(),
    }


# --- P4: latency ------------------------------------------------------------

@router.get("/ping")
async def ping() -> dict:
    """Smallest possible handler. Client-side RTT to this = platform overhead.

    Call it once after a sleep for cold-start wake latency, then repeatedly for
    the warm baseline. The delta is what sleep-when-idle actually costs.
    """
    return {"probe": "P4-latency", "t_server_ns": time.perf_counter_ns(), "boot_id": BOOT_ID}


@router.get("/work")
async def work(ms: int = Query(0, ge=0, le=5000)) -> dict:
    """Ping plus a controlled delay, to separate platform overhead from handler time."""
    if ms:
        await asyncio.sleep(ms / 1000)
    return {"probe": "P4-latency-work", "slept_ms": ms, "boot_id": BOOT_ID}


# --- Custom-LLM depth: OpenAI-compatible streaming --------------------------

@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> StreamingResponse:
    """OpenAI-compatible SSE endpoint — the deepest voice integration.

    ElevenLabs and Vapi both let you point an agent's model at your own
    OpenAI-compatible URL. If that URL is a Maritime agent, Maritime *is* the
    mind of the voice agent, and time-to-first-token lands directly in the
    caller's ear.

    `?echo=1` streams a canned reply with no model call, isolating Maritime's
    transport overhead from model latency. Without it, this proxies Anthropic so
    the number reflects a realistic end-to-end path.
    """
    body = await request.json()
    echo_only = request.query_params.get("echo") == "1"
    messages = body.get("messages", [])
    created = int(time.time())

    def chunk(delta: dict, finish: str | None = None) -> str:
        payload = {
            "id": f"chatcmpl-otto-{created}",
            "object": "chat.completion.chunk",
            "created": created,
            "model": body.get("model", "otto-probe"),
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    async def stream():
        # First token goes out immediately — the measurement that matters.
        yield chunk({"role": "assistant", "content": ""})

        if echo_only:
            last = next(
                (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
            for word in f"echo: {last}".split():
                yield chunk({"content": word + " "})
        else:
            from .llm import stream_text  # lazy: keeps the echo path dependency-free

            async for piece in stream_text(messages):
                yield chunk({"content": piece})

        yield chunk({}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Webhook payload capture ------------------------------------------------

def _capture_path():
    d = settings.data_dir / "probe"
    d.mkdir(parents=True, exist_ok=True)
    return d / "captures.jsonl"


@router.post("/capture")
async def capture(request: Request) -> dict:
    """Record whatever arrives, verbatim.

    Pointed at by Maritime's own POST /api/webhooks/{agent_id} to discover what
    that endpoint actually delivers to a custom container — undocumented, and it
    decides whether a voice vendor's webhook can be aimed at Maritime directly
    or needs a shim.
    """
    raw = await request.body()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw.decode("utf-8", errors="replace")

    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.url.path,
        "headers": dict(request.headers),
        "body": parsed,
    }
    with _capture_path().open("a") as f:
        f.write(json.dumps(record) + "\n")
    return {"probe": "capture", "ok": True, "bytes": len(raw)}


@router.get("/captures")
async def captures() -> dict:
    """Read back what /capture recorded — including across a sleep/wake cycle,
    which doubles as proof that /data really is persistent."""
    path = _capture_path()
    if not path.exists():
        return {"count": 0, "captures": []}
    lines = path.read_text().splitlines()[-_CAPTURE_MAX:]
    return {
        "count": len(lines),
        "captures": [json.loads(x) for x in lines],
        "persisted_at": str(path),
    }
