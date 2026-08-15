# Security Addendum

**Document ID:** KD-LEG-008 · **Version:** 3.1 · **Owner:** Marcus Oyelaran, CISO
**Effective:** 1 February 2026 · Forms part of the Master Subscription Agreement

This addendum states the security commitments Kestrel makes contractually. It is
deliberately narrower than the Information Security Policy: everything here is an
obligation, not an aspiration.

## 1. Certifications maintained

Kestrel will maintain, for the term of the Agreement, a SOC 2 Type II report covering
Security, Availability, and Confidentiality, and ISO/IEC 27001:2022 certification. Current
reports are made available annually within 30 days of issuance.

If a certification lapses or an audit opinion is qualified, Kestrel will notify affected
customers within **10 business days**.

## 2. Encryption commitments

- Customer data encrypted at rest using AES-256 or stronger.
- Customer data encrypted in transit using TLS 1.2 or higher; TLS 1.3 is the default.
- Encryption keys managed in a dedicated key management service with role separation.

## 3. Access commitments

- Multi-factor authentication mandatory for all Kestrel personnel with production access.
- Production access on a least-privilege, just-in-time basis, expiring within 8 hours.
- No routine Kestrel access to customer production data; support access requires
  per-incident customer authorisation.
- Access revoked within 24 hours of personnel departure. Measured median in 2025 was
  4 minutes.

## 4. Incident notification

- Personal data breach: notification within **72 hours** of confirmation.
- Confirmed unauthorised access to customer production data: **24 hours**.
- Notification content as set out in the DPA Section 7.

## 5. Testing commitments

- Independent penetration test at least **annually**; executive summary available under
  NDA.
- Vulnerability remediation to the SLAs in KD-SEC-006: Critical 7 days, High 30 days.
- Disaster recovery restore tested at least **quarterly**.

## 6. Availability commitment

99.9% monthly uptime for Enterprise and Growth tiers, with service credits as set out in
the Availability SLA (KD-ENG-007). No availability SLA applies to the Starter tier.

## 7. What Kestrel does NOT commit to

Stated explicitly, because a security addendum that omits its limits is misleading:

- **On-premises or self-hosted deployment is not offered.** Kestrel Insight is a
  multi-tenant SaaS product only. There is no single-tenant, private-cloud, air-gapped,
  or customer-VPC deployment option, and none is on the roadmap.
- **Customer-managed encryption keys** are offered for the Snowflake analytics layer only,
  not for the primary application database or object storage.
- **Data residency** is offered for the US and EU only. No UK-only, Swiss-only, Canadian,
  Australian, or Indian residency option exists.
- **FedRAMP** authorisation is not held, and Kestrel is not listed in the FedRAMP
  Marketplace.
- Regional RTO is **4 hours**, not sub-hour. Kestrel does not operate active-active
  multi-region failover.
- No paid bug bounty programme is operated.

## 8. Changes

Kestrel may update this addendum, provided that no update materially reduces the
commitments during a paid term. Material changes are notified 30 days in advance.
