# Attestor — submission write-up

**All Things Agentic Hackathon · Track 3: The Fortified Enterprise Fleet**

> An enterprise agent fleet that answers vendor security questionnaires from your own
> documents, cites every claim, refuses what it cannot support, and holds the rest for a
> person.

**Live:** https://attestor-web-elrhl52mkq-uc.a.run.app
**Repository:** this one. **Architecture:** [`docs/architecture.svg`](architecture.svg).
**Evidence:** [`docs/proof/`](proof/) — every number below traces to a file there.

---

## The problem

Sell software to a company larger than five people and they send you a vendor security
review before they will sign: 200–400 questions across SOC 2, ISO 27001 and CAIQ, plus a
DPA, a subprocessor list and a data-residency questionnaire. Someone spends 20–40 hours
digging through internal policy documents. Every answer needs evidence. Every answer has to
agree with what that same customer was told three weeks ago. Then round two arrives.

It is unglamorous, it is entirely real, and it blocks revenue.

---

## Features and functionality

**Work arrives without anyone starting it.** A customer emails a questionnaire to a watched
address. Gmail publishes a change notification to Pub/Sub, Eventarc pushes it to a Cloud Run
dispatcher, and an `InboxAgent` decides what the message *is* — a new review, a follow-up
round on an existing one, or not a security review at all. Nothing downstream of intake
learns that email exists: the attachment becomes a GCS URI and the fleet runs exactly as it
does for a browser upload. A questionnaire dropped into the web console takes the same path
from the second step onward.

**The work is partitioned by department and run in parallel.** A `TriageAgent` routes every
question to security, legal or engineering. Three partitions are published as separate
Pub/Sub messages, claimed independently under a 900-second lease, and drafted concurrently
by three Agent Runtime engines with three different identities.

**Every answer cites the document it came from.** Each department engine is bound by IAM to
its own Vertex AI Search datastore. Retrieval is query-expanded and section-reranked before
drafting; an answer with no supporting passage is not written, it is refused and flagged.

**A different agent identity checks the work.** The `VerifierAgent` runs on its own engine
with **no corpus tool at all** — it is handed only the passages the drafter cited and asked
whether they support the claim. It returns supported / partially supported / unsupported /
unknown. `assert_separation` refuses to record a verdict when the verifying identity equals
the drafting one, so the check cannot silently degrade into self-review.

**It remembers what it promised.** Commitments made in round one are written to Memory Bank
and loaded before any question in round two is drafted. A round-two answer that contradicts
round one is caught before it is written, not after it is sent.

**The customer's own spreadsheet is treated as untrusted input.** Model Armor screens every
question on the way in and every drafted answer on the way out. The dispatcher fails
**closed**: when Model Armor cannot be reached the work is refused, because a guardrail that
fails open is not a guardrail.

**A person is in the loop where it matters.** Anything the fleet will not stand behind is
held. The orchestrator makes three judgement calls — which pipeline to run, which weak
answers to retry, whether to release the round — and each is recorded with the reason and
with whether the model decided it or the cautious fallback branch did.

**The console is a conversation, not a dashboard.** The Review Thread is a *projection* over
the append-only audit trail: it writes nothing and calls no model. Asking it "who approved
Q47" or "what is outstanding" answers from the trail itself and shows the rows the answer
was read out of. On a surface whose whole purpose is being checkable, a model narrating
events it did not observe is not checkable.

**The deliverable is the customer's own file.** Answers are written back into their rows and
their columns, exported as XLSX and PDF, filed to Drive, and replied in-thread after a named
human approval.

### The run of record

> **150 questions · 136 cited (91%) · 79 checked by a separate agent identity · 63 held for
> a person · 13 minutes 26 seconds · 613 audit events.**

Verdict distribution from the verifier: **29 supported · 21 partially supported · 4
unsupported · 25 could not be checked**. The four unsupported went back to the drafting
agent. The 25 unchecked had no drafted claim to check — they are reported rather than folded
into the passes.

---

## Technologies used

