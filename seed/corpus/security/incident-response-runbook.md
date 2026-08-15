# Incident Response Runbook

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
