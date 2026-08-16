---
name: soc2-controls
description: How to answer SOC 2 Trust Services Criteria questions (CC/A/C series) so the answer satisfies an auditor rather than merely reading well.
department: security
frameworks: [soc2]
---

# Answering SOC 2 control questions

A SOC 2 question is not asking "do you do this". It is asking **"can you evidence that
you did this, consistently, across the report period"**. An answer that describes intent
without evidence fails.

## Required shape

1. **State the control as implemented**, present tense, with the mechanism named. Not
   "access is restricted" but "access is granted to Okta groups, never to individuals,
   across 41 defined roles".
2. **Name the evidence artifact** an auditor would examine — the report, the log, the
   ticket queue, the review record.
3. **Give the measured figure** where one exists. A percentage or a duration beats an
   adjective every time.
4. **State the report period** when the question implies coverage over time.

## Criterion-specific guidance

| Criterion | What the answer must contain |
|---|---|
| CC1.x | Governance: who is accountable, meeting cadence, board reporting line |
| CC2.x | How the control is communicated, and acknowledgement evidence |
| CC3.x | Risk assessment cadence, scoring method, treatment threshold |
| CC6.1–6.3 | Provisioning, modification, revocation — with a measured revocation time |
| CC6.6–6.8 | Boundary protection, encryption in transit and at rest, malware controls |
| CC7.1–7.2 | Detection: scanning coverage by layer, cadence, remediation SLAs |
| CC7.3–7.5 | Incident lifecycle: classification, response, recovery, post-incident review |
| CC8.1 | Change authorisation, testing, approval — quantified where possible |
| CC9.2 | Vendor tiering, diligence requirements, reassessment cadence |
| A1.x | Availability: capacity, backup, and a *tested* recovery plan with results |
| C1.x | Confidentiality: classification tiers and disposal evidence |

## Scope honesty

If a criterion is **not in scope** for the current report, say so plainly and name what
is. "SOC 2 Type II covering Security, Availability, and Confidentiality; Privacy and
Processing Integrity are not in scope" is a better answer than one that lets
"SOC 2 certified" imply everything.

Complementary user entity controls belong in the answer whenever the question touches a
shared responsibility.

## Anti-patterns

- "We follow industry best practice." Names no control and evidences nothing.
- A bare "Yes" for a question that asks *how*.
- Claiming a certification without its scope statement.
- Any figure not present in the retrieved evidence.
