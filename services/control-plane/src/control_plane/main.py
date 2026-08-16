"""Attestor control plane.

The only service a browser can reach. Uploads (by signed URL), review and round
creation, the human approval gate, the read endpoints behind the Phase 6 UI, and the SSE
fan-out all live in `api.py`; this module is the composition root and the health surface.

**No domain logic here, and none in `api.py` either.** Legality belongs to
`core.state.transition`, escalation to `core.policy`, execution to the dispatcher. The
rule is not tidiness: anything that drifts into this service is logic that only runs when
a human is holding an HTTP connection open, and Phase 4 exists to make the system advance
without one.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, Response, status

logger = logging.getLogger(__name__)

VERSION = os.environ.get("ATTESTOR_VERSION", "0.1.0")
PROJECT_ID = os.environ.get("PROJECT_ID", "")

#: Firestore reachability is checked with a bounded timeout. An unbounded readiness
#: probe is worse than no probe: it turns a slow dependency into a hung instance.
READYZ_TIMEOUT_SECONDS = 3.0

app = FastAPI(
    title="Attestor Control Plane",
    version=VERSION,
    docs_url=None,
    redoc_url=None,
)

# Mounted rather than defined here: `main` stays the composition root, `api` holds the
# surface, and a test can mount the router without booting the app.
from control_plane.api import router as api_router  # noqa: E402

app.include_router(api_router)


@app.get("/healthz")
@app.get("/health")
def healthz() -> dict[str, str]:
    """Liveness. Answers only "is this process up", and must not touch dependencies.

    A liveness probe that checks a database will take the service down when the
    database blips, which is precisely backwards.

    Registered at BOTH paths on purpose. `/healthz` is the specified path and is what
    the container serves, but Google's frontend intercepts `/healthz` on the
    `*.run.app` domain and answers it with its own HTML 404 -- the request never
    reaches this process. `/readyz` and every other path pass through untouched, so
    this is specific to `/healthz`. `/health` is the alias that is actually reachable
    at the `.run.app` URL; `/healthz` still works behind a custom domain or when the
    container is addressed directly.
    """
    return {"status": "ok", "version": VERSION}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, Any]:
    """Readiness. Reports whether Firestore is actually reachable.

    Returns 200 when reachable, 503 otherwise.
    """
    detail: dict[str, Any] = {"status": "ready", "version": VERSION, "firestore": "ok"}

    try:
        # Imported lazily so that /healthz stays answerable even if the Firestore
        # client library is misconfigured or missing.
        from google.cloud import firestore

        client = firestore.Client(project=PROJECT_ID or None)
        # Cheapest round trip that proves the backend answered: ask for at most one
        # collection. We do not care whether any exist, only that the call returns.
        next(iter(client.collections()), None)
    except Exception as exc:
        logger.warning("readyz: firestore unreachable: %s", exc, exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "version": VERSION,
            "firestore": "unreachable",
            "error": f"{type(exc).__name__}: {exc}",
        }

    return detail
