"""The one place the control plane calls the dispatcher.

## Why this hop exists at all

The dispatcher holds the mailbox credential. The control plane is the service a browser can
reach, running `--allow-unauthenticated` behind a shared demo token, and it has never held a
Google credential of its own — `GET /inbox` reads the watch state out of Firestore precisely
so that stays true.

Phase 8 needs Connect Gmail to be a button. Registering a watch requires the credential, so
one of two things had to give: either the credential moves to the public service, or the
public service asks the private one. This is the second, and it is the right way round.
Widening the demo token from "can start work" to "holds a Google refresh token" is a real
posture change for a UI affordance; an authenticated call between two of our own services is
the shape Pub/Sub already uses to reach the dispatcher.

## How the call is authenticated

The dispatcher runs `--no-allow-unauthenticated`. The control plane mints a Google-signed
OIDC token for the dispatcher's URL as audience, from its own service account via the
metadata server, and sends it as a bearer token. Cloud Run verifies it before the request
reaches the process, and the control plane's service account needs `roles/run.invoker` on
the dispatcher — granted in `infra/deploy.sh` and nowhere else.

## Unreachable is not the same as disconnected

Every function here raises `DispatcherUnreachable` rather than returning a falsy default.
A Connections page that renders "Gmail: not connected" because the dispatcher was scaling
from zero would be the failure-impersonating-empty shape this codebase has now found nine
times, on the one page whose entire job is reporting whether something is connected.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

#: Resolved at deploy time and folded into the control plane's environment. Empty in local
#: development, which the caller reports as "cannot be managed from here" rather than
#: guessing at a URL.
DISPATCHER_URL_ENV = "DISPATCHER_URL"

#: A watch registration is three API calls deep — an IAM policy read, a subscription list,
#: and `users.watch` — behind a service that may be scaling from zero.
TIMEOUT_SECONDS = 25.0


class DispatcherUnreachable(Exception):
    """The dispatcher could not be asked. Distinct from anything it might have answered."""


def dispatcher_url() -> str:
    return os.environ.get(DISPATCHER_URL_ENV, "").strip().rstrip("/")


def _identity_token(audience: str) -> str:
    """A Google-signed OIDC token for one audience, from this service's own identity.

    Falls back to no token when the metadata server is absent, which is local development.
    A local dispatcher runs unauthenticated, so an absent token there is correct rather
    than a silent downgrade of a production check — on Cloud Run the metadata server is
    always present, so this branch cannot be reached in the deployment it protects.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        # google-auth ships `py.typed` but leaves this constructor unannotated, so
        # --strict reads it as an untyped call. Ignored at the call site rather than by
        # relaxing the rule for `google.*`, which would silence it for the annotated
        # clients too.
        return str(id_token.fetch_id_token(Request(), audience))  # type: ignore[no-untyped-call]
    except Exception as exc:  # the metadata server, or google-auth, is unavailable
        logger.info("no identity token for %s (%s); calling without one", audience, exc)
        return ""


def call(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    """One authenticated call to the dispatcher. Raises rather than degrading."""
    base = dispatcher_url()
    if not base:
        raise DispatcherUnreachable(
            f"{DISPATCHER_URL_ENV} is not set on this service, so the dispatcher's address "
            "is unknown."
        )
    url = f"{base}{path}"
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method)  # noqa: S310 - our URL
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    token = _identity_token(base)
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        # A 4xx from the dispatcher is an *answer*, not an outage, and its body carries the
        # reason a registration was refused. Returned to the caller to re-raise as itself.
        detail = exc.read().decode(errors="replace")
        raise DispatcherResponse(exc.code, detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DispatcherUnreachable(f"{url}: {exc}") from exc


class DispatcherResponse(Exception):
    """A non-2xx the dispatcher chose to send. Carries its status and its body."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail

    def payload(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.detail)
        except json.JSONDecodeError:
            return {"refusal": self.detail}
        return parsed if isinstance(parsed, dict) else {"refusal": self.detail}
