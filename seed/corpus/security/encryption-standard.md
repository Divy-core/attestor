# Encryption Standard

**Document ID:** KD-SEC-003 · **Version:** 2.4 · **Owner:** Marcus Oyelaran, CISO
**Approved:** 8 January 2026 · **Next review:** 8 January 2027
**Maps to:** SOC 2 CC6.7 · ISO 27001:2022 A.8.24 · GDPR Art. 32(1)(a)

## 1. Data at rest

All customer data at rest is encrypted using **AES-256-GCM**. No customer data is stored
unencrypted at any tier.

| Store | Mechanism | Key custody |
|---|---|---|
| Amazon RDS (PostgreSQL 16) | Storage-level encryption | AWS KMS CMK, per-environment |
| Amazon S3 | SSE-KMS | AWS KMS CMK, per-bucket |
| Snowflake | Native encryption, Tri-Secret Secure | Customer-managed key in AWS KMS |
| Amazon EBS | Encrypted volumes, enforced at Organization level | AWS KMS CMK |
| Backups | Same CMK class as source | AWS KMS |

Application-level field encryption is applied additionally to authentication secrets, API
tokens, and OAuth refresh tokens using AES-256-GCM with keys held in AWS Secrets Manager
and rotated every 90 days.

## 2. Data in transit

**TLS 1.3** is the default for all external connections. TLS 1.2 remains enabled solely
for one legacy customer SFTP integration (INT-LEGACY-0004), scheduled for decommission in
Q3 2026 and tracked as RISK-2025-0088. TLS 1.0 and 1.1 were disabled across all endpoints
on 12 April 2024.

Cipher suites are restricted to the Mozilla Intermediate profile. HSTS is enabled with
`max-age=31536000; includeSubDomains; preload`. Certificates are issued by AWS
Certificate Manager and rotated automatically, so there is no manual certificate
handling. Internal service-to-service traffic within the EKS cluster is encrypted with
mutual TLS enforced by Istio 1.24, with certificates rotated every 24 hours.

## 3. Key management

Customer-facing encryption keys are AWS KMS Customer Managed Keys. Key policies restrict
use to named service roles. Key administrators and key users are disjoint sets: no
individual holds both roles, enforced by a Service Control Policy at the AWS Organization
level.

Automatic annual rotation is enabled on all CMKs. Manual rotation is triggered on
suspected compromise. Key deletion requires a 30-day waiting period and dual
authorisation from the CISO and the VP Engineering.

## 4. Customer-managed encryption keys

Enterprise-tier customers may supply their own key material for the Snowflake data layer
through Tri-Secret Secure. As of 1 February 2026, 3 customers use this option. CMEK is
**not** currently offered for the RDS or S3 tiers. That limitation is stated explicitly in
the Security Addendum (KD-LEG-008) rather than glossed over.

## 5. Algorithm policy

Approved: AES-256-GCM, AES-256-CBC (legacy read paths only), RSA-2048 and above, ECDSA
P-256 and P-384, SHA-256 and above, Argon2id and bcrypt (cost factor 12 or higher) for
password hashing.

Prohibited: DES, 3DES, RC4, MD5 and SHA-1 for any security purpose, RSA below 2048 bits,
and any custom cryptographic construction. Kestrel does not implement its own
cryptographic primitives.

## 6. Post-quantum posture

Kestrel completed a cryptographic inventory (`SEC-GOV/crypto-inventory`, 19 January 2026)
as the first step toward post-quantum readiness. TLS connections terminated at Cloudflare
already negotiate the hybrid X25519MLKEM768 key agreement where the client supports it.
No migration commitment date has been set for internal services.
