"""Emit the legal corpus. Run once; the .md files are the committed artefact."""

from pathlib import Path

OUT = Path(__file__).parent / "corpus" / "legal"

DOCS: dict[str, str] = {}

DOCS["data-processing-agreement.md"] = """# Data Processing Agreement (Template)

**Document ID:** KD-LEG-001 · **Version:** 5.1 · **Owner:** Aaron Feldstein, General Counsel
**Effective:** 1 February 2026 · **Supersedes:** v4.3 of 12 May 2025
**Governing law:** Delaware, USA, save where the SCCs specify otherwise

This DPA forms part of the Kestrel Data Master Subscription Agreement and applies where
Kestrel processes Personal Data on behalf of Customer.

## 1. Roles

Customer is **Controller**. Kestrel Data, Inc. is **Processor**. Where Customer is itself
a processor for its own customers, Kestrel is sub-processor and the same obligations apply
down the chain.

Kestrel is **Controller** for a limited set of data it determines the purposes of: account
administrator contact details, billing records, and product telemetry described in
Annex 3.

## 2. Scope and duration

Processing continues for the term of the Agreement plus the retention periods in Annex 2.
Kestrel processes Personal Data only on documented instructions from Customer. The
Agreement, this DPA, and Customer configuration of the Service constitute those
instructions.

If Kestrel is required by Union or Member State law to process beyond those instructions,
it will inform Customer before processing unless that law prohibits the notification on
important grounds of public interest.

## 3. Confidentiality

Kestrel ensures that personnel authorised to process Personal Data are bound by
confidentiality obligations surviving termination of employment. All staff execute a
confidentiality agreement at onboarding.

## 4. Security

Kestrel implements the technical and organisational measures set out in **Annex 2**,
including encryption at rest (AES-256-GCM) and in transit (TLS 1.3), least-privilege
access control with mandatory hardware MFA, and continuous monitoring.

## 5. Sub-processing

Customer grants general authorisation for the sub-processors listed at
`kestreldata.com/subprocessors`. Kestrel gives **30 days** notice before adding or
replacing a sub-processor. Customer may object on reasonable data-protection grounds
within that period; if the objection cannot be resolved, Customer may terminate the
affected Service without penalty for the remainder of the prepaid term.

Kestrel imposes on each sub-processor data protection obligations no less protective than
those in this DPA, and remains fully liable for their performance.

## 6. Data subject rights

Kestrel provides self-service functionality allowing Customer to access, correct, export,
and delete Personal Data within the Service. Where a data subject contacts Kestrel
directly, Kestrel refers them to Customer and does not respond substantively, unless
legally obliged to do so.

Where the Service functionality is insufficient, Kestrel assists Customer within **10
business days** of a written request.

## 7. Personal data breach

Kestrel notifies Customer **without undue delay and in any event within 72 hours** of
becoming aware of a Personal Data Breach affecting Customer Personal Data, and within
**24 hours** where there is confirmed unauthorised access to Customer production data.
Notification includes the information required by Art. 33(3) GDPR to the extent known.

## 8. Deletion and return

On termination, Customer may export its data through the Service for **30 days**. After
that period Kestrel deletes Customer Personal Data within **90 days**, save where storage
is required by law. Backups containing deleted data expire on the standard backup cycle,
a maximum of **35 days** after deletion from primary storage. Certification of deletion is
provided on written request.

## 9. Audit

Kestrel makes available its current SOC 2 Type II report and ISO 27001 certificate, which
Customer accepts as satisfying Art. 28(3)(h) in the first instance. Where Customer has a
documented, specific concern not addressed by those reports, Customer may conduct an audit
no more than **once per twelve months**, on 30 days written notice, during business hours,
subject to confidentiality, and at Customer expense. Customer bears the reasonable costs
of Kestrel personnel time beyond 8 hours.

## 10. International transfers

Where Personal Data is transferred out of the EEA, UK, or Switzerland, the parties enter
into the **2021 EU Standard Contractual Clauses** (Commission Implementing Decision
2021/914), Module Two (Controller to Processor) or Module Three (Processor to
Sub-processor) as applicable, incorporated by reference. The UK International Data
Transfer Addendum and the Swiss Annex apply where relevant. See KD-LEG-006.

## 11. Liability

Liability under this DPA is subject to the limitations in the Master Subscription
Agreement, save that nothing limits liability that cannot be limited under applicable data
protection law.
"""

