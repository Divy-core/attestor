# Company and Security Programme Overview

**Document ID:** KD-SEC-021 · **Version:** 4.2 · **Owner:** Marcus Oyelaran, CISO
**Approved:** 6 February 2026 · **Next review:** 6 February 2027
**Maps to:** SOC 2 CC1.1, CC1.2, CC2.1, CC2.3 · ISO 27001:2022 Clauses 4–5

## 1. The company

Kestrel Data, Inc. was **founded in 2019** and is **incorporated in Delaware as a
C-corporation**. Headquarters are at 1401 Lavaca Street, Suite 210, Austin, TX 78701. The
company is privately held and has been through no change of control, merger, or material
corporate restructuring since incorporation.

As of 1 February 2026 Kestrel employs **187 employees and engages 24 contractors**, in the
United States (149), the European Union (22), the United Kingdom (11), and Canada (5). The
company is remote-first with one office.

## 2. The product

Kestrel Insight is a **multi-tenant SaaS** B2B analytics platform. Customers load their own
datasets and query them through the web application and the public API. There is one
production code base and one set of production environments; every customer runs on the
same version.

The platform is offered from two instances: the **US instance** in AWS `us-east-1` and the
**EU instance** in AWS `eu-west-1`. A customer's data resides in the instance they select
at contract signature and does not move between instances.

## 3. Security programme in summary

The programme is built on four commitments, each evidenced elsewhere in this corpus rather
than asserted here:

1. **Independent verification.** SOC 2 Type II (Prescient Assurance LLP, report issued
   14 March 2026 covering 1 January – 31 December 2025, **no exceptions**) and ISO 27001:2022
   (BSI certificate `IS-2025-44817`, issued 22 September 2025, valid to 21 September 2028).
2. **Least privilege by construction.** Federated identity, hardware MFA for 100% of
   personnel, just-in-time production access expiring after 8 hours, and no long-lived
   cloud access keys.
3. **Evidence that cannot be edited by the people it describes.** Logs and configuration
   history are delivered to a separate AWS account under object lock.
4. **Testing by people who do not work here.** Annual independent penetration testing
   (Include Security, `INSEC-2026-0219`, 3–14 February 2026: 0 Critical, 1 High, remediated
   27 February 2026).

## 4. Governance

The CISO reports to the CEO and to the board quarterly. A Security Steering Group meets
monthly. The risk register holds 41 open risks and is reviewed monthly and re-scored
quarterly. Full detail is in KD-SEC-018.

## 5. Named accountabilities

| Role | Holder |
|---|---|
| Chief Information Security Officer | Marcus Oyelaran |
| VP Engineering | Dana Whitfield |
| General Counsel | Aaron Feldstein |
| Data Protection Officer | Priya Raghunathan |
| Head of IT | Sofia Brenner |
| Board director with security oversight | Elaine Vasquez (non-executive) |

**Primary security contact for customers:** Marcus Oyelaran, `security@kestreldata.com`,
+1 512 555 0148. Vulnerability reports should go to `security@kestreldata.com`; the
acknowledgement target is one business day.

## 6. Scope of certification

The ISO 27001 certificate scope statement reads: *"The information security management
system supporting the development, operation, and support of the Kestrel Insight analytics
platform, including supporting corporate functions, at Austin, Texas and for remote
personnel."* The SOC 2 report covers the Security, Availability, and Confidentiality Trust
Services Criteria. Privacy and Processing Integrity are **not** in scope, which is stated
plainly rather than left for a reader to discover.

## 7. Communicating change to customers

Material security changes are communicated through the trust page at
`trust.kestreldata.com`, through the subprocessor change notice mailing list (30 days'
notice, see KD-LEG-001 §5), and through the account team for changes affecting a specific
contract. Incident communications follow the commitments in KD-SEC-008 §4.

## 8. How to obtain evidence

The SOC 2 Type II report, the ISO 27001 certificate, the penetration test executive
summary, and the standard DPA are available to customers and prospects under NDA through
the account team, usually within two business days. The certificate and the trust page are
available without an NDA.
