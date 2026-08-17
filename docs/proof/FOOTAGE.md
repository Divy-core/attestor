# Footage checklist

Every item the demo video needs, what to point the camera at, and the measured artefact
that backs it. The artefacts are the evidence; the footage is how a judge sees it in four
minutes.

**Why this file exists.** Reconstructing screen recordings on the last day is how demos get
faked — you re-run something, it behaves differently, and the pressure is to record the
version that looked right rather than the version that happened. Everything below is
already true and already recorded in `docs/proof/`, so a capture session is transcription
rather than performance.

**What this build cannot do for itself.** Screen recordings and Cloud Console screenshots
need a browser and a human. The console outputs and JSON artefacts below were produced by
the tools named; the *visual* capture of each is the outstanding half, and it is marked as
such rather than implied to be done.

---

## The nine captures

| # | Capture | Where | Backing artefact | Reproduce with |
|---|---|---|---|---|
| 1 | Registry with five agents and distinct identities | Console → Agent Registry, or `/registry` | `registry-listing.json` | `tools/verify_registry.py` |
| 2 | The 403, **both directions** | terminal | `iam-runtime-denial.json` | `tools/verify_iam_denial.py` |
| 3 | The span tree | Console → Cloud Trace | `observability-planes.json` | `tools/capture_traces.py` |
| 4 | The 22-day resume | terminal + Firestore | `resume-22-day.json` | `tools/verify_resume.py` |
| 5 | The live approval | control-plane URL + terminal | `drill-approval.json` | `tools/drill_approval.py` |
| 6 | Cloud Run dashboard | Console → Cloud Run | `deployed-review-312.json` | `infra/deploy.sh` |
| 7 | Agent Runtime dashboard | Console → Agent Runtime | `fleet-deployment.json` | `services/runtime/deploy_fleet.py` |
| 8 | Model Armor template | Console → Model Armor | `armor-smoke-output.txt`, `run-injected.json` | `tools/armor_smoke.py` |
| 9 | The `.run.app` URL responding | browser | — | `curl <control-plane>/health` |

---

## Notes per capture, in the order the video uses them

**1 — Registry.** Show all five, then show that each entry's `agent_id` URN names a
different `reasoningEngine`. Say the true sentence: the registry's *list* endpoint returns
`effective_identity` as null, so identity distinctness is read from the engine resource
(`spec.effectiveIdentity`) and from the live conditioned bucket bindings, not from this
page. Overclaiming here is unnecessary — the identities are real and provable one screen
over.

**2 — The 403.** The pair is the beat, not the denial. A denial with no matching success is
indistinguishable from a broken deployment, so show `security/access-control-standard.txt`
returning 4,298 bytes *first*, then the legal object returning
`403 GET https://storage.mtls.googleapis.com/…`. Mention that `probe_platform_boundary` is
a deliberate bypass tool and that the interceptor is not in the way — that is what makes it
a platform-layer proof rather than a demonstration of our own code refusing.

**3 — The span tree.** Cloud Trace, filtered to the review's window. The shape to point at:

```
/pubsub/push
invoke_workflow security_agent
  invoke_agent security_agent
    execute_tool search_security_corpus
    call_llm → generate_content gemini-3.7-flash
```

Then cut to the audit events for the same review and name the split out loud: Trace answers
*where did the latency go*, `audit_events` answers *why did we answer yes to Q112*. Two
planes, different consumers, different retention. The permission denial is written to the
compliance plane by design, not because the span was unavailable.

**4 — The 22-day resume.** Show the review's `created_at`, then the round-1 answer
fingerprint before and after. The claim is two claims: it **resumed** (round 1 untouched,
byte-identical fingerprint) and it resumed **with context** (`prior_commitments` loaded
before any round-2 question was drafted). The second is the one that is easy to fake, which
is why the harness checks the count the handler recorded rather than that the stage
succeeded.

**5 — The approval.** The pause is the point: no process is waiting, no HTTP request is
open, no timer is counting. The review sits in `awaiting_human` and would sit there for
three weeks. One POST, and the dispatcher — not the endpoint — applies the decision, which
is what makes a redelivered approval idempotent rather than usually-fine.

**6, 7 — The dashboards.** `min-instances=0` on both services is worth saying out loud: the
system costs nothing while idle, which is the honest version of "production shaped" for a
hackathon budget.

**8 — Model Armor.** Show the template and the floor setting at `Inspect and block`, not
log-only. Then the Q47 injection blocked, quarantined, and the run continuing on the other
questions. If there is time, the chunker: the injection filter caps at 512 tokens and the
payload sits at ~1,400, so `screen_long_text` is what catches it.

**9 — The URL.** Thirty seconds, unglamorous, and the thing a judge checks first.

---

## Numbers to say out loud

From `docs/proof/deployed-review-312.json` — the deployed figures — with Phase 3's local
run (`docs/proof/run-clean.json`) as the labelled comparison. If the two differ materially,
say both and say which one the video is showing. A demo that quotes the better number
without naming its provenance is the one thing this build has spent three sessions refusing
to do.
