# Information Security Policy

**Document ID:** KD-SEC-001
**Version:** 4.2
**Owner:** Marcus Oyelaran, Chief Information Security Officer
**Approved by:** Board Audit & Risk Committee, 11 February 2026
**Last review:** 11 February 2026 · **Next review:** 11 February 2027
**Classification:** Internal — may be shared with customers under NDA

## 1. Purpose and scope

This policy establishes how Kestrel Data, Inc. ("Kestrel") protects the confidentiality,
integrity, and availability of information belonging to Kestrel and to customers of the
Kestrel Insight platform.

It applies to all 187 employees and to all 24 contractors engaged as of 1 February 2026,
to every system that processes Kestrel or customer data, and to all facilities including
the Austin, TX headquarters and remote work locations. No exemption exists for
engineering, executive, or contractor populations.

## 2. Governance

Security governance sits with the Security Council, chaired by the CISO and meeting
monthly. Standing members are the CISO, VP Engineering (Dana Whitfield), General Counsel
(Aaron Feldstein), Head of IT (Sofia Brenner), and the Data Protection Officer
(Priya Raghunathan). Minutes are retained for seven years in Confluence space `SEC-GOV`.

The Board Audit & Risk Committee receives a security report quarterly. The CISO has a
direct reporting line to the Committee independent of the CEO, so that a security concern
cannot be suppressed by management.

## 3. Certifications and attestations

| Framework | Status | Evidence |
|---|---|---|
| SOC 2 Type II | Current | Report issued 14 March 2026 by Prescient Assurance LLP, covering 1 January – 31 December 2025. No exceptions noted. |
| ISO/IEC 27001:2022 | Certified | Certificate IS-2025-44817, issued by BSI on 22 September 2025, valid to 21 September 2028 |
| ISO/IEC 27701:2019 | Certified | Extension to IS-2025-44817, issued 22 September 2025 |
| PCI DSS | Not applicable | Kestrel does not store, process, or transmit cardholder data. Payment processing is delegated entirely to Stripe, Inc. |
| HIPAA | Available on request | BAA offered to customers on the Enterprise tier; 4 BAAs executed as of Q1 2026 |

## 4. Risk management

Kestrel maintains a risk register in Vanta, reviewed monthly by the Security Council.
Risks are scored on a 5×5 likelihood/impact matrix. Any risk scoring 15 or above requires
a documented treatment plan with a named owner and a due date, and is reported to the
Board Audit & Risk Committee at the next quarterly meeting.

As of the February 2026 review the register holds 34 open risks: 2 high (score ≥15),
19 medium, 13 low. Both high risks relate to third-party concentration on AWS and are
tracked under `RISK-2025-0112` and `RISK-2026-0007`.

## 5. Information classification

All information is classified into one of four tiers. Handling requirements are defined
in the Data Handling Standard (KD-SEC-004).

- **Restricted** — customer production data, credentials, private keys, personal data.
  Encrypted at rest and in transit, access logged, need-to-know only.
- **Confidential** — source code, architecture documents, contracts, financials.
- **Internal** — policies, runbooks, meeting notes. Default classification.
- **Public** — marketing material, published documentation.

Absence of a label means Internal. Restricted data may not be copied to personal devices,
personal cloud accounts, or generative AI tools that are not on the approved list
maintained in KD-SEC-011.

## 6. Personnel security

All employees and contractors undergo background screening appropriate to jurisdiction
before their start date, conducted by Checkr. Screening covers identity verification,
right to work, criminal records where lawful, and employment history for the preceding
seven years.

Security awareness training is delivered through Curricula at onboarding and annually
thereafter. Completion rate for the 2025 cycle was 100% of active staff, evidenced in the
SOC 2 Type II report at control CC1.4. Engineers additionally complete secure-development
training covering the OWASP Top 10 within 60 days of hire.

Simulated phishing exercises run quarterly. The Q4 2025 exercise recorded an 8.6% click
rate and a 2.1% credential-submission rate; both figures triggered targeted follow-up
training for affected staff, completed by 19 January 2026.

## 7. Access control

Access follows least privilege and is governed by the Access Control Standard
(KD-SEC-002). Identity is federated through Okta with SAML 2.0. Hardware-backed
multi-factor authentication (WebAuthn / FIDO2 security keys) is mandatory for all staff;
SMS and TOTP were retired as authentication factors on 30 June 2025.

Production access requires a documented business justification, expires automatically
after 8 hours, and is granted through a break-glass workflow that notifies the Security
Council channel in real time.

## 8. Cryptography

Data at rest is encrypted with AES-256-GCM. Data in transit is protected with TLS 1.3;
TLS 1.0 and 1.1 were disabled on 12 April 2024 and TLS 1.2 remains enabled only for a
single legacy integration scheduled for removal in Q3 2026. Key management is described
in the Encryption Standard (KD-SEC-003) and the Secrets Management Standard (KD-ENG-006).

## 9. Incident response

Security incidents are handled under the Incident Response Runbook (KD-SEC-005). Kestrel
commits contractually to notifying affected customers within 72 hours of confirming a
personal data breach, and within 24 hours for incidents involving confirmed unauthorised
access to customer production data.

## 10. Exceptions

Exceptions to this policy require written approval from the CISO, a documented
compensating control, and an expiry date not exceeding 180 days. Four exceptions are open
as of 11 February 2026, all tracked in Vanta and all with expiry dates before
30 September 2026.

## 11. Enforcement

Violation of this policy may result in disciplinary action up to and including
termination of employment or contract, and referral to law enforcement where warranted.

---
*Kestrel Data, Inc. · 1401 Lavaca Street, Suite 210, Austin, TX 78701 · Delaware C-corporation, incorporated 2019*