| | |
|---|---|
| **Gemini 3.7 Flash** | Triage, inbox classification, orchestration, verification, consistency checks. `gemini-3.7-flash` in `attestor_platform.config` |
| **Gemini 3.5 Flash** | Drafting inside the seven Agent Runtime engines. Pinned in `services/runtime/runtime_app.py` because the brief names it; 3.6 and 3.7 are one-line swaps. Every Gemini 3.x model is served only from the `global` location — a regional endpoint returns a 404 that reads like an entitlement problem and is not one |
| **Google ADK 2.7** | `LlmAgent`, tools derived from function signatures and docstrings, callbacks for audit and guardrails |
| **Agent Runtime** | Seven `reasoningEngines`: orchestrator, security, legal, engineering, evidence, verifier, probe |
| **Agent Registry** | All seven published and discoverable, with department and framework metadata |
| **Agent Identity** | One identity per engine. The IAM boundary is conditioned on the `corpus/<dept>/` GCS prefix, and the refusal is measured rather than asserted |
| **Memory Bank** | Cross-round commitments, recalled before drafting |
| **Model Armor** | Two templates — a strict ingress template for prompt injection and jailbreak, an egress template for PII |
| **Vertex AI Search** | Three datastores, one per department corpus |
| **Cloud Run** | Three services: dispatcher, control plane, Next.js console |
| **Pub/Sub + Eventarc** | `attestor.work` with a push subscription, `attestor-gmail` for inbound mail, `attestor.deadletter` after five attempts |
| **Firestore** | Reviews, rounds, questions, answers, work claims, and the append-only hash-chained `audit_events` |
| **Cloud Trace** | The operational plane, emitted by the Google client libraries |
| **Cloud Storage** | Uploads, corpus, exports. Uploads go browser-to-GCS on a v4 signed URL and never transit a service |
| **Gmail API** | `users.watch` scoped to a single label, `messages.send` for the in-thread reply |
| **Next.js 15 / React 19** | The console. Server components, SSE, a design-token scale enforced by a build-time linter |

---

## Other data sources used

**The Kestrel Data corpus is synthetic and written for this project.** It is 47 documents —
an information security policy, a SOC 2 description, a DPA, subprocessor lists, an incident
response plan, architecture and reliability documents — for a fictional company called
Kestrel Data. It is in [`seed/corpus/`](../seed/corpus/) and generated by
[`seed/build_questionnaires.py`](../seed/build_questionnaires.py) and its three siblings.

Nothing here is a real company's confidential material, and nothing was scraped. The
questionnaires are synthetic too, in three variants: `clean` (312 questions), `followup`
(round two, deliberately overlapping), and `injected` (the clean set with prompt-injection
payloads planted in customer-supplied cells).

**Nine planted gaps.** The clean questionnaire asks nine questions the corpus deliberately
cannot answer. All nine are refused. A system that answers 100% of a questionnaire is not
better than one that answers 91% — it is worse, and there is no way to tell from the output
alone unless you plant the gaps yourself.

**Attestor never reaches the web to answer a question.** This is a test
([`tests/unit/test_no_web_answers.py`](../tests/unit/test_no_web_answers.py)), not a
convention, because of the failure mode: not a blank, but a fluent, well-cited answer
sourced from a competitor's trust page and returned under this company's name, with a
citation that makes it look *more* trustworthy than the honest refusal it replaced.
Research about the *customer* at intake stays allowed — the boundary is the destination, not
the tool.

---

## Findings and learnings

### 1. Nine times, a failure arrived disguised as an empty result

This is the single thread running through the whole project. Nine separate times, in nine
different layers, something went wrong and the system's own honest reporting turned the
error into a *true-sounding false statement*.

- A corpus search that raised was caught and returned `[]`, so "the search failed" became
  "no supporting evidence exists in the corpus".
- An expired Gmail history window returned no messages, so "we lost a week of mail" became
  "no email arrived".
- A verification that never ran rendered identically to a verification that passed.
- A partition that died at question 120 persisted none of its 221 completed answers.
- …and five more, each written up in [`PROGRESS.md`](../PROGRESS.md).

Every layer behaved correctly and the aggregate statement was false. That is the signature.
The defence that generalises is: **an empty result and a failure must never share a
representation**, and the code that decides which one it is must be the code that knows.

### 2. The ninth had a named mechanism, and finding it took a measurement rather than an argument

A verified 150-question run answered **24%** — three to four times worse than every prior
measurement. Two causes fit the symptom exactly and had opposite fixes: the fixture was
wrong, or retrieval had regressed.

