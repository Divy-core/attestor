# Shot list — ten captures, about twenty-five minutes

Everything measurable through the DOM is already measured. This is the half that needs a
browser and a person, and it has needed one since Phase 5.

**How to use this.** Each shot below gives the URL, what to have on screen before you start
recording, what to click, what should appear, and the file in `docs/proof/` that backs the
claim. Follow it top to bottom. Nothing here needs me, and nothing here needs to be re-run —
every one of these is already true.

**Before you start**

- Browser at **1920×1080**, one window, no other tabs visible, bookmarks bar hidden.
- Light theme for shots 1–6, then flip to dark once on shot 7 so the theme switch is seen
  rather than described.
- Have `seed/questionnaires/clean/acme-vendor-review-r1.xlsx` on the desktop.
- Web: `https://attestor-web-elrhl52mkq-uc.a.run.app`
- Control plane: `https://attestor-control-plane-elrhl52mkq-uc.a.run.app`

**One thing to say out loud, on shot 1.** The file never touches either service. The browser
asks for a signed URL and PUTs straight to Cloud Storage. It is visible in the network tab if
anyone asks, and it is the difference between a demo and an architecture.

---

## 0 · A customer emails, and a review starts with nobody involved

| | |
|---|---|
| **Go to** | Gmail, then `/` in a second window |
| **On screen first** | The Attestor console at `/`, with the conversation rail visible. Nothing selected. |
| **Do** | From a **different** email account, send a questionnaire to `divy.ds.x+attestor@gmail.com`. Then switch to the console and do not touch it. |
| **Should appear** | Within about fifteen seconds the workspace **opens itself** on the new review, and the fleet starts reporting into the thread. Nobody clicked anything in the browser. |
| **Backed by** | `inbound-loop.json` |

**Send the questionnaire from a real mail client, not a script.** Two sends today were
rejected for reasons that were both correct and both avoidable: mail from the watched
mailbox itself is refused as its own echo, and an attachment re-encoded by a third-party
tool arrived corrupt and dead-lettered after five attempts with
`Error -3 while decompressing data`. A forward from an ordinary Gmail account has worked
every time.

**Do not rely on Gmail's push being quick.** Measured on the same mailbox within one hour:
13 seconds once, 4 minutes 18 seconds another time — and the slow one was *not* a cold
watch, Gmail had pushed twice in the nineteen seconds before that mail was sent. Warming
the watch does not fix it. What fixes it is that the dispatcher also drains the mailbox
every fifteen seconds on its own timer, so the ceiling is fifteen seconds regardless of
what Gmail decides. That is worth saying out loud on camera: the system does not depend on
a webhook arriving promptly.

This is the strongest thirty seconds in the video. Say two things over it:

- The watch is scoped to **one Gmail label**. Nothing else in that mailbox ever produces a
  notification, and the mailbox owner controls what the label catches with an ordinary Gmail
  filter — revocable without touching the deployment.
- Attestor **refuses mail it sent itself**. That is not a detail; it is why replying in-thread
  cannot open a round per reply, forever.

If a message arrives that is not a security review, that is worth showing too. The classifier
writes its reasoning to the trail — for a one-word test message it recorded *"a brief
connectivity test asking 'Working?' with no vendor security review content"*, and declined to
start a review.

---

## 1 · The front door, and handing over the work

| | |
|---|---|
| **Go to** | `/` |
| **On screen first** | A heading, a composer, and nothing else. Let it sit for two seconds before you touch anything. |
| **Do** | Drag the .xlsx onto the composer. Type the customer name in the dialog. Click Start. |
| **Should appear** | Each step named as it happens — signing, uploading, creating, publishing — then the page navigates to the review and the stream is already open. |
| **Backed by** | `journey.json` |

Do not narrate over the navigation. The silence is the beat.

## 2 · The fleet working with nobody touching it

| | |
|---|---|
| **Go to** | stay where shot 1 left you, `/reviews/{id}` |
| **On screen first** | The thread, empty, with the stream indicator live. |
| **Do** | Nothing. Do not click for ninety seconds. |
| **Should appear** | TriageAgent, then three department agents with counters climbing in parallel, then the verifier, then the assembler holding what a person has to see. |
| **Backed by** | `demo-run.json` |

Point at the fact that nothing is being clicked. On any orchestrator decision, the
`decided_by` chip reads `model` or `fallback:<why>` — show a fallback if one is there.

## 3 · A second identity checking the first one's work

