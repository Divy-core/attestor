# ADR-0005 — Work is partitioned by stage, and `partition` joins the dedup key

**Status:** Accepted · **Date:** 16 Aug 2026 · **Phase:** 4
**Amends:** the Phase 1 frozen protocol — one field, `WorkEnvelope.partition`

## Context

The authoritative Phase 3 run takes **11m49s**. That does not fit request/response, which
is what Phase 4 exists to fix. The question is what a Pub/Sub message should *contain*.

Three decompositions were considered.

**One message per run.** A single `run_review` message that does the whole 12 minutes.
Simple, and not durable in any useful sense: a crash at minute eleven loses everything,
there is no intermediate state to resume into, and the message either exceeds every
sensible ack deadline or requires ack extension for the full run. "Durable" would mean
"we retry the entire twelve minutes", which is not durability, it is repetition.

**One message per question.** 312 messages, each drafting one answer. Maximum
granularity, and it destroys the thing Phase 3 measured: concurrency moves out of
`ParallelAgent` and into Pub/Sub's subscriber pool. The measured **7.84-of-8 achieved
in-process concurrency** — and the architectural argument in ADR-0002 that drafting is
embarrassingly parallel *inside the fleet* — would both be replaced by "we set a
subscription concurrency number". It also multiplies fixed per-message cost by 312:
session hydration, state read, idempotency claim, audit write.

**One message per stage, partitioned where a stage is wide.** Chosen.

## Decision

**Each message is one bounded, resumable transition of the review state machine.**

```
intake_document    → parse, normalise, content-derived IDs        → triaging
triage_questions   → classify N questions in batches of 20        → drafting
draft_answer       → ONE MESSAGE PER DEPARTMENT, each running
                     the ParallelAgent fan-out for its slice      → assembling
assemble_round     → compose, score confidence, flag              → awaiting_human | delivered
close_round        → write commitments to Memory Bank             → delivered
open_follow_up     → new round, load prior commitments            → triaging
resume_after_human → apply the decision, continue                 → drafting | assembling
timer_fired        → SLA and follow-up deadlines
```

Drafting is partitioned by **department**, not by question. Three messages per round.
Each one runs the in-process fan-out across its own department's questions, so the
measured concurrency and the ADR-0002 argument survive intact, while the unit of
durability drops from 12 minutes to roughly 4.

Departments are also the natural partition for a second reason: they are already the
access boundary. A `draft_answer` message for `security` is executed by an agent holding
only the security corpus handle, so the partition key and the privilege boundary are the
same line.

## The amendment, and the bug that forced it

The Phase 1 dedup key was:

```python
make_dedup_key(review_id, round_id or "-", question_id or "-", kind.value)
```

For a department-partitioned `draft_answer`, `question_id` is null. All three partitions
of a round therefore share every component of the key. Measured before writing any
dispatcher code:

```
security     06cb4c077162efc5
legal        06cb4c077162efc5
engineering  06cb4c077162efc5
distinct keys: 1 of 3
```

The dispatcher claims a key before doing work and acks anything already claimed. Two of
those three messages would have been acked as redeliveries. **Two thirds of the drafting
work would have vanished, silently, with no error anywhere** — a run that looked like it
succeeded and delivered a third of its answers.

That is idempotency causing precisely the class of failure idempotency exists to prevent,
and it is invisible in exactly the way that matters: no exception, no dead letter, no
retry, just a smaller number at the end.

So the protocol is amended by exactly one field:

```python
#: Which slice of a stage this message covers, when a stage is split across several
#: messages that share every other correlation field.
partition: str | None = None
```

and the key becomes:

```python
make_dedup_key(review_id, round_id or "-", question_id or "-", partition or "-", kind.value)
```

After the amendment:

```
security     33cf344c5b5a3e78
legal        df542fe0d61adaa3
engineering  a7bafa8fe554dd7f
distinct keys: 3 of 3
redelivery of the security partition still collides: True
```

Both halves matter. Distinct partitions must not collide; a *redelivery* of one partition
must still collide, including across a different `run_id` and a higher `attempt`.

## Why a general `partition` rather than a `department` field

`department` would have fixed today's bug and left the next one. The same collision recurs
for any stage split across messages that share their correlation fields:

| Stage | Partition value |
|---|---|
| `draft_answer` | `security` / `legal` / `engineering` |
| `triage_questions` (if ever split) | batch index, `batch-0` … `batch-15` |
| a retry wave | `wave-2` |

One optional string covers all three without further churn on a frozen contract. The cost
is that it is untyped — a caller can put anything in it. Accepted: the dedup key only
requires the value to be *stable and distinct*, not to be from a closed set, and closing
the set would force a protocol change for every new partitioning scheme.

## Scope of the change

This is **the one permitted amendment** to the frozen protocol. Specifically:

- `WorkEnvelope` gains one optional field with a default, so a producer that omits it
  still validates. Wire-compatible in the additive direction.
- **Every dedup key changes**, including for kinds that will never be partitioned, because
  the key gains a `-` component. Nothing is in flight — Phase 4 is being built now and no
  message has ever been published in anger — so there is no migration. Recorded because a
  reader comparing a Phase 1 key to a Phase 4 key will otherwise think one of them is
  wrong.
- The 14 SSE event variants are **untouched**. This amends the work envelope only.
- `generated.ts` regenerated; `make types-check` fails if it drifts again.

**Re-frozen after this change.** The next amendment needs its own ADR and the same
justification: a demonstrated bug, not a convenience.

## Consequences

**Good.** Durability at ~4-minute granularity instead of ~12. A crash loses at most one
department's drafting, and the state machine says exactly where to resume. The in-process
fan-out and its measured 7.84-of-8 concurrency survive. Partition appears in every log
line and audit event, so "which slice failed" is answerable without correlation
archaeology.

**Bad.** Three messages must all complete before `assemble_round` can run, so the
dispatcher needs a join. Implemented as a completion count in the round document, written
in the same transaction as the state read — not as a timer, and not as "the last one to
finish wins", which would be a race.

**Rejected — per-question messages.** Covered above: it discards the measured concurrency
and multiplies fixed per-message cost by 312.

**Rejected — leaving the key alone and deduping on `message_id`.** `message_id` is unique
per publish, so every redelivery would look like new work and the idempotency guarantee
would be worth nothing. The whole point of a content-derived key is that a redelivery
computes the same one.

## Evidence

- `tests/unit/test_protocol.py::TestPartitionedDedup` — the collision, the fix, and the
  redelivery case, so a regression fails `make check` rather than a demo.
- `docs/proof/dedup-partition.txt` — the before/after key measurement above.
