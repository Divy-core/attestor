---
name: gdpr-dpa
description: How to answer GDPR, DPA, subprocessor, and international-transfer questions so the answer is legally precise rather than reassuring.
department: legal
frameworks: [gdpr, dpa]
---

# Answering GDPR and DPA questions

These are read by privacy counsel, not an engineer. Precision about **roles** and
**lawful basis** matters more than warmth.

## Establish the role first

Controller or processor, and for which data. The answer changes completely depending on
which. Where the company is processor for customer data but controller for account and
billing data, say both — conflating them is the most common error in these responses.

## Article-specific guidance

| Article | The answer must contain |
|---|---|
| Art. 28 | Executed DPA, documented instructions, confidentiality obligations, sub-processor terms no less protective |
| Art. 28(2) | Sub-processor authorisation model, notice period, the objection remedy |
| Art. 30 | Whether RoPA is maintained, and for which role |
| Art. 32 | The specific technical and organisational measures — algorithm, access model, testing cadence — never "appropriate measures" |
| Art. 33/34 | The **contractual** notification window and what the notification contains |
| Art. 35 | Whether a DPIA exists, its conclusion, whether prior consultation was required |
| Art. 44–49 | The transfer mechanism by name and module, plus supplementary measures |
| Art. 15–22 | How data subject rights are supported, and the turnaround commitment |

## Transfers

Name the mechanism precisely: which SCC module (Two for controller-to-processor, Three
for processor-to-sub-processor), whether the UK Addendum and Swiss Annex apply, and
whether the company is self-certified under the EU-US Data Privacy Framework. **If it is
not certified, say so** — implying certification is worse than lacking it.

A Transfer Impact Assessment answer states the conclusion, the supplementary measures,
and plainly whether any government access request has ever been received.

## Retention

Give the actual period and what starts the clock. Distinguish the **export window**, the
**deletion deadline**, and the **backup expiry** — three different numbers. A questioner
who asks about one usually needs all three, and omitting backup persistence is a common
way to be accidentally misleading.

## Anti-patterns

- "We are GDPR compliant." Meaningless; compliance is not a certification.
- Answering a processor question with controller obligations, or the reverse.
- Omitting that backups persist after primary deletion.
- Claiming a data residency option that is not offered.
