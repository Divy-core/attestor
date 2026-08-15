# Data Retention Schedule

**Document ID:** KD-LEG-004 · **Version:** 3.2 · **Owner:** Priya Raghunathan, DPO
**Approved:** 15 January 2026 · **Next review:** 15 January 2027
**Maps to:** GDPR Art. 5(1)(e) · SOC 2 C1.2 · ISO 27001:2022 A.5.33, A.8.10

## 1. Customer data

| Data class | Active retention | Post-termination | Deletion mechanism |
|---|---|---|---|
| Customer records in the platform | Term of subscription | **30 days** export window, then deleted within **90 days** | Automated `tenant-purge` job, nightly |
| Analytics warehouse (Snowflake) | Term | Deleted with tenant purge | Automated |
| Uploaded files (S3) | Term | Deleted with tenant purge | Lifecycle rule + explicit delete |
| Backups containing customer data | Rolling **35 days** | Expire naturally, maximum 35 days after primary deletion | Automatic expiry |
| Tenant audit events | **365 days** rolling | Deleted with tenant purge | Automated |

Kestrel does not retain customer data indefinitely for any purpose, including
"improvement of the service".

## 2. Kestrel-controlled data

| Data class | Retention | Basis |
|---|---|---|
| Account and contact records | Term + 3 years | Contract, limitation periods |
| Billing and tax records | **7 years** | US and Irish tax law |
| Product telemetry | 24 months | Legitimate interests, reviewed annually |
| Support tickets | Term + 2 years | Contract |
| Marketing contacts | Until consent withdrawal + 1 year | Consent / legitimate interests |
| Recruitment records | 12 months after decision | Legitimate interests |
| Employee records | Employment + 7 years | Employment and tax law |

## 3. Security and operational logs

| Log class | Retention | Notes |
|---|---|---|
| Security and audit (CloudTrail, Okta) | **7 years** | Object Lock compliance mode, immutable |
| Snowflake `ACCESS_HISTORY` | 7 years | Data access accountability |
| Application logs | 1 year | Personal data scrubbed at emission |
| VPC Flow / WAF logs | 1 year | |

## 4. Deletion verification

The `tenant-purge` job emits a structured completion record listing every store touched
and the row or object counts removed. That record is retained for 7 years as evidence of
deletion and is the artefact provided when a customer requests certification of deletion.

Deletion was exercised 11 times in 2025 (customer churn). Median time from termination to
completed purge was **41 days**, well inside the 90-day commitment.

## 5. Legal hold

A legal hold issued by the General Counsel suspends deletion for the identified data.
Holds are recorded in `LEG-GOV/holds` with scope, issuing date, and release date. Two
holds were in force during 2025, both released before year end.

## 6. Exceptions

Where a retention period conflicts with a customer contractual requirement, the shorter
period applies unless law requires otherwise. Longer retention requires DPO approval,
which has not been granted for any customer data class to date.
