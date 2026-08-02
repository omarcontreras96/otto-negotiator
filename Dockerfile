# Maritime custom-container contract — see maritime-platform-notes.md §3.
#   · bind 0.0.0.0:$PORT (injected; NEVER hardcode 8080 — collides with the VM port forwarder)
#   · GET /health -> 2xx, fast, side-effect-free
#   · POST /chat  -> reply within 30s
#   · persist only under /data
#   · CMD must launch a real program, NOT a shell string: micro-VM init flattens the CMD
#     to one string, quoting breaks, and the VM kernel-panics on boot.
FROM python:3.12-slim

# ca-certificates: slim images have none, and every outbound TLS call (ElevenLabs,
# Anthropic, Google) fails without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# NOTE: do NOT declare `VOLUME ["/data"]`. Maritime mounts its own persistent
# volume at /data; a VOLUME directive in the image made the micro-VM launch fail
# with `fc-manager 500 for /vms` after an otherwise clean build (2026-08-02).
CMD ["python", "start.py"]
