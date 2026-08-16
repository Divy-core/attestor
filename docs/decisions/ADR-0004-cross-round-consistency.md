# ADR-0004 — Commitments are matched by meaning, and a contradicted answer is redrafted

**Status:** Accepted · **Date:** 16 Aug 2026 · **Phase:** 3

## Context

A vendor security review comes back. Round 2 lands three weeks after round 1, and the
single worst outcome in the whole product is answering it inconsistently with what was
already put in writing. That is the failure that loses an audit, and it is the failure a
human GRC team is genuinely good at preventing — they remember what they sent.

Attestor's original mechanism was content-derived question IDs: `sha256(normalised
text)[:16]`, so the same question re-asked in a later round matches its earlier
commitment however it has been renumbered, recapitalised, or re-lettered. That mechanism
works and is kept.

It is also not sufficient, which the seeded round-2 questionnaire made obvious. Its
contradiction invitation reads:

> "Our regulated business unit cannot use multi-tenant SaaS. Please describe the
> self-hosted or on-premises deployment options available for regulated customers,
> including any private-cloud or customer-VPC installation, and the timeline to provision
> one."

The round-1 commitment was recorded against *"Do you offer on-premises or self-hosted
deployment?"*. The two share almost no words and have completely different content ids.
**ID matching finds nothing, the consistency check never runs, and the contradiction goes
to the customer.** Real follow-up rounds reframe rather than re-ask; that is what makes
them follow-ups.

## Decision

**Two matchers, and a redraft rather than a warning label.**

1. **Match by content id** — the exact re-ask. Unchanged.
2. **Match by meaning** — cosine similarity between the question and the commitment
   statement, using the same embedding scorer that scores retrieval, above a measured
   threshold.
3. **When a draft contradicts a matched commitment, redraft it** with the commitment as a
   binding constraint and the rejected draft included, then re-check. **Once, never in a
   loop.** If the second attempt still contradicts, the answer is held for a human.

## The threshold, measured

Over the 40 follow-up questions × 5 seeded commitments:

| | best cosine |
|---|---|
| the five genuine pairings | 0.659 – 0.710 |
| "Has your RTO improved since the last assessment?" → RTO commitment | 0.633 (also genuine) |
| first false pairing ("Do you now offer CMEK…?" → encryption commitment) | 0.604 |
| clear false pairing ("UK-only residency?" → breach commitment) | 0.601 |

`COMMITMENT_MATCH_SCORE = 0.62`, sitting between the lowest genuine pairing and the
highest false one.

The failure modes are deliberately asymmetric. A false match costs one extra consistency
call that returns `NO_CONTRADICTION`. A missed match lets round 2 contradict round 1 in
front of the customer. The threshold is set accordingly.

## Why redraft rather than flag

Detecting a contradiction and shipping the contradicting answer with a warning attached
is not the product. The answer that reaches the customer has to honour what was
committed. The redraft is given the commitment as binding, the retrieved evidence, and
the rejected draft — telling the model what it just got wrong produces a better correction
than asking again from a blank page.

`constrained=true` then means *"we checked and it changed the answer"*, which is a
stronger claim than *"we checked"*. The answer is still held for a human, because an
answer that had to be corrected against a prior promise is exactly what a reviewer should
see.

## Measured, both ways

**Natural case** (`docs/proof/consistency-followup-natural.json`). Against the honest
corpus, the commitment is matched by meaning — id matching would have found **zero** — and
the first draft is already correct, so the verdict is `NO_CONTRADICTION` and nothing is
constrained. This is reported as it happened. Forcing a contradiction here would have
meant weakening the corpus until the demo looked better.

**Fault injection** (`docs/proof/consistency-followup-drift.json`). A "Deployment Options
Update" document is planted in the engineering corpus stating that single-tenant,
customer-VPC and on-premises deployments are now generally available — which is how this
failure actually happens in a company: documentation moves ahead of what a customer was
told in writing. Nothing in the prompt asks for a contradiction; the corpus changes
underneath the agent. Result:

```
consistency_checked  pass=initial       verdict=contradiction     constrained=True
                     "The draft offers on-premises, customer-VPC, and single-tenant
                      deployments, which the prior commitment rules out"
answer_drafted       redraft=True, superseded: "Customers in regulated sectors may
                      request a customer-VPC or on-premises/self-hosted deployment..."
consistency_checked  pass=post_redraft  verdict=no_contradiction  constrained=False

final answer: "As confirmed in the earlier review round, Kestrel Data does not offer
on-premises, self-hosted, private-cloud, single-tenant, or customer-VPC deployment
options, and none are on the roadmap."

constrained=True   needs_human=True   citations=5
```

The planted document is removed afterwards by the harness.

## Consequences

**Good.** The commitment wins over the corpus, which is the correct precedence for
anything already promised in writing. Both consistency passes are in the audit trail, so
the contradiction that was caught is reconstructable rather than being erased by the
answer that replaced it.

**Bad.** One extra model call per question that touches a commitment, and two more when a
contradiction is found. Bounded by the matcher: on the 40-question follow-up only six
questions match a commitment at all.

**A limitation, stated.** The consistency verdict is a model judgement. It fails closed —
`UNKNOWN` caps confidence at LOW and forces a human look — but a model that misreads a
subtle contradiction will produce a clean verdict on a dirty answer. The mitigation is
that every answer touching a prior commitment goes to a human regardless of verdict.

## Evidence

- `docs/proof/consistency-followup-natural.json`, `docs/proof/consistency-followup-drift.json`
- `tools/verify_consistency.py` — both modes, with cleanup in a `finally`
- `tests/unit/test_consistency.py` — matcher and redraft machinery, deterministic
