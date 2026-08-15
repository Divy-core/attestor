# DPA Annex 2 - Technical and Organisational Measures

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
