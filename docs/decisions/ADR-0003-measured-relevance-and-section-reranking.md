# ADR-0003 — Retrieval is candidate generation; relevance is measured locally

**Status:** Accepted · **Date:** 20 Aug 2026 · **Phase:** 3

## Context

Vertex AI Search (Discovery Engine) standard edition returns, for each hit, a document
and one snippet chosen by its own matcher. It returns **no relevance score** — probed
directly: no `model_scores`, no `relevance_score` in `derived_struct_data`. Enterprise
edition exposes scoring but requires an engine/app serving config and a paid tier.

The first implementation therefore derived a score from rank: `0.95 - rank * 0.1`.

Two consequences, both worse than they first appeared.

**The confidence signal was hollow.** `compute_confidence` consumes `max_retrieval_score`
and `mean_retrieval_score`. With rank-derived inputs, two of its four signals were
positional artifacts: the top hit read 0.95 whether it was a bullseye or barely related.
Confidence collapsed into a function of citation count and hedging, while still being
described as "computed from observable signals" — and confidence drives `requires_human`,
which drives the approval queue.

**The snippet was often the wrong part of the right document.** Measured: asked *"How
long does a restore from backup take?"*, retrieval returns `backup-restore-procedure` —
the correct document — with a snippet about **backup encryption**. The drafter reads a
passage that does not answer the question and correctly replies `INSUFFICIENT_EVIDENCE`.
The retrieval hit counts in a recall metric; the answer does not exist.

## Decision

**Discovery Engine generates candidates. Relevance is measured locally, over sections.**

```
question ──> query expansion ──> Discovery Engine (per department)  # candidates
                                        │
                                        ▼
                          candidate documents, deduped
                                        │
                        split on their own markdown headings
                                        │
                    cosine(question, section) with text-embedding-005
                                        │
                        sections ranked GLOBALLY, top 5 cited
```

Three specifics that are load-bearing:

1. **Cosine similarity, asymmetric task types.** The question is embedded as
   `RETRIEVAL_QUERY`, passages as `RETRIEVAL_DOCUMENT`. This is a measured distance
   between two vectors — not a model asked how confident it feels.
2. **Sections compete globally, not one per document.** Asked about on-premises
   deployment, `infrastructure-architecture-overview` scored 0.613 on its opening "Cloud
   footprint" section and 0.607 on "Tenant isolation" — which is the section that says
   there is no single-tenant or customer-VPC option. One-section-per-document loses the
   answer by 0.006.
3. **Scored against the original question, never the expanded variant.** A variant is a
   retrieval device; scoring against it would flatter exactly the passages the expansion
   dragged in.

## Measured

Over the 63 hand-labelled retrieval pairs (`docs/proof/confidence-calibration.json`),
against the 26-document corpus this was first built on:

| | snippet cosine | section rerank |
|---|---|---|
| top hit is the labelled document | 47 / 60 | **55 / 60** |
| median score, relevant passages | 0.653 | 0.691 |
| median score, other retrieved passages | 0.601 | 0.609 |
| median separation | 0.051 | **0.080** |

**Re-measured against the expanded 46-document corpus**, where the numbers move and one
of them moves alarmingly:

| | value |
|---|---|
| top hit is the labelled document | 53 / 60 |
| best labelled passage vs best distractor, median | 0.691 vs 0.635 → **0.054** |
| best labelled passage outranks the best distractor | 30 / 44 questions that retrieved a distractor |
| **pooled** relevant vs irrelevant passages, median | 0.628 vs 0.623 → **0.007** |

The pooled figure looks like a collapse and is mostly an artifact of the metric, which is
worth stating rather than quietly dropping. Once sections compete globally, a question
routinely retrieves **several sections of the correct document** — including its "Review
cadence" and its approval header, which answer nothing. Those count as "relevant" under a
document-level label and drag the pooled distribution down. The decision-relevant
comparison is best-answering-passage against best-distractor, which is 0.054.

What is genuinely true: on a broader corpus, more documents legitimately bear on a given
question — MFA appears in the access control standard *and* in personnel security — so
"not the labelled document" stops meaning "irrelevant". The score discriminates modestly,
and the confidence function leans correspondingly more on citation count, hedging,
contradiction, and cross-department signals.

Thresholds are derived from the two distributions `compute_confidence` actually consumes
— per-question max and per-question mean over the cited passages, not the pooled
passage-level scores: `_WEAK_SCORE` **0.57** (p05 of per-question max),
`_STRONG_MAX_SCORE` **0.69** (its median), `_STRONG_MEAN_SCORE` **0.59** (p25 of
per-question mean).

## Cost

Passage vectors are cached by content hash in one scorer shared across the run, so a
section read by thirty questions is embedded once. A full 312-question run embeds on the
order of 10⁵ characters. At the published `text-embedding-005` rate this is a fraction of
a cent, recorded in the run's budget line rather than assumed.

## Consequences

**Good.** Confidence means something. Citations point at the section that actually
answers, so a reviewer clicking a citation lands on the sentence rather than the
document. Scoring is deterministic and reproducible — the same question over the same
corpus produces the same scores.

**Bad.** A second retrieval hop: corpus documents are read from GCS and split locally, so
retrieval now depends on the bucket as well as the datastore. Mitigated by degrading to
snippet scoring when a document cannot be read, and by caching.

**Rejected — Enterprise edition.** It returns a relevance score, but it is a paid tier, it
requires restructuring the serving path through an engine/app config, and the score is
still opaque. A measured cosine is cheaper, explainable, and testable.

**Rejected — asking the model to rate relevance.** One extra model call per passage, a
number that cannot be reproduced, and precisely the self-reported-confidence pattern the
domain model exists to avoid.

## Evidence

- `docs/proof/confidence-calibration.json` — both distributions and the derived thresholds.
- `docs/proof/retrieval-recall.md` — recall@5 95% on the expanded corpus, gate 85%.
- `packages/attestor-platform/src/attestor_platform/search/sections.py` — the splitter.
- `tests/unit/test_relevance.py`, `tests/unit/test_sections.py`, `tests/unit/test_expansion.py`.
