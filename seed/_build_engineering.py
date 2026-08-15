"""Emit the engineering corpus. Run once; the .md files are the committed artefact."""

from pathlib import Path

OUT = Path(__file__).parent / "corpus" / "engineering"

DOCS: dict[str, str] = {}

DOCS["sdlc-policy.md"] = """# Secure Software Development Lifecycle Policy

**Document ID:** KD-ENG-001 · **Version:** 4.1 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 20 January 2026 · **Next review:** 20 January 2027
**Maps to:** SOC 2 CC8.1 · ISO 27001:2022 A.8.25-A.8.31

## 1. Branching and review

`main` is protected and always releasable. All work happens on short-lived branches
merged by pull request. `main` requires:

- At least **one approving review** from a engineer other than the author. Self-approval
  is blocked at the platform level.
- Two approvals for changes touching authentication, authorisation, tenant isolation, or
  cryptography. These paths are enforced by `CODEOWNERS`.
- All required status checks green: unit tests, integration tests, Semgrep, CodeQL, Snyk,
  Checkov, and type checking.
- Linear history; merge commits are disabled.

In 2025 Kestrel merged **3,847 pull requests**, of which 100% carried at least one
approving review. Administrator bypass of branch protection is disabled and its use would
generate an audit event; it was used zero times in 2025.

## 2. Environments

| Environment | Purpose | Data |
|---|---|---|
| `local` | Developer machines | Synthetic fixtures only |
| `ci` | Automated testing | Synthetic fixtures only |
| `staging` | Pre-production verification | Synthetic and anonymised data |
| `production` | Live service | Customer data |

**Production data is never copied to a lower environment.** Staging fixtures are generated
synthetically by `tools/gen_fixtures.py`. There is no "sanitised production dump" process,
because sanitisation is unreliable and the temptation to skip it is high.

## 3. Testing requirements

- Unit test coverage gate at **80%** on changed lines, enforced in CI.
- Integration tests run against ephemeral infrastructure provisioned per pull request.
- Tenant isolation tests run on every build: a suite of 84 assertions attempting
  cross-tenant reads through every public API surface.
- Load tests before any release expected to change the performance profile.

## 4. Security in the pipeline

Static analysis (Semgrep, CodeQL), dependency scanning (Snyk), IaC scanning (Checkov), and
secret scanning (Gitleaks pre-commit plus GitHub push protection) all run on every pull
request. A Critical or High dependency finding blocks the merge.

## 5. Dependency policy

New third-party dependencies require review against the Third-Party Library Policy
(KD-ENG-008). Lockfiles are committed. Renovate opens automated upgrade pull requests
weekly; security patches are auto-merged when CI is green and the change is a patch-level
bump.

## 6. Release

Releases are continuous: merge to `main` triggers a deployment to staging, an automated
verification suite, and then a progressive rollout to production. Rollout is canary-based:
5% of traffic for 15 minutes, then 25%, then 100%, with automatic rollback on error-rate
or latency regression.

Median lead time from merge to production in 2025 was **34 minutes**. Change failure rate
was 2.1%; mean time to restore was 18 minutes.

## 7. Separation of duties

Engineers cannot deploy their own change to production without the automated pipeline: no
human has the ability to push a container image to the production registry directly. The
deployment role is held by the CI service identity, which authenticates through OIDC and
holds no long-lived credentials.
"""

