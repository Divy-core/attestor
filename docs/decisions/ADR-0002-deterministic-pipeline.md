# ADR-0002 — Deterministic pipeline, LLM routing only for judgement

**Status:** Accepted · **Date:** 19 Aug 2026 · **Phase:** 3

## Context

A questionnaire run has to get from an uploaded spreadsheet to a set of evidenced
answers. There are two ways to orchestrate that:

1. Give a root `LlmAgent` a set of tools and let it decide what to do next.
2. Encode the sequence as a workflow and reserve the model for decisions that are
   genuinely judgement calls.

Option 1 is the default shape in most agent demos, and it is what "agentic" is often
taken to mean.

## Decision

**The pipeline is a workflow. The orchestrator handles judgement only.**

```
SequentialAgent(
    Intake,                                       # parse, normalise, content-derived IDs
    Triage,                                       # flash-lite, batch classify by department
    ParallelAgent(Security, Legal, Engineering),  # drafting, concurrency 8
    Assembler,                                    # compose, score confidence, flag
)
```

## Why

**The sequence is known in advance.** You cannot draft before you triage, and you cannot
assemble before you draft. There is no decision for a model to make. Handing that
sequence to an LLM router buys nothing and costs three things: latency (an extra model
round-trip per step), money (~312 routing decisions per run), and determinism — a router
that picks a different order on a re-run makes the audit trail unreproducible, and the
audit trail is the deliverable.

**Drafting is embarrassingly parallel.** No question's answer depends on another's, so
the drafters fan out. This is not a micro-optimisation: a single `gemini-3.7-flash` call
measured ~9s cold, so 40 sequential drafts would be six minutes and would kill the demo.
At concurrency 8 the measured 312-question run drafts in ~5 minutes wall-clock with a p50
of 8.3s per question.

**Department scoping is structural, not instructed.** Each drafter is constructed with
the search object for its own department and holds no handle on any other. A prompt that
says "only use the security corpus" is a request; a drafter that cannot reach the legal
datastore is a boundary.

**The model still does the hard part.** Every answer is drafted by a model against
retrieved evidence, triage is a model classification, and the consistency check is a model
comparison. What is *not* model-driven is the plumbing between them.

## What the orchestrator is still for

Genuine judgement, with a hard turn cap enforced by `callbacks/budget.py`:

- which pipeline suits this artifact (312-question CAIQ vs a 40-question follow-up)
- when to escalate to a human beyond what `policy.requires_human` already forces
- when to re-run a question that failed transiently, and when to give up
- when to stop

## Consequences

**Good.** Reproducible runs. Lower cost. Lower latency. The audit trail reconstructs the
same sequence every time. Failure is localised — one question's draft failing does not
end the run.

**Bad.** Adding a genuinely new stage means editing `pipeline.py` rather than adding a
tool and letting a router discover it. Accepted: over sixteen days the stages are known,
and the flexibility is worth less than the determinism.

**Rejected alternative — full LLM orchestration.** Would demo as "more agentic" while
being slower, costlier, and unreproducible. The Architectural Discipline criterion
rewards sound engineering choices, and delegating a known sequence to a probabilistic
router is not one.

## Evidence

- Measured drafting p50 8.3s / p95 13.0s at concurrency 8 over 312 questions.
- Triage: 16 flash-lite calls for 312 questions rather than 312 calls.
- `docs/proof/run-clean.json` carries the full measured numbers.
