# The two drafting partitions that vanished

Session two ran the full 312-question review and it stalled. Triage published all three
drafting partitions and returned `ok`; the security partition was claimed and drafted; the
legal and engineering partitions were never claimed, never dead-lettered, and never
delivered to a subsequent pull. That was written up as **undiagnosed**, deliberately,
rather than guessed at. This closes it.

```
  1  intake_document    -         f4c8ef554910f43e  ok   published 1
  2  triage_questions   -         9ed0beb9c746bdee  ok   published 3
  3  draft_answer       security  930868ff28fef3e8  ok   published 0
     no message for 240s -- stopping
  final state: drafting · 645.0s
```

---

## The hypothesis, and why it was worth testing

The suggested explanation was a client-side prefetch buffer: a pull client that holds
messages locally while the loop synchronously dispatches a 269-second partition, whose ack
deadlines expire in the buffer, exhausting `maxDeliveryAttempts` and dead-lettering them
unseen. It fitted every observed fact, and `attestor.work.local` is configured exactly the
way that would hurt — `ackDeadlineSeconds: 600`, `maxDeliveryAttempts: 5`, and a
dead-letter topic with **no subscription attached**, so anything moved there is discarded
with no record.

`tools/diagnose_lost_partitions.py` replays that shape on a scratch topic at a 10-second
ack deadline with a 60-second hold — six deadlines, rather than the fifty minutes a
faithful reproduction at 600s would cost. Full record in
`docs/proof/lost-partitions-diagnosis.json`.

```
  pulled and holding: security
  sleeping 60s without acking, as a 269s partition would...

  siblings expected : ['engineering', 'legal']
  siblings drained  : ['engineering', 'legal', 'security']
      engineering    delivery_attempt=1
      security       delivery_attempt=2
      legal          delivery_attempt=1
  dead-lettered     : none

  VERDICT : REFUTED -- the siblings survived the hold intact
```

**Refuted.** Holding one message across six ack deadlines cost the siblings nothing: both
arrived on the next pull at `delivery_attempt=1`, and nothing was dead-lettered. Whatever
happened in session two, it was not this.

## What the experiment found instead

Two things the replay showed that the hypothesis did not predict:

1. **The held message came back at `delivery_attempt=2`.** Its deadline expired while it
   was being worked, Pub/Sub redelivered it, and the ack sent afterwards — with the
   original, now-invalid ack id — did nothing. That is the redelivery the 900s lease
   exists to make harmless, observed rather than reasoned about.
2. **A unary `pull` against an empty backlog does not reliably return an empty response.**
   It can raise `DeadlineExceeded`. The session-two harness calls `subscriber.pull` with no
   exception handling, so on that path an idle moment is a traceback.

## The actual cause, found by reading the loop

Neither of those is it either. The cause is in `tools/run_async_review.py`, and it is
four lines apart from itself:

```python
received = response.received_messages[0]
last_message_at = time.perf_counter()      # <- set when the message ARRIVES
envelope = WorkPublisher.decode(received.message.data)

outcome = dispatch_envelope(envelope, attempt=1)   # <- ~600s for the security partition
```

against the check at the top of the loop:

```python
while not delivered:
    if time.perf_counter() - last_message_at > IDLE_TIMEOUT_SECONDS:   # 240
        print(f"\n  no message for {IDLE_TIMEOUT_SECONDS}s -- stopping")
        break
```

`last_message_at` is stamped at **receipt**, not after the work finishes. The security
partition took roughly 600 seconds to draft. By the time `dispatch_envelope` returned, more
than 240 seconds had elapsed since that stamp, so the very next iteration hit the idle check
and broke — **without ever pulling again**.

The legal and engineering messages were never lost. They were never asked for. The line
`no message for 240s` is not a measurement of silence; it is a measurement of how long the
previous message took to process, printed under the wrong label. Every observed fact
follows: published `ok`, no claim, no dead letter, nothing in the backlog to explain, and a
645-second run that is exactly intake plus triage plus one drafting partition.

The 24-question run passed because every partition finished inside 240 seconds, so the
stamp was never stale when the check ran. The bug is invisible below the threshold and
certain above it — the class of thing that only appears at full scale, which is the reason
the brief insisted the full-scale run be real rather than extrapolated.

## Why it is not fixed

Because the loop is being deleted, not repaired. `infra/deploy.sh` puts the dispatcher on
Cloud Run behind an Eventarc push subscription, which is the architecture the plan
specified from the start. There is no client loop, no idle timer, and no single-threaded
dispatch: Pub/Sub delivers each message as an HTTP request and Cloud Run starts an instance
per concurrent message, so the three drafting partitions overlap for the first time.

**Can the same failure recur under push? No — and the reason is worth stating.** The
failure was a *consumer-side* bookkeeping error: one process deciding, on its own clock,
that there was nothing left to do. Under push, no component owns that decision. A message
that is published is delivered because Pub/Sub retries until it is acked or dead-lettered,
and the dispatcher's only say in the matter is the status code it returns, which is a
table (`services/dispatcher/src/dispatcher/main.py`) rather than a timer.

The two incidental findings above outlive the loop, though, and both are already handled:
the redelivery at `delivery_attempt=2` is what the lease ordering (900s > 600s) makes safe,
and the dead-letter topic now has a subscription so that a dead-lettered message is
findable rather than discarded.
