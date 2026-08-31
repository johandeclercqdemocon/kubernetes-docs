"""pingd — the running example for the book.

Deliberately small, but with the properties that make containerisation interesting:
a dependency on an external service, configuration from the environment, a health
endpoint, structured logging to stdout, and a real graceful-shutdown path.
"""

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

# Structured logs to stdout. Never to a file: see Chapter 27.
logging.basicConfig(
    stream=sys.stdout,
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(message)s",
)
log = logging.getLogger("pingd")


def emit(event: str, **fields) -> None:
    log.info(json.dumps({"event": event, "ts": time.time(), **fields}))


STARTED_AT = time.time()
VERSION = os.environ.get("PINGD_VERSION", "dev")
GREETING = os.environ.get("PINGD_GREETING", "pong")
DATABASE_URL = os.environ.get("DATABASE_URL")

_shutting_down = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    emit("startup", version=VERSION, database=bool(DATABASE_URL))
    yield
    # Chapter 26: this runs on SIGTERM, but only if the process actually
    # receives SIGTERM — which requires it to be PID 1 (exec-form CMD) or to
    # have an init that forwards signals.
    #
    # Note what is deliberately NOT here: a signal.signal(SIGTERM, ...) call.
    # Uvicorn installs its own SIGTERM handler; registering ours on top would
    # replace it, the server would never be told to stop, and every `docker
    # stop` would take the full 10-second grace period and end in SIGKILL.
    # Chapter 26 measures exactly that failure.
    global _shutting_down
    _shutting_down = True
    emit("shutdown", uptime_s=round(time.time() - STARTED_AT, 3))


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"service": "pingd", "version": VERSION, "reply": GREETING}


@app.get("/healthz")
def healthz(response: Response):
    """Liveness. Cheap, no dependencies — see Chapter 26 on why."""
    if _shutting_down:
        response.status_code = 503
        return {"status": "draining"}
    return {"status": "ok", "uptime_s": round(time.time() - STARTED_AT, 3)}


@app.get("/readyz")
def readyz(response: Response):
    """Readiness. Checks dependencies, unlike liveness."""
    if DATABASE_URL is None:
        response.status_code = 503
        return {"status": "no database configured"}
    return {"status": "ready"}


@app.get("/slow")
def slow(seconds: float = 5.0):
    """An in-flight request to interrupt, for the shutdown experiments."""
    time.sleep(min(seconds, 60))
    return {"slept": seconds}


@app.get("/burn")
def burn(seconds: float = 5.0):
    """CPU load, for the cgroup throttling experiments in Chapter 21."""
    deadline = time.monotonic() + min(seconds, 60)
    n = 0
    while time.monotonic() < deadline:
        n += 1
    return {"iterations": n}


_held: list[bytearray] = []


@app.get("/eat")
def eat(mb: int = 64):
    """Allocate and hold memory, for the OOM experiments in Chapter 21."""
    _held.append(bytearray(min(mb, 4096) * 1024 * 1024))
    return {"held_mb": sum(len(b) for b in _held) // (1024 * 1024)}