DOCS["change-management-procedure.md"] = """# Change Management Procedure

**Document ID:** KD-ENG-002 · **Version:** 3.0 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 20 January 2026 · **Maps to:** SOC 2 CC8.1 · ISO 27001:2022 A.8.32

## 1. Change classes

| Class | Definition | Approval | Example |
|---|---|---|---|
| **Standard** | Pre-approved, low risk, automated | PR review + CI | Application code, config change through IaC |
| **Normal** | Material risk or customer-visible | PR review + Change Advisory sign-off | Schema migration, dependency major version |
| **Emergency** | Fixing an active SEV1/SEV2 | Retrospective within 1 business day | Hotfix during an incident |

The overwhelming majority of changes are Standard. In 2025 there were 3,847 Standard, 61
Normal, and 9 Emergency changes.

## 2. Normal change requirements

A Normal change additionally requires a written rollback plan, a customer-impact
assessment, and sign-off from the Change Advisory group (VP Engineering plus one SRE).
Schema migrations must be backward compatible for at least one release, so that a rollback
does not require a data migration.

## 3. Emergency changes

An Emergency change may bypass the Change Advisory step but never bypasses code review or
CI. The Incident Commander authorises it, and a retrospective record is filed within one
business day covering what changed, why the normal path was bypassed, and whether the
change should be reworked.

All 9 Emergency changes in 2025 have completed retrospectives on file.

## 4. Infrastructure changes

Infrastructure is managed exclusively through Terraform in the `kestrel-infra` repository.
Manual changes through the AWS console are prohibited in production; drift detection runs
every 6 hours and raises a SEV3 on any unmanaged change. Drift was detected 4 times in
2025, all traced to AWS-initiated maintenance rather than human action.

## 5. Database changes

Migrations run through Atlas with a plan-and-approve step. Destructive operations (column
drop, table drop) require a two-phase deployment separated by at least one release: the
column is first stopped being read, then dropped in a later release.

## 6. Customer notification

Customers are notified at least **7 days** in advance of any change expected to cause
degraded service or to require action on their part. Breaking API changes follow the
deprecation policy: minimum **12 months** notice, with the deprecated version remaining
available throughout.

## 7. Change freeze

A change freeze applies from 20 December to 2 January, and during any active SEV1. Only
Emergency changes are permitted during a freeze.
"""

DOCS["infrastructure-architecture-overview.md"] = """# Infrastructure Architecture Overview

**Document ID:** KD-ENG-003 · **Version:** 3.4 · **Owner:** Dana Whitfield, VP Engineering
**Last updated:** 5 February 2026 · **Classification:** Confidential, NDA required

## 1. Cloud footprint

Kestrel runs on **Amazon Web Services**. There are two independent production instances:

| Instance | Region | Serves |
|---|---|---|
| US | `us-east-1` (3 AZs) | North America, and customers with no residency requirement |
| EU | `eu-west-1` (3 AZs) | EEA, UK, and Swiss customers electing EU residency |

Backup replication targets `us-west-2` and `eu-central-1` respectively, staying within the
residency boundary.

A small number of internal, non-customer-facing workloads run on Google Cloud (BigQuery
for internal finance reporting). No customer data is processed there.

## 2. Account structure

AWS Organizations with 9 accounts: `management`, `security-tooling`, `log-archive`,
`shared-services`, `prod-us`, `prod-eu`, `staging`, `dev`, `sandbox`. Service Control
Policies enforce guardrails at the Organization level, including mandatory EBS encryption,
denial of root user actions, and denial of CloudTrail or GuardDuty disablement.

The `log-archive` account has no human standing access at all.

## 3. Compute

Amazon EKS (Kubernetes 1.31) with managed node groups spanning three availability zones.
Workloads run as containers built from distroless base images, as non-root, with read-only
root filesystems and dropped Linux capabilities. Pod Security Standards are enforced at
the `restricted` level.

Node images are immutable; nodes are recycled with a maximum age of 30 days rather than
patched in place.

## 4. Data layer

| Store | Technology | Purpose |
|---|---|---|
| Primary OLTP | Amazon RDS PostgreSQL 16, Multi-AZ | Application state |
| Analytics | Snowflake | Customer analytics workloads |
| Object storage | Amazon S3 | Uploads, exports, backups |
| Cache | Amazon ElastiCache (Redis 7) | Session and query cache |
| Search | OpenSearch 2.13 | In-product search |

## 5. Tenant isolation

Kestrel Insight is **multi-tenant** with logical isolation. There is no per-customer
infrastructure, no single-tenant deployment option, and no customer-VPC option.

Isolation is enforced at three layers:

1. **Database** - PostgreSQL row-level security policies keyed on `tenant_id`, applied at
   the connection level from an authenticated session variable. No application query can
   opt out.
2. **Application** - every request carries a validated tenant claim; the data access layer
   refuses any query lacking a tenant predicate. This is enforced by a repository base
   class, not by convention.
3. **Storage** - S3 object keys are tenant-prefixed and IAM policies constrain access by
   prefix.

Isolation is verified continuously by an 84-assertion test suite on every build, and was
independently tested in the February 2026 penetration test with no cross-tenant access
achieved.

## 6. Network

Private subnets for all compute and data. No public IP addresses on workload instances.
Ingress is exclusively through Cloudflare, then an AWS Application Load Balancer, then the
Istio ingress gateway. Egress is through NAT gateways with an allowlist for known
destinations.

Security groups are managed in Terraform and default-deny. There are no `0.0.0.0/0`
ingress rules in production; this is asserted by a Checkov rule that fails the build.

## 7. Observability

Datadog for metrics, logs, APM, and security monitoring. Distributed tracing uses W3C
Trace Context propagated across all services. Every log line carries `tenant_id` and
`request_id`.
"""

