"""Emit the security corpus. Run once; the .md files are the committed artefact."""

from pathlib import Path

OUT = Path(__file__).parent / "corpus" / "security"

DOCS: dict[str, str] = {}

DOCS["access-control-standard.md"] = """# Access Control Standard

**Document ID:** KD-SEC-002 · **Version:** 3.1 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 8 January 2026 · **Next review:** 8 January 2027
**Maps to:** SOC 2 CC6.1, CC6.2, CC6.3 · ISO 27001:2022 A.5.15, A.5.16, A.5.18, A.8.2

## 1. Identity provider

All Kestrel Data systems authenticate through Okta (tenant `kestreldata.okta.com`).
Local accounts on production systems are prohibited except for two documented break-glass
accounts held in a sealed 1Password vault, whose use triggers a PagerDuty alert to the
CISO. Both break-glass credentials were last rotated on 3 January 2026 and last used on
14 August 2025 during the INC-2025-0044 database failover.

## 2. Multi-factor authentication

Hardware-backed MFA (WebAuthn / FIDO2, YubiKey 5 series) is mandatory for every human
account. SMS and TOTP were removed as acceptable factors on 30 June 2025 following the
RISK-2025-0031 treatment plan. Coverage is 100% of the 187 active employee accounts and
100% of the 24 active contractor accounts, verified continuously by Vanta control AC-07.

## 3. Role-based access

Access is granted to Okta groups, never to individuals. There are 41 defined roles, each
mapping to a documented entitlement set in the Access Matrix (`SEC-GOV/access-matrix`),
which is the authoritative record reviewed during audit. Standing production access is
limited to 11 named Site Reliability Engineers. All other production access is
just-in-time.

## 4. Just-in-time production access

Engineers request elevated production access through the `/access` Slack workflow. A
request requires a written business justification, a linked incident or change ticket,
and approval from a second engineer holding the SRE role. Self-approval is blocked.

Granted access expires automatically after **8 hours**. All commands executed in an
elevated session are recorded via AWS Systems Manager Session Manager and shipped to the
immutable log archive described in KD-SEC-009. In Q4 2025 there were 312 JIT elevations
with a mean duration of 47 minutes.

## 5. Customer data access

Kestrel personnel do not access customer production data as a matter of routine. Access
for support purposes requires explicit per-incident customer authorisation recorded in
Zendesk, is limited to the specific tenant, and expires after 24 hours.

Between 1 January and 31 December 2025 there were 19 such authorised accesses across 7
customers, each evidenced in the SOC 2 Type II report under control CC6.1.

## 6. Joiners, movers, leavers

Joiners are provisioned via Okta Lifecycle Management from Rippling (HRIS); no access is
granted before the background check clears. Movers have entitlements recalculated on role
change, with the previous role revoked within 1 business day. Leavers have all access
revoked automatically on the HRIS termination event: the target is 1 hour, and the
measured median for 2025 was 4 minutes with a maximum of 38 minutes.

## 7. Access reviews

Quarterly user access reviews cover all production systems, the AWS Organization,
Snowflake, GitHub, and Okta itself. Reviews are performed by the system owner and
attested in Vanta. The Q4 2025 review completed on 12 January 2026 and removed 23
entitlements, of which 9 were stale contractor accesses.

## 8. Service accounts and machine identity

Workloads authenticate using short-lived credentials: AWS IAM Roles for Service Accounts
(IRSA) in EKS, and Workload Identity Federation for GCP components. Long-lived static
access keys are prohibited; the last was decommissioned on 17 November 2024. Automated
detection for newly created static keys runs hourly and pages the on-call SRE.

## 9. Password requirements

Where a password is used, minimum length is 14 characters, checked against the Have I
Been Pwned breached-password corpus at set time. Rotation is not forced on a schedule, in
line with NIST SP 800-63B guidance; rotation is forced on evidence of compromise.

## 10. Remote access

There is no traditional VPN. Access to internal applications is mediated by Cloudflare
Access with device posture checks requiring disk encryption, a current OS, and an
enrolled MDM profile (Kandji for macOS, 163 devices as of 1 February 2026).
"""

