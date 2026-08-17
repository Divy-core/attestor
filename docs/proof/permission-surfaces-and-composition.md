# Two questions the brief asked, answered by measurement

Both answers narrow a claim rather than support one. Recorded here in full because the
narrowed claim is the true one and a reviewer will check.

---

## B. Retrieval has two permission surfaces. Only one is scoped.

Retrieval touches the platform twice: it queries a **Discovery Engine datastore** for
candidates, then reads the **GCS objects** those candidates point at, because section
reranking (ADR-0003) splits the document's own text. The question was whether both are
scoped per agent identity.

### The GCS surface — scoped ✅

Three conditioned bindings, live on the bucket (`docs/proof/iam-denial.txt`), each engine
limited to its own prefix by `resource.name.startsWith(...)`. Verified against the runtime
in `docs/proof/iam-runtime-denial.json`.

### The datastore surface — not scoped, and not scopable

Two measurements settle it.

**1. No agent identity has any Discovery Engine permission at all.** Agent Identity
carries one automatic project-level grant:

```
roles/aiplatform.agentDefaultAccess
  principalSet://agents.global.proj-906988347581.system.id.goog/
    attribute.platformContainer/aiplatform/projects/
```

That role has **19 permissions and not one is `discoveryengine.*`**:

```
aiplatform.endpoints.predict          cloudapiregistry.mcpTools.list
cloudapiregistry.locations.get        cloudtrace.traces.patch
cloudapiregistry.locations.list       logging.logEntries.create
cloudapiregistry.mcpServers.get       logging.logEntries.route
cloudapiregistry.mcpServers.list      monitoring.* (6)
cloudapiregistry.mcpTools.get         resourcemanager.projects.get
                                      serviceusage.services.use
                                      telemetry.traces.write
```

So the datastore surface is not an *unscoped* surface — it is an **ungranted** one. A
deployed engine cannot currently query any datastore, including its own.

**2. Per-datastore IAM is not expressible.** From the Discovery Engine v1 discovery
document, the only resources carrying `setIamPolicy` / `getIamPolicy` are:

```
projects.locations.collections.engines.getIamPolicy
projects.locations.collections.engines.setIamPolicy
```

`dataStores` has neither. Attestor queries **datastores** directly — standard edition, no
engine/app serving config, which ADR-0003 records as a deliberate choice — so there is no
resource whose policy could carry a per-department binding. Granting datastore access to
an engine means a project-level role, and a project-level role reaches all three
datastores.

### What this does to the claim

| Surface | Policy layer (`before_tool`) | Platform layer (IAM) |
|---|---|---|
| GCS objects (`corpus/<dept>/**`) | ✅ refuses, with an audit event | ✅ conditioned binding, 403 proven |
| Discovery Engine datastore query | ✅ refuses, with an audit event | ❌ not expressible |

**The narrowed claim, which is what goes in the write-up:** the object surface is defended
in depth — twice, independently, and both proven. The datastore surface is defended by the
policy interceptor and by the fact that each department agent's search tool is bound to one
department at *build* time, so the binding is pickled into the deployed artifact and cannot
be argued with by a prompt. That is a code-level and deploy-level control, not an IAM one,
and it is not defence in depth.

Adopting Enterprise-edition engines would make the datastore surface bindable. ADR-0003
declined Enterprise edition for retrieval-quality reasons; this is a second, independent
argument for revisiting it, and it is recorded rather than acted on.

---

## C. How the pipeline composes across five engines

**It does not. The five engines are deployed and identity-scoped, but they are not on the
drafting path.**

`services/dispatcher/src/dispatcher/runner.py::PipelineFleetRunner` runs the Phase 3
`ReviewPipeline` **in this process**. Nothing in the dispatcher or the fleet package calls
`async_stream_query`, `stream_query`, or any `reasoningEngines/*` resource:

```
$ grep -rn "async_stream_query|stream_query|reasoningEngines" services/dispatcher packages/attestor-fleet
(no matches)
```

### The three answers

**Is the pipeline still a workflow agent inside the orchestrator engine calling the others
remotely, or has it been restructured?** Neither. It is unchanged from Phase 3 — a Python
workflow in `pipeline.py` with `draft_many` fanning out over a `ThreadPoolExecutor`. The
ADR-0002 argument (deterministic workflow over LLM routing) still holds exactly as written,
because the execution path it describes is the one still running.

**Is the parallel fan-out still parallel across engines?** The question does not apply
yet: the fan-out is in-process, so it is parallel in the same way and for the same reason
it was in Phase 3. Once drafting routes to the deployed engines, three engines drafting
concurrently is a *different* kind of parallelism — network-bound rather than
thread-bound — and it would need re-measuring on its own terms.

**Concurrency versus 7.84.** Unchanged and expected to remain unchanged, because nothing
about how drafting executes has changed. Measuring it again on the deployed dispatcher
measures the same in-process fan-out from a different host.

### Why this matters, stated rather than glossed

"The fleet is deployed" and "the fleet is what runs the review" are different claims, and
only the first is currently true. The `FleetRunner` Protocol was built in Phase 4 as the
seam for exactly this swap — `PipelineFleetRunner` is one implementation, and an
`AgentRuntimeFleetRunner` that calls the deployed engines is the other. The seam exists;
the second implementation does not.

The consequence for the demo is precise and worth being precise about: the deployed
engines are real, registered, identity-scoped, and independently provable (the runtime 403
comes from a deployed engine executing a tool). The 312-question review runs the same fleet
code, driven by real Pub/Sub messages, in the dispatcher process. Both statements are true.
Saying "312 questions ran on Agent Runtime" would not be.
