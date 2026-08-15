# Change Management Procedure

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
