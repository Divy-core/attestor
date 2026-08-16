# Data Protection Impact Assessment Register

**Document ID:** KD-LEG-011 · **Version:** 1.9 · **Owner:** Priya Raghunathan, DPO
**Approved:** 16 January 2026 · **Next review:** 16 January 2027
**Maps to:** GDPR Articles 35, 36, 9, 8, 22

## 1. When Kestrel performs a DPIA

A DPIA is performed where processing is likely to result in a high risk to data subjects,
and in every case triggered by the Article 35(3) list or the lead supervisory authority's
own list. In practice Kestrel triggers an assessment for: a new machine learning feature
processing customer data, a new sub-processor receiving customer content, a change to the
residency architecture, and any processing of a new category of personal data.

The DPO owns the assessment, the VP Engineering supplies the technical description, and
the outcome is recorded in the Security Steering Group minutes.

## 2. Completed assessments

**Four DPIAs have been completed**, all reviewed and signed off by the DPO:

| ID | Subject | Completed | Residual risk | Outcome |
|---|---|---|---|---|
| DPIA-2024-01 | Product telemetry collection and retention | 14 May 2024 | Low | Proceed. Event allowlist introduced; free-text fields excluded; retention set to 13 months |
| DPIA-2025-01 | EU instance and residency architecture | 3 March 2025 | Low | Proceed. EU content confined to `eu-west-1` including backups |
| DPIA-2025-02 | Anomaly-highlighting model | 22 July 2025 | Low | Proceed. Per-tenant training only; artefact confined to tenant boundary |
| DPIA-2025-03 | Natural-language query via AWS Bedrock | 4 November 2025 | Low | Proceed subject to conditions: zero-retention configuration, regional invocation, tenant-level opt-out, subprocessor disclosure |

No assessment has concluded that residual high risk remains, and consequently **no prior
consultation with a supervisory authority under Article 36 has been required**. The Irish
Data Protection Commission is Kestrel's lead supervisory authority through its EU
establishment.

## 3. Special category data

**Kestrel does not knowingly process special category personal data under Article 9.** The
platform is not designed for it, the Acceptable Use terms in the master agreement prohibit
uploading it, and no field in the data model is intended to hold it.

Kestrel cannot inspect customer-uploaded datasets — they are opaque by design — so the
control is contractual and architectural rather than detective: uniform encryption, uniform
access control, and uniform retention are applied to all customer content, at a standard
appropriate to sensitive data, so that a customer who uploads something they should not
have has not thereby lowered the protection applied.

Where a customer requires processing of special category data, that requires a written
agreement with Kestrel's General Counsel; **no such agreement is currently in place with
any customer**.

## 4. Children's data

**Kestrel Insight is not directed at children.** It is a business-to-business analytics
product sold to organisations, made available under a master agreement executed by a
business entity, and it is not marketed to consumers. End users are the customer's own
personnel.

Kestrel does not knowingly collect personal data from anyone under 16. There is no age
verification because there is no consumer sign-up route; account creation is by tenant
administrator invitation only. This is the assessment recorded in DPIA-2024-01 and
reflected in the ROPA (KD-LEG-004).

## 5. Automated decision-making

Kestrel performs **no automated decision-making producing legal effects or similarly
significant effects** concerning data subjects, within the meaning of Article 22. The two
machine learning features described in KD-SEC-019 §2 surface information to the customer's
own analysts; the decisions that follow are made by people at the customer, using the
customer's own data.

## 6. Review cadence

Each completed DPIA is reviewed annually, and immediately on a material change to the
processing it covers. The 2026 review cycle for all four assessments completed on
16 January 2026, with no changes to the residual risk ratings.

## 7. Availability

DPIA summaries are provided to customers under NDA on request through the account team.
The full assessments are not distributed, because they contain architectural detail that
is itself Confidential, but the DPO will walk a customer's privacy team through an
assessment on a call.
