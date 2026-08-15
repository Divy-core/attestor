# Infrastructure Architecture Overview

**Document ID:** KD-ENG-003 · **Version:** 3.4 · **Owner:** Dana Whitfield, VP Engineering
**Last updated:** 5 February 2026 · **Classification:** Confidential, NDA required

## 1. Cloud footprint

Kestrel runs on **Amazon Web Services**. There are two independent production instances:

| Instance | Region | Serves |
|---|---|---|
| US | `us-east-1` (3 AZs) | North America, and customers with no residency requirement |
| EU | `eu-west-1` (3 AZs) | EEA, UK, and Swiss customers electing EU residency |

Backup replication targets `us-west-2` and `eu-central-1` respectively, staying within the
residency boundary.

A small number of internal, non-customer-facing workloads run on Google Cloud (BigQuery
for internal finance reporting). No customer data is processed there.

## 2. Account structure

AWS Organizations with 9 accounts: `management`, `security-tooling`, `log-archive`,
`shared-services`, `prod-us`, `prod-eu`, `staging`, `dev`, `sandbox`. Service Control
Policies enforce guardrails at the Organization level, including mandatory EBS encryption,
denial of root user actions, and denial of CloudTrail or GuardDuty disablement.

The `log-archive` account has no human standing access at all.

## 3. Compute

Amazon EKS (Kubernetes 1.31) with managed node groups spanning three availability zones.
Workloads run as containers built from distroless base images, as non-root, with read-only
root filesystems and dropped Linux capabilities. Pod Security Standards are enforced at
the `restricted` level.

Node images are immutable; nodes are recycled with a maximum age of 30 days rather than
patched in place.

## 4. Data layer

| Store | Technology | Purpose |
|---|---|---|
| Primary OLTP | Amazon RDS PostgreSQL 16, Multi-AZ | Application state |
| Analytics | Snowflake | Customer analytics workloads |
| Object storage | Amazon S3 | Uploads, exports, backups |
| Cache | Amazon ElastiCache (Redis 7) | Session and query cache |
| Search | OpenSearch 2.13 | In-product search |

## 5. Tenant isolation

Kestrel Insight is **multi-tenant** with logical isolation. There is no per-customer
infrastructure, no single-tenant deployment option, and no customer-VPC option.

Isolation is enforced at three layers:

1. **Database** - PostgreSQL row-level security policies keyed on `tenant_id`, applied at
   the connection level from an authenticated session variable. No application query can
   opt out.
2. **Application** - every request carries a validated tenant claim; the data access layer
   refuses any query lacking a tenant predicate. This is enforced by a repository base
   class, not by convention.
3. **Storage** - S3 object keys are tenant-prefixed and IAM policies constrain access by
   prefix.

Isolation is verified continuously by an 84-assertion test suite on every build, and was
independently tested in the February 2026 penetration test with no cross-tenant access
achieved.

## 6. Network

Private subnets for all compute and data. No public IP addresses on workload instances.
Ingress is exclusively through Cloudflare, then an AWS Application Load Balancer, then the
Istio ingress gateway. Egress is through NAT gateways with an allowlist for known
destinations.

Security groups are managed in Terraform and default-deny. There are no `0.0.0.0/0`
ingress rules in production; this is asserted by a Checkov rule that fails the build.

## 7. Observability

Datadog for metrics, logs, APM, and security monitoring. Distributed tracing uses W3C
Trace Context propagated across all services. Every log line carries `tenant_id` and
`request_id`.
