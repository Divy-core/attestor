"""Registering, checking and stopping the Gmail watch — the thing that makes email work.

## Why this is a module and not the body of a script

It was the body of a script. `tools/gmail_watch.py --apply` was the only way to turn the
inbound path on, and that command was printed **in the product**, on the fleet page, as the
instruction a reader was expected to follow. A CLI invocation rendered in an interface is
the clearest possible statement that the interface documents the system rather than being
it, and Phase 8 exists to remove that sentence.

So the logic lives here, where the dispatcher can call it, and the script becomes a thin
wrapper over the same functions. There is exactly one implementation of "is this watch
safe to register", and the operator command and the Connect button run it identically.

## The three pre-flight checks, and why refusing is the feature

`users.watch` will happily succeed against a topic nobody is subscribed to. It returns a
history id and an expiry, Firestore records a healthy-looking registration, and every
notification Gmail publishes falls into a void. **That is the worst outcome available**,
because it looks exactly like it worked and it fails silently for seven days.

So `register` checks that the topic exists, that Gmail's own publisher identity has
`roles/pubsub.publisher` on it, and that at least one subscription is attached — and
refuses with a named reason rather than registering. The refusal is the check.

## The seven-day expiry

Gmail does not renew a watch, does not warn before it lapses, and does not fail loudly
when it does; the notifications simply stop, and a mailbox that has stopped notifying is
indistinguishable from a mailbox nobody has emailed. The expiry is therefore recorded next
to the history cursor and surfaced everywhere the connection is shown, with the hours
remaining. A production deployment would put renewal on Cloud Scheduler; saying that is
more honest than pretending a cron job is a design.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from attestor_platform.firestore import InboxStateRepository
from attestor_platform.gmail.client import SCOPES, GmailClient

logger = logging.getLogger(__name__)

#: Gmail's own publisher. A fixed, documented Google identity — not one of ours.
GMAIL_PUBLISHER = "serviceAccount:gmail-api-push@system.gserviceaccount.com"

DEFAULT_TOPIC = "attestor-gmail"

#: What each scope actually permits, in a sentence a person can check the product against.
#:
#: Rendered on the Connections page rather than summarised as "email access", because the
#: narrowness is the point: `drive.file` sees only files this application created, which is
#: a least-privilege story worth being able to read off the screen.
SCOPE_NOTES: dict[str, str] = {
    "https://www.googleapis.com/auth/gmail.readonly": (
        "Read messages in this mailbox. Needed to see a questionnaire arrive."
    ),
    "https://www.googleapis.com/auth/gmail.send": (
        "Send as this mailbox. Used only to reply with a finished pack, after a person "
        "authorises it by name."
    ),
    "https://www.googleapis.com/auth/gmail.modify": (
        "Label threads in this mailbox. Used to mark what was started, refused, or ignored."
    ),
    "https://www.googleapis.com/auth/drive.file": (
        "See and manage only the files this application creates. It cannot read anything "
        "already in the Drive."
    ),
}


def topic_path(project: str, topic: str = DEFAULT_TOPIC) -> str:
    return f"projects/{project}/topics/{topic}"


@dataclass(frozen=True)
class TopicCheck:
    """Whether Gmail can actually deliver to this topic, and who is listening."""

    exists: bool
    publisher_bound: bool
    subscriptions: tuple[str, ...] = ()
    note: str = ""

    @property
    def deliverable(self) -> bool:
        return self.exists and self.publisher_bound and bool(self.subscriptions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "publisher_bound": self.publisher_bound,
            "subscriptions": list(self.subscriptions),
            "deliverable": self.deliverable,
            "note": self.note,
        }


@dataclass(frozen=True)
class WatchStatus:
    """Everything the product needs to describe the mailbox connection."""

    connected: bool
    address: str = ""
    topic: str = ""
    history_id: str = ""
    registered_at: str = ""
    expires_at: str = ""
    expires_in_hours: float | None = None
    expired: bool = False
    scopes: tuple[str, ...] = field(default_factory=lambda: SCOPES)
    #: Set when the last connect attempt was refused, with the reason it was refused.
    refusal: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "address": self.address,
            "topic": self.topic,
            "history_id": self.history_id,
            "registered_at": self.registered_at,
            "expires_at": self.expires_at,
            "expires_in_hours": self.expires_in_hours,
            "expired": self.expired,
            "scopes": [
                {"scope": scope, "grants": SCOPE_NOTES.get(scope, "")} for scope in self.scopes
            ],
            "refusal": self.refusal,
        }


def has_consent() -> bool:
    """Whether a mailbox has approved the scopes. Never raises, never reads the token out.

    The consent document is the precondition for every other thing in this module, and its
    absence is a normal state of a fresh deployment rather than an error -- so it is a
    boolean that callers can render, not an exception they have to catch.
    """
    from attestor_core.errors import AttestorError
    from attestor_platform.gmail.client import oauth_payload

    try:
        oauth_payload()
    except (AttestorError, OSError) as exc:
        logger.info("no gmail consent on file: %s", exc)
        return False
    return True


def check_topic(project: str, topic: str = DEFAULT_TOPIC) -> TopicCheck:
    """Can Gmail publish here, and is anyone subscribed? A 403 is the usual first failure."""
    from google.api_core import exceptions as gexc
    from google.cloud import pubsub_v1  # type: ignore[attr-defined]

    client = pubsub_v1.PublisherClient()
    path = topic_path(project, topic)
    try:
        policy = client.get_iam_policy(request={"resource": path})
    except gexc.NotFound:
        return TopicCheck(False, False, note=f"The topic {path} does not exist.")
    except gexc.PermissionDenied as exc:
        return TopicCheck(False, False, note=f"The IAM policy on {path} could not be read: {exc}")

    bound = any(
        binding.role == "roles/pubsub.publisher" and GMAIL_PUBLISHER in binding.members
        for binding in policy.bindings
    )
    try:
        subscriptions = tuple(
            str(name) for name in client.list_topic_subscriptions(request={"topic": path})
        )
    except gexc.GoogleAPIError as exc:
        subscriptions = ()
        logger.warning("could not list subscriptions on %s: %s", path, exc)

    if not bound:
        note = (
            f"Gmail's publisher identity is not permitted to publish to {path}, so a "
            "registration here would never deliver anything."
        )
    elif not subscriptions:
        note = (
            f"Nothing is subscribed to {path}. A watch registered against it would look "
            "healthy and deliver into a void."
        )
    else:
        note = f"Gmail may publish to {path}, and {len(subscriptions)} subscription(s) listen."
    return TopicCheck(True, bound, subscriptions, note)


def status(
    *,
    state: InboxStateRepository | None = None,
    address: str = "",
) -> WatchStatus:
    """The mailbox connection, read from Firestore alone.

    No Gmail credential is touched. The mailbox address is stored beside the cursor at
    registration time precisely so a service that only reports on the connection does not
    have to read a refresh token to render a status line.
    """
    cursor = (state or InboxStateRepository()).cursor()
    expiration_ms = int(cursor.get("expiration_ms") or 0)
    if not expiration_ms:
        return WatchStatus(connected=False, address=str(cursor.get("address") or address))

    expires_at = datetime.fromtimestamp(expiration_ms / 1000, tz=UTC)
    remaining = (expires_at - datetime.now(UTC)).total_seconds() / 3600
    return WatchStatus(
        connected=remaining > 0,
        address=str(cursor.get("address") or address),
        topic=str(cursor.get("topic") or ""),
        history_id=str(cursor.get("history_id") or ""),
        registered_at=str(cursor.get("registered_at") or ""),
        expires_at=expires_at.isoformat(timespec="seconds"),
        expires_in_hours=round(remaining, 1),
        expired=remaining <= 0,
    )


class WatchRefused(Exception):
    """Registration was refused because it would not have worked.

    Its own exception rather than a `False` return, because the reason is the whole value:
    "the topic has no subscribers" and "Gmail cannot publish to the topic" need different
    actions, and a boolean loses both.
    """


def register(
    *,
    project: str = "",
    topic: str = "",
    gmail: GmailClient | None = None,
    state: InboxStateRepository | None = None,
) -> WatchStatus:
    """Register the watch. Refuses rather than registering one that cannot deliver."""
    project = project or os.environ.get("PROJECT_ID", "").strip()
    if not project:
        raise WatchRefused("No Google Cloud project is configured for this service.")
    topic = topic or os.environ.get("ATTESTOR_GMAIL_TOPIC", DEFAULT_TOPIC)

    if not has_consent():
        # The first thing that goes wrong, and it went wrong as a 500 reading "Internal
        # Server Error" until this check existed. No mailbox has approved the scopes, so
        # there is no refresh token, so there is nothing to register a watch on -- and that
        # is a state a person can act on, not a fault.
        raise WatchRefused(
            "No mailbox has granted this deployment access yet, so there is nothing to "
            "watch. Consent is a person signing in to a Google account and approving the "
            "scopes; no service can grant it on their behalf."
        )

    check = check_topic(project, topic)
    if not check.deliverable:
        raise WatchRefused(check.note)

    client = gmail or GmailClient()
    repository = state or InboxStateRepository()
    registration = client.watch(topic_path(project, topic))
    repository.record_watch(
        registration.history_id,
        registration.expiration_ms,
        registration.topic,
        address=client.address,
    )
    logger.info(
        "gmail watch registered for %s on %s (historyId %s)",
        client.address,
        registration.topic,
        registration.history_id,
    )
    return status(state=repository, address=client.address)


def stop(
    *,
    gmail: GmailClient | None = None,
    state: InboxStateRepository | None = None,
) -> WatchStatus:
    """Unregister the watch, and clear the recorded expiry so the product agrees.

    The expiry is zeroed rather than left behind. A stopped watch whose Firestore record
    still says "expires in 140 hours" is the same class of lie as an unperformed check
    rendering as a passed one.
    """
    client = gmail or GmailClient()
    repository = state or InboxStateRepository()
    client.stop_watch()
    repository.record_watch("", 0, "", address=client.address)
    logger.info("gmail watch stopped for %s", client.address)
    return status(state=repository, address=client.address)
