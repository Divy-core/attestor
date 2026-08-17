#!/usr/bin/env python3
"""Where did the legal and engineering drafting messages go?

    PROJECT_ID=attestor-505506 uv run python tools/diagnose_lost_partitions.py --write-proof

Phase 5 session two ran the full 312-question review and it stalled. Triage published all
three drafting partitions and reported `ok`; the security partition was claimed and drafted;
the other two were never claimed, never dead-lettered anywhere anybody looked, and never
delivered to a subsequent pull. That was recorded as **undiagnosed** rather than guessed
at, and this is the experiment that settles it.

## The hypothesis under test

A consumer holds one message for longer than the ack deadline while the siblings sit in
the backlog. The claim to test is whether the siblings survive that: does a long
synchronous dispatch cost the *other* messages their delivery attempts, and can they reach
`maxDeliveryAttempts` and be dead-lettered without ever being handed to the application?

`attestor.work.local` carries `ackDeadlineSeconds: 600` and
`deadLetterPolicy.maxDeliveryAttempts: 5`, and the dead-letter topic has **no
subscription**, so anything dead-lettered there is discarded with no record. If the
mechanism is real, that combination explains every observed fact: published, `ok`, never
claimed, no trace.

## Why this is run at 10 seconds rather than 600

Reproducing it at the real ack deadline costs fifty minutes per attempt. The mechanism is
a function of the *ratio* between how long the consumer holds a message and the deadline,
not of the absolute number, so the experiment runs on a scratch subscription with a
10-second deadline and a hold of 60 seconds -- six deadlines, the same shape as a
269-second partition against... well, that is the point: a 269-second partition against a
600-second deadline is 0.45 deadlines and should be safe, which is what makes the
observed failure worth explaining rather than shrugging at.

The scratch subscription and topic are created and deleted by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import pubsub_v1  # type: ignore[attr-defined]

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"

ACK_DEADLINE = 10
MAX_ATTEMPTS = 5
#: Six ack deadlines. Long enough that if holding one message costs the siblings their
#: attempts, they are exhausted well before the hold ends.
HOLD_SECONDS = 60
SIBLINGS = ("security", "legal", "engineering")


class _Empty:
    """What an empty pull should have returned in the first place."""

    received_messages: tuple[()] = ()


def _pull(subscriber: Any, subscription: str, max_messages: int, timeout: float = 20.0) -> Any:
    """Pull, treating an expired deadline as "nothing there" rather than as an error.

    Found the hard way while writing this script: a unary `pull` against an empty backlog
    does not reliably return an empty response -- it can raise `DeadlineExceeded`. The
    session-two harness calls `subscriber.pull` with no exception handling at all, so on
    that path an idle moment is a traceback rather than a retry. That is a second defect
    in the same loop, independent of the one this script is here to diagnose, and it is
    recorded because a pull loop that dies on silence would explain a run ending early.
    """
    try:
        return subscriber.pull(
            request={"subscription": subscription, "max_messages": max_messages},
            timeout=timeout,
        )
    except gexc.DeadlineExceeded:
        return _Empty()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    project = os.environ.get("PROJECT_ID")
    if not project:
        sys.exit("error: PROJECT_ID must be set")

    suffix = uuid.uuid4().hex[:8]
    topic_id = f"attestor.diag.{suffix}"
    dlq_id = f"attestor.diag.dlq.{suffix}"
    sub_id = f"attestor.diag.sub.{suffix}"
    dlq_sub_id = f"attestor.diag.dlqsub.{suffix}"

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    topic = publisher.topic_path(project, topic_id)
    dlq = publisher.topic_path(project, dlq_id)
    sub = subscriber.subscription_path(project, sub_id)
    dlq_sub = subscriber.subscription_path(project, dlq_sub_id)

    print("=" * 78)
    print("WHERE DID THE PARTITIONS GO -- controlled replay at a 10s ack deadline")
    print("=" * 78)
    print(f"  topic         : {topic_id}")
    print(f"  ack deadline  : {ACK_DEADLINE}s")
    print(f"  max attempts  : {MAX_ATTEMPTS}")
    print(f"  hold          : {HOLD_SECONDS}s ({HOLD_SECONDS // ACK_DEADLINE} deadlines)\n")

    created: list[Any] = []
    try:
        publisher.create_topic(request={"name": topic})
        publisher.create_topic(request={"name": dlq})
        created += [topic, dlq]
        subscriber.create_subscription(
            request={
                "name": sub,
                "topic": topic,
                "ack_deadline_seconds": ACK_DEADLINE,
                "dead_letter_policy": {
                    "dead_letter_topic": dlq,
                    "max_delivery_attempts": MAX_ATTEMPTS,
                },
            }
        )
        # A subscription on the dead-letter topic, which the real deployment did NOT have.
        # Without one, anything dead-lettered is discarded and the investigation has
        # nothing to find -- which is itself part of what went wrong in session two.
        subscriber.create_subscription(request={"name": dlq_sub, "topic": dlq})
        created += [sub, dlq_sub]

        # Pub/Sub needs to be allowed to move messages into the dead-letter topic.
        _grant_dlq(project, publisher, subscriber, dlq, sub)

        for partition in SIBLINGS:
            publisher.publish(topic, partition.encode("utf-8"), partition=partition).result(
                timeout=30
            )
        print(f"  published {len(SIBLINGS)} messages: {', '.join(SIBLINGS)}\n")

        # One message pulled and HELD -- exactly what a long drafting partition does.
        held = _pull(subscriber, sub, 1)
        if not held.received_messages:
            sys.exit("error: nothing delivered on the first pull; experiment inconclusive")
        first = held.received_messages[0]
        held_partition = first.message.attributes.get("partition")
        print(f"  pulled and holding: {held_partition}")
        print(f"  sleeping {HOLD_SECONDS}s without acking, as a 269s partition would...\n")
        time.sleep(HOLD_SECONDS)

        subscriber.acknowledge(request={"subscription": sub, "ack_ids": [first.ack_id]})
        print("  acked the held message; draining what is left\n")

        drained: list[dict[str, Any]] = []
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline and len(drained) < len(SIBLINGS) - 1:
            response = _pull(subscriber, sub, 10)
            if not response.received_messages:
                continue
            for message in response.received_messages:
                drained.append(
                    {
                        "partition": message.message.attributes.get("partition"),
                        "delivery_attempt": message.delivery_attempt,
                    }
                )
                subscriber.acknowledge(
                    request={"subscription": sub, "ack_ids": [message.ack_id]}
                )

        dead_lettered: list[str] = []
        response = _pull(subscriber, dlq_sub, 10)
        for message in response.received_messages:
            dead_lettered.append(str(message.message.attributes.get("partition")))
            subscriber.acknowledge(request={"subscription": dlq_sub, "ack_ids": [message.ack_id]})

        expected_siblings = {p for p in SIBLINGS if p != held_partition}
        survived = {str(d["partition"]) for d in drained}

        print(f"  held            : {held_partition}")
        print(f"  siblings expected: {sorted(expected_siblings)}")
        print(f"  siblings drained : {sorted(survived)}")
        for record in drained:
            print(f"      {record['partition']:<14} delivery_attempt={record['delivery_attempt']}")
        print(f"  dead-lettered    : {sorted(dead_lettered) or 'none'}")

        confirmed = bool(expected_siblings - survived)
        print(
            "\n  VERDICT : "
            + (
                "CONFIRMED -- holding one message cost the siblings their delivery"
                if confirmed
                else "REFUTED -- the siblings survived the hold intact"
            )
        )

        report = {
            "case": "lost_drafting_partitions",
            "hypothesis": (
                "holding one message past the ack deadline consumes the sibling "
                "messages' delivery attempts, dead-lettering them unseen"
            ),
            "confirmed": confirmed,
            "ack_deadline_seconds": ACK_DEADLINE,
            "max_delivery_attempts": MAX_ATTEMPTS,
            "hold_seconds": HOLD_SECONDS,
            "held_partition": held_partition,
            "siblings_expected": sorted(expected_siblings),
            "siblings_drained": drained,
            "dead_lettered": sorted(dead_lettered),
        }
        if args.write_proof:
            PROOF_DIR.mkdir(parents=True, exist_ok=True)
            out = PROOF_DIR / "lost-partitions-diagnosis.json"
            out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
            print(f"\nwrote {out}")
        return 0

    finally:
        print("\n  cleaning up scratch resources")
        for name in reversed(created):
            try:
                if "/subscriptions/" in name:
                    subscriber.delete_subscription(request={"subscription": name})
                else:
                    publisher.delete_topic(request={"topic": name})
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask the result
                print(f"    could not delete {name}: {exc}")


def _grant_dlq(project: str, publisher: Any, subscriber: Any, dlq: str, sub: str) -> None:
    """Let the Pub/Sub service agent publish to the DLQ and ack on the subscription.

    Without both, exhausted messages are redelivered forever and the dead-letter topic
    stays empty -- a failure mode that looks exactly like nothing happening.
    """
    agent = (
        f"serviceAccount:service-{_project_number(project)}"
        "@gcp-sa-pubsub.iam.gserviceaccount.com"
    )
    policy = publisher.get_iam_policy(request={"resource": dlq})
    policy.bindings.add(role="roles/pubsub.publisher", members=[agent])
    publisher.set_iam_policy(request={"resource": dlq, "policy": policy})

    policy = subscriber.get_iam_policy(request={"resource": sub})
    policy.bindings.add(role="roles/pubsub.subscriber", members=[agent])
    subscriber.set_iam_policy(request={"resource": sub, "policy": policy})


def _project_number(project: str) -> str:
    from google.cloud import resourcemanager_v3

    client = resourcemanager_v3.ProjectsClient()
    return str(client.get_project(name=f"projects/{project}").name.split("/")[-1])


if __name__ == "__main__":
    raise SystemExit(main())
