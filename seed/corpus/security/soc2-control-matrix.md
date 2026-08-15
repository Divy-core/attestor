# SOC 2 Control Matrix

**Document ID:** KD-SEC-012 · **Version:** 2.1
**Report period:** 1 January - 31 December 2025 · **Report issued:** 14 March 2026
**Auditor:** Prescient Assurance LLP · **Opinion:** Unqualified, no exceptions noted
**Trust Services Criteria in scope:** Security, Availability, Confidentiality

Privacy and Processing Integrity are **not** in scope for the current report. Kestrel
states this plainly rather than allowing "SOC 2 certified" to imply broader coverage.

## Common Criteria

| Control | Description | Evidence | Test result |
|---|---|---|---|
| CC1.1 | Board oversight of security; Audit and Risk Committee meets quarterly | Committee minutes, 4 meetings in 2025 | No exceptions |
| CC1.4 | Security awareness training at hire and annually | Curricula completion records, 100% of active staff | No exceptions |
| CC2.1 | Internal communication of security responsibilities | Policy acknowledgement records in Vanta | No exceptions |
| CC3.2 | Risk assessment performed and updated | Risk register, 12 monthly reviews | No exceptions |
| CC4.1 | Monitoring of controls | Vanta continuous monitoring, 118 automated checks | No exceptions |
| CC5.2 | Technology controls deployed | Configuration baselines, IaC in Terraform | No exceptions |
| CC6.1 | Logical access provisioning and restriction | Okta group membership, JIT access records | No exceptions |
| CC6.2 | Registration and authorisation of new users | Rippling to Okta lifecycle records | No exceptions |
| CC6.3 | Access modification and removal | Leaver records, median revocation 4 minutes | No exceptions |
| CC6.6 | Boundary protection | Cloudflare WAF, security group configuration | No exceptions |
| CC6.7 | Encryption of data in transit and at rest | TLS configuration scans, KMS key inventory | No exceptions |
| CC6.8 | Malicious software prevention | Kandji device compliance, ECR image scanning | No exceptions |
| CC7.1 | Vulnerability detection | Snyk, Semgrep, Inspector findings and closure | No exceptions |
| CC7.2 | Monitoring and anomaly detection | Datadog detections, alert response records | No exceptions |
| CC7.3 | Security incident evaluation | 41 events triaged, 2 SEV2 | No exceptions |
| CC7.4 | Incident response execution | INC-2025-0044, INC-2025-0071 post-incident reviews | No exceptions |
| CC7.5 | Incident recovery | Restore test evidence, 4 quarterly tests | No exceptions |
| CC8.1 | Change management authorisation and testing | GitHub PR records, 3,847 merged PRs, 100% reviewed | No exceptions |
| CC9.2 | Vendor risk management | Vanta vendor records, 8 Tier 1 reviews | No exceptions |

## Availability

| Control | Description | Evidence | Test result |
|---|---|---|---|
| A1.1 | Capacity monitoring | Datadog capacity dashboards, scaling policies | No exceptions |
| A1.2 | Backup and recovery | 35-day PITR, 4 quarterly restore tests | No exceptions |
| A1.3 | Recovery plan testing | Full failover exercise 17 January 2026 | No exceptions |

## Confidentiality

| Control | Description | Evidence | Test result |
|---|---|---|---|
| C1.1 | Confidential information identified and maintained | Data classification, 4 tiers | No exceptions |
| C1.2 | Confidential information disposed of | Retention schedule KD-LEG-004, deletion job logs | No exceptions |

## Complementary user entity controls

The report identifies 6 controls that remain the customer responsibility, including
managing their own user accounts and entitlements within the Kestrel tenant, configuring
SSO correctly, protecting API credentials issued to them, and reviewing their own audit
logs. These are reproduced in full in Section 4 of the SOC 2 report.
