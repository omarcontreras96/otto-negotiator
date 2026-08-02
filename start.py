"""Entrypoint. Launched directly by the Dockerfile CMD (never via a shell string).

Reads the PORT Maritime injects (18789 in micro-VMs today) and binds 0.0.0.0.
The 8080 default is for local `python start.py` only — Maritime always injects PORT.
"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        # Maritime snapshots and resumes the process rather than restarting it, so
        # keep a single worker: forked workers do not survive a snapshot cleanly.
        workers=1,
        log_level=os.getenv("LOG_LEVEL", "info"),
    )
