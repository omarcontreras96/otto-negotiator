#!/usr/bin/env bash
# Start Otto locally + a public tunnel, and print the webhook URL to paste into
# ElevenLabs. Cloudflare quick tunnels need no account.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8099}"
pkill -f "start.py" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

PORT=$PORT .venv/bin/python start.py > /tmp/otto.log 2>&1 &
echo "otto starting on :$PORT ..."
sleep 3

cloudflared tunnel --url "http://localhost:$PORT" > /tmp/otto-tunnel.log 2>&1 &
echo "tunnel starting ..."
for i in $(seq 1 30); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/otto-tunnel.log | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done

if [ -z "${URL:-}" ]; then
  echo "tunnel failed to come up; see /tmp/otto-tunnel.log"; exit 1
fi

# Otto needs to know its own public base for anything it generates.
python3 - "$URL" <<'PY'
import re, sys, pathlib
url = sys.argv[1]
p = pathlib.Path(".env")
t = p.read_text()
t = re.sub(r"^BASE_URL=.*$", f"BASE_URL={url}", t, flags=re.M)
p.write_text(t)
PY

cat <<EOF

  Otto is up.

  local      http://localhost:$PORT
  public     $URL
  dashboard  $URL/

  Paste this into ElevenLabs -> Settings -> Webhooks (post-call):
      $URL/webhooks/elevenlabs

  logs:  tail -f /tmp/otto.log
EOF
