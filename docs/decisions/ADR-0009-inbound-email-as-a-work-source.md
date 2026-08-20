# ADR-0009 — Inbound email is a work source, not a second pipeline

**Status:** Accepted (Phase 7, 20 August 2026)
**Amends:** the frozen `WorkEnvelope` contract, previously amended by
[ADR-0005](ADR-0005-work-partitioning-and-the-dedup-key.md). Re-frozen at this revision.
**Supersedes:** nothing.

## Context

Everything Attestor does after a questionnaire lands has been autonomous since Phase 4. A
review advances because messages are delivered, not because anyone is watching. But the
first message was always published by a person: `tools/run_review.py` from a terminal in
Phases 3–6, and from Phase 6.5 a browser form that uploads a spreadsheet.

The hackathon brief asks for agents that *"take a goal, make a plan, and actually carry it
out … while you do something else"*, and Track 3 for *"long-running, asynchronous
background execution"*. Against that, "a human uploads a file and then everything is
automatic" is a materially weaker claim than it looks, because the interesting part of the
job — noticing that a questionnaire has arrived and deciding what it is — was still the
human's.

It is also not how the work actually arrives. A prospect's procurement team emails a
questionnaire to `security@` or `trust@`. It sits in a shared inbox. Three weeks later
they reply on the same thread with follow-ups. Nobody logs into a dashboard to check
whether their questionnaire is done.

## Decision

**Gmail becomes a source of `WorkEnvelope`s, through the transport that already exists.**

```
customer emails the watched mailbox
  → Gmail users.watch publishes to Pub/Sub
  → push subscription → dispatcher POST /gmail/push
  → WorkEnvelope(kind=inbox_message) on the existing work topic
  → dispatcher → InboxAgent classifies → review created → the fleet runs unchanged
```

Concretely, three things:

### 1. Two new `WorkKind` values, and no other change to the envelope

* `inbox_message` — one email from the watched mailbox, carrying **ids only**
  (`gmail_message_id`, `gmail_thread_id`, `history_id`).
* `deliver_pack` — send the completed pack back to the customer, carrying `approved_by`.

No existing field changes. A producer on an older revision is unaffected; a consumer on an
older revision rejects the new kinds with `ContractViolation` at the edge, which is the
failure mode a frozen protocol is meant to have. That is why this is an amendment and not
a new contract.

### 2. `inbox_message` carries no email content

The payload is deliberately three ids. The body of an inbound email is attacker-controlled
and unbounded; putting it on the bus would mean an untrusted payload replayed verbatim on
every redelivery, and a Pub/Sub message that can exceed the 10MB limit because somebody
pasted a spreadsheet inline. The handler fetches the message from Gmail, which is also the
only way a redelivery observes the message as it is *now* rather than as it was when the
notification fired.

### 3. The dedup key is derived from a synthetic review id

An inbound email has no review yet, and `make_dedup_key` needs a `review_id`. So the
envelope carries `review_id = "inbox-{gmail message id}"` until a real review exists.

This is not a workaround; it is the correct key. Gmail redelivers, Pub/Sub redelivers, and
`history.list` returns a message id twice across overlapping windows — three independent
sources of duplication over a boundary we do not control. Deriving the key from the Gmail
message id makes every one of them produce the same key, so the second arrival is refused
by the same `WorkClaimRepository` that protects every other stage. The synthetic id is
replaced by the real `review_id` the moment the handler creates one, and both appear in the
audit trail: a `stage_completed` event under the synthetic id, and a
`review_started_by_email` event under the real one.

## Alternatives considered

**Poll the mailbox on a schedule.** Simplest, and wrong for the claim being made. A poll
loop is a thing that runs constantly and mostly finds nothing; the brief's "asynchronous
background execution" is about work that starts when something happens. It would also add
latency proportional to the poll interval to the one moment a judge is watching.

**A separate ingestion service.** More isolated, and it would have meant a second
deployment, a second dead-letter path, a second audit surface, and a second place for the
claim logic to drift out of step with the first. The dispatcher already consumes Pub/Sub
through Eventarc; an email is new *work*, not a new *pipeline*.

**Fold the Gmail notification into `POST /pubsub/push`.** Rejected. Both are Pub/Sub push
deliveries, but they carry different contracts: `/pubsub/push` receives a `WorkEnvelope` we
published and therefore control, and `parse_push` treats a shape error as permanent because
it genuinely is. A Gmail notification is Google's shape, carries no correlation ids, and its
characteristic failure is an expired history window — recoverable, and with nothing to
dead-letter against. One endpoint would have to guess which contract it was looking at.

**Classify in the push endpoint.** Rejected. It would put a model call, a GCS write, and a
review creation inside an HTTP handler that Gmail will not call again. The push endpoint
resolves a history delta and publishes; every judgement happens under the claim, the lease,
and the dead-letter path, like every other stage.

## Consequences

### What gets better

* A review starts with no human action, and a reply on a known thread wakes a dormant
  review, loads its commitments from Memory Bank, and opens round two. The cross-round
  consistency guarantee proved in Phase 5 now has an autonomous trigger rather than a
  script.
* The work lands where compliance owners already are. A Gmail label says what the fleet did
  with a thread without opening Attestor at all.
* Nothing downstream of intake changed. `intake_document` still takes a GCS URI.

### What gets worse, and what it costs

* **A new credit-burn surface, larger than the last one.** Anyone who learns an email
  address can now cause work. The `max_active_reviews` ceiling is therefore enforced in the
  handler as well as in the control plane, the question ceiling still applies at intake, and
  a refusal is recorded and labelled rather than silent. This is a bound, not an
  authorisation model, and it is described as one.
* **A credential with real reach.** A Gmail refresh token can read and send mail as that
  account. It lives in Secret Manager, is granted once by hand, and is scoped to
  `gmail.readonly`, `gmail.send`, `gmail.modify`, and `drive.file` — the last of which can
  only see files Attestor itself created.
* **A watch that expires every seven days.** Gmail does not renew it and does not warn. A
  lapsed watch looks exactly like a quiet mailbox, so the expiry is recorded next to the
  history cursor and printed by `tools/gmail_watch.py`. For a demo window this is a tool; a
  production deployment would put it on a schedule, and saying so is more honest than
  calling a cron job a design.
* **An unrecoverable gap is possible.** If the history window expires before the cursor
  advances, messages in it cannot be listed. That case is detected (`restarted=True`) and
  written to the audit trail as `inbox_history_gap` rather than reported as an empty
  mailbox — the ninth instance of the failure-impersonating-empty family this project has
  had to handle, and the first one anticipated before it happened rather than after.

### The irreversible direction is gated separately

`deliver_pack` exists as its own kind, rather than as a branch of `close_round`, because
sending an email leaves the system and cannot be taken back. Its payload requires
`approved_by`, so the protocol itself refuses to carry an unapproved send. That is a
structural gate rather than a sentence in a policy document, which is the same reasoning
that put the citation requirement in `Answer`'s validator rather than in a prompt.
