# Subprocessor List

**Document ID:** KD-LEG-002 · **Version:** 6.4 · **Owner:** Priya Raghunathan, DPO
**Last updated:** 1 February 2026 · **Published at:** `kestreldata.com/subprocessors`
**Change notice:** 30 days, subscribe at `kestreldata.com/subprocessors/subscribe`

## Infrastructure subprocessors

| Subprocessor | Purpose | Location of processing | Transfer mechanism | Engaged since |
|---|---|---|---|---|
| Amazon Web Services, Inc. | Cloud hosting, compute, storage, database | US (`us-east-1`, `us-west-2`); EU (`eu-west-1`, `eu-central-1`) | SCCs + AWS DPA; EU data stays in EU | Mar 2019 |
| Snowflake Inc. | Analytics data warehouse | US (AWS `us-east-1`); EU (AWS `eu-west-1`) | SCCs; EU deployment for EU customers | Aug 2021 |
| Cloudflare, Inc. | CDN, WAF, DDoS protection, zero-trust access | Global edge; EU traffic terminated in EU under Regional Services | SCCs + Cloudflare DPA | Jan 2020 |

## Operational subprocessors

| Subprocessor | Purpose | Location | Transfer mechanism | Engaged since |
|---|---|---|---|---|
| Datadog, Inc. | Observability, logs, APM | US; EU site (`datadoghq.eu`) for EU customers | SCCs | Jun 2020 |
| Okta, Inc. | Identity and access management | US; EU cell for EU customers | SCCs | Feb 2021 |
| Twilio SendGrid | Transactional email delivery | US | SCCs | Mar 2019 |
| Zendesk, Inc. | Customer support ticketing | US; EU data centre option enabled | SCCs | Sep 2020 |
| Stripe, Inc. | Payment processing (billing contacts only) | US, IE | SCCs | Mar 2019 |

## Notes on scope

- **Stripe** processes billing contact details and payment instruments. Kestrel does not
  store, process, or transmit cardholder data itself and is therefore out of PCI DSS
  scope as a merchant using a fully hosted checkout.
- **Zendesk** receives only what a Customer chooses to place in a support ticket.
  Customers are advised in the support UI not to paste production data into tickets.
- **Datadog** receives application logs which are scrubbed of personal data at emission by
  the shared logging library. Residual personal data exposure is limited to `tenant_id`
  and `user_id`, both opaque identifiers.

## EU data residency

Customers on the EU instance have their data processed exclusively within the EEA for the
infrastructure subprocessors above. Kestrel does **not** currently offer a UK-only,
Swiss-only, Canadian, or Australian data residency option. Requests for those are recorded
but not committed to.

## Changes in the last 12 months

| Date | Change | Notice given |
|---|---|---|
| 12 Nov 2025 | Added Cloudflare Zero Trust (existing vendor, new purpose) | 30 days, no objections |
| 4 Jun 2025 | Removed Segment, Inc. (product analytics discontinued) | N/A, removal |
| 18 Feb 2025 | Added Snowflake EU deployment for EU instance | 30 days, no objections |