DOCS["encryption-standard.md"] = """# Encryption Standard

**Document ID:** KD-SEC-003 · **Version:** 2.4 · **Owner:** Marcus Oyelaran, CISO
**Approved:** 8 January 2026 · **Next review:** 8 January 2027
**Maps to:** SOC 2 CC6.7 · ISO 27001:2022 A.8.24 · GDPR Art. 32(1)(a)

## 1. Data at rest

All customer data at rest is encrypted using **AES-256-GCM**. No customer data is stored
unencrypted at any tier.

| Store | Mechanism | Key custody |
|---|---|---|
| Amazon RDS (PostgreSQL 16) | Storage-level encryption | AWS KMS CMK, per-environment |
| Amazon S3 | SSE-KMS | AWS KMS CMK, per-bucket |
| Snowflake | Native encryption, Tri-Secret Secure | Customer-managed key in AWS KMS |
| Amazon EBS | Encrypted volumes, enforced at Organization level | AWS KMS CMK |
| Backups | Same CMK class as source | AWS KMS |

Application-level field encryption is applied additionally to authentication secrets, API
tokens, and OAuth refresh tokens using AES-256-GCM with keys held in AWS Secrets Manager
and rotated every 90 days.

## 2. Data in transit

**TLS 1.3** is the default for all external connections. TLS 1.2 remains enabled solely
for one legacy customer SFTP integration (INT-LEGACY-0004), scheduled for decommission in
Q3 2026 and tracked as RISK-2025-0088. TLS 1.0 and 1.1 were disabled across all endpoints
on 12 April 2024.

Cipher suites are restricted to the Mozilla Intermediate profile. HSTS is enabled with
`max-age=31536000; includeSubDomains; preload`. Certificates are issued by AWS
Certificate Manager and rotated automatically, so there is no manual certificate
handling. Internal service-to-service traffic within the EKS cluster is encrypted with
mutual TLS enforced by Istio 1.24, with certificates rotated every 24 hours.

## 3. Key management

Customer-facing encryption keys are AWS KMS Customer Managed Keys. Key policies restrict
use to named service roles. Key administrators and key users are disjoint sets: no
individual holds both roles, enforced by a Service Control Policy at the AWS Organization
level.

Automatic annual rotation is enabled on all CMKs. Manual rotation is triggered on
suspected compromise. Key deletion requires a 30-day waiting period and dual
authorisation from the CISO and the VP Engineering.

## 4. Customer-managed encryption keys

Enterprise-tier customers may supply their own key material for the Snowflake data layer
through Tri-Secret Secure. As of 1 February 2026, 3 customers use this option. CMEK is
**not** currently offered for the RDS or S3 tiers. That limitation is stated explicitly in
the Security Addendum (KD-LEG-008) rather than glossed over.

## 5. Algorithm policy

Approved: AES-256-GCM, AES-256-CBC (legacy read paths only), RSA-2048 and above, ECDSA
P-256 and P-384, SHA-256 and above, Argon2id and bcrypt (cost factor 12 or higher) for
password hashing.

Prohibited: DES, 3DES, RC4, MD5 and SHA-1 for any security purpose, RSA below 2048 bits,
and any custom cryptographic construction. Kestrel does not implement its own
cryptographic primitives.

## 6. Post-quantum posture

Kestrel completed a cryptographic inventory (`SEC-GOV/crypto-inventory`, 19 January 2026)
as the first step toward post-quantum readiness. TLS connections terminated at Cloudflare
already negotiate the hybrid X25519MLKEM768 key agreement where the client supports it.
No migration commitment date has been set for internal services.
"""

