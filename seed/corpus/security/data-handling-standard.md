# Data Handling Standard

**Document ID:** KD-SEC-004 · **Version:** 2.4 · **Owner:** Marcus Oyelaran, CISO
**Approved:** 15 January 2026 · **Next review:** 15 January 2027
**Maps to:** SOC 2 CC6.1, CC6.7, C1.1 · ISO 27001:2022 A.5.12, A.5.13, A.8.10, A.8.11, A.8.12

## 1. Classification tiers

Every piece of information Kestrel Data holds carries one of four labels, assigned by the
system owner at creation:

| Tier | Definition | Examples |
|---|---|---|
| Restricted | Customer content and credentials | Tenant database rows, API keys, backups |
| Confidential | Internal information whose disclosure would harm Kestrel or a customer | Pen test reports, security architecture, contracts |
| Internal | Ordinary business information | Runbooks, meeting notes, roadmaps |
| Public | Approved for external release | Marketing pages, published privacy policy |

Unlabelled information is treated as Confidential until classified.

## 2. Categories of customer data processed

Kestrel Insight processes exactly four categories of customer data, defined in the DPA
and in the Records of Processing Activities (KD-LEG-004):

1. **Account identity data** — end-user name, business email address, role, tenant ID.
2. **Product usage events** — timestamps, feature identifiers, tenant ID, session ID.
3. **Customer-uploaded analytics datasets** — whatever the customer chooses to load; the
   content is opaque to Kestrel and is not inspected.
4. **Support correspondence** — Zendesk tickets and attachments raised by the customer.

Kestrel does not process payment card data: Stripe collects and holds card details
directly and Kestrel receives only a token and the last four digits.

## 3. Handling rules by tier

Restricted data may be stored only in the production AWS accounts (`kd-prod-us`,
`kd-prod-eu`), in Snowflake under the `KD_PROD` role hierarchy, and in the encrypted
backup vault. It may not be placed in Slack, in Google Drive, in a local spreadsheet, in
a Jira ticket, or in any generative AI tool. This is enforced technically by the DLP
controls in KD-SEC-014 rather than by instruction alone.

Confidential data may be stored in Google Workspace with link sharing disabled by domain
policy, and may be shared externally only under an executed NDA recorded by the General
Counsel.

## 4. Production data in non-production environments

**Customer production data is never copied into development or staging environments.**
This is a hard prohibition, not a preference. Staging is populated by
`kd-synthetic-gen` (KD-ENG-003 §2), which generates structurally identical but wholly
fabricated datasets from a fixed seed. The last audit of staging table contents against
production tenant identifiers ran on 4 February 2026 and returned zero matches; the check
runs weekly and alerts the Head of IT on any hit.

## 5. Data minimisation

Product telemetry is collected against an allowlist of 41 named event types. Adding an
event type requires DPO review, recorded in the ROPA change log. Free-text fields are
excluded from telemetry entirely, because free text is where personal data arrives
unannounced.

## 6. Media and device sanitisation

Kestrel operates no owned data centre and therefore performs no physical media
destruction for production systems; AWS handles decommissioning under its own NIST
800-88 process, evidenced in the AWS SOC 2 report Kestrel holds on file.

Company laptops are wiped through Kandji's remote-wipe workflow at offboarding, and the
wipe event is recorded against the asset record described in KD-SEC-016. Nine devices
were wiped and recycled through Austin-based vendor Techno Rescue in 2025, each with a
serialised certificate of destruction.

## 7. Data export monitoring

Egress from Snowflake and from the production S3 buckets is monitored for anomalous
volume. The threshold is a rolling 7-day tenant baseline plus three standard deviations,
evaluated hourly in Datadog. Two alerts fired in 2025, both traced to legitimate
customer-initiated bulk exports and closed the same day.

## 8. Exceptions

Exceptions require written CISO approval, a compensating control, and an expiry date not
exceeding 90 days. Two exceptions were open as of 1 February 2026, both relating to the
legacy TLS 1.2 SFTP integration scheduled for decommission on 30 June 2026.
