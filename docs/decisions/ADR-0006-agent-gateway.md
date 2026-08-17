# ADR-0006 — Agent Gateway: evaluated, and not adopted

**Status:** Accepted · **Date:** 17 Aug 2026 · **Phase:** 5
**Evidence:** `docs/proof/agent-gateway-discovery.txt`

## Context

Agent Gateway is one of the six components Track 3 names, described in the brief as
*"unified routing and policy enforcement"* and, in Google's own worked example, as the
thing an orchestrator uses for *"coordinating with a logistics sub-agent."* It is the one
named component Attestor had never touched. This ADR records what it actually is and why
it is not in the architecture.

## What the discovery found

The first pass concluded, wrongly, that it did not exist:

```
$ gcloud services list --available --filter="name~agent AND name~googleapis"
agentidentity.googleapis.com
agentidentitycredentials.googleapis.com
agentregistry.googleapis.com
libraryagent.googleapis.com
```

No `agentgateway.googleapis.com`. That conclusion was an artifact of the filter — Agent
Gateway is **not an agent-platform API at all**. It lives under **Network Services**, and
the Gemini Enterprise Agent Platform documentation gives it away only in a navigation
entry: `gcloud network-services agent-gateways`.

With `networkservices.googleapis.com` enabled, the resource type is real and reachable in
this project — `list` returns `Listed 0 items` in both `us-central1` and `global`, an
empty result rather than an error. But the verb surface is **delete, describe, export,
import, list**. There is no `create`.

The REST discovery document says what it models:

| Field | Meaning |
|---|---|
| `googleManaged` | proxy orchestrated by Google in a tenant project |
| `selfManaged` | *"Attach to existing Application Load Balancers or Secure Web Proxies"* |
| `registries` | *"Agent registries containing the agents, **MCP servers and tools** governed by…"* |
| `networkConfig.egress` | *"PSC-Interface network attachment for connectivity to your **private VPCs**"* |
| `networkConfig.dnsPeeringConfig` | *"DNS peering … to your **private VPC** network"* |
| `agentConnectivityTemplate` | reference to an `AgentConnectivityTemplate` |

**Agent Gateway is an L7 network data plane for agentic traffic.** It fronts agents, MCP
servers, and tools, and its distinguishing capability is reaching **private VPC
endpoints** through PSC interfaces and DNS peering. That lineage is visible in the
Marketplace listings too: `agentgateway.endpoints.solo-io-public.cloud.goog`.

## Decision

**Evaluated and not adopted.** Attestor does not have the problem Agent Gateway solves.

Every tool the fleet calls is a **Google-managed API reached over a public Google
endpoint**: Vertex AI Search for retrieval, Firestore for domain data, GCS for the corpus
and uploads, Model Armor for screening, Memory Bank for commitments, Vertex embeddings for
relevance. There is no private VPC. There is no MCP server. There is no self-hosted tool
endpoint. There is no cross-network hop to broker.

Provisioning an AgentGateway here would produce a proxy resource with nothing behind it —
a component in the diagram and a line in the write-up, routing zero traffic. That is
exactly the "half-wired integration that does nothing" a reviewer finds in ten seconds,
and it would be worse than the honest absence.

It is also, as of this date, **not creatable through the public gcloud surface** — no
`create` verb, and `import` takes an undocumented spec. Adopting it would have meant
guessing at a schema to build something with no traffic to carry.

## What plays the routing-and-policy role instead

The function Agent Gateway names is real and Attestor performs it — in two layers that are
already built, already tested, and already demonstrated.

**Routing** is the control plane plus Pub/Sub:

```
POST /reviews/{id}/rounds  ──►  attestor.work topic  ──►  Eventarc push
                                                            │
                                             dispatcher /pubsub/push
                                                            │
                          dispatch table keyed on (WorkKind, partition)
```

Work is routed by `WorkKind` and, for the wide stage, by department `partition`
(ADR-0005). The routing decision is a flat dispatch table rather than a proxy rule, and it
is durable: a routing decision survives a crash because it is a message, not a connection.

**Policy enforcement** is three independent layers, none of which is a prompt:

| Layer | Mechanism | Proven by |
|---|---|---|
| Tool authorisation | `enforce_tool_policy` — `before_tool` deny/ask/allow, checked against the datastore the search object is *bound to* | `docs/proof/defence-denial.json` |
| Content | Model Armor floor setting (`inspectAndBlock`) + templates, screening ingress **and** egress including retrieved tool output | `docs/proof/defence-poison.json` |
| Platform | Per-agent service accounts scoped to specific Firestore collections and GCS prefixes | Phase 5 D2 |

The third layer is the one that most resembles what a gateway would give: `SecurityAgent`
cannot read `corpus/legal/**` because IAM refuses, independently of whether the
interceptor is bypassed. A gateway would enforce that at a network hop; Attestor enforces
it at the credential, which is a shorter path with fewer moving parts for a system whose
tools are all first-party APIs.

## Consequences

**Good.** No component in the architecture that carries no traffic. The routing and policy
story is told with mechanisms that are demonstrated working, and each has a proof artefact
behind it rather than a diagram box.

**Bad.** One of the six named Track 3 components is absent, and a reviewer scanning for
all six will notice. Mitigated by saying so explicitly here and in the write-up, with the
measurement that justifies it — which is a stronger position than a provisioned proxy that
routes nothing.

**When this decision would flip.** If Attestor grew a self-hosted tool, an MCP server, or
any tool endpoint inside a private VPC, Agent Gateway becomes the right answer immediately
and this ADR should be revisited. The PSC-interface and DNS-peering fields are precisely
the capability that would then be needed, and nothing in the current design would have to
be unwound to adopt it — the `before_tool` interceptor and the gateway are complementary,
not alternatives.

## Evidence

- `docs/proof/agent-gateway-discovery.txt` — the filtered API list, the verb surface, the
  empty-but-successful `list` in both locations, and the REST schema.
