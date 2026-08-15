# Business Continuity and Disaster Recovery Plan

**Document ID:** KD-SEC-010 · **Version:** 3.0 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 29 January 2026 · **Last tested:** 17 January 2026 (full failover exercise)
**Maps to:** SOC 2 A1.2, A1.3 · ISO 27001:2022 A.5.29, A.5.30, A.8.14

## 1. Objectives

| Metric | Commitment | Achieved in 17 Jan 2026 test |
|---|---|---|
| **RTO** (recovery time objective) | 4 hours | 2 hours 41 minutes |
| **RPO** (recovery point objective) | 15 minutes | 4 minutes |
| Availability SLA | 99.9% monthly | 99.97% (2025 average) |

These figures apply to the Kestrel Insight production platform on the Enterprise and
Growth tiers. The Starter tier carries no contractual availability SLA.

## 2. Architecture

Production runs in AWS `us-east-1` across three Availability Zones (`use1-az1`, `use1-az4`,
`use1-az6`). The EU instance runs in `eu-west-1`, also across three AZs. The two regions
are independent deployments serving different customer populations; they are **not** an
active-active pair, and neither is a hot standby for the other.

Within a region, loss of a single AZ is handled automatically: RDS Multi-AZ fails over,
EKS node groups span all three AZs, and S3 is regionally durable by design.

## 3. Regional failure

Loss of an entire region is handled by restore-into-a-new-region from cross-region
backups, not by traffic shifting. This is a deliberate cost and complexity trade-off and
is the reason the RTO is 4 hours rather than minutes. Customers requiring sub-hour
regional RTO are told this directly during procurement.

Cross-region backup replication targets `us-west-2` for the US instance and `eu-central-1`
for the EU instance. Backups never leave the customer data residency boundary.

## 4. Backups

- RDS automated backups with point-in-time recovery, **35-day** retention.
- Daily logical dumps to S3, retained 90 days, encrypted with a separate CMK.
- Snowflake Time Travel at 90 days on Enterprise, plus Fail-safe at 7 days.
- Configuration and infrastructure state in Terraform Cloud, versioned indefinitely.

Backups are encrypted with AES-256 and replicated cross-region within the residency
boundary.

## 5. Restore testing

Restore is tested **quarterly**, not merely backed up. The test restores the previous
day production snapshot into an isolated account and runs a data integrity suite of 1,240
assertions. Results for the last four quarters:

| Quarter | Date | Restore duration | Integrity assertions | Result |
|---|---|---|---|---|
| Q1 2026 | 17 Jan 2026 | 2h 41m | 1,240 / 1,240 | Pass |
| Q4 2025 | 11 Oct 2025 | 3h 02m | 1,238 / 1,240 | Pass with 2 known-stale fixtures |
| Q3 2025 | 19 Jul 2025 | 3h 20m | 1,240 / 1,240 | Pass |
| Q2 2025 | 12 Apr 2025 | 3h 55m | 1,240 / 1,240 | Pass |

## 6. Business continuity

Kestrel is a remote-first company with no dependency on the Austin office for operations.
Loss of the office would not affect the platform. Critical business functions have named
deputies documented in `SEC-GOV/succession`.

## 7. Communications during an outage

Status is published at `status.kestreldata.com` (Statuspage, hosted independently of AWS
`us-east-1`). Updates are posted within 30 minutes of a confirmed SEV1 or SEV2 and every
60 minutes thereafter until resolution.
