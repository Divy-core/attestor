# ADR-0007 — Drafting executes on the deployed engines, not in the dispatcher

**Status:** Accepted · **Date:** 17 Aug 2026 (Phase 5, session three) · Supersedes nothing;
extends [ADR-0002](ADR-0002-deterministic-pipeline.md)

## Context

Phase 5 sessions one and two deployed the fleet as five `reasoningEngine` resources, each
with its own Agent Identity, and proved at runtime that the platform refuses one of them a
cross-department read: the `attestor-security` engine reads
`gs://…/security/access-control-standard.txt` and is given 4,298 bytes, and reads
`gs://…/legal/data-processing-agreement.txt` and is given a 403
(`docs/proof/iam-runtime-denial.json`).

Session two then recorded the consequence it had to record: **the engines were not on the
drafting path.** `PipelineFleetRunner` ran the Phase 3 `ReviewPipeline` in the dispatcher
process, under the dispatcher's service account. So the strongest security evidence in the
project was attached to a component that was idle, and the honest sentence about the
Track 3 rubric's first bullet was "the fleet is deployed to Agent Runtime; the review runs
in a script."

Two claims, only the first of which was true:

* the fleet is deployed, registered, and identity-scoped — **true**
* the fleet is what runs the review — **not true**

## Decision

Route the drafting stage to the deployed department engines. A `draft_answer` message is
partitioned by department (ADR-0005) and each department has exactly one engine, so the
mapping is 1:1 and a partition handler calls one engine.

`AgentRuntimeFleetRunner` is selected by `ATTESTOR_FLEET_RUNNER`, **defaulting to Agent
Runtime**. `PipelineFleetRunner` remains and remains selectable, because it is what
produced Phase 3's authoritative numbers and it is the documented fallback.

### Exactly which calls moved

Stated as a table because a vague version of this claim would be worse than none:

| Call | Where it runs | Under whose identity |
|---|---|---|
| Corpus retrieval | the department engine | the engine's Agent Identity |
| The draft itself | the department engine | the engine's Agent Identity |
| Triage classification | the dispatcher | the dispatcher's service account |
| Commitment consistency check | the dispatcher | the dispatcher's service account |
| Constrained redraft | the dispatcher | the dispatcher's service account |

Triage is not a department's work — it is what *decides* the department, so routing it to
one would be circular. The consistency check and the redraft are compliance controls over
an answer rather than authorship of it, and they read Memory Bank commitments the
department engines have no business holding. The two calls that touch the corpus and
produce customer-facing text are the two that moved, and those are the two the IAM
scoping covers.

## Consequences

### The IAM proof becomes load-bearing

The conditioned GCS bindings now scope the identity that the production path actually
runs under. Defence in depth on the object surface is a property of the deployed system
rather than of a probe.

### Two overrides, not a second implementation

`RemoteDraftingPipeline` subclasses `ReviewPipeline` and overrides `_guarded_retrieve` and
`_generate`. The per-passage Model Armor screening, the consistency check, the one-shot
constrained redraft, the computed confidence, the audit events and the escalation rule are
the *same code on the same objects*. This is deliberate and it is what makes the deployed
numbers comparable with Phase 3's at all — reimplementing the surrounding logic would mean
any difference in the figures could be the new code rather than the new environment.

### An engine failure must not look like an empty answer

`ReviewPipeline.draft` wraps its model call in `except Exception` and falls back to "no
supporting evidence was found in the corpus". Correct for a local model hiccup;
catastrophic for a remote executor, because it would file *"the engine was unreachable"* as
*"we have no policy on this"* at `confidence: low` with a human flag and no error anywhere.

So the entire remote round-trip happens inside `_guarded_retrieve`, whose only caught
exceptions are `PolicyViolation` and `SearchUnavailable`. `EngineUnavailable` is neither,
and propagates out of `draft_many`, out of the handler, and into the dispatcher's retry
path as a 500. This is the **fifth** member of the family recorded in
`attestor_core.errors.ContextUnavailable` and the first caught before it shipped;
`tests/unit/test_remote_drafting.py` pins it.

