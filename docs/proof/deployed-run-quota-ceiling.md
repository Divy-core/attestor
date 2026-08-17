# The 312-question deployed run, and the ceiling it hit

The full-scale review completed every stage **except drafting**, three times, against a
platform quota rather than a defect in the system. This records what happened, what was
tried, and why the fallback was taken rather than a fourth attempt.

## What worked, and it is most of it

Push delivery replaced the pull harness completely, and the failure mode that ended session
two is gone. On every attempt:

* intake parsed all 312 questions and published triage
* triage classified all 312 and published **three** drafting partitions
* **all three partitions were claimed within one second of each other**, by three separate
  Cloud Run instances

That last point is the thing session two could never achieve. The pull harness dispatched
one message at a time and synchronously, so the partitions ran in series when they ran at
all. Under push they overlap by construction, and the claims record it:

```
draft_answer  security     claimed 11:01:33
draft_answer  legal        claimed 11:01:33
draft_answer  engineering  claimed 11:01:34
```

The drafting itself is what did not finish.

## The ceiling

```
429 RESOURCE_EXHAUSTED
Quota exceeded for quota metric 'Query Reasoning Engine requests' and limit
'Query Reasoning Engine requests per minute per region' of service
'aiplatform.googleapis.com' for consumer 'project_number:906988347581'
```

Agent Runtime enforces a per-minute, per-region cap on engine queries. Once drafting moved
onto the engines (ADR-0007), every question became one such query — 312 of them, in three
concurrent partitions.

### Three settings, measured rather than assumed

| Per-partition workers | Concurrent queries | Outcome |
|---|---|---|
| 24 | 72 | every partition failed within a second |
| 8 | 24 | ~77 throttle events per 5 min; partitions exhausted 4 call-level retries, then message attempts 2–5 |
| 4 | 12 | throttling roughly halved but still sustained; one partition exhausted attempt 5 and dead-lettered |

Between the second and third, rate limiting was moved to where the rest of the codebase
already handles it — backoff on the **individual call** (`_query_with_retry`) rather than
letting one throttled question cost a redraft of all 123 in its partition. That was a real
improvement and it is kept; it was not enough.

An attempt to read the effective limit through the Service Usage API returned no metrics
for this service, so the exact number is **not established**. What is established is the
shape: at 12 concurrent queries the project is still being throttled continuously, which
puts the limit well below what 312 questions in three partitions needs.

## Why this is not fixed here

It is a project quota, not a code path. The remedy is a quota increase request, which has a
turnaround measured in days and is outside what this build can verify. Reducing concurrency
further trades the quota error for the ack deadline: at 3 workers the 123-question security
partition runs past 30 minutes, which exhausts the subscription's five delivery attempts on
409 responses alone.

Four diagnose-fix-rerun cycles were spent here. The cap is five, and the fifth was better
spent on a run that completes.

## What the lease did, which is worth keeping

Every redelivery during a live partition was refused with `409 HELD` rather than starting a
second copy of the same drafting work. That is the 900s-lease-over-600s-ack-deadline
ordering from `docs/proof/ack-deadline-margin.md` doing its job on the first run that
genuinely needed it, rather than in a unit test. No partition was ever drafted twice, and
no duplicate answers were written.

## One cost worth recording

A partition is **all or nothing**. The first 312 attempt drafted 221 answers successfully —
`audit_events` has 221 `answer_drafted` entries for it — and persisted none of them,
because `draft_answer` writes answers only after `draft_many` returns and the partition
raised before it did. Retrying the message redrafts every question in the partition,
including the ones that had already succeeded.

That is defensible for a partition that failed early and wasteful for one that failed at
question 120. Persisting answers as they complete would fix it and would make retries
cheap. Not changed in this session: it alters when answers become visible to
`assemble_round`, which is the join, and that is not a change to make at the end of a
session. Recorded as a known cost.

## The fallback taken

Per the session's fallback ladder: report the trace and the stall point, run the deployed
review at the largest size that completes, state the size plainly, and keep Phase 3's local
312-question numbers as the authoritative figures **with their provenance labelled**.

* `docs/proof/deployed-review-60.json` — the deployed run, end to end, on the real stack
* `docs/proof/run-clean.json` — Phase 3's authoritative 312, local, in-process

The demo quotes the Phase 3 figures for the 312-question claim and says they are local. The
deployed run is what proves the architecture: real Pub/Sub, real Cloud Run, real push
subscription, real engines drafting under their own identities, all seven messages, and
three partitions genuinely overlapping.