DOCS["subprocessor-list.md"] = """# Subprocessor List

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
"""

DOCS["privacy-policy.md"] = """# Privacy Policy

**Document ID:** KD-LEG-003 · **Version:** 4.0 · **Owner:** Priya Raghunathan, DPO
**Effective:** 15 January 2026 · **Published at:** `kestreldata.com/privacy`

This policy describes how Kestrel Data, Inc. handles personal data where Kestrel is the
**controller**. Where Kestrel processes personal data on behalf of a business customer,
that customer is the controller and its own privacy notice governs; see the DPA
(KD-LEG-001).

## 1. Controller and contact

Kestrel Data, Inc., 1401 Lavaca Street, Suite 210, Austin, TX 78701, USA.
EU representative under Art. 27 GDPR: Instant EU Rep Ltd, Dublin, Ireland.
Data Protection Officer: Priya Raghunathan, `dpo@kestreldata.com`.
Lead supervisory authority: Irish Data Protection Commission.

## 2. What we collect as controller

| Category | Examples | Lawful basis | Retention |
|---|---|---|---|
| Account data | Name, work email, job title, company | Contract (Art. 6(1)(b)) | Term + 3 years |
| Billing data | Billing contact, VAT number, payment method token | Contract; legal obligation | 7 years (tax) |
| Product telemetry | Feature usage, page views, error events | Legitimate interests (Art. 6(1)(f)) | 24 months |
| Support records | Ticket content, correspondence | Contract; legitimate interests | Term + 2 years |
| Marketing contacts | Name, work email, company | Consent (EEA/UK); legitimate interests (US) | Until withdrawal + 1 year |
| Recruitment | CV, interview notes | Legitimate interests; consent for retention | 12 months after decision |

A Legitimate Interests Assessment is recorded for each Art. 6(1)(f) basis in
`LEG-GOV/lia`.

## 3. What we do not do

- We do not sell personal data, and have never done so.
- We do not share personal data with advertising networks.
- We do not use customer content to train machine learning models, our own or anyone
  else. This is stated contractually in the MSA and is not merely a policy statement.
- We do not engage in automated decision-making producing legal or similarly significant
  effects.

## 4. Cookies

The marketing site uses strictly necessary cookies plus, with consent, analytics cookies
(Plausible, which is cookieless, and Google Analytics 4 where consent is given). The
application uses only strictly necessary cookies: a session cookie and a CSRF token. A
consent banner meeting EDPB guidance is served to EEA, UK, and Swiss visitors, with reject
as prominent as accept.

## 5. Your rights

Data subjects in the EEA, UK, and Switzerland have rights of access, rectification,
erasure, restriction, portability, and objection, and the right to withdraw consent.
California residents have rights under the CCPA as amended by the CPRA, including the
right to know, delete, correct, and opt out of sharing.

Requests to `privacy@kestreldata.com` are acknowledged within 5 business days and
fulfilled within **30 days**, extendable by a further 60 days for complex requests with
notice. In 2025 Kestrel received 14 data subject requests and fulfilled all within 30
days; the median was 9 days.

## 6. International transfers

Personal data is processed in the United States and the European Union. Transfers out of
the EEA rely on the 2021 Standard Contractual Clauses with supplementary measures
described in the Transfer Impact Assessment (KD-LEG-006).

## 7. Complaints

Complaints may be made to the Irish Data Protection Commission or to the supervisory
authority of the data subject habitual residence.
"""

