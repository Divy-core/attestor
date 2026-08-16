# Data Flow and Shared Responsibility Model

**Document ID:** KD-ENG-011 · **Version:** 2.0 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 2 February 2026 · **Next review:** 2 February 2027
**Maps to:** SOC 2 CC2.3, CC3.2, CC6.1 · ISO 27001:2022 A.5.2, A.5.8, A.8.9 · GDPR Article 28

## 1. Data flow, end to end

1. **Ingress.** A customer's browser or API client resolves `app.kestreldata.com` or
   `api.kestreldata.com` to Cloudflare, which terminates TLS 1.3 (1.2 minimum) and applies
   WAF and rate limiting.
2. **Instance selection.** DNS and tenant routing send the request to the instance chosen
   at contract signature: `us-east-1` or `eu-west-1`. **A tenant's requests never cross to
   the other instance.**
3. **Authentication.** The request is authenticated against the tenant's configured method
   — SSO through the customer's IdP, or a scoped API credential.
4. **Application tier.** EKS workloads in the private application subnet handle the
   request. Every query carries the tenant identifier, and row-level security in the data
   tier enforces it independently of application logic (KD-ENG-004 §5).
5. **Data tier.** Account identity data and usage events live in Aurora PostgreSQL;
   customer-uploaded datasets live in Snowflake and S3. All are encrypted at rest with
   AES-256-GCM under AWS KMS keys held in the instance's own region.
6. **Egress to the customer.** Results return by the same path. Bulk exports are served
   from pre-signed URLs valid for 15 minutes.
7. **Operational telemetry.** Logs and metrics flow to Datadog with customer content
   fields redacted at source; security-relevant events additionally flow to the immutable
   archive in the `kd-security` AWS account.
8. **Backups.** Snapshots and continuous log shipping remain within the instance's region,
   with a second copy in a separate AWS account under object lock — `us-east-2` for the US
   instance, and within `eu-west-1` for the EU instance, so that EU content never leaves
   the EEA.

## 2. What crosses a boundary, and what does not

| Data | Leaves the tenant's region? |
|---|---|
| Customer-uploaded analytics datasets | **No** |
| Backups of customer content | **No** |
| Account identity data (EU tenants) | Only for support access, under §4 of KD-SEC-002 |
| Pseudonymised product telemetry | Yes — aggregated to the US, assessed in KD-LEG-010 |
| Operational logs (content-redacted) | Yes — Datadog US, assessed in KD-LEG-010 |
| Transactional email recipient address | Yes — SendGrid US |

## 3. The shared responsibility model

Security of the platform is Kestrel's. Security of what the customer does with it is the
customer's. The line is drawn here rather than left implicit, because an unstated boundary
is where a breach lands on whoever is less able to argue.

**Kestrel is responsible for:**

* physical and environmental security of the infrastructure (through AWS), and the cloud
  configuration on top of it;
* platform patching, hardening, and the immutable node lifecycle;
* tenant isolation, including the row-level enforcement and its continuous testing;
* encryption at rest and in transit, and key management;
* availability, backup, and restore against the committed RTO of 4 hours and RPO of 15
  minutes;
* logging, monitoring, and incident detection and response for the platform;
* vulnerability management, penetration testing, and independent audit;
* vetting and managing sub-processors.

**The customer is responsible for:**

* deciding what data they upload, and its lawfulness — Kestrel does not inspect it;
* configuring SSO, and enforcing MFA at their own identity provider;
* managing their own users, roles, and permissions, including timely removal of leavers;
* the scope, storage, and rotation of the API credentials they create;
* reviewing their own audit log, which Kestrel makes available for 13 months;
* acting as controller for data subject requests concerning their content;
* notifying their own data subjects and regulators where they are the controller;
* device and endpoint security for the people who use the product.

## 4. Complementary user entity controls

The SOC 2 report lists these as complementary user entity controls; they are restated here
because a customer reading only this document should still see them. If a customer does not
operate the controls in the second list, Kestrel's controls in the first do not compensate:
Kestrel cannot detect that a departed employee of the customer still holds a valid session
if the customer never de-provisioned them at their IdP.

## 5. Categories of customer data

The four categories the platform processes are defined in KD-SEC-004 §2 and mirrored in the
Records of Processing Activities (KD-LEG-004). Kestrel does not process payment card data:
Stripe collects card details directly and Kestrel holds a token and the last four digits.

## 6. Where processing occurs

Processing occurs in AWS `us-east-1` (US instance) and AWS `eu-west-1` (EU instance), by
personnel located in the United States, the European Union, the United Kingdom, and
Canada, under the access controls in KD-SEC-002. **No processing occurs outside the stated
regions**, and no support is subcontracted to a third-party provider in another
jurisdiction.
