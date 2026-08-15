# Logging and Monitoring Standard

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
