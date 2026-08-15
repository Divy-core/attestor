# Secure Software Development Lifecycle Policy

**Document ID:** KD-ENG-001 · **Version:** 4.1 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 20 January 2026 · **Next review:** 20 January 2027
**Maps to:** SOC 2 CC8.1 · ISO 27001:2022 A.8.25-A.8.31

## 1. Branching and review

`main` is protected and always releasable. All work happens on short-lived branches
merged by pull request. `main` requires:

- At least **one approving review** from a engineer other than the author. Self-approval
  is blocked at the platform level.
- Two approvals for changes touching authentication, authorisation, tenant isolation, or
  cryptography. These paths are enforced by `CODEOWNERS`.
- All required status checks green: unit tests, integration tests, Semgrep, CodeQL, Snyk,
  Checkov, and type checking.
- Linear history; merge commits are disabled.

In 2025 Kestrel merged **3,847 pull requests**, of which 100% carried at least one
approving review. Administrator bypass of branch protection is disabled and its use would
generate an audit event; it was used zero times in 2025.

## 2. Environments

| Environment | Purpose | Data |
|---|---|---|
| `local` | Developer machines | Synthetic fixtures only |
| `ci` | Automated testing | Synthetic fixtures only |
| `staging` | Pre-production verification | Synthetic and anonymised data |
| `production` | Live service | Customer data |

**Production data is never copied to a lower environment.** Staging fixtures are generated
synthetically by `tools/gen_fixtures.py`. There is no "sanitised production dump" process,
because sanitisation is unreliable and the temptation to skip it is high.

## 3. Testing requirements

- Unit test coverage gate at **80%** on changed lines, enforced in CI.
- Integration tests run against ephemeral infrastructure provisioned per pull request.
- Tenant isolation tests run on every build: a suite of 84 assertions attempting
  cross-tenant reads through every public API surface.
- Load tests before any release expected to change the performance profile.

## 4. Security in the pipeline

Static analysis (Semgrep, CodeQL), dependency scanning (Snyk), IaC scanning (Checkov), and
secret scanning (Gitleaks pre-commit plus GitHub push protection) all run on every pull
request. A Critical or High dependency finding blocks the merge.

## 5. Dependency policy

New third-party dependencies require review against the Third-Party Library Policy
(KD-ENG-008). Lockfiles are committed. Renovate opens automated upgrade pull requests
weekly; security patches are auto-merged when CI is green and the change is a patch-level
bump.

## 6. Release

Releases are continuous: merge to `main` triggers a deployment to staging, an automated
verification suite, and then a progressive rollout to production. Rollout is canary-based:
5% of traffic for 15 minutes, then 25%, then 100%, with automatic rollback on error-rate
or latency regression.

Median lead time from merge to production in 2025 was **34 minutes**. Change failure rate
was 2.1%; mean time to restore was 18 minutes.

## 7. Separation of duties

Engineers cannot deploy their own change to production without the automated pipeline: no
human has the ability to push a container image to the production registry directly. The
deployment role is held by the CI service identity, which authenticates through OIDC and
holds no long-lived credentials.
