# Service Operations, Maintenance and Support

**Document ID:** KD-ENG-009 · **Version:** 2.2 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 23 January 2026 · **Next review:** 23 January 2027
**Maps to:** SOC 2 A1.1, A1.2, CC7.3, CC8.1 · ISO 27001:2022 A.5.29, A.8.6, A.8.32

## 1. Status page

Service status is published at **`status.kestreldata.com`**, hosted by Statuspage on
infrastructure independent of Kestrel's own — a status page that shares a failure domain
with the service is worthless during exactly the event it exists for. The page shows
per-component status for the US and EU instances, current and historical incidents, and
scheduled maintenance. Customers may subscribe to email, SMS, RSS, and webhook updates.

Incidents are posted to the status page **within 15 minutes** of a Sev-1 or Sev-2
declaration, and updated at least every 30 minutes until resolution.

## 2. Scheduled maintenance

The standard maintenance window is **Sundays 06:00–10:00 UTC**. Most changes do not use it:
deployments are rolling and zero-downtime, and 2025 saw 1,412 production deployments with
no customer-visible downtime.

**Customers receive at least 7 days' notice of scheduled maintenance expected to be
customer-affecting**, and 30 days' notice where the change requires customer action.
Notice is given by email to tenant administrators and on the status page. Emergency
maintenance — a security fix that cannot wait — may be performed with shorter notice under
the emergency change path (KD-ENG-002 §3), with the customer notification sent as soon as
the change is scheduled.

Three customer-affecting maintenance events were performed in 2025, each notified 14 days
in advance.

## 3. Support channels and hours

| Channel | Availability |
|---|---|
| Email `support@kestreldata.com` | 24×7 intake |
| In-product support widget | 24×7 intake |
| Named CSM (Enterprise tier) | Business hours, US Central |
| Emergency pager (Enterprise tier) | **24×7 for Severity 1** |

**24×7 support is available for production-down incidents**, through the emergency pager
for Enterprise customers and through 24×7 monitoring for everyone: a platform-wide outage
is detected and worked by the on-call SRE whether or not any customer has reported it.

## 4. Support severity definitions and response targets

| Severity | Definition | First response | Update cadence |
|---|---|---|---|
| Sev 1 | Production down, or data loss affecting the customer | **30 minutes**, 24×7 | Hourly |
| Sev 2 | Major feature unusable, no workaround | 2 business hours | Daily |
| Sev 3 | Degraded or impaired function with a workaround | 1 business day | Every 3 business days |
| Sev 4 | Question, documentation, or enhancement request | 2 business days | Weekly |

These are **response** targets, not resolution times, which is stated plainly because a
resolution commitment on an unknown defect is not a commitment anyone can keep. Measured
2025 performance: Sev 1 median first response 11 minutes across 4 cases; Sev 2 median 41
minutes across 37 cases; overall target attainment 98.6%.

## 5. On-call

Two rotations run continuously: platform SRE and application engineering, each with a
primary and a secondary, managed in PagerDuty with a 5-minute acknowledgement target and
automatic escalation to the VP Engineering after 15 minutes. Rotations are weekly, and a
handover document is required at each change.

## 6. Monitoring and alerting

Datadog holds infrastructure metrics, application performance monitoring, and synthetic
checks running every 60 seconds from five geographic locations against both instances.
Alerts are symptom-based — error rate, latency percentile, saturation, and synthetic
failure — rather than cause-based, so a novel failure still pages someone.

Uptime is measured from the synthetic checks, which run outside Kestrel's infrastructure;
this is the measurement the availability commitment is assessed against (KD-ENG-001 §2).

## 7. Incident communication to customers

Customer-facing incident communication is owned by the incident commander and follows the
commitments in KD-SEC-008 §4. In practice: status page inside 15 minutes, direct email to
affected tenant administrators inside 60 minutes for a Sev 1, and a written post-incident
review within 5 business days for any Sev 1 or Sev 2, shared with affected customers on
request.

## 8. Capacity and performance

Capacity is reviewed monthly against a 90-day forecast. Autoscaling handles ordinary
variation; the review exists for the changes autoscaling cannot absorb, such as a large
tenant onboarding. Two capacity increases were made ahead of demand in 2025, neither
triggered by a saturation alert.
