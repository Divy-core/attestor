# Access Control Standard

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
