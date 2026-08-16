---
name: caiq-lite
description: How to answer CAIQ-Lite yes/no questions so the binary answer carries the qualification an assessor actually needs.
department: engineering
frameworks: [caiq]
---

# Answering CAIQ-Lite questions

CAIQ-Lite is nominally yes/no. A bare yes/no is almost always the wrong answer: the
assessor needs the qualification that makes the binary meaningful.

## Required shape

**`<Yes|No|Partial>. <one sentence of specifics>. <scope limit, if any>.`**

- *"Yes. Data at rest is encrypted with AES-256-GCM across the database, object storage,
  and analytics tiers, with keys held in a managed KMS."*
- *"No. Customer-managed keys are available for the analytics tier only, not for the
  primary application database or object storage."*
- *"Partial. EU and US data residency are offered; UK-only, Swiss-only, Canadian, and
  Australian residency are not."*

## When to answer "No"

Answer **No** plainly when the capability does not exist. A "No" with a clear scope
statement is stronger than a "Yes" that has to be walked back in a follow-up round — and
a walked-back Yes is precisely what breaks round-to-round consistency and fails an audit.

Never soften a No into a Partial to look better. If the product does not do this and it is
not on the roadmap, that is the answer.

## When to answer "Partial"

Only when the capability genuinely exists for a bounded subset. Always name the boundary:
which tier, which region, which plan.

## Scope limits worth stating unprompted

- Which pricing tiers a commitment applies to (an SLA that excludes the entry tier).
- Which data stores a capability covers.
- Which regions a residency option covers.
- Whether a certification's scope includes the criterion being asked about.

## Evidence

CAIQ-Lite rarely asks for evidence explicitly, but citing the governing document makes the
answer auditable and costs one clause. Cite the policy, standard, or addendum that governs
the control.

## Anti-patterns

- A bare "Yes" with no specifics.
- "Yes" where the honest answer is "Partial" for one tier only.
- Answering the question you wish had been asked.
- Any capability claim not present in the retrieved evidence.
