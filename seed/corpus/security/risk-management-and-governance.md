# Risk Management and Security Governance

**Document ID:** KD-SEC-018 · **Version:** 3.0 · **Owner:** Marcus Oyelaran, CISO
**Approved:** 26 January 2026 · **Next review:** 26 January 2027
**Maps to:** SOC 2 CC1.1, CC1.2, CC1.3, CC3.1, CC3.2, CC3.4 · ISO 27001:2022 A.5.1–A.5.4, Clauses 5–6, 9

## 1. Executive accountability

**Marcus Oyelaran, Chief Information Security Officer, is accountable for information
security at Kestrel Data.** The CISO reports to the Chief Executive Officer, not to the VP
Engineering or the CTO, so that a security objection cannot be resolved by the person
whose delivery schedule it obstructs. The CISO has a standing right of direct access to
the board and exercised it twice during 2025.

## 2. Board oversight

The board receives a formal information security report at each quarterly meeting,
presented by the CISO. The standing agenda covers: the risk register movement since the
last meeting, incidents and their classification, certification and audit status, the
results of penetration testing, and any exception granted above the CISO's own approval
threshold. Board meetings covering security in 2025 were held on 18 March, 17 June,
16 September, and 9 December.

The board has designated Elaine Vasquez, non-executive director and former CIO of a
regulated financial services firm, as the director with security oversight responsibility.

## 3. Security Steering Group

A Security Steering Group meets **monthly** and comprises the CISO (chair), VP Engineering,
Head of IT, General Counsel, and DPO. It approves policy changes, reviews the risk
register, and decides on exceptions. Minutes are retained for seven years and are made
available to auditors; they are not shared with customers, but the decisions they record
are reflected in the SOC 2 report.

## 4. The risk register

**Kestrel maintains a formal risk register, reviewed monthly by the Security Steering
Group and formally re-scored quarterly.** It is held in Vanta with a mirrored export in
the `SEC-GOV` repository so that history is preserved even if the tooling changes.

Each risk carries an identifier (`RISK-YYYY-NNNN`), a description, an owner who is a named
individual rather than a team, an inherent score, the controls applied, a residual score,
a treatment decision (accept, mitigate, transfer, avoid), and a review date.

Scoring is a 5×5 likelihood-by-impact matrix. Anything scoring residual 15 or above
requires an explicit board-level acceptance; nothing has been accepted at that level since
the register was established in 2022.

As of 1 February 2026 the register holds **41 open risks**: 3 high, 17 medium, 21 low.
Movement during 2025: 22 risks opened, 19 closed, and 4 re-scored upward following the
February 2026 penetration test.

## 5. Worked examples from the register

| Risk | Description | Treatment | Residual |
|---|---|---|---|
| RISK-2025-0031 | SMS and TOTP factors phishable | Mitigate — hardware factors mandated 30 June 2025 | Low |
| RISK-2024-0017 | No file-scanning anti-malware on container hosts | Accept — immutable hosts, signed images, runtime detection | Low |
| RISK-2025-0044 | Single-cloud concentration on AWS | Accept — documented exit plan (KD-ENG-011), reviewed annually | Medium |
| RISK-2026-0003 | Rate limiting absent on password reset endpoint | Mitigate — remediated 27 February 2026 | Closed |

## 6. Risk assessment cadence

A full risk assessment is performed annually, most recently completed on 20 January 2026.
Additional assessments are triggered by: a new subprocessor handling customer data, a
material architecture change, a Sev-1 incident, entry into a new regulated market, and any
finding rated High or above from an external test.

## 7. Internal audit

Kestrel is too small to maintain a separate internal audit function, and says so rather
than claiming one. Independent assurance comes from the annual SOC 2 Type II examination
(Prescient Assurance LLP), the ISO 27001 surveillance audits (BSI), and the annual
penetration test (Include Security). Control operation is monitored continuously by Vanta
across 187 automated checks, with failures routed to the control owner.

## 8. Policy management

All security policies carry a document ID, a version, a named owner, an approval date, and
a review date. Policies are reviewed at least annually and on material change. The 2026
review cycle covered 14 policies between 8 January and 12 February 2026; three were
revised, and the changes were communicated at the February all-hands and through the
annual acknowledgement cycle.

## 9. Exceptions

Exceptions to policy require: a written business justification, a compensating control,
CISO approval, and an expiry date no later than 90 days out. Exceptions above residual
score 12 additionally require Security Steering Group approval. Two exceptions were open
as of 1 February 2026.
