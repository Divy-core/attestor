# Cloud Security Posture and Configuration Standard

**Document ID:** KD-SEC-017 · **Version:** 1.8 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 29 January 2026 · **Next review:** 29 January 2027
**Maps to:** SOC 2 CC6.1, CC7.1, CC8.1 · ISO 27001:2022 A.8.9, A.8.19, A.8.32

## 1. Account structure and guardrails

Kestrel runs an AWS Organization with six accounts and a dedicated management account that
holds no workloads. **Organisation-level Service Control Policies enforce security
configuration centrally**, so a misconfiguration in a member account is refused by the
platform rather than caught later by a report. The enforced SCPs are:

| SCP | Effect |
|---|---|
| `deny-root-user` | Root credentials cannot perform any action outside break-glass |
| `deny-region-except-approved` | Only `us-east-1`, `eu-west-1`, and `us-east-2` (backup) are usable |
| `deny-cloudtrail-disable` | CloudTrail cannot be stopped or deleted in any account |
| `deny-guardduty-disable` | GuardDuty cannot be suspended |
| `deny-public-s3` | Account-level public access block cannot be removed |
| `deny-kms-key-delete` | Customer master keys cannot be scheduled for deletion |
| `deny-imdsv1` | EC2 instances requiring IMDSv1 cannot be launched |

## 2. Infrastructure as code

**All production infrastructure is defined in Terraform** and applied through Terraform
Cloud workspaces bound to the `kd-infra` GitHub repository. Plans require review by a
second engineer and apply runs only from the `main` branch. There were 1,847 applies
during 2025, every one traceable to a merged pull request.

## 3. Manual console changes

**Manual changes in the production AWS console are not permitted.** Human IAM principals
in production accounts hold read-only permissions by default; write access is obtained
through the just-in-time process in KD-SEC-002 §4, is time-boxed to 8 hours, and is
intended for incident response rather than routine change.

Any write action taken by a human principal in production generates a CloudTrail event
that is matched against open incident tickets; unmatched events are reviewed weekly by the
VP Engineering. In 2025 there were 34 such events, 31 matched to incidents and 3 followed
up, of which 2 resulted in the Terraform being corrected to match reality.

## 4. Drift detection

Configuration drift is detected three ways:

1. **Terraform Cloud drift detection** runs every 24 hours against all 19 production
   workspaces and opens a PagerDuty ticket on any difference. Mean drift events per month
   in 2025: 1.4, nearly all from AWS-side attribute defaults changing.
2. **AWS Config** evaluates 147 rules continuously across every account, with the
   conformance pack results published to the security account.
3. **Prowler** runs weekly against the CIS AWS Foundations Benchmark v3.0. The most
   recent run, on 2 February 2026, reported 94% pass with 11 accepted deviations, each
   documented with a rationale and an owner.

## 5. Vulnerability and patch posture of cloud compute

Production compute nodes are immutable: they are replaced, never patched in place. The
node group is recycled on a rolling basis so that **no production compute node is older
than 30 days**; the measured maximum node age during 2025 was 27 days. Container base
images are rebuilt nightly from upstream and redeployed if the digest changes.

Operating system CVEs are therefore addressed by the next rebuild rather than by an
emergency patch cycle, with a maximum exposure window of 24 hours for the image and 30
days for the host.

## 6. Handling a vulnerability with no upstream fix

Where no upstream fix exists, the finding is recorded in the risk register with a named
owner and the mitigation is chosen from: removing the affected component, blocking the
attack path at the network or WAF layer, or applying a vendored patch maintained by
Kestrel (see KD-ENG-007 §7). The vulnerability is re-reviewed weekly until an upstream fix
lands. Two such findings were open during 2025, both resolved by upstream releases within
45 days.

## 7. Cloud identity

Human access to AWS is federated from Okta through IAM Identity Center; there are **no IAM
users with long-lived access keys in any account**, and the SCP set prevents their
creation. Workloads use IAM Roles for Service Accounts. The last static access key in the
estate was decommissioned on 17 November 2024.

## 8. Logging and evidence

CloudTrail (all regions, all accounts), VPC flow logs, GuardDuty findings, and Config
history are delivered to a dedicated `kd-security` account that no engineering role can
write to, with object lock set to 400 days. This is the arrangement that makes the audit
trail defensible: the accounts that generate the evidence cannot alter it.
