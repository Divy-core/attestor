# Network Security Standard

**Document ID:** KD-SEC-016 · **Version:** 2.0 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 5 February 2026 · **Next review:** 5 February 2027
**Maps to:** SOC 2 CC6.6, CC6.7, CC7.1 · ISO 27001:2022 A.8.20, A.8.21, A.8.22, A.8.23

## 1. Perimeter

All customer traffic enters through Cloudflare. The AWS Application Load Balancers accept
connections only from Cloudflare IP ranges, enforced by security group rules and verified
by a synthetic check that attempts a direct origin connection hourly and alerts if it
succeeds. The origin has not been reachable directly since 4 September 2024.

## 2. Web application firewall

**Cloudflare WAF is enabled on all production hostnames**, running the Cloudflare Managed
Ruleset, the OWASP Core Ruleset at paranoia level 2, and 14 Kestrel-authored rules. Rules
are deployed in log mode for 7 days before enforcement so that a false positive is found
before it blocks a customer. Blocked request volume averaged 41,000 per day during 2025,
of which the largest categories were automated credential stuffing and SQL injection
probes; none reached the application tier.

Rate limiting is applied at the edge: 120 requests per minute per tenant for the public
API, and 10 authentication attempts per five minutes per source address.

## 3. Segmentation

Each environment is a separate AWS account inside the AWS Organization
(`kd-prod-us`, `kd-prod-eu`, `kd-staging`, `kd-dev`, `kd-security`, `kd-shared-services`).
There is **no VPC peering and no transit gateway route between production and
non-production**; the boundary is an account boundary, not a firewall rule that can be
edited.

Within a production VPC there are three subnet tiers — public (load balancers only),
private application, and private data — with security groups referencing other security
groups rather than CIDR ranges, so a rule cannot silently widen when an address is reused.

## 4. Inbound rules

**There are no unrestricted inbound rules (`0.0.0.0/0`) in any production security group
other than ports 80 and 443 on the load balancer security group, which are themselves
restricted to Cloudflare ranges.** SSH ingress is closed entirely; administrative access is
through AWS Systems Manager Session Manager, which requires an IAM identity and produces a
recorded session.

Config rules `restricted-ssh` and `vpc-sg-open-only-to-authorized-ports` evaluate this
continuously and were compliant for 100% of evaluation intervals in 2025.

## 5. Egress

Production workloads egress through NAT gateways with a domain allowlist enforced by AWS
Network Firewall. The allowlist contains 23 destinations, each with a named owner and a
business justification. An attempted connection to a destination outside the allowlist is
blocked and alerted; 61 such alerts in 2025 were all traced to dependency mirrors being
retired upstream.

## 6. Internal application access

There is no traditional VPN. Internal applications are published through Cloudflare Access
with device posture and Okta identity required on every request. This is a zero-trust
model in the practical sense: there is no network position that grants access, and being
"inside" confers nothing.

## 7. Container security

Workloads run on EKS with:

* non-root user enforced (`runAsNonRoot: true`) and read-only root filesystems;
* all Linux capabilities dropped except `NET_BIND_SERVICE`;
* Pod Security Admission in `restricted` mode at the namespace level, which rejects a
  non-conforming workload at admission rather than reporting it later;
* network policies denying pod-to-pod traffic by default, with 34 explicit allow rules;
* image admission restricted to signed images from the Kestrel ECR registry.

## 8. Wireless

The Austin office wireless network is WPA3-Enterprise with certificate-based
authentication for the corporate SSID and a fully segregated guest SSID that routes
straight to the internet. Neither network grants any access to production; production is
reached the same way from the office as from a coffee shop, which is deliberate.

## 9. DDoS protection

Cloudflare provides layer 3/4 and layer 7 DDoS protection. The largest mitigated event in
2025 peaked at 18 Gbps on 3 June 2025 and caused no measurable increase in application
error rate.

## 10. Testing

Network controls are exercised annually by the external penetration test. The February
2026 test (Include Security, INSEC-2026-0219) attempted lateral movement from a
compromised application container and did not achieve it; the finding raised was the rate
limiting gap on the password reset endpoint, remediated on 27 February 2026.
