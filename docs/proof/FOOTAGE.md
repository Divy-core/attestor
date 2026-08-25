# Footage checklist

> **For the capture session, use [`SHOTLIST.md`](SHOTLIST.md).** It is nine shots with the
> URL, what to have on screen, what to click, what should appear and which artefact backs
> each one — followable without me, in about twenty minutes. This file is the longer
> reference: every capture the project can offer, and the notes on what to say over each.


Every item the demo video needs, what to point the camera at, and the measured artefact
that backs it. The artefacts are the evidence; the footage is how a judge sees it in four
minutes.

**Why this file exists.** Reconstructing screen recordings on the last day is how demos get
faked — you re-run something, it behaves differently, and the pressure is to record the
version that looked right rather than the version that happened. Everything below is
already true and already recorded in `docs/proof/`, so a capture session is transcription
rather than performance.

**What this build cannot do for itself.** Screen recordings and Cloud Console screenshots need a
browser and a human. The console outputs and JSON artefacts below were produced by the tools
named; the *visual* capture of each is the outstanding half, and it is marked as such rather
than implied to be done.

**The one thing to say out loud on capture 0a.** The file does not transit either of our
services. `POST /uploads` mints a v4 signed URL and the browser PUTs straight to
`storage.googleapis.com` — visible in the network tab if anyone asks, and stated in the dialog
while it happens. That is worth twelve seconds because it is the difference between a demo and
an architecture.

---

## The captures

Phase 6.5 added three, and they go **first** in the video rather than being appended. The
opening beat is a founder handing over a 312-question spreadsheet, and until this phase that
beat had to cut to a terminal at 0:30 — which costs the "live, unedited demo" requirement in the
30% category and undercuts the 40% one, because a judge cannot see hand-holding-free operation
if the only way to hand work in is a CLI.

| # | Capture | Where | Backing artefact | Reproduce with |
|---|---|---|---|---|
| 0a | **Upload and start** — drag the .xlsx into **New review**, name the customer, click Start | `/` or `/reviews` | `journey.json` | `tools/verify_journey.py` |
| 0b | **The fleet working, hands off** — counters climbing, three department engines advancing in parallel, the orchestrator's `decided_by` | `/reviews/{id}` | `journey.json` | the same run, watched |
| 0c | **The deliverable** — download the completed workbook, open it, show the answers in the customer's own rows next to their own columns | `/reviews/{id}` → Export | `export-{id}.xlsx`, `export-{id}.pdf` | `tools/verify_journey.py` |
| 1 | Registry with five agents and distinct identities | Console → Agent Registry, or `/registry` | `registry-listing.json` | `tools/verify_registry.py` |
| 2 | The 403, **both directions** | terminal | `iam-runtime-denial.json` | `tools/verify_iam_denial.py` |
| 3 | The span tree | Console → Cloud Trace | `observability-planes.json` | `tools/capture_traces.py` |
| 4 | The 22-day resume | terminal + Firestore | `resume-22-day.json` | `tools/verify_resume.py` |
| 5 | The live approval | control-plane URL + terminal | `drill-approval.json` | `tools/drill_approval.py` |
| 6 | Cloud Run dashboard | Console → Cloud Run | `deployed-review-312.json` | `infra/deploy.sh` |
| 7 | Agent Runtime dashboard | Console → Agent Runtime | `fleet-deployment.json` | `services/runtime/deploy_fleet.py` |
| 8 | Model Armor template | Console → Model Armor | `armor-smoke-output.txt`, `run-injected.json` | `tools/armor_smoke.py` |
| 9 | The `.run.app` URL responding | browser | — | `curl <control-plane>/health` |
| 10 | **The verifier's verdict distribution** — a second identity checking the first's work | `/reviews/rev-ead968ab9f94` → the VerifierAgent post, expanded | `verified-run-150.json` | the run itself |
| 11 | **The chat front door** — drop a questionnaire, watch the fleet report into the thread | `/` | `verified-run-150.json` | drag the .xlsx onto the composer |
| 12 | **Asking the thread** — a question answered from the audit trail, expanded to the rows it was read from | `/reviews/{id}` | the trail itself | type it |
| 13 | **A command refusing** — `send the pack` on a review that never arrived by email | `/reviews/{id}` | the trail itself | type it |

---

## Notes per capture, in the order the video uses them

**0a — Upload and start.** Drag `seed/questionnaires/clean/acme-vendor-review-r1.xlsx` onto the
drop target. 312 questions, 20KB. The dialog names each step as it happens — signing, uploading
direct to Cloud Storage, creating, publishing `intake_document` to `attestor.work` — and then
navigates to the review page, where the stream is already open. Do not narrate over the
navigation; let the counters start moving on their own. That silence is the point of the beat.

**0b — Hands off.** The three progress bars are the fan-out. Point at the fact that nothing is
being clicked. The `decided_by` chip on each orchestrator decision is worth naming: it reads
`model` when the orchestrator's own judgement produced the call and `fallback:<why>` when a
deterministic path did, and showing the fallbacks is deliberate — a judgement layer that
silently degraded and still reported a decision would be the overclaim this build spends its
time avoiding.

