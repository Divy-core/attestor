# Penetration Test Executive Summary

**Document ID:** KD-SEC-007 · **Engagement:** INSEC-2026-0219
**Tester:** Include Security, Inc. · **Dates:** 3-14 February 2026
**Report issued:** 24 February 2026 · **Retest completed:** 18 March 2026
**Classification:** Confidential - shareable with customers under NDA

## 1. Scope

- Kestrel Insight web application (`app.kestreldata.com`), authenticated and
  unauthenticated surfaces
- Public REST API v2 (`api.kestreldata.com`), 89 endpoints
- Multi-tenant isolation testing across 3 seeded tenant accounts
- AWS cloud configuration review (IAM, S3, VPC, EKS)
- Authentication and session management, including the Okta SAML integration

Out of scope: physical security, social engineering against staff, denial-of-service
testing, and third-party SaaS operated by subprocessors.

## 2. Methodology

Grey-box testing against OWASP ASVS 4.0 Level 2 and the OWASP API Security Top 10.
Testers were given standard-tier and admin-tier credentials for each seeded tenant plus
API documentation. 74 person-hours across two testers.

## 3. Findings summary

| Severity | Found | Remediated | Retest verified |
|---|---|---|---|
| Critical | 0 | - | - |
| High | 1 | 1 | 18 March 2026 |
| Medium | 4 | 4 | 18 March 2026 |
| Low | 7 | 5 | 18 March 2026 |
| Informational | 9 | Accepted / tracked | - |

**No cross-tenant data access was achieved.** Tenant isolation held under every tested
condition, including direct object reference manipulation, JWT tenant-claim tampering,
and GraphQL alias-based enumeration.

## 4. The High finding

**INSEC-2026-0219-H1 - Insufficient rate limiting on the password reset endpoint.**

`POST /v2/auth/password-reset` was not rate limited per account, permitting user
enumeration through response timing and, in combination with a slow SMTP path, a
potential mail-flood against a targeted address. No authentication bypass was possible
and no data was exposed.

Remediated on 6 March 2026 by adding a per-account token bucket (5 requests per hour) and
normalising the response body and timing across existing and non-existing accounts.
Retest on 18 March 2026 confirmed closure.

## 5. Medium findings, in brief

1. Session cookie missing `SameSite=Strict` on one legacy subdomain. Fixed 2 March 2026.
2. Verbose error messages disclosing library versions on 4 API endpoints. Fixed 4 March 2026.
3. S3 bucket `kestrel-public-assets` permitted `s3:ListBucket` to authenticated AWS
   principals outside the Organization. Fixed 27 February 2026 with an explicit bucket
   policy deny.
4. Missing `Content-Security-Policy` on the marketing site. Fixed 9 March 2026.

## 6. Two accepted low findings

- **L6** - Username enumeration remains possible through the SAML metadata endpoint for
  customers using IdP-initiated flows. Accepted; mitigation would break a documented
  integration pattern. Tracked as RISK-2026-0004.
- **L7** - TLS 1.2 remains enabled on the legacy SFTP endpoint. Accepted until the Q3 2026
  decommission. Tracked as RISK-2025-0088.

## 7. Tester statement

*"Kestrel Data demonstrates a mature security posture for an organisation of its size.
Tenant isolation is well implemented and defence in depth is evident throughout the
platform. The single High finding was an availability and privacy concern rather than a
confidentiality one."* - Include Security, 24 February 2026