DOCS["data-retention-schedule.md"] = """# Data Retention Schedule

**Document ID:** KD-LEG-004 · **Version:** 3.2 · **Owner:** Priya Raghunathan, DPO
**Approved:** 15 January 2026 · **Next review:** 15 January 2027
**Maps to:** GDPR Art. 5(1)(e) · SOC 2 C1.2 · ISO 27001:2022 A.5.33, A.8.10

## 1. Customer data

| Data class | Active retention | Post-termination | Deletion mechanism |
|---|---|---|---|
| Customer records in the platform | Term of subscription | **30 days** export window, then deleted within **90 days** | Automated `tenant-purge` job, nightly |
| Analytics warehouse (Snowflake) | Term | Deleted with tenant purge | Automated |
| Uploaded files (S3) | Term | Deleted with tenant purge | Lifecycle rule + explicit delete |
| Backups containing customer data | Rolling **35 days** | Expire naturally, maximum 35 days after primary deletion | Automatic expiry |
| Tenant audit events | **365 days** rolling | Deleted with tenant purge | Automated |

Kestrel does not retain customer data indefinitely for any purpose, including
"improvement of the service".

## 2. Kestrel-controlled data

| Data class | Retention | Basis |
|---|---|---|
| Account and contact records | Term + 3 years | Contract, limitation periods |
| Billing and tax records | **7 years** | US and Irish tax law |
| Product telemetry | 24 months | Legitimate interests, reviewed annually |
| Support tickets | Term + 2 years | Contract |
| Marketing contacts | Until consent withdrawal + 1 year | Consent / legitimate interests |
| Recruitment records | 12 months after decision | Legitimate interests |
| Employee records | Employment + 7 years | Employment and tax law |

## 3. Security and operational logs

| Log class | Retention | Notes |
|---|---|---|
| Security and audit (CloudTrail, Okta) | **7 years** | Object Lock compliance mode, immutable |
| Snowflake `ACCESS_HISTORY` | 7 years | Data access accountability |
| Application logs | 1 year | Personal data scrubbed at emission |
| VPC Flow / WAF logs | 1 year | |

## 4. Deletion verification

The `tenant-purge` job emits a structured completion record listing every store touched
and the row or object counts removed. That record is retained for 7 years as evidence of
deletion and is the artefact provided when a customer requests certification of deletion.

Deletion was exercised 11 times in 2025 (customer churn). Median time from termination to
completed purge was **41 days**, well inside the 90-day commitment.

## 5. Legal hold

A legal hold issued by the General Counsel suspends deletion for the identified data.
Holds are recorded in `LEG-GOV/holds` with scope, issuing date, and release date. Two
holds were in force during 2025, both released before year end.

## 6. Exceptions

Where a retention period conflicts with a customer contractual requirement, the shorter
period applies unless law requires otherwise. Longer retention requires DPO approval,
which has not been granted for any customer data class to date.
"""

