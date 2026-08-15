# Backup and Restore Procedure

**Document ID:** KD-ENG-004 · **Version:** 2.5 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 29 January 2026 · **Maps to:** SOC 2 A1.2 · ISO 27001:2022 A.8.13

## 1. What is backed up

| Asset | Method | Frequency | Retention |
|---|---|---|---|
| RDS PostgreSQL | Automated snapshots + WAL archiving (PITR) | Continuous | **35 days** |
| RDS logical dump | `pg_dump` to S3, encrypted with a separate CMK | Daily 03:00 UTC | 90 days |
| S3 objects | Versioning + cross-region replication | Continuous | 90 days for non-current versions |
| Snowflake | Time Travel | Continuous | 90 days (Enterprise), plus 7-day Fail-safe |
| EKS cluster state | Terraform state in Terraform Cloud | Every apply | Indefinite, versioned |
| Secrets | AWS Secrets Manager with versioning | On change | 30 days for prior versions |

## 2. Encryption and location

All backups are encrypted with AES-256 using AWS KMS. The daily logical dump uses a
**separate CMK** from the primary database, so that compromise of the primary key does not
compromise the backups.

Cross-region replication stays inside the residency boundary: `us-east-1` replicates to
`us-west-2`, `eu-west-1` replicates to `eu-central-1`. EU customer data never leaves the
EEA, including in backups.

## 3. Restore procedure

1. Declare the restore scope: point in time, and whether tenant-scoped or full.
2. Provision an isolated restore account from the Terraform module `restore-target`.
3. Restore the RDS snapshot or PITR target into the isolated account.
4. Run the integrity suite: 1,240 assertions covering row counts, referential integrity,
   tenant isolation invariants, and known-value spot checks.
5. Verify against the pre-incident state.
6. Cut over, or export the required subset back to production.

Restores are always performed into an isolated account first. Restoring directly over
production is prohibited: it removes the ability to compare and eliminates the option to
abandon a bad restore.

## 4. Test results

Restore is tested **quarterly**, and the test is a real restore, not a backup-listing
check.

| Quarter | Date | Duration | Assertions | Outcome |
|---|---|---|---|---|
| Q1 2026 | 17 Jan 2026 | 2h 41m | 1,240 / 1,240 | Pass |
| Q4 2025 | 11 Oct 2025 | 3h 02m | 1,238 / 1,240 | Pass, 2 known-stale fixtures corrected |
| Q3 2025 | 19 Jul 2025 | 3h 20m | 1,240 / 1,240 | Pass |
| Q2 2025 | 12 Apr 2025 | 3h 55m | 1,240 / 1,240 | Pass |

The improving duration reflects work in Q3 2025 to parallelise the integrity suite.

## 5. Tenant-scoped restore

A single customer can be restored without affecting others, by restoring to an isolated
account and exporting that tenant rows back. This was exercised once in production, on
22 September 2025, at a customer request following an erroneous bulk delete on their side.
Time to complete was 5 hours 10 minutes.

## 6. Backup deletion

Backups expire automatically. When a customer terminates and the tenant purge runs,
backups containing that data expire on the normal cycle, a maximum of **35 days** later.
Kestrel does not selectively surgically remove a tenant from historical backup images, and
says so rather than implying otherwise.