DOCS["incident-response-runbook.md"] = """# Incident Response Runbook

**Document ID:** KD-SEC-005 · **Version:** 5.0 · **Owner:** Marcus Oyelaran, CISO
**Approved:** 27 January 2026 · **Last exercised:** 3 December 2025 (tabletop)
**Maps to:** SOC 2 CC7.3, CC7.4, CC7.5 · ISO 27001:2022 A.5.24-A.5.28 · GDPR Art. 33, 34

## 1. Severity definitions

| Sev | Definition | Page | Exec notify |
|---|---|---|---|
| **SEV1** | Confirmed unauthorised access to customer production data, or full platform outage | Immediate, 24/7 | CEO + CISO within 30 min |
| **SEV2** | Suspected compromise, partial outage affecting more than 10% of tenants, or confirmed data integrity fault | Immediate, 24/7 | CISO within 1 hour |
| **SEV3** | Security-relevant defect with no evidence of exploitation | Business hours | Weekly report |
| **SEV4** | Informational; hygiene finding | None | Monthly report |

## 2. Roles

The Incident Commander owns the response and is deliberately not the person fixing the
problem; ICs are drawn from a rota of 6 trained staff. The Communications Lead owns
customer, internal, and regulator messaging. A Scribe maintains the timeline for every
SEV1 and SEV2. Subject matter experts are pulled in as needed.

## 3. Lifecycle

Detect, triage, contain, eradicate, recover, learn.

Detection sources are Datadog Security Monitoring, AWS GuardDuty, AWS Security Hub, Okta
ThreatInsight, Snyk, customer reports to `security@kestreldata.com`, and the disclosure
address published at `kestreldata.com/.well-known/security.txt`.

Triage target is 15 minutes from alert to severity assignment for SEV1 and SEV2, measured
from PagerDuty acknowledgement. Median time to triage across the 41 security events
recorded in 2025 was 6 minutes.

## 4. Customer notification commitments

These are contractual and are reproduced in the DPA (KD-LEG-001) and the Security
Addendum (KD-LEG-008):

- **Personal data breach** - affected customers notified without undue delay and in any
  event **within 72 hours** of Kestrel confirming the breach.
- **Confirmed unauthorised access to customer production data** - affected customers
  notified **within 24 hours** of confirmation.

Notification goes to the customer security contact recorded on the account and includes
the nature of the incident, the categories and approximate volume of data involved, the
likely consequences, the measures taken, and a named contact point.

Kestrel acts as processor for customer personal data and will not notify supervisory
authorities on a customer behalf unless separately instructed in writing.

## 5. Regulatory notification

Where Kestrel is controller (employee data, prospect data), the DPO assesses Art. 33
notification to the Irish Data Protection Commission as lead supervisory authority within
72 hours. The assessment is recorded even where the conclusion is that notification is
not required.

## 6. Evidence handling

The IC directs preservation before remediation wherever containment allows. EBS
snapshots, memory captures where feasible, and relevant log ranges are exported to a
dedicated forensics S3 bucket with Object Lock in compliance mode, retention 1 year.
Chain of custody is recorded in the incident ticket.

## 7. External support

Kestrel retains **Mandiant** on a pre-negotiated incident response retainer (contract
MNDT-2025-1180, effective 1 September 2025, 40 hours pre-paid annually) with a 4-hour
response SLA for SEV1. Outside breach counsel is Fenwick and West LLP.

## 8. Post-incident review

Every SEV1 and SEV2 requires a blameless post-incident review within 5 business days,
published internally, with action items tracked to closure in Jira. Reviews are shared
with affected customers on request.

## 9. 2025 incident summary

41 security events recorded. Zero SEV1. Two SEV2, both resolved without customer data
exposure:

- **INC-2025-0044** (14 August 2025) - RDS failover fault causing 41 minutes of degraded
  service. No data loss; RPO and RTO both met. Root cause was a misconfigured connection
  pool ceiling.
- **INC-2025-0071** (2 November 2025) - a contractor account remained active for 38
  minutes after termination due to an HRIS webhook delay. No access occurred during the
  window, confirmed by Okta system log review. Remediated with a reconciliation job that
  runs every 15 minutes.

**No customer data breach has occurred in the history of the company.**
"""