DOCS["gdpr-records-of-processing.md"] = """# GDPR Records of Processing Activities (Art. 30)

**Document ID:** KD-LEG-005 · **Version:** 3.0 · **Owner:** Priya Raghunathan, DPO
**Last updated:** 20 January 2026 · **Next review:** 20 July 2026

Maintained under Art. 30(1) where Kestrel is controller and Art. 30(2) where Kestrel is
processor. Available to supervisory authorities on request.

## Part A - Kestrel as Processor (Art. 30(2))

**Controller:** each Kestrel customer, as identified in the applicable Order Form.
**Processor:** Kestrel Data, Inc.
**DPO:** Priya Raghunathan, `dpo@kestreldata.com`

| # | Processing activity | Categories of data subject | Categories of personal data | Transfers | Retention |
|---|---|---|---|---|---|
| P1 | Hosting and operating the Kestrel Insight platform | Customer employees, and end users whose data Customer uploads | Identifiers, contact details, employment attributes, and any content Customer chooses to upload | US and EU per instance; SCCs | Term + 90 days |
| P2 | Analytics processing in Snowflake | As P1 | As P1 | Same region as P1 | Term + 90 days |
| P3 | Customer support | Customer employees raising tickets | Name, work email, ticket content | US (Zendesk), EU option | Term + 2 years |
| P4 | Backup and disaster recovery | As P1 | As P1 | Within residency boundary | 35 days rolling |

Technical and organisational measures for all activities: as set out in Annex 2 of the
DPA (KD-LEG-001).

## Part B - Kestrel as Controller (Art. 30(1))

| # | Purpose | Lawful basis | Data subjects | Personal data | Retention |
|---|---|---|---|---|---|
| C1 | Account administration | Art. 6(1)(b) contract | Customer administrators | Name, work email, job title | Term + 3 years |
| C2 | Billing and tax | Art. 6(1)(b), 6(1)(c) | Billing contacts | Name, email, VAT, payment token | 7 years |
| C3 | Product telemetry | Art. 6(1)(f) legitimate interests | Platform users | Pseudonymous usage events | 24 months |
| C4 | Marketing | Art. 6(1)(a) consent (EEA/UK) | Prospects | Name, work email, company | Withdrawal + 1 year |
| C5 | Recruitment | Art. 6(1)(f), 6(1)(a) | Applicants | CV, interview notes | 12 months |
| C6 | Employment administration | Art. 6(1)(b), 6(1)(c) | Employees, contractors | HR records | Employment + 7 years |

## Special category data

Kestrel does **not** intentionally process special category data under Art. 9 in the
course of providing the Service. Customers are contractually prohibited from uploading
special category data without a prior written agreement; no such agreement is in force as
of 20 January 2026.

## Children

The Service is not directed at children and Kestrel does not knowingly process the
personal data of anyone under 16.

## Data Protection Impact Assessments

One DPIA is on file, covering the introduction of the anomaly detection feature
(`LEG-GOV/dpia-2025-01`, completed 8 September 2025). Conclusion: residual risk low, no
prior consultation with the supervisory authority required.
"""

DOCS["standard-contractual-clauses-summary.md"] = """# Standard Contractual Clauses and Transfer Impact Assessment - Summary

**Document ID:** KD-LEG-006 · **Version:** 2.2 · **Owner:** Aaron Feldstein, General Counsel
**Last updated:** 28 January 2026 · **Next review:** 28 January 2027

## 1. Which clauses apply

Kestrel incorporates the **2021 EU Standard Contractual Clauses** (Commission
Implementing Decision (EU) 2021/914 of 4 June 2021):

- **Module Two** (Controller to Processor) where a customer established in the EEA
  transfers personal data to Kestrel in the US.
- **Module Three** (Processor to Sub-processor) where a customer is itself a processor,
  and in Kestrel own contracts with its US sub-processors.

Docking clause (Clause 7) is included. Option 2 of Clause 9 (general written
authorisation for sub-processors) applies, with the 30-day notice period recorded in the
DPA.

Governing law for the SCCs is Irish law; the competent supervisory authority is the Irish
Data Protection Commission; the forum for disputes is the courts of Ireland.

## 2. UK and Switzerland

- **UK** - the ICO International Data Transfer Addendum (version B1.0, in force
  21 March 2022) is appended to the EU SCCs. UK GDPR references replace EU GDPR
  references; the competent authority is the ICO.
- **Switzerland** - the Swiss Annex applies. The FDPIC is the competent authority, and
  references to Member State law are read as references to Swiss law. The SCCs are
  extended to protect data of legal entities as required by the revised FADP.

## 3. EU-US Data Privacy Framework

Kestrel is **not** currently self-certified under the EU-US Data Privacy Framework.
Transfers rely on the SCCs plus the supplementary measures below. Self-certification is
under evaluation but no commitment date has been set. This is stated plainly rather than
implied.

## 4. Transfer Impact Assessment - conclusion

A TIA was completed on 28 January 2026 covering transfers to the US. Summary of
conclusions:

**Assessment of US law.** Kestrel assessed FISA 702 and EO 12333. Kestrel is not an
"electronic communications service provider" as defined in 50 U.S.C. 1881(b)(4) and has
never received a national security request of any kind, nor a National Security Letter,
nor any request under FISA. A warrant canary is not maintained; the absence of requests is
stated directly here instead.

**Supplementary measures in place:**

1. Encryption in transit (TLS 1.3) and at rest (AES-256-GCM) with keys held by Kestrel in
   AWS KMS, not by any sub-processor in plaintext form.
2. EU customers may elect the EU instance, keeping data within the EEA for all
   infrastructure subprocessors.
3. Enterprise customers may hold their own key material for the Snowflake layer through
   Tri-Secret Secure, so that layer cannot be read without customer participation.
4. A documented government-access response procedure requiring legal review, a challenge
   to overbroad or unlawful requests, and customer notification unless legally prohibited.
5. Transparency reporting: Kestrel publishes an annual count of government data requests
   received. The count for 2025 was **zero**.

**Residual risk:** assessed as low for customers electing the EU instance, and low to
moderate for EU customers electing the US instance, with the moderate rating driven by
theoretical rather than observed exposure.
"""

