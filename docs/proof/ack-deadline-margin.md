# Ack deadline, lease, and the longest partition

Stated before the full-scale run, because the number that matters at 312 questions does
not exist at 24: **Pub/Sub's ack deadline caps at 600 seconds.** If a drafting partition
ever exceeds it, Pub/Sub redelivers, and whether that costs anything depends entirely on
what the redelivery finds.

## The four numbers

| Quantity | Value | Source |
|---|---|---|
| Configured ack deadline | **600s** | `gcloud pubsub subscriptions describe attestor.work.local` → `ackDeadlineSeconds: 600` |
| Configured lease | **900s** | `attestor_platform.firestore.claims.LEASE_SECONDS` |
| Longest partition at 312 questions | **269s** | derived from `docs/proof/run-clean.json` |
| Margin to the ack deadline | **331s (2.2×)** | |
| Margin to the lease | **631s (3.3×)** | |

600s is already the Pub/Sub maximum, so "extend the ack deadline" is not available as a
mitigation — it is already at its ceiling.

### How the partition duration is derived

The Phase 3 authoritative run drafted all 312 questions in one pool: 681.9s wall clock at
7.84 achieved concurrency, so effective worker time per question is
`681.9 × 7.84 ÷ 312 = 17.13s`. Under ADR-0005 each department drafts its own slice at
concurrency 8:

| Partition | Questions | Expected duration |
|---|---|---|
| security | 123 (incl. 3 unassigned) | **269s** |
| legal | 96 | 210s |
| engineering | 90 | 197s |

## What protects the run, in order

**1. The lease outlives the ack deadline — 900s against 600s.** This is the load-bearing
ordering and it is not accidental. When Pub/Sub redelivers a message at 600s because the
handler has not acked yet, the dispatcher claims the key, finds a **live** claim, and
returns `409 HELD` — no second copy of the drafting work starts. Had the lease been
*shorter* than the ack deadline, that redelivery would have found an expired claim, taken
it over, and drafted the same 123 questions a second time: double model spend, two sets of
answer writes, and nothing anywhere reporting an error.

**2. A running handler now extends its own lease.** The margin above depends on triage
spreading questions across three departments. It does that because the corpus and the
questionnaire genuinely span three domains — but nothing *enforces* it. Concentrate 312
questions into one partition and that partition runs ~682s: still inside the 900s lease,
but at 1.3× rather than 3.3×, and a slow model day closes that.

So `LeaseKeeper` pushes the lease forward every 60s while the handler runs
(`services/dispatcher/src/dispatcher/lease.py`). The estimate then only has to cover one
heartbeat interval instead of one whole partition. Heartbeat failures are logged and
ignored — the lease still has minutes on it, and a blip in lease bookkeeping must never be
the reason a twelve-minute review aborts.

Both are kept. The heartbeat is the belt; the 900s-over-600s ordering is the braces, and it
is what still protects the run if the heartbeat thread is starved by eight concurrent
drafting workers.

## What is deliberately not done

**Shortening the lease to recover dead work faster.** With a heartbeat in place a 300s
lease would make an abandoned claim recoverable in five minutes instead of fifteen. It
would also drop the lease *below* the ack deadline, re-creating the duplicate-drafting
window above. Slower recovery from a rare crash is a better trade than routine duplicate
work, so the lease stays at 900s.

## Verified by

- `tests/unit/test_claims.py::TestLeaseExtension` — an extended claim is not reclaimable;
  a completed claim is not resurrected by a late heartbeat.
- `tests/unit/test_lease_keeper.py` — the heartbeat fires for a long handler, costs
  nothing for a short one, stops when the handler returns, and survives a failing
  Firestore.
