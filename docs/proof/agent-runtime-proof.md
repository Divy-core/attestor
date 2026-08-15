# Agent Runtime — Section 6 proof

All output below is verbatim from live calls against `attestor-505506`, 14 Aug 2026.

## The resource exists

```
projects/906988347581/locations/us-central1/reasoningEngines/8598754324522205184
displayName : attestor-probe
agent_framework : google-adk
```

Two engines carry the display name `attestor-probe`; `37411432890892288` is the earlier
attempt that failed at startup (see PROGRESS.md), `8598754324522205184` is the working
one. Both scale to zero, so idle cost is nil, but both are listed for teardown.

## Tool calling works — not just text generation

Query: *"How many review questions are currently open?"*

```json
=== TOOL CALLS ===
[ { "id": "call_50308", "args": {}, "name": "get_review_count" } ]

=== TOOL RESPONSES ===
[ { "id": "call_50308", "name": "get_review_count",
    "response": { "result": 312 } } ]

=== FINAL TEXT ===
There are currently 312 open review questions.
```

The tool returns a hardcoded `312`, which the model cannot arrive at by inference, so a
correct answer is itself evidence the tool executed. The `function_call` /
`function_response` pair proves it directly, and the Cloud Trace span below proves it a
third, independent way.

## Distinct Agent Identity

```
identity_type      : IdentityType.AGENT_IDENTITY
effective_identity : agents.global.proj-906988347581.system.id.goog/resources/aiplatform/
                     projects/906988347581/locations/us-central1/reasoningEngines/8598754324522205184
```

Not a service account — a distinct per-agent principal minted by Agent Identity. This is
what Phase 5 scopes per department so `SecurityAgent` physically cannot read the legal corpus.

## Auto-registered in Agent Registry — no manual step

`GET https://agentregistry.googleapis.com/v1/projects/attestor-505506/locations/us-central1/agents`

```
registry entries: 3
 - Workspace Agent   urn:agent:googleapis.com:locations:global:workspaceagent:workspaceagent--a2a
 - attestor-probe    urn:agent:projects-906988347581:...:reasoningEngines:37411432890892288
 - attestor-probe    urn:agent:projects-906988347581:...:reasoningEngines:8598754324522205184
```

Nothing registered these. Deploying to Agent Runtime catalogued them automatically, which
is exactly the "cataloged for cross-department use" requirement in Track 3. The `/registry`
page in Phase 6 reads this API rather than a mock.

Note `v1` and `v1alpha` both serve this; `v1beta1` returns HTTP 404.

## Cloud Trace shows the full span tree

Trace ID: **`36cdab764d4a49f1761835532bf3487d`**

```
invoke_workflow attestor_probe
invoke_agent attestor_probe
call_llm
generate_content gemini-3.5-flash
execute_tool get_review_count      <-- the tool call, independently corroborated
call_llm
generate_content gemini-3.5-flash
```

Requires `enable_tracing=True` on `AdkApp`. Without it there are no spans and this gate
cannot pass.
