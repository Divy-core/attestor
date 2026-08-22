#!/usr/bin/env python
"""Report on, register, or stop the Gmail watch — from a terminal, for an operator.

    PROJECT_ID=attestor-505506 uv run python tools/gmail_watch.py            # report
    PROJECT_ID=attestor-505506 uv run python tools/gmail_watch.py --apply    # register
    PROJECT_ID=attestor-505506 uv run python tools/gmail_watch.py --stop     # unregister

## This is no longer the way to turn inbound email on

It was, and that was the defect. Until Phase 8 the fleet page carried the sentence *"No
watch is registered, so no email will start a review. Register one with
`tools/gmail_watch.py --apply`"* — a CLI invocation printed inside the product, as an
instruction the reader was expected to follow. The product now does it: **Connections →
Gmail → Connect** runs exactly the code below, through the dispatcher, which is the one
service holding the mailbox credential.

The script stays because an operator on a terminal is a real user of a deployed system,
and because it prints the Pub/Sub diagnostics in full when something is wrong. What it no
longer is, is the only door.

## What it needs to exist first

* The secret written by `tools/gmail_authorize.py`.
* A Pub/Sub topic, with `gmail-api-push@system.gserviceaccount.com` granted
  `roles/pubsub.publisher` **on that topic**. Gmail publishes as that fixed service
  account; without the binding, `watch` returns a 403 naming the topic.
* A push subscription pointing at the dispatcher's `/gmail/push`.

All three are checked before registering, by `attestor_platform.gmail.watch.register`,
which refuses rather than registering a watch that cannot deliver.
"""

from __future__ import annotations

import argparse
import os
import sys

from attestor_platform.gmail import GmailClient
from attestor_platform.gmail.watch import (
    DEFAULT_TOPIC,
    WatchRefused,
    check_topic,
    register,
    status,
    stop,
    topic_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="register the watch")
    parser.add_argument("--stop", action="store_true", help="unregister it")
    parser.add_argument("--topic", default=os.environ.get("ATTESTOR_GMAIL_TOPIC", DEFAULT_TOPIC))
    args = parser.parse_args()

    project = os.environ.get("PROJECT_ID", "").strip()
    if not project:
        sys.exit("error: PROJECT_ID must be set")

    gmail = GmailClient()
    current = status(address=gmail.address)

    print("=" * 78)
    print("GMAIL WATCH")
    print("=" * 78)
    print(f"  mailbox     : {gmail.address}")
    print(f"  topic       : {topic_path(project, args.topic)}")
    if current.expires_at:
        print(f"  registered  : expires {current.expires_at}")
        print(
            f"                {current.expires_in_hours}h remaining"
            + ("  ** EXPIRED **" if current.expired else "")
        )
    else:
        print("  registered  : never")
    print(f"  cursor      : historyId {current.history_id or '(none)'}")

    if args.stop:
        stop(gmail=gmail)
        print("\n  watch stopped. No further notifications will be published.")
        return 0

    check = check_topic(project, args.topic)
    print(f"  publisher   : {'ok' if check.publisher_bound else 'MISSING'}")
    print(f"  subscribers : {len(check.subscriptions)}")
    for subscription in check.subscriptions:
        print(f"                {subscription}")
    print(f"  verdict     : {check.note}")

    if not args.apply:
        print("\n  re-run with --apply to register, or use Connections in the product.")
        return 0

    try:
        registered = register(project=project, topic=args.topic, gmail=gmail)
    except WatchRefused as refusal:
        sys.exit(f"\nerror: {refusal}")
    print(f"\n  registered. historyId {registered.history_id}")
    print(f"  expires {registered.expires_at} -- renew before then.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
