# Endpoint Protection and Data Loss Prevention Standard

**Document ID:** KD-SEC-015 · **Version:** 2.2 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 19 January 2026 · **Next review:** 19 January 2027
**Maps to:** SOC 2 CC6.6, CC6.8, CC7.1 · ISO 27001:2022 A.8.1, A.8.7, A.8.12, A.8.16

## 1. Managed devices

Every device with access to Kestrel systems is company-owned and centrally managed. There
is no bring-your-own-device programme for laptops. As of 1 February 2026 the estate is
163 macOS laptops managed by Kandji and 11 Linux workstations managed by Fleet; there are
no Windows endpoints in use.

Enrolment is a precondition of access: Cloudflare Access performs a device posture check
on every session and refuses any device that is not enrolled, not encrypted, or more than
30 days behind on operating system patches.

## 2. Endpoint detection and response

**CrowdStrike Falcon is deployed on 100% of managed endpoints and on all production
compute nodes.** Coverage is reconciled nightly against the Kandji and AWS inventories,
and a device present in inventory but absent from Falcon raises a ticket to the Head of IT
within one business day. The reconciliation found two gaps during 2025, both new
contractor laptops, both closed within 24 hours.

Falcon detections route to the `#sec-alerts` Slack channel and page the on-call engineer
for High and Critical severities. In 2025 there were 41 detections, 39 of which were
classified as false positives or unwanted-but-benign software; two were investigated as
incidents (INC-2025-0021, INC-2025-0038) and neither involved customer data exposure.

## 3. Anti-malware on servers

Production compute runs immutable, minimal container images on Bottlerocket hosts.
Traditional file-scanning anti-malware is not deployed on those hosts; the compensating
controls are read-only root filesystems, no interactive shell access in normal operation,
image signing enforced at admission, and the Falcon sensor running in Linux mode for
runtime behavioural detection. This position is documented as an accepted risk
(RISK-2024-0017) and was reviewed by the auditors during the 2025 SOC 2 period without
exception.

## 4. Disk encryption

FileVault 2 is enforced on all macOS devices, with recovery keys held in Kandji custody;
LUKS is enforced on Linux workstations. Compliance is 100% of 174 devices, verified
continuously by Vanta control DE-03.

## 5. Data loss prevention

DLP controls operate at three points rather than relying on a single agent:

* **Endpoint** — Kandji policy blocks USB mass storage write access on all laptops. AirDrop
  to non-Kestrel devices is disabled.
* **Email** — Google Workspace DLP inspects outbound mail for tenant identifiers, bulk
  personal data patterns, and credential-shaped strings, quarantining matches for review by
  the Head of IT. 27 messages were quarantined in 2025; 25 were released as false
  positives, 2 were genuine mis-sends stopped before delivery.
* **Cloud egress** — anomalous export volumes from Snowflake and production S3 are alerted
  on, as described in the Data Handling Standard (KD-SEC-004 §7).

## 6. Automatic email forwarding

**Automatic forwarding of Kestrel email to external addresses is blocked by Google
Workspace policy for all users.** There are no exceptions, and the setting is monitored by
Vanta control EM-02. Forwarding rules created by an attacker after a mailbox compromise
are the standard exfiltration route, so this is enforced technically rather than by
policy text.

## 7. Software installation

Personnel may install software from the Kandji self-service catalogue without approval.
Anything outside the catalogue requires a request to the Head of IT and is assessed
against the Acceptable Use Policy (KD-SEC-005), including whether the tool transmits data
to a third party. Administrative rights on macOS are granted just-in-time for 60 minutes
through Kandji Privileges, with each elevation logged.

## 8. Patching of endpoints

Operating system updates are enforced within 14 days of release for standard updates and
72 hours for updates addressing a Critical vulnerability, with a Kandji deferral window
that cannot be extended by the user. Measured compliance at the last audit point
(1 February 2026) was 168 of 174 devices within policy, the remaining six being on
extended leave with access suspended.
