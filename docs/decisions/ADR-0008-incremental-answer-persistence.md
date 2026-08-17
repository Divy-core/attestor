# ADR-0008 — A drafting attempt persists as it goes, and ends itself before the deadline

**Status:** Accepted · **Date:** 17 Aug 2026 (Phase 6.5) · Extends
[ADR-0005](ADR-0005-work-partitioning-and-the-dedup-key.md) and
[ADR-0007](ADR-0007-drafting-on-the-deployed-engines.md)

## Context

The deployed 312-question run has never completed. Three attempts, three different
concurrency settings, and the same shape of failure each time: the largest drafting partition
exhausts its five Pub/Sub delivery attempts and the round never reaches `assemble_round`.

The obvious reading was a quota problem, and it was wrong. The measurements say otherwise:

| Run | Workers per partition | Longest partition | Answers persisted | Outcome |
|---|---|---|---|---|
| First full-scale | 24 | died in <1s | 0 | `429 RESOURCE_EXHAUSTED`, regional quota |
| Second | 8 | ~1,550s (est.) | 0 of ~123 | 5 attempts exhausted |
| Third (A3) | 2 | 793.2s measured | 189 of 312 | 2 of 3 partitions completed; the third exhausted 5 attempts |

The third run is the one that settles it. Achieved concurrency was 1.98 of 2 — 99% efficient,
the same efficiency as 7.84 of 8. The fan-out is not the problem at any setting tried. What
fails is the *unit of work*:

```
draft_answer(partition=security)
  → fleet.draft(123 questions)      ~1,550s
  → for answer in answers: put()    ← every write happens HERE
  → close the join
```

Nothing is persisted until `draft` returns. So a partition that runs past the 600s ack
deadline is redelivered, refused by the live lease with a 409, and eventually redelivered
again after the claim expires — at which point it starts from question 1. Five attempts of the
same 1,550s of work is **five failures, not five chances**. The delivery-attempt count was
never the binding constraint, and no amount of raising it would have helped.

The A3 run also produced the first negative margin to the ack deadline in this project:
793.2s against a 600s deadline, −193.2s, survivable only because the 900s lease sits above the
deadline. That ordering was sized on an estimate in `docs/proof/ack-deadline-margin.md`; A3 was
the first run where it was the thing standing between a redelivery and a second copy of 123
questions being drafted.

## Decision

**Persist each answer at the moment it is drafted, skip on redelivery what is already written,
and stop the attempt before the ack deadline rather than being stopped by it.**

The first two were the original decision. The third was added after the first deployed run to
use them, and the run is why — see "The half that persistence did not solve" below.

Three changes, one per layer:

1. `ReviewPipeline.draft_many(questions, on_outcome=None)` calls `on_outcome` with each
   outcome as it completes, on the worker thread that produced it.
2. `PipelineFleetRunner.draft(..., on_answer=None)` forwards answers — and only answers, never
   an outcome that produced none — to the caller's callback.
3. `HandlerRegistry.draft_answer` reads the round's existing answers, hands the fleet only the
   questions that have none, and passes `self.answers.put` as the callback.

Then a fourth, which the measurement forced:

4. `HandlerRegistry.draft_answer` passes a **time budget** (`ATTESTOR_DRAFT_BUDGET_SECONDS`,
   420s). `draft_many` starts no new question after it expires; in-flight questions finish.
   If anything is left, the handler publishes a continuation for the same department and
   returns normally — which Pub/Sub acks.

`AnswerRepository.put` is keyed on `(round_id, question_id)`, so a rewrite of the same answer
is idempotent and a resume needs no extra bookkeeping collection. The skip set is derived from
the answers themselves rather than from a progress document, which means it cannot disagree
with the data — a progress record claiming question 62 was done while no answer exists for it
is a state this design cannot enter.

## The half that persistence did not solve

Measured, on the first deployed 312-question run with the resume in place
(`docs/proof/journey.json`, first attempt):

| Partition | Questions | Outcome |
|---|---|---|
| engineering | 93 | **completed** on attempt 5, having resumed 70 |
| security | 121 | 118 answered, dead-lettered after 5 attempts |
| legal | 98 | 98 answered, dead-lettered after 5 attempts |

309 of 312 answers written — against 189 on the best previous run — and the round still never
reached `assemble_round`. The resume was doing exactly what it was designed to do and it was
not enough.

The reason is that **being interrupted is what costs a delivery attempt.** A partition too
large for the 600s ack deadline runs past it; Pub/Sub redelivers; the live 900s lease refuses
the redelivery with a 409; and *that refusal consumes an attempt*. Five attempts can be spent
on refusals of one long attempt rather than on five pieces of work. The attempt count was never
the binding constraint here either — the same shape of mistake as reading the original failure
as a quota problem.

So the attempt ends itself. That converts a redelivery into a continuation, which resets the
counter, and the round advances by however many attempts it needs.

**The continuation needs a different dedup key, and a test caught that it would not have had
one.** `WorkEnvelope.for_work` derives the key from `(review_id, round_id, question_id,
partition, kind)` and deliberately *not* from `message_id` (ADR-0005), so that a redelivery is
recognised as the same work. A continuation published with the same partition would therefore
carry the identical key, be refused by the claim repository as a duplicate, and stall the round
**permanently** — a worse failure than the one being fixed, and one that would have looked from
outside exactly like the problem it was meant to solve.