### The fan-out is a different kind of parallel, and the number changed

`DRAFT_CONCURRENCY = 8` was sized for in-process drafting, where a worker holds a thread
that is genuinely computing. A remote call is a thread parked on a socket while an Agent
Runtime instance does the work, so the ceiling is the platform's rather than the local
machine's.

The number is load-bearing rather than a knob, and it was settled by being wrong twice.

Measured against the deployed security engine, one question costs ~45s end to end. At 8
workers the arithmetic put the 123-question security partition at ~690s — past the 600s
ack deadline — so the first full-scale attempt used 24. Every partition then died inside a
second:

```
429 RESOURCE_EXHAUSTED  Quota exceeded for quota metric
'Query Reasoning Engine requests' and limit
'Query Reasoning Engine requests per minute per region'
```

Three partitions at 24 is 72 concurrent queries, and the binding limit is **regional**,
not per-engine — so the fan-out that fixed the deadline broke the quota. The resolution is
8 per partition (24 in total) plus backoff on the individual call in `_query_with_retry`,
which is where the Model Armor and search clients already handle rate limits. Retrying at
the *message* level was the wrong altitude: one throttled question would cost a redraft of
all 123, arriving back into the same congestion.

A partition may still outrun the ack deadline, and that is what the lease is for. The
redelivery at 600s finds a live, heartbeated claim and is refused with 409 rather than
starting a second copy of the same drafting work — the 900s-over-600s ordering doing
exactly the job `docs/proof/ack-deadline-margin.md` sized it for, on the first run that
genuinely needed it rather than in a unit test.

### The datastore surface had to be granted at all

Moving retrieval onto the engines turned an observation into a blocker. Agent Identity's
automatic grant (`roles/aiplatform.agentDefaultAccess`) carries 19 permissions and not one
is `discoveryengine.*`, so the engines could not query their own datastores. The engine's
own logs named it exactly:

```
PERMISSION_DENIED  permission: discoveryengine.servingConfigs.search
resource: .../dataStores/attestor-corpus-security/servingConfigs/default_config
```

Each department engine now holds `roles/discoveryengine.viewer` at **project** level. A
conditioned binding limited to its own datastore was attempted first; the probe that judged
it ran inside the IAM propagation window, so that result is **inconclusive and recorded as
untested rather than as a failure**. The narrowed claim from
`docs/proof/permission-surfaces-and-composition.md` stands: the object surface is defended
in depth, the datastore surface is defended by the policy interceptor and a build-time tool
binding, which is a code and deploy control rather than an IAM one.

### What this costs

Two hops instead of one on the drafting path, and a dependency on engine availability that
did not exist before. The `EngineUnavailable` path is why that is a retry rather than a
silent quality regression. Cold-start latency on an engine at `min_instances=0` is paid by
the first question of a partition and amortised across the rest.

## Alternatives considered

**Keep drafting in the dispatcher and document the gap (fallback J1).** Available, and it
was the fallback if this failed after five cycles. Rejected because the gap is not
cosmetic: it is the difference between IAM scoping that protects the work and IAM scoping
that protects nothing.

**Deploy one engine with nested `sub_agents` and call it once.** Rejected in session one
for the reason that produced the five-engine split: nested sub-agents share a single Agent
Identity, which is a single credential holding the union of every department's permissions
— the exact violation the fleet exists to avoid.

**Move the whole pipeline, triage and consistency included, onto the orchestrator engine.**
Rejected. It would put the Memory Bank commitments and the cross-round consistency check
inside an agent whose instruction a prompt can argue with, and ADR-0002's argument — that
a known sequence belongs in a workflow, not in a model — applies to the stages exactly as
it did before.
