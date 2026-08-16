# API, Authentication and Integration Standard

**Document ID:** KD-ENG-005 · **Version:** 2.6 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 21 January 2026 · **Next review:** 21 January 2027
**Maps to:** SOC 2 CC6.1, CC6.6, CC8.1 · ISO 27001:2022 A.5.15, A.8.5, A.8.26

## 1. API authentication

The public API at `https://api.kestreldata.com/v2` accepts two credential types:

* **OAuth 2.0 bearer tokens** issued through the client-credentials flow, valid for 60
  minutes, scoped to a single tenant and to an explicit permission set. This is the
  recommended integration path.
* **API keys** for simple server-to-server use, presented as a bearer token, scoped the
  same way.

Every request is authenticated; there is **no anonymous surface** other than the status
endpoint and the OpenAPI document. Requests without a valid credential receive 401 with no
information about whether the tenant or resource exists.

## 2. Customer control of API credentials

**Customers create, scope, rotate, and revoke their own API credentials** from the tenant
administrator console, without involving Kestrel support. A credential can be limited to
read-only, to specific datasets, and to a source IP allowlist. Revocation takes effect
within 30 seconds across all regions.

**Kestrel support cannot recover a customer API key.** Keys are displayed once at creation
and stored only as a salted SHA-256 hash; there is no path, for support or for engineering,
that returns the plaintext. A lost key is replaced, not recovered.

## 3. Rate limiting

Rate limits are enforced at Cloudflare, ahead of application code:

| Surface | Limit |
|---|---|
| Public API, per tenant | 120 requests/minute |
| Bulk export endpoint, per tenant | 6 requests/minute |
| Authentication endpoints, per source address | 10 attempts/5 minutes |
| Password reset, per account | 5 attempts/hour (added 27 February 2026) |

Exceeding a limit returns HTTP 429 with a `Retry-After` header. Enterprise tenants may
request a higher limit through the account team; 7 tenants had raised limits as of
1 February 2026.

## 4. Single sign-on

**SAML 2.0 and OpenID Connect single sign-on are supported on all paid tiers**, at no
additional charge — SSO is a security control, not an upsell. Kestrel has verified
integrations with Okta, Microsoft Entra ID, Google Workspace, OneLogin, and Ping Identity,
and supports any conforming IdP through generic metadata exchange.

Tenant administrators may enforce SSO-only login, disabling password authentication for
their users entirely; 34 of the tenants on the platform had done so as of 1 February 2026.
Just-in-time user creation on first SSO login is supported and can be restricted to
verified email domains. Group-to-role mapping is available through SAML attribute
assertions.

## 5. Session management

Web sessions expire after 12 hours of absolute lifetime and 60 minutes of inactivity.
Session cookies are `Secure`, `HttpOnly`, and `SameSite=Lax`. Tenant administrators can
terminate any active session for their users from the console, and a password change or
SSO de-provisioning terminates all sessions for that user immediately.

## 6. Customer access to audit logs

Tenant administrators can retrieve their own audit log from the console and from
`GET /v2/audit-events`, covering: sign-in and sign-out, permission changes, dataset
create/read/update/delete, export, API credential lifecycle, and configuration changes.
Each entry records the actor, the action, the target, the source IP, and a UTC timestamp.

**Customer-accessible audit history covers the last 13 months**, and is exportable as JSON
for onward ingestion into a customer SIEM. Kestrel's own internal security logs are
retained longer (KD-SEC-009 §2) but are not customer-accessible, because they contain
cross-tenant operational detail.

## 7. Versioning and deprecation

The API is versioned in the path (`/v1`, `/v2`). Backwards-incompatible changes are made
only in a new version.

**The deprecation policy is 12 months' notice** for a whole API version and **90 days'
notice** for an individual endpoint or field, delivered by email to tenant administrators,
by a `Deprecation` and `Sunset` response header, and on the developer changelog. `v1` was
deprecated on 14 October 2024 and reached end of life on 14 October 2025; 100% of tenants
had migrated 6 weeks before the deadline.

Breaking changes to the web application follow the customer notification commitments in
the Change Management Procedure (KD-ENG-002 §6).

## 8. Webhooks

Outbound webhooks are signed with an HMAC-SHA256 signature over the raw body using a
per-endpoint secret the customer can rotate. Delivery is retried with exponential backoff
for 24 hours. Kestrel will not deliver a webhook to a plaintext HTTP endpoint.

## 9. Integration security review

A new first-party integration is reviewed against this standard before release, covering
credential handling, scope minimisation, rate limiting, and audit event emission. The
review is recorded in the pull request and is a required check on the `kd-api` repository.
