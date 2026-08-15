# Acceptable Use Policy

**Document ID:** KD-SEC-011 · **Version:** 2.3 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 15 January 2026 · **Next review:** 15 January 2027
**Applies to:** all employees, contractors, and anyone issued Kestrel credentials

## 1. Company devices

All laptops are company-owned and managed through Kandji (macOS) or Intune (Windows).
Personal devices may not access Restricted or Confidential data. BYOD is permitted only
for calendar and chat through managed applications with app-level containerisation.

Required device posture, enforced continuously and checked at every Cloudflare Access
authentication: full-disk encryption enabled, screen lock at 5 minutes, OS within one
minor version of current, EDR agent running (CrowdStrike Falcon), and no unmanaged admin
accounts.

163 macOS and 11 Windows devices were enrolled as of 1 February 2026. Compliance rate at
the last audit was 100%.

## 2. Generative AI tools

Use of generative AI is permitted only with approved tools under a zero-retention
agreement. As of 1 February 2026 the approved list is:

| Tool | Approved for | Data tier permitted |
|---|---|---|
| GitHub Copilot Business | Code completion | Confidential (source code) |
| Anthropic Claude (Team) | Analysis, drafting | Internal |
| OpenAI ChatGPT Enterprise | Analysis, drafting | Internal |

**Customer production data may not be entered into any generative AI tool**, approved or
otherwise, without an executed DPA covering that specific processing and written approval
from the DPO. No such approval has been granted as of 1 February 2026.

Consumer tiers of the above tools are prohibited on company devices; they are blocked at
the DNS layer through Cloudflare Gateway.

## 3. Credentials

Credentials are personal and may not be shared, including with other Kestrel staff.
Shared team accounts are prohibited. Where a shared credential is unavoidable for a
third-party service that does not support SSO, it is held in a 1Password shared vault
with access logged, and the service is recorded on the SSO-gap register.

## 4. Data handling

Restricted data may not be copied to personal cloud storage, personal email, USB media,
or unmanaged devices. USB mass storage is blocked at the OS level on all managed devices.

Screen sharing during customer calls must use a dedicated demo tenant, never production.

## 5. Email and communications

Kestrel uses Google Workspace with DMARC set to `p=reject`, SPF, and DKIM enforced on all
sending domains. External email is banner-tagged. Automatic forwarding to external
addresses is disabled at the tenant level and cannot be enabled by end users.

## 6. Monitoring

Kestrel monitors use of its systems for security purposes as described in the Logging and
Monitoring Standard. Monitoring is proportionate, is not used for productivity
surveillance, and is disclosed to staff at onboarding.

## 7. Reporting

Suspected security issues must be reported to `security@kestreldata.com` or the
`#security-incidents` Slack channel immediately. Kestrel operates a no-blame reporting
culture: reporting a mistake promptly is treated as the correct behaviour, and no
disciplinary action has ever followed a good-faith self-report.