DOCS["vulnerability-management-policy.md"] = """# Vulnerability Management Policy

**Document ID:** KD-SEC-006 · **Version:** 3.3 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 22 January 2026 · **Next review:** 22 January 2027
**Maps to:** SOC 2 CC7.1, CC7.2 · ISO 27001:2022 A.8.8

## 1. Remediation SLAs

Severity is assigned from CVSS v3.1 base score, adjusted for exploitability and exposure.
Internet-facing findings are escalated one level.

| Severity | CVSS | Remediation SLA | Measured 2025 compliance |
|---|---|---|---|
| Critical | 9.0-10.0 | **7 days** | 100% (11 of 11) |
| High | 7.0-8.9 | **30 days** | 96% (74 of 77) |
| Medium | 4.0-6.9 | **90 days** | 91% |
| Low | 0.1-3.9 | Next maintenance window | Best effort |

The three High findings that missed SLA in 2025 are documented with compensating controls
in `SEC-GOV/vuln-exceptions`; the longest overrun was 9 days beyond SLA.

## 2. Scanning coverage

| Layer | Tool | Cadence |
|---|---|---|
| Dependencies (SCA) | Snyk Open Source | Every pull request, plus daily on `main` |
| Static analysis (SAST) | Semgrep + GitHub CodeQL | Every pull request |
| Container images | Amazon ECR enhanced scanning (Inspector) | On push, plus continuous rescan |
| Infrastructure | AWS Inspector, AWS Security Hub | Continuous |
| IaC | Checkov | Every pull request |
| Secrets | Gitleaks (pre-commit) + GitHub secret scanning with push protection | Every commit |
| External attack surface | Detectify | Weekly |

A pull request cannot merge with an unresolved Critical or High SCA finding. That gate is
enforced by a required status check on the `main` branch and cannot be bypassed by
repository administrators.

## 3. Patch management

Operating system patching is handled by rebuilding immutable AMIs; instances are replaced
rather than patched in place. The base image is rebuilt weekly and on any Critical CVE
affecting it. Kubernetes node groups are recycled on a rolling basis with a maximum node
age of **30 days**.

Managed services (RDS, ElastiCache, EKS control plane) follow AWS maintenance windows,
scheduled Sundays 04:00-06:00 UTC. Customers are notified 7 days in advance of any window
expected to cause degraded service.

## 4. Penetration testing

An independent penetration test is commissioned **annually** at minimum, and additionally
before any significant architectural change. The most recent was performed by Include
Security from 3 to 14 February 2026 (engagement INSEC-2026-0219). See the Penetration
Test Executive Summary (KD-SEC-007).

## 5. Responsible disclosure

Kestrel publishes a security contact at `kestreldata.com/.well-known/security.txt` and
commits to acknowledging reports within 2 business days. Researchers acting in good faith
under the published policy will not be pursued legally.

**Kestrel does not currently operate a paid bug bounty programme.** Reports are handled
through the responsible disclosure process above.

## 6. Exceptions

A finding that cannot be remediated within SLA requires a documented exception approved by
the CISO, with a compensating control and an expiry date not exceeding 180 days.
"""

DOCS["logging-monitoring-standard.md"] = """# Logging and Monitoring Standard

**Document ID:** KD-SEC-009 · **Version:** 2.2 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 15 January 2026 · **Next review:** 15 January 2027
**Maps to:** SOC 2 CC7.2 · ISO 27001:2022 A.8.15, A.8.16

## 1. What is logged

- **Authentication** - every Okta event: sign-in success and failure, MFA challenge,
  factor enrolment, admin action.
- **Authorisation** - every JIT production elevation, approval, and expiry.
- **Data access** - all queries against customer data in Snowflake, captured via
  `ACCESS_HISTORY`, including the querying identity and the objects touched.
- **Infrastructure** - AWS CloudTrail across all accounts in the Organization, including
  management events and S3 data events for buckets holding customer data.
- **Application** - structured JSON logs with a `tenant_id` and `request_id` on every
  line. Request IDs propagate across service boundaries via W3C Trace Context.
- **Network** - VPC Flow Logs, AWS WAF logs, Cloudflare HTTP logs.

Application logs are scrubbed of personal data at emission by a shared logging library.
Field-level redaction is applied to a denylist of 34 attribute names, and the library
fails closed by redacting any field whose name matches `.*(token|secret|password|ssn).*`.

## 2. Retention

| Log class | Hot (searchable) | Archive | Total |
|---|---|---|---|
| Security and audit (CloudTrail, Okta) | 90 days in Datadog | 7 years in S3 Glacier | **7 years** |
| Application | 30 days in Datadog | 1 year in S3 | 1 year |
| Snowflake ACCESS_HISTORY | 365 days native | 7 years in S3 | 7 years |
| VPC Flow / WAF | 30 days | 1 year | 1 year |

## 3. Tamper resistance

The security archive bucket uses S3 Object Lock in **compliance mode**, which cannot be
disabled or shortened by any principal including the AWS account root. CloudTrail log file
integrity validation is enabled. The archive account is a separate AWS account with no
human standing access; writes arrive only through the organisation trail.

## 4. Alerting

Detections run in Datadog Security Monitoring. Alert routing is by severity through
PagerDuty. Current detection coverage includes impossible-travel sign-ins, MFA fatigue
patterns, IAM policy changes granting broad permissions, disabling of CloudTrail or
GuardDuty, unusual Snowflake export volume, and break-glass account use.

Alert quality is reviewed monthly. Alerts with a false-positive rate above 60% over a
rolling quarter are tuned or retired: an alert nobody trusts is worse than no alert.

## 5. Time synchronisation

All hosts synchronise to the Amazon Time Sync Service. Log timestamps are recorded in UTC
in RFC 3339 format. Clock drift beyond 1 second triggers a SEV3.

## 6. Customer access to logs

Enterprise-tier customers can retrieve tenant-scoped audit events for their own
organisation through the Audit API (`GET /v2/audit/events`), covering the previous 365
days. Kestrel does not expose its own infrastructure logs to customers; relevant extracts
are provided during incident response on request.
"""

