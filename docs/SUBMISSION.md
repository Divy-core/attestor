# Attestor — submission

**All Things Agentic Hackathon · The Fortified Enterprise Fleet**

**Elevator pitch.** An enterprise agent fleet that answers vendor security questionnaires
from your own documents, cites every claim, refuses what it cannot support, and holds the
rest for a person.

**Live:** https://attestor-web-elrhl52mkq-uc.a.run.app
**Repository:** https://github.com/Divy-core/attestor
**Architecture:** [`docs/architecture.svg`](architecture.svg)

---

## What it does

Sell software to any company larger than five people and you get sent a vendor security
review: a 200–400 question spreadsheet, a DPA, a subprocessor list, a data-residency
questionnaire. It takes 20–40 hours of archaeology through internal policy documents. Every
answer needs evidence. Every answer has to be consistent with what you told that same
customer three weeks ago. Then round two arrives.

Attestor takes the questionnaire — from an email nobody read, or from a file dropped into a
browser — and answers it.

A customer emails the questionnaire to a watched mailbox. An inbox agent classifies it and
opens a review. Triage splits the questions across departments. Three drafting agents, each
a separately deployed engine with its own identity and its own corpus, answer in parallel
with citations. A verifier — a different agent identity that did not write the answers and
cannot reach the corpus — reads each claim against the passages it cites and returns the
ones the passages do not carry. Whatever the system will not stand behind is held for a
person. Everything else is assembled into the customer's own workbook plus a PDF evidence
pack, filed to Drive, and replied in-thread.

**Run of record: 150 questions · 136 cited (91%) · 79 checked by a separate agent identity ·
63 held for a person · 13 minutes 26 seconds · 613 audit events.** Inbound from a real
email: 51 seconds end to end, nobody involved.

What it will not do is the point. It will not answer a question your documents do not
support. There is a test that keeps it off the web. Nine planted questions with no
supporting document anywhere in the corpus were refused nine times out of nine — a fluent,
well-cited answer sourced from somewhere other than your own policies, returned under your
company's name, is a worse outcome than a blank.

---

## Features

- **Starts itself.** Gmail `users.watch`, scoped to a single label, publishes to Pub/Sub;
  Eventarc pushes into Cloud Run. No console, no upload, no human.
- **Seven agents, seven identities.** Orchestrator, three department drafters, evidence,
  verifier, and a boundary probe — each a separate Agent Runtime engine with its own Agent
  Identity, published to the Agent Registry. Nested sub-agents share one identity, which
  would mean one service account holding every department's permissions.
- **The corpus boundary is a credential, not an instruction.** The security engine asked for
  the legal corpus does not decline — it is refused by a conditioned IAM binding, before any
  model runs, with the 403 captured.
- **Separation of duties.** The verifier cannot be the drafter; a verdict is refused when the
  verifying identity equals the drafting one.
- **Every claim cites its source** — document, section, and a relevance score computed from
  embedding cosine similarity, never asked of a model. Confidence is composed
  deterministically from citation count, relevance, hedging and contradiction signals.
- **Model Armor in both directions.** Every question screened before an agent reads it, every
  drafted answer before it leaves. Tool output is screened in 200-token windows, because a
  filter's sensitivity depends on how much legitimate text shares its window with the attack
  — the same payload was denied alone and allowed inside 400 characters of ordinary prose.
- **Memory across weeks.** Commitments go to Vertex AI Memory Bank when a round closes and
  are loaded before any later round drafts. A 37-day-dormant review woke on a customer reply,
  matched a commitment by meaning where ID matching found none, and redrafted the
  contradicting answer under the constraint.
- **Durable async.** One Pub/Sub message per stage, claimed under a 900s lease against a 600s
  ack deadline, so a redelivery finds a live claim rather than drafting 123 questions twice.
  Answers persist as they complete, so a redelivered partition resumes.
- **Two observability planes, deliberately.** Cloud Trace for what was slow; an append-only
  Firestore audit log for who decided what, on which evidence, under which identity. A
  sampled trace store cannot answer a compliance question.
- **Human in the loop, then out of it.** One approval releases a round, or auto-send closes
  it without you — and the trail records that the decision was automated and who authorised
  the automation.

---

## Technologies used

Gemini 3.7 Flash (drafting, intake, verification) and Gemini 3.5 Flash-Lite (high-volume
triage) on Vertex AI · Google ADK 2.7 · Vertex AI Agent Runtime — seven `reasoningEngine`
deployments · Agent Identity and Agent Registry · Vertex AI Memory Bank · Model Armor
(project floor setting, `inspectAndBlock`) · Vertex AI Search — one datastore per department
· Cloud Run (three services) · Pub/Sub + Eventarc · Cloud Tasks · Firestore (append-only
audit) · Cloud Storage · Secret Manager · Cloud Trace · Gmail API and Drive API · Python
3.12, FastAPI, Next.js 15, TypeScript strict.

Agent Gateway was evaluated and not adopted — it is an L7 proxy whose distinguishing
capability is reaching private VPC endpoints, and Attestor has none. The reasoning and the
condition that would reverse it are in [`docs/decisions/ADR-0006-agent-gateway.md`](decisions/ADR-0006-agent-gateway.md).

Total spend: under $20 of the $150 credit.

---

## Other data sources

A synthetic corpus for a fictional company, Kestrel Data, Inc. — 46 policy documents across
security, legal and engineering, internally consistent on named auditors, certificate
numbers, dated incidents and control IDs. Three generated questionnaires: clean, one carrying
a hidden prompt injection, and a round-two follow-up. Six deliberate evidence gaps,
grep-verified at zero corpus hits, so that "we have no document on this" is a claim the
system can be tested on. No real customer data. No web retrieval.

---

## What I learned

Nine times, something failed and returned an empty result instead of an error — and every one
became a confident false statement. Discovery Engine returning `[]` under a 429 became "the
corpus has no answer." A commitment read that caught every exception became "this customer
has no prior commitments," silently disabling the consistency check for a whole round. An
engine that returned fifteen passages at 0.744 relevance and no prose became "no supporting
evidence was found in the corpus."

The largest was in Google's own platform. A completed run reported 172 of 312 questions
unsupported. Queried directly, the same corpus returned passages for five of six of them at
0.950 top relevance. The deployed search was returning empty result sets *successfully* under
load — so the retry never fired, because a call that succeeds with nothing in it is not a
call that failed. Retrying empties before believing them moved citations from 43% to 91%. The
mechanism turned out to be a per-minute quota, correlated minute-for-minute, and the first
fix made it worse: the retry backoff was shorter than the quota's reset window, so all three
attempts landed inside the same exhausted minute. A backoff has to outlast the thing it is
backing off from.

A guardrail's sensitivity is a function of window size. The same injection was denied alone
and allowed when embedded in ~400 characters of legitimate prose. Screening concatenated
evidence in one call is not a weaker version of the same defence; it is a different and much
worse one.

A fix that is written, reviewed by eye, and never called is indistinguishable from no fix.
The recursive batch-split that handles Model Armor blocking our own prompts existed for a
full phase before anyone noticed `triage()` never called it. Reading call sites, not function
bodies, is what found it.

And the project's own guardrail fired on its own prompts. A batch of 40 diverse security
questions — break-glass access, secrets in repositories, national security requests — reads
collectively as an injection. Measured boundary: batches of 5, 10, 15, 20, 30 pass; 40
blocks.

Every number above traces to a file in [`docs/proof/`](proof/). [`PROGRESS.md`](../PROGRESS.md)
records eleven phases, what was built, how it was verified, and what did not work.