The questionnaire was ruled out arithmetically — question ids are content hashes, so all 150
mapped onto rows of the existing fixture. Retrieval was ruled out by re-measuring: 30 of the
77 questions the run had filed as *no supporting evidence* were re-asked through both the
local pipeline and the deployed engines, and the deployed path cited **25 of 30** with 6.83
passages each. The corpus answered them.

The cause was in the engines' own logs: **188 occurrences of `Quota exceeded for quota
metric 'Session Event Append Requests' … per minute per region`**, plus 36 `Failed to create
session`. ADK appends an event to a managed session for every model turn and every tool
call. When that per-minute ceiling is hit the append fails *without failing the call*, the
tool-response part carrying the passages is lost, and `stream_query` returns a stream that
looks exactly like a corpus with nothing in it.

Minute by minute, the two series are the same series:

| minute (UTC) | engine quota errors | dispatcher "retrieved nothing" |
|---|---|---|
| 16:33 | 7 | 9 |
| **16:34** | **144** | **109** |
| 16:35 | 81 | 52 |
| 16:36 | 58 | 39 |

Verification is what pushed it over: it was on for the first time, and a verification is a
second engine invocation — a second session — per drafted answer.

Full diagnosis: [`docs/proof/the-24-percent.md`](proof/the-24-percent.md).

### 3. A backoff has to outlast the thing it is backing off from

The defence against empty retrievals already existed and it only recovered a third of them:
79 confirmed empty, 33 recovered. It retried three times with a 3-second base — 3s, then 6s,
giving up nine seconds after the first attempt. **The quota it was retrying against resets on
a minute boundary.** All three attempts landed inside the same exhausted minute, so
"confirmed empty" meant only that the quota was still exhausted nine seconds later.

Moving the base to 25 seconds spreads the attempts across about 75 seconds, so one of them
lands in a minute the quota has reset in. **24% → 91% on the next run.** The retry logic was
never wrong; its time constant was, and nothing in the code said what the constant was
supposed to outlast.

### 4. A filter's sensitivity depends on how much legitimate text shares its window with the attack

The same prompt-injection payload, planted in a corpus document, was caught when the
surrounding passage was short and missed when it was long. Model Armor scores a window, and
a paragraph of genuine policy prose around an injected instruction dilutes the signal that
triggers the filter. The fix was not a stricter template — it was screening at the passage
level rather than the document level, so the attack never shares a window with 3,000 words
of legitimate text.

This also means a guardrail's measured catch rate is a property of the *chunking*, not only
of the filter. Ours is reported with the chunk size it was measured at.

### 5. Nested sub-agents share one Agent Identity, which is why this is seven engines

The natural ADK shape is one root agent with department sub-agents. Deployed, that is **one**
`reasoningEngine` — and therefore one service account, one set of IAM bindings, and one
credential reaching every corpus. The separation would be a prompt asking the model to stay
in its lane.

Seven separately deployed engines cost more to build and are the entire security story: the
security engine reaching for the legal corpus gets a **403 from IAM**, not a refusal from a
model that could be talked out of it. The refusal is captured verbatim in
[`docs/proof/iam-runtime-denial.json`](proof/iam-runtime-denial.json), next to the same probe
reading its own prefix successfully in the same run — because a denial with no matching
success proves nothing about the boundary.

It is also why verification is a real control. Separation of duties needs two credentials; a
single engine checking its own work is a prompt, not a control.

### 6. Two observability planes, because one cannot answer both questions

Cloud Trace is sampled, best-effort, and may drop a span without anything being wrong. That
is correct for "what was slow at 3am" and disqualifying for "who decided this, on what
evidence, under which identity". `audit_events` is append-only, hash-chained, never sampled,
and written *before* the action it describes — so a crash leaves a record of the attempt
rather than a silence. The product renders the second plane, and the Review Thread is a
pure projection over it.

### 7. Writing rationale into the product is a failure mode with its own linter

Three times, design reasoning that belonged in an ADR ended up rendered on screen —
sentences addressed to whoever built the thing rather than to whoever is using it. It kept
coming back because nothing stopped it. `scripts/check-copy.mjs` now fails the build on
twelve phrases that only appear when a sentence is defending a choice, matched against
rendered text only: a five-state scanner strips comments first, so reasoning beside the code
is untouched. Twenty-nine sentences were removed. **If a sentence would be at home in an
ADR, it does not belong on the screen.**

---
