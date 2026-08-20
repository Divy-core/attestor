#!/usr/bin/env python
"""Register (or renew) the Gmail watch that turns inbound email into work.

    PROJECT_ID=attestor-505506 uv run python tools/gmail_watch.py            # report
    PROJECT_ID=attestor-505506 uv run python tools/gmail_watch.py --apply    # register
    PROJECT_ID=attestor-505506 uv run python tools/gmail_watch.py --stop     # unregister

## The seven-day expiry is the whole reason this is a tool

`users.watch` expires after seven days. Gmail does not renew it, does not warn, and does
not fail loudly when it lapses -- the notifications simply stop, and a mailbox that has
gone quiet looks exactly like a mailbox nobody has emailed. So the expiry is printed every
time this runs, recorded in Firestore next to the history cursor, and surfaced by
`GET /inbox` on the control plane. For a demo window measured in days that is the right
amount of machinery; a production deployment would put this on Cloud Scheduler, and saying
that is more honest than pretending a cron job is a design.

## What it needs to exist first

* The secret written by `tools/gmail_authorize.py`.
* A Pub/Sub topic, with `gmail-api-push@system.gserviceaccount.com` granted
  `roles/pubsub.publisher` **on that topic**. Gmail publishes as that fixed service
  account; without the binding, `watch` returns a 403 naming the topic.
* A push subscription pointing at the dispatcher's `/gmail/push`.

`--apply` checks the binding and the subscription before registering, because a watch that
succeeds against a topic nobody is subscribed to is the worst outcome available: it looks
like it worked.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from attestor_platform.firestore import InboxStateRepository
from attestor_platform.gmail import GmailClient

#: Gmail's own publisher. A fixed, documented identity -- not one of ours.
GMAIL_PUBLISHER = "serviceAccount:gmail-api-push@system.gserviceaccount.com"

DEFAULT_TOPIC = "attestor-gmail"


def _topic_path(project: str, topic: str) -> str:
    return f"projects/{project}/topics/{topic}"


def _check_binding(project: str, topic: str) -> tuple[bool, str]:
    """Is Gmail allowed to publish to this topic? A 403 here is the usual first failure."""
    from google.api_core import exceptions as gexc
    from google.cloud import pubsub_v1  # type: ignore[attr-defined]

    client = pubsub_v1.PublisherClient()
    path = _topic_path(project, topic)
    try:
        policy = client.get_iam_policy(request={"resource": path})
    except gexc.NotFound:
        return False, f"topic {path} does not exist"
    except gexc.PermissionDenied as exc:
        return False, f"cannot read the IAM policy on {path}: {exc}"
    for binding in policy.bindings:
        if binding.role == "roles/pubsub.publisher" and GMAIL_PUBLISHER in binding.members:
            return True, "gmail-api-push has roles/pubsub.publisher"
    return False, (
        f"{GMAIL_PUBLISHER} is not a publisher on {path}. Run:\n"
        f"    gcloud pubsub topics add-iam-policy-binding {topic} "
        f'--member="{GMAIL_PUBLISHER}" --role="roles/pubsub.publisher"'
    )


def _subscriptions(project: str, topic: str) -> list[str]:
    from google.api_core import exceptions as gexc
    from google.cloud import pubsub_v1  # type: ignore[attr-defined]

    client = pubsub_v1.PublisherClient()
    try:
        return list(client.list_topic_subscriptions(request={"topic": _topic_path(project, topic)}))
    except gexc.GoogleAPIError:
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="register the watch")
    parser.add_argument("--stop", action="store_true", help="unregister it")
    parser.add_argument("--topic", default=os.environ.get("ATTESTOR_GMAIL_TOPIC", DEFAULT_TOPIC))
    args = parser.parse_args()

    project = os.environ.get("PROJECT_ID", "").strip()
    if not project:
        sys.exit("error: PROJECT_ID must be set")

    state = InboxStateRepository()
    cursor = state.cursor()
    gmail = GmailClient()

    print("=" * 78)
    print("GMAIL WATCH")
    print("=" * 78)
    print(f"  mailbox     : {gmail.address}")
    print(f"  topic       : {_topic_path(project, args.topic)}")

    expiration = int(cursor.get("expiration_ms") or 0)
    if expiration:
        expires_at = datetime.fromtimestamp(expiration / 1000, tz=UTC)
        remaining = expires_at - datetime.now(UTC)
        hours = remaining.total_seconds() / 3600
        print(f"  registered  : expires {expires_at.isoformat(timespec='seconds')}")
        print(f"                {hours:.1f}h remaining" + ("  ** EXPIRED **" if hours < 0 else ""))
    else:
        print("  registered  : never")
    print(f"  cursor      : historyId {cursor.get('history_id') or '(none)'}")

    if args.stop:
        gmail.stop_watch()
        print("\n  watch stopped. No further notifications will be published.")
        return 0

    ok, note = _check_binding(project, args.topic)
    print(f"  publisher   : {'ok' if ok else 'MISSING'} -- {note}")
    subs = _subscriptions(project, args.topic)
    print(f"  subscribers : {len(subs)}")
    for sub in subs:
        print(f"                {sub}")
    if not subs:
        print(
            "                none. Notifications would be published into a void; create a\n"
            "                push subscription to <dispatcher>/gmail/push before relying on this."
        )

    if not args.apply:
        print("\n  re-run with --apply to register.")
        return 0
    if not ok:
        sys.exit("\nerror: refusing to register a watch Gmail cannot publish to.")

    registration = gmail.watch(_topic_path(project, args.topic))
    state.record_watch(
        registration.history_id,
        registration.expiration_ms,
        registration.topic,
        address=gmail.address,
    )
    expires_at = datetime.fromtimestamp(registration.expiration_ms / 1000, tz=UTC)
    print(f"\n  registered. historyId {registration.history_id}")
    print(f"  expires {expires_at.isoformat(timespec='seconds')} -- renew before then.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
