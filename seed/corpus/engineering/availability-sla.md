# Availability Service Level Agreement

**Document ID:** KD-ENG-007 · **Version:** 3.0 · **Owner:** Aaron Feldstein, General Counsel
**Effective:** 1 January 2026 · Forms part of the Master Subscription Agreement

## 1. Commitment

Kestrel commits to **99.9% monthly uptime** for the Kestrel Insight platform on the
Enterprise and Growth tiers.

The **Starter tier carries no availability commitment.** It is offered on a reasonable
efforts basis and is excluded from service credits.

## 2. Definitions

**Uptime** is the percentage of minutes in a calendar month during which the Service is
Available, calculated as:

    Uptime % = ((Total Minutes - Downtime Minutes) / Total Minutes) x 100

**Downtime** is any minute during which all requests to the production API from the
external monitoring network return a 5xx error or fail to connect. Measurement is by
independent third-party monitoring (Checkly, 7 global locations, 30-second interval), not
by Kestrel internal instrumentation. The monitoring data is available to customers on
request.

**Available** excludes degraded performance that does not produce errors. A slow response
is not Downtime under this SLA.

## 3. Exclusions

Downtime does not include unavailability caused by:

- Scheduled maintenance, notified at least 7 days in advance, in the window Sundays
  04:00-06:00 UTC, capped at 4 hours per month.
- Emergency maintenance to address a security vulnerability, notified as soon as
  practicable.
- Customer configuration, customer code, or customer exceeding documented rate limits.
- Failure of a customer-controlled dependency such as their identity provider.
- Force majeure.
- Suspension for non-payment or breach, per the Agreement.

## 4. Service credits

| Monthly uptime | Credit (% of monthly fee) |
|---|---|
| Below 99.9% but at or above 99.0% | 10% |
| Below 99.0% but at or above 95.0% | 25% |
| Below 95.0% | 50% |

Credits are the sole and exclusive remedy for failure to meet this SLA. A claim must be
submitted within **30 days** of the end of the affected month, with supporting detail.
Credits are applied to the next invoice and are not refundable in cash.

## 5. Historical performance

| Year | Measured availability | Months below 99.9% | Credits issued |
|---|---|---|---|
| 2025 | 99.97% | 0 | 0 |
| 2024 | 99.94% | 1 (March, 99.87%) | 1 customer, 10% |
| 2023 | 99.91% | 1 (August, 99.82%) | 3 customers, 10% |

## 6. Support response targets

Support targets are separate from this SLA and are not credit-bearing.

| Severity | First response | Coverage |
|---|---|---|
| Urgent (production down) | 1 hour | 24 x 7 |
| High (major feature impaired) | 4 business hours | Business hours |
| Normal | 1 business day | Business hours |
| Low | 3 business days | Business hours |

Business hours are 08:00-18:00 US Central, Monday to Friday, excluding US public holidays.