DOCS["security-addendum.md"] = """# Security Addendum

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
"""

DOCS["dpa-annex-2-technical-measures.md"] = """# DPA Annex 2 - Technical and Organisational Measures

**Document ID:** KD-LEG-009 · **Version:** 4.0 · **Owner:** Marcus Oyelaran, CISO
**Effective:** 1 February 2026 · Annex to the DPA (KD-LEG-001)
**Maps to:** GDPR Art. 32

Measures are described at a level that allows a controller to assess adequacy under
Art. 32 without exposing information that would itself create risk.

## 1. Pseudonymisation and encryption (Art. 32(1)(a))

Data at rest is encrypted with AES-256-GCM across RDS, S3, EBS, and Snowflake. Data in
transit uses TLS 1.3 externally and mutual TLS internally within the service mesh.
Application logs are pseudonymised at emission: direct identifiers are replaced with
opaque `tenant_id` and `user_id` values before the log leaves the process.

## 2. Confidentiality, integrity, availability, resilience (Art. 32(1)(b))

**Confidentiality.** Least-privilege access via Okta with mandatory hardware MFA;
just-in-time production elevation expiring in 8 hours; tenant isolation verified by
independent penetration testing (no cross-tenant access achieved, February 2026).

**Integrity.** All changes reviewed and merged through pull request with a required
second approver; 3,847 pull requests merged in 2025, 100% reviewed. Infrastructure defined
as code in Terraform. Immutable, tamper-evident audit logging with S3 Object Lock in
compliance mode.

**Availability.** Multi-AZ deployment across three availability zones; 99.97% measured
availability in 2025 against a 99.9% commitment; autoscaling with capacity headroom
monitored continuously.

**Resilience.** Point-in-time recovery with 35-day retention; cross-region backup
replication within the residency boundary; quarterly restore testing with a 1,240-assertion
integrity suite.

## 3. Restoring availability (Art. 32(1)(c))

RTO 4 hours, RPO 15 minutes. The 17 January 2026 full failover exercise achieved
2 hours 41 minutes and 4 minutes respectively. Results are documented in KD-SEC-010.

## 4. Regular testing and evaluation (Art. 32(1)(d))

| Activity | Frequency | Last performed |
|---|---|---|
| Independent penetration test | Annual minimum | 3-14 February 2026 |
| SOC 2 Type II audit | Annual | Period ended 31 December 2025 |
| ISO 27001 surveillance audit | Annual | 22 September 2025 |
| Disaster recovery restore test | Quarterly | 17 January 2026 |
| User access review | Quarterly | 12 January 2026 |
| Phishing simulation | Quarterly | Q4 2025 |
| Continuous control monitoring | Continuous | Vanta, 118 automated checks |

## 5. Organisational measures

Security governance through a monthly Security Council with a direct board reporting line;
background screening before start date; confidentiality obligations surviving employment;
annual security awareness training at 100% completion; documented incident response with a
trained IC rota; vendor risk management with tiered diligence.

## 6. Data minimisation

The Service collects only data the customer chooses to provide. Kestrel product telemetry
is pseudonymous and retained for 24 months. Kestrel does not use customer content to train
machine learning models.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in DOCS.items():
        (OUT / name).write_text(body, encoding="utf-8")
        print(f"  {name:44} {len(body.split()):5} words")
    print(f"wrote {len(DOCS)} legal documents")


if __name__ == "__main__":
    main()
