# Personnel Security and Security Training Programme

**Document ID:** KD-SEC-013 · **Version:** 2.1 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 22 January 2026 · **Next review:** 22 January 2027
**Maps to:** SOC 2 CC1.1, CC1.4, CC2.1, CC6.2 · ISO 27001:2022 A.6.1, A.6.2, A.6.3, A.6.6

## 1. Pre-employment screening

Every employee and every contractor is background screened before their start date. No
system access — including email — is provisioned until the check clears; Okta Lifecycle
Management is gated on the Rippling "screening cleared" attribute, so this is enforced by
the provisioning pipeline rather than by a checklist.

Screening is performed by Checkr and covers, to the extent permitted by local law:
identity and right to work, seven-year criminal records, employment history for the prior
five years, and education verification for roles requiring a named qualification.
Personnel with standing production access additionally undergo a global sanctions and
adverse-media check.

Of 47 people onboarded during 2025, 47 were screened before their start date. One
screening returned an adverse result; the offer was withdrawn before any access was
created.

## 2. Confidentiality obligations

All personnel sign a confidentiality agreement as a condition of employment or
engagement. **The confidentiality obligation survives termination indefinitely for
customer data and for five years for other Kestrel confidential information.** Contractors
sign the same obligation through their engagement contract, and the obligation flows down
to any of their own personnel.

## 3. Acceptable use and policy acknowledgement

Personnel acknowledge the Acceptable Use Policy (KD-SEC-005) and this standard at
onboarding and annually thereafter. Acknowledgement is tracked in Vanta; the 2025 annual
cycle closed on 30 November 2025 at **100%** completion across 187 employees and 24
contractors.

## 4. Security awareness training

Security awareness training is **mandatory for all personnel, annually, plus at
onboarding within the first 14 days**. Training is delivered through Curricula and runs
approximately 45 minutes, covering phishing, credential handling, data classification,
secure remote working, and incident reporting.

Measured completion for the 2025 cycle was **99.5%** (210 of 211 in-scope personnel); the
single outstanding case was a contractor on extended leave whose access was suspended
until completion. Completion is a Vanta-monitored control (AC-19) and non-completion after
14 days of reminders results in automatic access suspension.

## 5. Simulated phishing

Kestrel runs simulated phishing exercises **quarterly** through Curricula, targeting all
personnel with a Kestrel mailbox. Results for 2025:

| Quarter | Sent | Click rate | Credential submission | Reported to security |
|---|---|---|---|---|
| Q1 2025 | 198 | 6.1% | 1.0% | 41% |
| Q2 2025 | 203 | 4.4% | 0.5% | 52% |
| Q3 2025 | 208 | 3.4% | 0.0% | 58% |
| Q4 2025 | 211 | **2.8%** | 0.0% | 63% |

Anyone who clicks is enrolled in a short remedial module within one business day. Repeat
clicks in a rolling twelve months trigger a conversation with the person's manager; two
occurred in 2025.

## 6. Secure development training

Engineers complete role-specific secure development training annually, in addition to the
general awareness training. The 2025 curriculum was Secure Code Warrior's OWASP Top 10
path plus an internal module on tenant isolation written by the platform team after the
INSEC-2026-0219 penetration test. Completion for the 63 engineers in scope was 100% as of
19 December 2025.

## 7. How security responsibilities are communicated

Responsibilities are communicated four ways, deliberately overlapping: the offer pack and
onboarding session; the annual policy acknowledgement; a standing item in the monthly
all-hands delivered by the CISO; and the `#security` Slack channel, which is the
documented route for reporting anything suspicious and is monitored during business hours
with PagerDuty escalation outside them.

## 8. Disciplinary process

Wilful violation of security policy is handled under the Kestrel disciplinary procedure
and may result in termination. One formal disciplinary action was taken in 2025, relating
to the storage of a customer dataset in a personal cloud drive; the data was deleted under
supervision, the incident was recorded as INC-2025-0038, and the affected customer was
notified within the contractual window.