**0c — The deliverable.** Download the workbook and **open it**. The whole beat is that the
answers are in the customer's own rows, beside the customer's own columns, in the customer's own
file — not a report to reconcile. Then show the `Release status` column and say the true
sentence: only an answer a named human approved reads as approved; a drafted answer says it was
drafted with citations and lists them. Then open the PDF at any page and show one answer with
its passages, sections and relevance scores.

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

---

## Revised after Phase 6 — the UI carries five of the nine

The console is now the place to point the camera for most of these, which is the point of
building it against the deployed backend rather than against mocks. Where a capture has both
a console view and a terminal artefact, the console view is the one the video uses and the
terminal artefact is what a judge browsing the repo checks it against.

| # | Now captured at | Note |
|---|---|---|
| 1 | `/registry` on the deployed web service | Renders the live Agent Registry read, the scope matrix, and the identity caveat **on screen**. Do not narrate "distinct identities per the registry" — the page deliberately does not say it, and neither should the voiceover. |
| 2 | `/traces/[runId]` → Audit trail, filtered to refusals | The 403 is an `audit_event`, so it is a row in the compliance plane rather than a terminal scroll. `tools/verify_iam_denial.py` remains the artefact. |
| 3 | `/traces/[runId]` → Both planes | Shows the Cloud Trace span tree **and** the sentence "our own code emits no custom OTel spans". Say that sentence out loud; it is worth more than the tree. |
| 4 | `/reviews/rev-acme-2026-q3` → the round timeline | The 22-day gap is drawn to scale with a `+22d` label, so the dormancy is visible rather than asserted. |
| 5 | `/reviews/[reviewId]` → Needs a human tab | Approve one and the receipt shows the `dedup_key`. That key is why a redelivered approval is a no-op — worth pausing on for two seconds. |
| 8 | `/traces/[runId]` → Guardrail blocks | `InjectionDiff` renders the payload with its `chunk_index`. The line to say: the filter caps at 512 tokens, this document was longer, and the index says which ~450-token window caught it. |
| 9 | The web service's own `.run.app` URL, in the address bar | Recorded in `PROGRESS.md`. The sidebar also shows the project, region and Cloud Run revision as monospace metadata, which is the rubric's "visible proof it runs on Google Cloud" answered without a claim. |

Captures 6 and 7 stay in the Cloud Console — a Cloud Run dashboard and an Agent Runtime
dashboard are not things a product UI should imitate, and imitating them would be the one
place this interface could be accused of dressing up someone else's evidence as its own.

**Still outstanding, and still needing a human with a browser:** every visual capture above.
Phase 6 makes them cheaper and does not make them done.


**10 — The verifier's verdict distribution.** This is the beat the project owed since Phase 6
and did not have until 24 August. Open `rev-ead968ab9f94` and expand the **VerifierAgent**
post. It reads:

> Checked 36 answers against the passages they cite — 10 supported · 11 partially · 15 could
> not be checked.

Then open **Separation of duties** inside it, which is the whole point of the beat. The
verifying identity is
`projects/906988347581/locations/us-central1/reasoningEngines/1255723093024833536`; the
drafting identities beside it are `SecurityAgent`, `LegalAgent` and `EngineeringAgent`. Two
different credentials, and the verdict is refused outright when they are equal.

Say the honest sentence about the denominator, because a judge will ask why 36 and not 150.
Of the 150 questions: 77 had no passages at all and are flagged rather than answered, 1 was
quarantined by Model Armor, and 36 are the engine-returned-passages-but-no-prose recovery
path, where there is no drafted claim to check. **Every answer that carried a draft was
checked.** The 15 `unknown` are the verifier declining to decide, and they are reported
rather than folded into the passes.

Worth one more sentence if there is room: `empty_retrievals_recovered: 33`. Thirty-three
retrievals came back empty, were retried rather than believed, and returned passages on the
second attempt. Without that defence they would have been filed as "no supporting evidence in
the corpus" — the eighth failure-impersonating-empty, doing its job in a live run.

**11 — The chat front door.** Start at `/` with no conversation open: a heading, a composer,
and nothing else. Drop the questionnaire on the composer. The dialog takes the customer name,
and the moment it publishes, the conversation appears in the rail and the fleet starts
reporting into the thread — TriageAgent first, then three department agents with their
counters rising, then the verifier, then the assembler holding what a person has to see. Do
not click anything while it runs.

**12 — Asking the thread.** Type `who approved Q47`, or `what is outstanding`. The reply is
composed from the audit trail with **no model call**, and expanding it shows the question
text, its cell in the customer's file, the model that triaged it, the drafting agent, the
verdict, and every audit row the answer was read out of. Say that part out loud: this is not
a model narrating a run it did not observe.

**13 — A command refusing.** Type `send the pack`. Nothing is written and nothing is
published; the confirmation names the effect and says it cannot be recalled. Press *Go ahead*
and the refusal is the interesting half:

> review 'rev-...' did not arrive by email, so there is no thread to reply on.

A gate that only ever says yes is not a gate.