DOCS["backup-restore-procedure.md"] = """# Backup and Restore Procedure

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
"""

DOCS["secrets-management-standard.md"] = """# Secrets Management Standard

**Document ID:** KD-ENG-006 · **Version:** 2.1 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 22 January 2026 · **Maps to:** SOC 2 CC6.1 · ISO 27001:2022 A.8.24

## 1. Where secrets live

| Secret class | Store | Rotation |
|---|---|---|
| Application secrets, API keys | AWS Secrets Manager | 90 days, automated |
| Database credentials | AWS Secrets Manager with RDS-managed rotation | 30 days, automated |
| Encryption keys | AWS KMS (key material never leaves KMS) | Annual, automated |
| TLS certificates | AWS Certificate Manager | Automatic before expiry |
| CI/CD credentials | GitHub OIDC to AWS IAM roles - **no stored credentials** | N/A |
| Human-held shared credentials | 1Password shared vaults | On personnel change |

## 2. What is prohibited

- Secrets in source code, including test fixtures, comments, and commit history.
- Secrets in environment files committed to a repository.
- Secrets in CI logs, container image layers, or Terraform state in plaintext.
- Long-lived static AWS access keys. The last was decommissioned 17 November 2024, and
  creation of a new one triggers an hourly detection that pages the on-call SRE.
- Sharing a secret over Slack, email, or ticket. If it has been sent that way, it is
  treated as compromised and rotated.

## 3. Detection

Three layers, because one is not enough:

1. **Pre-commit** - Gitleaks runs locally through a managed pre-commit hook.
2. **Push protection** - GitHub secret scanning with push protection enabled
   organisation-wide, which blocks the push rather than alerting after the fact.
3. **Continuous** - GitHub Advanced Security scans all repository history daily, including
   private repositories.

In 2025 push protection blocked **7** attempted secret commits. None reached the
repository. Zero secrets were found in historical scanning.

## 4. Runtime access

Workloads obtain secrets at runtime through the AWS Secrets Manager CSI driver, mounted as
in-memory `tmpfs` volumes. Secrets are never written to disk, never baked into container
images, and never passed as environment variables where they could leak into a crash dump
or a process listing.

## 5. Rotation on compromise

A suspected compromised secret is rotated immediately, before investigation completes.
The order is rotate, then investigate, then decide whether it was actually exposed:
delaying rotation while establishing certainty extends the exposure window for no benefit.

## 6. Customer-issued API credentials

API keys issued to customers are stored as Argon2id hashes; Kestrel cannot recover the
plaintext of a customer API key and will not do so on request. Keys are displayed once at
creation. Customers can create, scope, and revoke keys through the console, and each key
carries a last-used timestamp so stale keys are visible.
"""

