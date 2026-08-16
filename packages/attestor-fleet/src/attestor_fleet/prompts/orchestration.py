"""Prompts for the Orchestrator's three judgement calls.

The Orchestrator does not drive the pipeline stage by stage -- that sequence is known in
advance and is encoded as a workflow (`ADR-0002`). What it does is decide, and these are
the three decisions:

1. **Plan.** Which pipeline suits this artifact: a full first-round review, or a
   follow-up round that must be checked against prior commitments.
2. **Retry.** Which failed questions failed *transiently* and are worth another attempt,
   and which failed for a reason that will simply fail again.
3. **Finalise.** Whether the run's shape warrants escalating beyond what
   `policy.requires_human` already forced, and whether to stop.

Each answers in a fixed line format so the parse is mechanical and a malformed reply is
detectable rather than silently mis-read. Every static prefix is a module-level literal,
so it is byte-stable by construction.
"""

from __future__ import annotations

from attestor_fleet.prompts.base import build_prompt, render_list

# --------------------------------------------------------------------------------------
# 1. Plan
# --------------------------------------------------------------------------------------

PLAN_STATIC = """\
You are the Orchestrator of a vendor security review fleet. You do not answer questions \
and you do not retrieve evidence; specialist agents do that. You decide how a submitted \
artifact should be processed.

Choose exactly one pipeline:
- full_review: a first-round questionnaire. Triage, draft against the department corpora, \
assemble.
- follow_up_round: a later round of a review that already has answered rounds on file. \
Same drafting, but every answer must additionally be checked against the commitments \
made in earlier rounds.

Reply with exactly four lines, in this order, no commentary:
PIPELINE|<full_review or follow_up_round>
CONSISTENCY|<yes or no>
RETRY_WAVES|<0, 1 or 2>
REASON|<one short sentence>

Guidance:
- Prior commitments on file are the deciding signal for follow_up_round, not the question \
count. A short questionnaire with no prior rounds is still a first round.
- CONSISTENCY must be yes whenever any prior commitment exists. Contradicting an earlier \
round fails the audit, and that risk outweighs the cost of the check.
- RETRY_WAVES is how many times a transient failure may be re-attempted. Use 1 unless the \
artifact is large enough that transient throttling is likely, in which case use 2."""


def plan_prompt(
    filename: str,
    question_count: int,
    prior_round_count: int,
    prior_commitment_count: int,
) -> str:
    """Ask for a run plan for one submitted artifact."""
    dynamic = (
        f"ARTIFACT: {filename}\n"
        f"QUESTIONS: {question_count}\n"
        f"PRIOR ROUNDS ON FILE: {prior_round_count}\n"
        f"PRIOR COMMITMENTS ON FILE: {prior_commitment_count}"
    )
    return build_prompt(PLAN_STATIC, dynamic)


# --------------------------------------------------------------------------------------
# 2. Retry
# --------------------------------------------------------------------------------------

RETRY_STATIC = """\
You decide which failed questions in a vendor security review are worth re-attempting.

A retry costs money and time, and a permanent failure retried is both wasted. Judge each \
failure by its error text.

Reply with one line per question, no commentary:
<index>|<RETRY or GIVE_UP>

Guidance:
- RETRY a failure that is transient: a timeout, a deadline exceeded, a 429 or 503, a \
connection reset, a rate limit, a transient internal error from a Google API.
- GIVE_UP on a failure that will recur: a permissions error, a missing datastore, an \
invalid request, a policy denial, a guardrail block. Re-running these produces the same \
result and spends the budget twice.
- When the error text is unclear, GIVE_UP. An unretried question is flagged for a human, \
which is safe; a retry loop on a permanent failure is not."""


def retry_prompt(failures: list[tuple[int, str, str]]) -> str:
    """Ask which failures are transient. `failures` is [(index, question, error), ...]."""
    lines = [
        f"{index}. QUESTION: {question}\n   ERROR: {error}" for index, question, error in failures
    ]
    return build_prompt(RETRY_STATIC, "\n".join(lines))


# --------------------------------------------------------------------------------------
# 3. Finalise
# --------------------------------------------------------------------------------------

FINALISE_STATIC = """\
You review the shape of a completed vendor security review run and decide whether it can \
be handed back as it stands.

The per-answer escalation rules have already run: low-confidence answers, unevidenced \
answers, quarantined answers and answers contradicting a prior commitment are already \
held for a human. You are deciding whether the run as a whole shows a pattern that \
warrants holding more than that.

Reply with exactly three lines, no commentary:
DECISION|<release or escalate_review>
WIDEN|<none or commitments or all_flagged>
REASON|<one short sentence>

Guidance:
- escalate_review when the run shows a systemic problem: a guardrail fired repeatedly, a \
contradiction against a prior commitment was found, or a large share of answers could not \
be evidenced.
- WIDEN=commitments holds every answer that touches a prior commitment, not only the ones \
already flagged. Choose it when any contradiction was detected, because one detected \
contradiction means the round is arguing with an earlier one.
- WIDEN=all_flagged holds every answer that carries no citation.
- WIDEN=none leaves the per-answer decisions as they are.
- Prefer release when the run looks ordinary. Escalating everything is not caution, it is \
handing the work back to the human this system exists to spare."""


def finalise_prompt(counters: dict[str, int], notes: list[str]) -> str:
    """Ask whether a completed run may be released. `counters` is rendered sorted."""
    rendered = "\n".join(f"- {key}: {counters[key]}" for key in sorted(counters))
    dynamic = f"RUN SHAPE:\n{rendered}"
    if notes:
        dynamic = f"{dynamic}\n\nNOTES:\n{render_list(notes)}"
    return build_prompt(FINALISE_STATIC, dynamic)