DOCS["vendor-risk-management-policy.md"] = """# Vendor Risk Management Policy

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
"""

DOCS["business-continuity-dr-plan.md"] = """# Business Continuity and Disaster Recovery Plan

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
"""

DOCS["pen-test-executive-summary.md"] = """# Penetration Test Executive Summary

**Document ID:** KD-SEC-007 · **Engagement:** INSEC-2026-0219
**Tester:** Include Security, Inc. · **Dates:** 3-14 February 2026
**Report issued:** 24 February 2026 · **Retest completed:** 18 March 2026
**Classification:** Confidential - shareable with customers under NDA

## 1. Scope

- Kestrel Insight web application (`app.kestreldata.com`), authenticated and
  unauthenticated surfaces
- Public REST API v2 (`api.kestreldata.com`), 89 endpoints
- Multi-tenant isolation testing across 3 seeded tenant accounts
- AWS cloud configuration review (IAM, S3, VPC, EKS)
- Authentication and session management, including the Okta SAML integration

Out of scope: physical security, social engineering against staff, denial-of-service
testing, and third-party SaaS operated by subprocessors.

## 2. Methodology

Grey-box testing against OWASP ASVS 4.0 Level 2 and the OWASP API Security Top 10.
Testers were given standard-tier and admin-tier credentials for each seeded tenant plus
API documentation. 74 person-hours across two testers.

## 3. Findings summary

| Severity | Found | Remediated | Retest verified |
|---|---|---|---|
| Critical | 0 | - | - |
| High | 1 | 1 | 18 March 2026 |
| Medium | 4 | 4 | 18 March 2026 |
| Low | 7 | 5 | 18 March 2026 |
| Informational | 9 | Accepted / tracked | - |

**No cross-tenant data access was achieved.** Tenant isolation held under every tested
condition, including direct object reference manipulation, JWT tenant-claim tampering,
and GraphQL alias-based enumeration.

## 4. The High finding

**INSEC-2026-0219-H1 - Insufficient rate limiting on the password reset endpoint.**

`POST /v2/auth/password-reset` was not rate limited per account, permitting user
enumeration through response timing and, in combination with a slow SMTP path, a
potential mail-flood against a targeted address. No authentication bypass was possible
and no data was exposed.

Remediated on 6 March 2026 by adding a per-account token bucket (5 requests per hour) and
normalising the response body and timing across existing and non-existing accounts.
Retest on 18 March 2026 confirmed closure.

## 5. Medium findings, in brief

1. Session cookie missing `SameSite=Strict` on one legacy subdomain. Fixed 2 March 2026.
2. Verbose error messages disclosing library versions on 4 API endpoints. Fixed 4 March 2026.
3. S3 bucket `kestrel-public-assets` permitted `s3:ListBucket` to authenticated AWS
   principals outside the Organization. Fixed 27 February 2026 with an explicit bucket
   policy deny.
4. Missing `Content-Security-Policy` on the marketing site. Fixed 9 March 2026.

## 6. Two accepted low findings

- **L6** - Username enumeration remains possible through the SAML metadata endpoint for
  customers using IdP-initiated flows. Accepted; mitigation would break a documented
  integration pattern. Tracked as RISK-2026-0004.
- **L7** - TLS 1.2 remains enabled on the legacy SFTP endpoint. Accepted until the Q3 2026
  decommission. Tracked as RISK-2025-0088.

## 7. Tester statement

*"Kestrel Data demonstrates a mature security posture for an organisation of its size.
Tenant isolation is well implemented and defence in depth is evident throughout the
platform. The single High finding was an availability and privacy concern rather than a
confidentiality one."* - Include Security, 24 February 2026
"""

