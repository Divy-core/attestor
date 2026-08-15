# Vendor Risk Management Policy

**Document ID:** KD-SEC-008 · **Version:** 2.0 · **Owner:** Aaron Feldstein, General Counsel
**Approved:** 4 February 2026 · **Next review:** 4 February 2027
**Maps to:** SOC 2 CC9.2 · ISO 27001:2022 A.5.19-A.5.22 · GDPR Art. 28

## 1. Tiering

Vendors are tiered on data sensitivity and operational dependency.

| Tier | Definition | Diligence | Review |
|---|---|---|---|
| **Tier 1** | Processes customer production data, or an outage stops the platform | Full security review, SOC 2 or ISO 27001 evidence required, DPA required | Annual |
| **Tier 2** | Processes Kestrel internal or employee data | Security questionnaire, attestation review | Every 2 years |
| **Tier 3** | No access to Kestrel or customer data | Lightweight checklist | On renewal |

As of 1 February 2026 Kestrel tracks 61 vendors: 8 Tier 1, 17 Tier 2, 36 Tier 3.

## 2. Onboarding requirements

No Tier 1 vendor is engaged without: a current SOC 2 Type II report or ISO 27001
certificate reviewed by the Security team; an executed DPA including the 2021 EU Standard
Contractual Clauses where personal data leaves the EEA; documented data flows and
retention; and a recorded exit plan.

Reviews are recorded in Vanta with the reviewer, date, evidence reviewed, and residual
risk. A Tier 1 engagement additionally requires Security Council sign-off.

## 3. Subprocessors

Subprocessors engaged for customer personal data are listed publicly in the Subprocessor
List (KD-LEG-002) at `kestreldata.com/subprocessors`. Customers may subscribe to change
notifications and receive **30 days** advance notice of any addition, during which they
may object on reasonable data-protection grounds.

## 4. Concentration risk

Kestrel is materially dependent on Amazon Web Services. This is recorded as an accepted
risk (RISK-2025-0112, score 16) with the Board informed. The mitigation is architectural
portability rather than active multi-cloud: infrastructure is defined in Terraform,
workloads run in containers on Kubernetes, and the data layer uses PostgreSQL and
Snowflake, both available outside AWS. A full provider migration is estimated at 4 to 6
months and is not currently funded.

## 5. Ongoing monitoring

Tier 1 vendors are monitored continuously through SecurityScorecard, with a score drop of
more than 10 points in 30 days triggering review. Attestation expiry is tracked in Vanta,
with a reminder at 60 days before lapse.

## 6. Offboarding

On termination, the vendor must certify deletion of Kestrel and customer data within 30
days. Certification is filed in the vendor record. Access is revoked on the termination
date through the standard leaver process.
