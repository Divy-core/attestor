# Government Requests Policy and Transparency Report

**Document ID:** KD-LEG-012 · **Version:** 2.0 · **Owner:** Aaron Feldstein, General Counsel
**Approved:** 2 February 2026 · **Next review:** 2 February 2027
**Maps to:** GDPR Articles 48, 28(3)(a) · SOC 2 CC2.3 · ISO 27001:2022 A.5.5, A.5.31, A.5.34

## 1. Who may receive a request

All law enforcement, regulatory, and national security requests for customer data must be
directed to the General Counsel at `legal@kestreldata.com`. No other person at Kestrel is
authorised to accept service or to disclose customer data to a government authority.
Support, sales, and engineering personnel are trained to route any such approach to the
General Counsel without responding, and this is covered in the annual awareness training.

## 2. How a request is handled

1. **Log.** The request is recorded in the government request register on the day of
   receipt, with the requesting authority, the legal instrument, and the data sought.
2. **Validate.** The General Counsel, with outside counsel where warranted, assesses
   whether the instrument is valid, whether it is binding on Kestrel in the jurisdiction
   concerned, and whether it is properly scoped.
3. **Challenge.** Kestrel challenges requests that are overbroad, defective, or unlawful,
   and will seek to narrow scope before producing anything. A request for bulk or
   indiscriminate access will be refused and litigated if pressed.
4. **Redirect where possible.** Where the data sought is customer content, Kestrel's
   position is that the request should be directed to the customer as controller. Kestrel
   will tell the authority so and, where lawful, tell the customer.
5. **Notify.** **Kestrel notifies the affected customer before disclosing their data**,
   unless legally prohibited from doing so. Where a non-disclosure order applies, Kestrel
   seeks to have it lifted or time-limited, and notifies the customer as soon as it
   expires.
6. **Minimise.** If production is unavoidable, Kestrel produces the narrowest set of data
   responsive to the instrument, and records exactly what was produced.

Foreign requests that are not backed by an MLAT, letter rogatory, or other recognised
mechanism are refused as a matter of Article 48.

## 3. Transparency report

Kestrel publishes a transparency report annually at `trust.kestreldata.com/transparency`.
Figures for the calendar year 2025:

| Category | Received | Data produced |
|---|---|---|
| US law enforcement — subpoena | 2 | 0 |
| US law enforcement — search warrant | 0 | 0 |
| US national security process (NSL, FISA) | **0** | 0 |
| Non-US government requests | 1 | 0 |
| Civil discovery requests from third parties | 3 | 1 (redacted, under protective order) |
| Customers notified | 3 of 3 permitted | — |

Both 2025 subpoenas sought subscriber billing information for a Kestrel *customer account*
rather than customer content; one was withdrawn after challenge and one was satisfied by
directing the requester to the customer. The non-US request was refused for lack of a
recognised legal mechanism.

Cumulative figures since 2019: 7 requests, 1 production, and **zero national security
requests of any kind**.

## 4. Statement on national security process

As of 2 February 2026, **Kestrel Data has never received a National Security Letter, a
FISA order, or any other national security process, and has never provided any government
with direct, bulk, or unfettered access to customer data.** Kestrel operates no
government-facing interception capability and has deposited no encryption keys with any
authority.

Kestrel does not operate a warrant canary, because a canary whose removal is itself
constrained by a non-disclosure order provides false assurance. This figure is instead
restated in each annual transparency report and, where a customer requires it, confirmed in
writing by the General Counsel at the point of contracting.

## 5. Emergency disclosure requests

An emergency request citing an imminent risk to life is escalated to the General Counsel
immediately, at any hour, through the `legal-oncall` PagerDuty rotation. Disclosure in
those circumstances is limited to what is necessary to address the emergency, is documented
in the register, and the customer is notified afterwards unless prohibited. No emergency
request has been received.

## 6. Records

The government request register is retained for ten years and is available to the auditors
and, in redacted form, to a customer exercising audit rights. Retention is longer than the
ordinary schedule deliberately: the value of this record is precisely that it goes back
further than anyone's memory.