| | |
|---|---|
| **Go to** | `/reviews/rev-b6c7fd460d9a` |
| **On screen first** | Scroll to the **VerifierAgent** post. |
| **Do** | Expand the post, then expand **Separation of duties** inside it. |
| **Should appear** | *29 supported · 21 partially · 25 unknown · 4 unsupported*, and beneath it two different credentials: the verifying engine `…/reasoningEngines/1255723093024833536`, and the drafting identities `SecurityAgent` (30), `EngineeringAgent` (27), `LegalAgent` (22). |
| **Backed by** | `demo-run.json` |

**Point at the four unsupported.** That is the verifier reading a drafted claim against the
passages it cites and saying they do not hold it up. A verifier that never dissents is
decoration.

The `unknown` verdicts are it declining to decide, reported rather than folded into the
passes. A verdict is refused outright when the two identities are equal.

## 4 · Asking the record a question

| | |
|---|---|
| **Go to** | any review with answers |
| **On screen first** | The composer at the foot of the thread. |
| **Do** | Type `who approved Q47`. Then expand the reply. |
| **Should appear** | Your line, boxed. The reply, as prose. Expanded: the question text, its cell in the customer's file, the model that triaged it, the drafting agent, the verdict, and every audit row the answer was read out of. |
| **Backed by** | the audit trail itself |

The sentence that matters: **no model was called to produce that reply.** On a surface whose
purpose is being checkable, a model narrating events is not checkable.

## 5 · A gate that says no

| | |
|---|---|
| **Go to** | the same review |
| **On screen first** | The composer. |
| **Do** | Type `send the pack`. Read the confirmation. Press **Go ahead**. |
| **Should appear** | A confirmation naming the effect, with nothing written and nothing published. Then the refusal: *"did not arrive by email, so there is no thread to reply on."* |
| **Backed by** | the audit trail itself |

A gate that only ever says yes is not a gate.

## 6 · The deliverable, in the customer's own file

| | |
|---|---|
| **Go to** | `/reviews/{id}` → Export |
| **On screen first** | The export panel. |
| **Do** | Download the workbook and **open it in Excel**. Then open the PDF at any page. |
| **Should appear** | The answers in the customer's own rows, beside the customer's own columns. The `Release status` column. In the PDF, one answer with its passages, sections and relevance scores. |
| **Backed by** | `export-rev-673ce276597e.xlsx`, `export-rev-673ce276597e.pdf` |

Only an answer a named human approved reads as approved. A drafted one says it was drafted,
and lists what it cites.

## 7 · The boundary, refusing in both directions

| | |
|---|---|
| **Go to** | a terminal |
| **On screen first** | Clear terminal, large font. |
| **Do** | `PROJECT_ID=attestor-505506 uv run python tools/verify_iam_denial.py` |
| **Should appear** | The security engine reading `security/access-control-standard.txt` at **4,298 bytes**, then the *same* engine refused on the legal object with a verbatim `403` from `storage.mtls.googleapis.com`. |
| **Backed by** | `iam-runtime-denial.json` |

The pair is the beat. A denial with no matching success is indistinguishable from a broken
deployment. Mention that the probe is a deliberate bypass tool with our own interceptor out
of the way — that is what makes it a platform proof rather than our code refusing itself.

## 8 · It is running on Google Cloud

| | |
|---|---|
| **Go to** | Cloud Console, project `attestor-505506` |
| **On screen first** | Cloud Run service list. |
| **Do** | Show **Cloud Run** (three services, revisions, `min-instances=0`), then **Agent Runtime** (six engines), then **Model Armor** (the two templates), then **Cloud Trace** filtered to the review's window. |
| **Should appear** | In Trace: `/pubsub/push` → `invoke_workflow security_agent` → `invoke_agent` → `execute_tool search_security_corpus` → `call_llm`. |
| **Backed by** | `fleet-deployment.json`, `armor-smoke-output.txt`, `observability-planes.json` |

`min-instances=0` is worth one sentence: the system costs nothing while idle.

Then name the split. Trace answers *where did the latency go*. `audit_events` answers *why
did we answer yes to Q112*. Two planes, two consumers, two retentions.

## 9 · Three weeks later, and it remembers

| | |
|---|---|
| **Go to** | a terminal |
| **On screen first** | Clear terminal. |
| **Do** | `PROJECT_ID=attestor-505506 uv run python tools/verify_resume.py` |
| **Should appear** | The review's `created_at`, 22 days before the round-2 work. Round 1's answer fingerprint **byte-identical** before and after. `prior_commitments` loaded before any round-2 question was drafted. |
| **Backed by** | `resume-22-day.json` |

Two claims, and the second is the one that is easy to fake: it resumed, and it resumed *with
context*. The harness checks the count the handler actually recorded, not that the stage
returned successfully.

---

## What to leave out

The registry listing page, the fleet board, the raw audit table, and the connections page.
They are all real and all reachable, and none of them is worth thirty seconds against the
nine above.