DOCS["soc2-control-matrix.md"] = """# SOC 2 Control Matrix

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
"""

DOCS["acceptable-use-policy.md"] = """# Acceptable Use Policy

**Document ID:** KD-SEC-011 · **Version:** 2.3 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 15 January 2026 · **Next review:** 15 January 2027
**Applies to:** all employees, contractors, and anyone issued Kestrel credentials

## 1. Company devices

All laptops are company-owned and managed through Kandji (macOS) or Intune (Windows).
Personal devices may not access Restricted or Confidential data. BYOD is permitted only
for calendar and chat through managed applications with app-level containerisation.

Required device posture, enforced continuously and checked at every Cloudflare Access
authentication: full-disk encryption enabled, screen lock at 5 minutes, OS within one
minor version of current, EDR agent running (CrowdStrike Falcon), and no unmanaged admin
accounts.

163 macOS and 11 Windows devices were enrolled as of 1 February 2026. Compliance rate at
the last audit was 100%.

## 2. Generative AI tools

Use of generative AI is permitted only with approved tools under a zero-retention
agreement. As of 1 February 2026 the approved list is:

| Tool | Approved for | Data tier permitted |
|---|---|---|
| GitHub Copilot Business | Code completion | Confidential (source code) |
| Anthropic Claude (Team) | Analysis, drafting | Internal |
| OpenAI ChatGPT Enterprise | Analysis, drafting | Internal |

**Customer production data may not be entered into any generative AI tool**, approved or
otherwise, without an executed DPA covering that specific processing and written approval
from the DPO. No such approval has been granted as of 1 February 2026.

Consumer tiers of the above tools are prohibited on company devices; they are blocked at
the DNS layer through Cloudflare Gateway.

## 3. Credentials

Credentials are personal and may not be shared, including with other Kestrel staff.
Shared team accounts are prohibited. Where a shared credential is unavoidable for a
third-party service that does not support SSO, it is held in a 1Password shared vault
with access logged, and the service is recorded on the SSO-gap register.

## 4. Data handling

Restricted data may not be copied to personal cloud storage, personal email, USB media,
or unmanaged devices. USB mass storage is blocked at the OS level on all managed devices.

Screen sharing during customer calls must use a dedicated demo tenant, never production.

## 5. Email and communications

Kestrel uses Google Workspace with DMARC set to `p=reject`, SPF, and DKIM enforced on all
sending domains. External email is banner-tagged. Automatic forwarding to external
addresses is disabled at the tenant level and cannot be enabled by end users.

## 6. Monitoring

Kestrel monitors use of its systems for security purposes as described in the Logging and
Monitoring Standard. Monitoring is proportionate, is not used for productivity
surveillance, and is disclosed to staff at onboarding.

## 7. Reporting

Suspected security issues must be reported to `security@kestreldata.com` or the
`#security-incidents` Slack channel immediately. Kestrel operates a no-blame reporting
culture: reporting a mistake promptly is treated as the correct behaviour, and no
disciplinary action has ever followed a good-faith self-report.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in DOCS.items():
        (OUT / name).write_text(body, encoding="utf-8")
        words = len(body.split())
        print(f"  {name:44} {words:5} words")
    print(f"wrote {len(DOCS)} security documents")


if __name__ == "__main__":
    main()
