# Cloud Exit and Portability Plan

**Document ID:** KD-ENG-010 · **Version:** 1.7 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 30 January 2026 · **Next review:** 30 January 2027
**Maps to:** SOC 2 A1.2, CC9.1 · ISO 27001:2022 A.5.19, A.5.21, A.5.30 · EBA/DORA-aligned

## 1. Why this exists

Kestrel runs entirely on AWS. That is a concentration risk, recorded as `RISK-2025-0044`
and **accepted** rather than mitigated by multi-cloud, on the reasoning that operating two
clouds badly is worse than operating one well at this size. Accepting a risk obliges you
to know what you would do if it materialised, which is what this document is. It is
reviewed annually and was last tested in a tabletop exercise on 19 November 2025.

## 2. Exit triggers

The plan would be invoked on: sustained material degradation of AWS service, a commercial
breakdown or unacceptable pricing change, a regulatory instruction, or a security failure
at the provider that could not be compensated for.

## 3. Portability by design

The architecture is deliberately biased toward portable components, and the exceptions are
listed rather than glossed over:

| Layer | Technology | Portability |
|---|---|---|
| Compute | Kubernetes (EKS) | High — standard manifests, no EKS-specific APIs in workloads |
| Container images | OCI in ECR | High — registry is a copy operation |
| Data warehouse | Snowflake | High — Snowflake runs on AWS, Azure, and GCP |
| Object storage | S3 | High — S3-compatible APIs are ubiquitous |
| Relational | Aurora PostgreSQL | Medium — PostgreSQL wire-compatible, migration by logical replication |
| Infrastructure definition | Terraform | Medium — provider blocks rewritten, structure retained |
| Identity | Okta (not AWS) | High — already provider-independent |
| Edge | Cloudflare (not AWS) | High — already provider-independent |
| Key management | AWS KMS | **Low** — keys are non-exportable by design; exit requires re-encryption under new keys |
| Queueing | SQS/SNS | Medium — thin adapter in `kd-platform`, one implementation to write |

Placing identity and edge outside AWS was an explicit architectural choice: it means an
exit does not simultaneously change how customers reach the service and how staff
authenticate to it.

## 4. Estimated exit timeline

The tabletop exercise produced these estimates, which are planning figures rather than
commitments:

| Phase | Work | Estimate |
|---|---|---|
| 1 | Target selection, landing zone, network and identity foundation | 4–6 weeks |
| 2 | Terraform provider rewrite, CI/CD retargeting | 6–8 weeks |
| 3 | Data migration — Snowflake region move, PostgreSQL logical replication, object storage sync | 4–6 weeks (overlapping) |
| 4 | Re-encryption under new KMS, key rotation for all data at rest | 2–3 weeks |
| 5 | Parallel run, cutover per instance, decommission | 3–4 weeks |
| | **Total** | **approximately 5–6 months** |

## 5. Customer-facing continuity during an exit

An exit would be executed instance by instance, with EU and US cut over separately, and
**data residency commitments would be honoured throughout**: an EU tenant would move to an
EU region of the target provider, never transiting a US region. Customers would be given a
minimum of 90 days' notice of a planned provider change and would retain their export
rights throughout.

## 6. Customer's own exit from Kestrel

Distinct from Kestrel's exit from AWS, and more likely to be what a reviewer is asking
about. A customer may leave at any time and take their data with them:

* full self-service export of all customer-uploaded datasets in their original format,
  plus account identity data and usage events as JSON or CSV;
* the same through the public API, with no volume charge;
* **30 days' post-termination access** for export, extendable by agreement;
* deletion of live data within 30 days of the export window closing, backups expiring
  within a further 35 days, and a certificate of deletion on request.

No proprietary format is used for customer-uploaded content: what was loaded is what comes
back.

## 7. Dependency on other subprocessors

Equivalent substitution analysis exists for the operational subprocessors: Datadog
(substitutable, 2–3 weeks), SendGrid (substitutable, 1 week), Zendesk (substitutable,
2 weeks), Stripe (substitutable, 3–4 weeks), Okta (substitutable, 4 weeks), Cloudflare
(substitutable, 2 weeks). None of these holds customer-uploaded content, which is why
their substitution is measured in weeks rather than months.

## 8. Testing

The plan is exercised annually as a tabletop with the SRE team, the VP Engineering, and
the CISO. The 19 November 2025 exercise identified two gaps — the SQS/SNS adapter was not
in place, and the KMS re-encryption path was undocumented — and both were closed by
14 January 2026. The next exercise is scheduled for November 2026.
