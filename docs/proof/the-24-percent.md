# Why the verified 150-question run answered 24%, and what it was not

The run of record (`docs/proof/verified-run-150.json`) drafted 36 answers out of 150 and
flagged 77 for want of evidence. Every prior measurement of this system is three to four
times that. This is the diagnosis, in the order the measurements were taken.

## It was not the questionnaire

The 150 questions are the **first 150 rows of the clean 312-question fixture**, sliced by
`slice_questionnaire.py`, which copies the workbook and deletes rows. Question ids are a
content hash (`attestor_core.domain.ids.make_question_id`), so this is checkable rather
than remembered: all 150 answer ids in the run map exactly onto rows 2–151 of
`seed/questionnaires/clean/acme-vendor-review-r1.xlsx`.

The customer name changed to "Meridian Health Systems" and nothing else did. The name is a
label on the review; it is not in any question, and no question asks about healthcare.

The failures are also not clustered where a fixture mismatch would put them:

| Domain | n | cited | flagged |
|---|---|---|---|
| Security | 57 | 24 (42%) | 33 |
| Legal & Privacy | 43 | 20 (47%) | 22 |
| Engineering | 36 | 22 (61%) | 14 |
| Cross-cutting | 14 | 6 (43%) | 8 |

Roughly half of every department, and the flagged list includes *"What encryption algorithm
and key length is used for data at rest?"* — which the Kestrel corpus answers in a titled
section.

## It was not a retrieval regression

`tools/compare_retrieval.py` gained a `--only-status` filter so it measures the failures
rather than a balanced slice of the round. Thirty of the 77 flagged questions, both paths,
same guard, same audit sink:

`docs/proof/retrieval-on-the-77-flagged.json`:

| | local, in-process | deployed engines |
|---|---|---|
| cited | 23 of 30 | **25 of 30** |
| citation rate | 76.7% | **83.3%** |
| questions retrieving 0 passages | 4 | **0** |
| mean passages retrieved | 4.33 | **6.83** |
| mean top relevance | 0.684 | 0.690 |
| document overlap between the paths (Jaccard) | 0.712 | |
| engine ran no search at all | — | **0 of 30** |
| engine replied INSUFFICIENT_EVIDENCE | — | 5 of 30 |

The deployed path cites *above* the local one and retrieved passages for every one of the
thirty. Twenty-five of the thirty questions the production run filed as "no supporting
evidence in the corpus" are answered by the corpus, with citations, through the same
engines. The remaining five are the engine declining on the evidence, which is the system
working — several of these thirty are the planted gaps.

Whatever emptied those retrievals during the run was not present when the same questions
were asked again through the same code against the same corpus.

## What it was: a session-store quota, arriving as an empty result

The engines' own logs for the nine minutes of the run
(`resource.type="aiplatform.googleapis.com/ReasoningEngine"`, 17,892 lines):

```
Quota exceeded for quota metric 'Session Event Append Requests' and limit
'Session Event Append Requests per minute per region' of service
'aiplatform.googleapis.com'                                          188 occurrences
Quota exceeded for quota metric 'Vertex Session Write Requests'       36 occurrences
RuntimeError: Failed to create session.                               36 occurrences
```

This is **not** the quota Phase 6.5 hit. That one was *Query Reasoning Engine requests* and
it arrives as a 429 the caller can see. This one is ADK's managed session service: every
model turn and every tool call inside an engine invocation appends an event to a session,
and the per-minute regional ceiling on those appends was exhausted. The appends that fail
mid-stream do not fail the call. They lose the tool-response part that carries the
passages, and `stream_query` returns a stream with no `function_response` in it.

Minute by minute, the two series are the same series:

| minute (UTC) | engine quota errors | dispatcher "retrieved nothing" |
|---|---|---|
| 16:32 | 0 | 1 |
| 16:33 | 7 | 9 |
| **16:34** | **144** | **109** |
| 16:35 | 81 | 52 |
| 16:36 | 58 | 39 |

**This is the ninth failure that impersonates an empty result**, and the first one whose
mechanism is named rather than inferred. Phase 6.5 found the *behaviour* — "the engine's own
search returns an empty result set under sustained load, and returns it successfully" — and
built `EMPTY_RETRIEVAL_ATTEMPTS` against it without knowing why. This is why.

## Why the existing defence only recovered a third of them

`empty_retrievals_confirmed: 79`, `empty_retrievals_recovered: 33`.

The defence retries an empty retrieval three times with a 3-second base and exponential
backoff: it waits 3s, then 6s, and gives up 9 seconds after the first attempt. **The quota
it is retrying against resets on a minute boundary.** All three attempts land inside the
same exhausted minute, so a confirmation means only that the quota was still exhausted nine
seconds later. The 33 that recovered are the ones whose first attempt happened to fall near
the end of a minute.

## What was in the run that had never been in a run before

Verification. It was on for the first time, and a verification is a second engine
invocation per drafted answer — a second session, and a second stream of appends, on the
same per-minute regional ceiling as drafting. The 150-question run made roughly 186
invocations in nine minutes where the 312-question Phase 6.5 runs made one per question.

## The fix

Two changes, both in `services/dispatcher/src/dispatcher/remote.py`:

1. **The empty-retrieval backoff crosses the quota window.** 3s → 25s base, so the three
   attempts span roughly 75 seconds and at least one of them lands in a minute the quota
   has reset. Only questions that retrieved nothing pay it, and they pay it concurrently
   with the rest of the partition.
2. **The peak append rate comes down.** Drafting concurrency per partition drops from 8 to
   5, which with three simultaneous partitions is 15 concurrent engine invocations rather
   than 24.

Neither is a quota increase. That is the real remedy and it has a turnaround measured in
days, which is outside what this build can verify.

## What the fix did

Same 150 questions, same corpus, same engines, same customer name, one changed constant.
`docs/proof/demo-run.json` — review `rev-b6c7fd460d9a`, 13m 26s.

| | before | after |
|---|---|---|
| answers carrying a citation | 72 (48%) | **136 (91%)** |
| flagged for want of evidence | 77 | **13** |
| checked by a separate identity | 36 | **79** |
| empty retrievals confirmed | 79 | **14** |
| empty retrievals recovered on retry | 33 | **120** |

The recovery ratio inverted, which is the whole claim: retrievals that came back empty were
overwhelmingly the quota rather than the corpus, and a backoff that outlasts the quota window
gets them back.

Engine quota errors fell too — a peak of 34 per minute against 144 before — because drafting
concurrency came down from 8 to 5 per partition.

**One cost.** The longer backoff pushed the two longest partitions past the 600-second ack
deadline. Pub/Sub redelivered them and the 900-second lease refused each duplicate with
`409 HELD` while the original kept working. Five drafting stages appear in the trail for
three departments. No question was drafted twice.
