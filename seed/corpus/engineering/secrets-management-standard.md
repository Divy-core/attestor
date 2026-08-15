# Secrets Management Standard

**Document ID:** KD-ENG-006 · **Version:** 2.1 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 22 January 2026 · **Maps to:** SOC 2 CC6.1 · ISO 27001:2022 A.8.24

## 1. Where secrets live

| Secret class | Store | Rotation |
|---|---|---|
| Application secrets, API keys | AWS Secrets Manager | 90 days, automated |
| Database credentials | AWS Secrets Manager with RDS-managed rotation | 30 days, automated |
| Encryption keys | AWS KMS (key material never leaves KMS) | Annual, automated |
| TLS certificates | AWS Certificate Manager | Automatic before expiry |
| CI/CD credentials | GitHub OIDC to AWS IAM roles - **no stored credentials** | N/A |
| Human-held shared credentials | 1Password shared vaults | On personnel change |

## 2. What is prohibited

- Secrets in source code, including test fixtures, comments, and commit history.
- Secrets in environment files committed to a repository.
- Secrets in CI logs, container image layers, or Terraform state in plaintext.
- Long-lived static AWS access keys. The last was decommissioned 17 November 2024, and
  creation of a new one triggers an hourly detection that pages the on-call SRE.
- Sharing a secret over Slack, email, or ticket. If it has been sent that way, it is
  treated as compromised and rotated.

## 3. Detection

Three layers, because one is not enough:

1. **Pre-commit** - Gitleaks runs locally through a managed pre-commit hook.
2. **Push protection** - GitHub secret scanning with push protection enabled
   organisation-wide, which blocks the push rather than alerting after the fact.
3. **Continuous** - GitHub Advanced Security scans all repository history daily, including
   private repositories.

In 2025 push protection blocked **7** attempted secret commits. None reached the
repository. Zero secrets were found in historical scanning.

## 4. Runtime access

Workloads obtain secrets at runtime through the AWS Secrets Manager CSI driver, mounted as
in-memory `tmpfs` volumes. Secrets are never written to disk, never baked into container
images, and never passed as environment variables where they could leak into a crash dump
or a process listing.

## 5. Rotation on compromise

A suspected compromised secret is rotated immediately, before investigation completes.
The order is rotate, then investigate, then decide whether it was actually exposed:
delaying rotation while establishing certainty extends the exposure window for no benefit.

## 6. Customer-issued API credentials

API keys issued to customers are stored as Argon2id hashes; Kestrel cannot recover the
plaintext of a customer API key and will not do so on request. Keys are displayed once at
creation. Customers can create, scope, and revoke keys through the console, and each key
carries a last-used timestamp so stale keys are visible.