The fix is that the partition string carries a sequence: `security`, then `security@2`. It is
the only component of the dedup tuple that can honestly differ between one attempt at a
department's slice and the next. `partition` was `str | None` and stays `str | None`, so the
frozen envelope and `generated.ts` are untouched; what changed is that the dispatcher reads
structure in a string it was already the only consumer of.

The join still closes on the **department**, never on the suffixed string. `_close_partition`
compares against `{d.value for d in DRAFT_PARTITIONS}`, so closing as `security@2` would leave
the set one short forever and no round would ever assemble. Pinned by
`test_the_join_closes_on_the_department_not_the_sequence`.

**A continuation that makes no progress is refused.** If an attempt drafts zero questions
within its budget, the handler raises `ContractViolation` rather than republishing — permanent,
so it dead-letters. A continuation loop that republishes itself forever is a stalled round that
keeps spending money, which is strictly worse than a stalled round.

## Consequences

### Answers become visible to readers mid-partition

This is the real consequence and it needs stating precisely, because "answers appear earlier"
sounds harmless and the join is what makes it so.

`assemble_round` reads every answer in the round and pauses if any is `NEEDS_HUMAN`. It is
only published when `_close_partition` reports no partitions outstanding, and that is a
transaction over a set of partition names (ADR-0005). Partial answers were always *possible*
to observe — a completed partition's answers have always been readable while the other two ran
— and what changes is only how partial. The gate on assembly is unchanged and remains the
join, not the answer count.

What genuinely changes: an interrupted partition now leaves durable answers behind that no
`assemble_round` will ever see unless the partition is retried to completion. If a partition
exhausts all five attempts, the round holds a real subset of its answers and never assembles.
That is strictly better than holding none — the export can produce what exists, the console can
show it, and the questions that were answered do not have to be paid for twice — but it is not
"the round is done". The review sits in `drafting` with `partitions_outstanding` naming the one
that failed, which is what the audit trail already recorded and now also matches the data.

### The audit trail's `answers` figure changes meaning

`detail["answers"]` on a `draft_answer` stage event now counts what *this attempt* drafted, not
what the partition holds. On a redelivery those differ, and an event reading `answers: 61` for a
partition of 123 would look like a partition that lost half its work. So the event carries all
four numbers: `questions` (the partition), `resumed_from_previous_attempt`,
`drafted_this_attempt`, and `partition_total`. Earlier runs' events are unaffected and their
`answers` figure was already the partition total, because nothing resumed.

### Commitments are unaffected

`record_commitments` runs in `close_round`, reads the round's answers from Firestore, and is
keyed by `make_dedup_key(review, round, question)`. It sees exactly the same set it saw before,
because it runs after the join.

### The callback must not swallow failures

`on_outcome` exceptions propagate and fail the partition. A callback whose job is to persist,
and which silently failed, would produce precisely the resume that reports progress it does not
have — the eighth member of the failure-impersonating-empty family, arrived at from a new
direction. This is the same reasoning as `attestor_platform.retry.retrying` re-raising rather
than returning a default.

### It had to be added to the override too

`RemoteDraftingPipeline.draft_many` overrides the base method to fan out at a different width,
and it took only `questions`. Adding the callback to `ReviewPipeline` and stopping there would
have wired the resume into the in-process runner and left the deployed path — the one with the
deadline problem — silently unchanged: the dispatcher would pass a callback, the override would
drop it, every partition would still restart from zero, and the audit trail would report a
resume that never happened. `mypy --strict` caught it as an incompatible override. The failure
mode is worth recording because the code would have run, the base-class tests would have
passed, and the artefact would have looked like a fix.

## Alternatives rejected

**Raise the ack deadline.** 600s is Pub/Sub's maximum. There is no higher number.

**Partition by question instead of by department.** 312 messages, each well inside the
deadline, and the deadline problem disappears. Rejected in ADR-0005 for reasons that still
hold: it moves the fan-out into the subscription, which throws away the measured in-fleet
concurrency, multiplies the per-message overhead by 100, and makes the drafting join a
312-name set. It also does not fix this on its own — a per-question message that failed would
still redraft its own question, which is the correct granularity, but the change is a protocol
re-freeze for a problem a callback solves.

**Split the partition when it is too large.** A recursive split is more machinery than a
callback and leaves the same question of what to do with the work already done.

**Accept it and demo the 60-question run.** This was the position through Phase 5 and it was
defensible while the interface was a viewer. It is not defensible now: the demo shows a person
uploading a questionnaire and watching the fleet answer it, and the questionnaire of record is
312 questions.

## Verification

`tests/unit/test_incremental_persistence.py` — seventeen tests. The three that carry the
decision:

- `test_an_interrupted_partition_leaves_its_finished_work_behind` — a fleet that raises
  part-way through leaves four persisted answers where it previously left zero.
- `test_the_second_attempt_is_handed_only_the_unfinished_questions` — the redelivery drafts
  six of ten, not ten.
- `test_it_publishes_a_continuation_and_does_not_close_the_join` — the budgeted attempt
  publishes the remainder and leaves the join open, because releasing `assemble_round` on an
  unfinished slice would look like success.

`test_every_answer_is_written_before_the_slice_returns` asserts the write log rather than the
final state, because `for answer in answers: put(answer)` after the fact produces an identical
end state and none of the benefit.

The deployed re-run and its figures are recorded in `PROGRESS.md` under Phase 6.5 and in
`docs/proof/deployed-review-312-resumable.json`.