DOCS["availability-sla.md"] = """# Availability Service Level Agreement

**Document ID:** KD-ENG-007 · **Version:** 3.0 · **Owner:** Aaron Feldstein, General Counsel
**Effective:** 1 January 2026 · Forms part of the Master Subscription Agreement

## 1. Commitment

Kestrel commits to **99.9% monthly uptime** for the Kestrel Insight platform on the
Enterprise and Growth tiers.

The **Starter tier carries no availability commitment.** It is offered on a reasonable
efforts basis and is excluded from service credits.

## 2. Definitions

**Uptime** is the percentage of minutes in a calendar month during which the Service is
Available, calculated as:

    Uptime % = ((Total Minutes - Downtime Minutes) / Total Minutes) x 100

**Downtime** is any minute during which all requests to the production API from the
external monitoring network return a 5xx error or fail to connect. Measurement is by
independent third-party monitoring (Checkly, 7 global locations, 30-second interval), not
by Kestrel internal instrumentation. The monitoring data is available to customers on
request.

**Available** excludes degraded performance that does not produce errors. A slow response
is not Downtime under this SLA.

## 3. Exclusions

Downtime does not include unavailability caused by:

- Scheduled maintenance, notified at least 7 days in advance, in the window Sundays
  04:00-06:00 UTC, capped at 4 hours per month.
- Emergency maintenance to address a security vulnerability, notified as soon as
  practicable.
- Customer configuration, customer code, or customer exceeding documented rate limits.
- Failure of a customer-controlled dependency such as their identity provider.
- Force majeure.
- Suspension for non-payment or breach, per the Agreement.

## 4. Service credits

| Monthly uptime | Credit (% of monthly fee) |
|---|---|
| Below 99.9% but at or above 99.0% | 10% |
| Below 99.0% but at or above 95.0% | 25% |
| Below 95.0% | 50% |

Credits are the sole and exclusive remedy for failure to meet this SLA. A claim must be
submitted within **30 days** of the end of the affected month, with supporting detail.
Credits are applied to the next invoice and are not refundable in cash.

## 5. Historical performance

| Year | Measured availability | Months below 99.9% | Credits issued |
|---|---|---|---|
| 2025 | 99.97% | 0 | 0 |
| 2024 | 99.94% | 1 (March, 99.87%) | 1 customer, 10% |
| 2023 | 99.91% | 1 (August, 99.82%) | 3 customers, 10% |

## 6. Support response targets

Support targets are separate from this SLA and are not credit-bearing.

| Severity | First response | Coverage |
|---|---|---|
| Urgent (production down) | 1 hour | 24 x 7 |
| High (major feature impaired) | 4 business hours | Business hours |
| Normal | 1 business day | Business hours |
| Low | 3 business days | Business hours |

Business hours are 08:00-18:00 US Central, Monday to Friday, excluding US public holidays.
"""

DOCS["third-party-library-policy.md"] = """# Third-Party Library Policy

**Document ID:** KD-ENG-008 · **Version:** 1.4 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 22 January 2026 · **Maps to:** SOC 2 CC7.1 · ISO 27001:2022 A.8.28

## 1. Adding a dependency

A new direct dependency requires a pull request that documents:

1. Why an existing dependency or the standard library is insufficient.
2. The licence, checked against the allowlist below.
3. Maintenance signal: last release date, open critical issues, number of maintainers.
4. Transitive dependency count added.

Review is by any engineer other than the author. Dependencies pulling in more than 20
transitive packages require VP Engineering approval.

## 2. Licence policy

**Allowed:** MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, Python Software Foundation,
Unlicense, CC0.

**Requires legal review:** MPL-2.0, LGPL-2.1, LGPL-3.0, EPL-2.0.

**Prohibited:** GPL-2.0, GPL-3.0, AGPL-1.0, AGPL-3.0, SSPL, BUSL, Commons Clause, and any
licence without a clear grant. AGPL and SSPL are prohibited because Kestrel distributes a
network service.

Licence compliance is checked automatically in CI by FOSSA on every pull request. A
prohibited licence fails the build.

## 3. Software Bill of Materials

Kestrel generates a CycloneDX SBOM for every production container image at build time.
SBOMs are retained for 2 years and are provided to customers on request under NDA. The
current production SBOM covers 1,184 components across 6 services.

## 4. Vulnerability response

Dependency vulnerabilities follow the SLAs in KD-SEC-006: Critical 7 days, High 30 days,
Medium 90 days. Snyk runs on every pull request and daily against `main`. A Critical or
High finding blocks merge.

Where no upstream fix exists within the SLA, the options in order of preference are: apply
a vendored patch, replace the dependency, or accept the risk with a documented
compensating control and CISO approval.

## 5. Upgrades

Renovate opens upgrade pull requests weekly. Patch-level security upgrades auto-merge when
CI is green. Minor and major upgrades are reviewed manually.

An explicit goal is to stay within one minor version of current for all direct
dependencies. As of 1 February 2026, 94% of direct dependencies met that target; the 6%
that did not are tracked with named owners.

## 6. Unmaintained dependencies

A dependency with no release in 24 months and no active maintainer is flagged for
replacement. Three such dependencies were identified in the January 2026 review, all
scheduled for replacement by Q3 2026.

## 7. Internal forks

Forking a third-party library requires VP Engineering approval and creates an obligation
to track upstream security advisories manually. Kestrel currently maintains **zero**
internal forks, deliberately.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in DOCS.items():
        (OUT / name).write_text(body, encoding="utf-8")
        print(f"  {name:44} {len(body.split()):5} words")
    print(f"wrote {len(DOCS)} engineering documents")


if __name__ == "__main__":
    main()
