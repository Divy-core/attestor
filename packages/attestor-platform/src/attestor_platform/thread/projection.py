"""The audit trail, read back as a conversation between the agents that wrote it.

## Why this is a projection and not a new collection

Nothing in this module records anything. Every post is derived from ``audit_events``, the
``answers`` collection and the review's own rows -- all of which already existed and all of
which were written for compliance reasons rather than for this view. That is the property
worth having: the thread cannot drift from the audit trail, because it *is* the audit
trail, and a post claiming an agent did something is a post that would vanish if the event
behind it were removed. There is no separate activity feed to keep in step.

The cost is that the trail was not designed to be read as prose, so the composition work
lives here. That is the right side of the trade -- a second write path for a UI is a second
thing that can be wrong about what happened.

## Aggregation, and the rule about counts

Twelve hundred events become roughly a dozen posts. That is only honest if the figures
survive the compression, so two rules hold throughout:

* **Counts come from the answers collection, never from event arithmetic.** An audit write
  is non-fatal by contract (``append_safe``), so a dropped write under-counts. "82
  answered" is a length of a filtered list of answers, which is the same number the grid
  shows.
* **Narrative comes from events.** What the fleet *did* -- routed, retrieved, refused,
  returned work to the drafter -- is only in the trail, and where the trail is silent the
  post is silent too. There is no post for a stage that did not report.

## What is deliberately not here

No model call. A thread that asks a model to describe what happened produces plausible
sentences about a run it did not observe, and the one thing this surface is for is being
checkable. Every sentence below is a template over counted facts.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from attestor_core.domain import Answer, Question, Review, Round
from attestor_core.domain.enums import AnswerStatus, Department, ReviewState
from attestor_platform.thread.model import (
    SAMPLE_CEILING,
    Action,
    Detail,
    Post,
    Progress,
    Row,
    Thread,
    kept,
)

#: States in which the fleet is still working. Used to decide whether a drafting post
#: renders as a live counter or as a finished figure -- a spinner on a review that
#: finished three weeks ago is a claim about the present tense that is not true.
_IN_FLIGHT = frozenset(
    {
        ReviewState.INTAKE,
        ReviewState.TRIAGING,
        ReviewState.DRAFTING,
        ReviewState.AWAITING_EVIDENCE,
        ReviewState.ASSEMBLING,
    }
)

_DEPARTMENT_AGENT: dict[str, str] = {
    "security": "SecurityAgent",
    "legal": "LegalAgent",
    "engineering": "EngineeringAgent",
    "unassigned": "EvidenceAgent",
}

_DEPARTMENT_ORDER = ("security", "legal", "engineering", "unassigned")

#: What each export format is called in a sentence. Two exports of the same round five
#: seconds apart are two different files, and a summary that reads identically for both
#: looks like the thread repeating itself.
_EXPORT_NAME = {"xlsx": "Workbook", "pdf": "Evidence pack"}


# ---------------------------------------------------------------------------------
# Small readers over the raw event dicts
# ---------------------------------------------------------------------------------


def _at(event: dict[str, Any]) -> str:
    return str(event.get("occurred_at") or event.get("recorded_at") or "")


def _detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    return detail if isinstance(detail, dict) else {}


def _actor(event: dict[str, Any], fallback: str) -> str:
    """Who wrote this event, in the name a person would use for them.

    The remote verifier writes its own **engine resource name** as the actor -- a
    seventy-character `projects/.../reasoningEngines/1255723093024833536` -- and that is
    exactly right for the audit trail, where the question is which credential did the work.
    It is wrong as a byline in a 768px column, where it pushes the sentence off the line and
    tells a reader nothing they can hold onto.

    So the byline is the role and the resource name stays in the expansion, under
    "Separation of duties", where it is the evidence rather than the label. Nothing is lost:
    the two names are shown together, one click apart, and the trail itself is untouched.
    """
    actor = str(event.get("actor") or "")
    if not actor:
        return fallback
    if actor.startswith("projects/") and "/reasoningEngines/" in actor:
        return fallback
    return actor


def _question_id(event: dict[str, Any]) -> str:
    return str(event.get("question_id") or "")


def _by_kind(events: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get("kind", ""))].append(event)
    return grouped


def _plural(count: int, one: str, many: str | None = None) -> str:
    return one if count == 1 else (many or f"{one}s")


def _humanise(value: str) -> str:
    return value.replace("_", " ")


def _sample(rows: tuple[Row, ...], total: int, noun: str) -> tuple[tuple[Row, ...], str]:
    """The rows shown, and a note naming what was left out. Never a silent truncation."""
    if total <= len(rows):
        return rows, ""
    remaining = total - len(rows)
    return rows, f"{remaining} further {_plural(remaining, noun)} not listed here."


def _pairs(detail: dict[str, Any]) -> tuple[Row, ...]:
    """Every field of an event's detail, rendered structurally.

    Used where the shape varies by kind and guessing at fields would drop whatever the
    stage actually recorded. Lists and dicts are stringified rather than skipped: an
    auditor expanding a block wants what was written, not a subset somebody chose.
    """
    return tuple(
        Row(_humanise(str(key)).capitalize(), _truncate(_stringify(value)), mono=_is_machine(value))
        for key, value in detail.items()
        if value not in (None, "", [], {})
    )


def _stringify(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={_stringify(v)}" for k, v in value.items())
    return str(value)


def _is_machine(value: Any) -> bool:
    text = _stringify(value)
    return bool(re.fullmatch(r"[\w\-.:/@]+", text)) and not text.isalpha()


def _truncate(text: str, ceiling: int = 200) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= ceiling else text[: ceiling - 1].rstrip() + "…"


def _stages(grouped: dict[str, list[dict[str, Any]]], stage: str) -> list[dict[str, Any]]:
    """Every `stage_completed` row for one stage, oldest first.

    The dispatcher writes one of these per stage attempt with the stage's own telemetry
    attached -- wall seconds, achieved concurrency, remote calls, what was deferred. That
    is the most useful expansion material in the whole trail and it was going unread.
    """
    return [
        event
        for event in grouped.get("stage_completed", [])
        if _detail(event).get("stage") == stage
    ]


def _attempt_line(run: dict[str, Any]) -> str:
    """One drafting attempt, as the figures the dispatcher recorded for it."""
    parts = [
        f"{run.get('drafted_this_attempt', 0)} drafted",
        f"{run.get('wall_seconds', 0)}s wall",
        f"{run.get('remote_calls', 0)} engine calls",
        f"{run.get('achieved_concurrency', 0)} concurrent",
    ]
    carried = run.get("resumed_from_previous_attempt")
    if carried:
        parts.append(f"{carried} carried in")
    deferred = run.get("deferred_to_next_attempt")
    if deferred:
        parts.append(f"{deferred} deferred")
    return " · ".join(parts)


# ---------------------------------------------------------------------------------
# Question labels
# ---------------------------------------------------------------------------------


def question_labels(questions: Sequence[Question]) -> dict[str, str]:
    """A short human handle for each question: ``Q1`` .. ``Q312``, by position in the round.

    ## Why position, and not the id, and not the spreadsheet row

    A content-derived id is the right *key* and the wrong *label*. Nobody asks "why did we
    answer no to ``069f2677425aef30``"; they ask about Q112.

    The spreadsheet row was tried first and is worse than it looks. The 112th question of
    this questionnaire sits on row 301, so a person typing "Q112" got an answer opening
    "row 301 is held for a person" -- correct, checkable, and disorienting, because the two
    numbers name the same thing and neither of them is the one the reader used. Row numbers
    are an artefact of how the customer laid out their file, including its header rows and
    its section breaks; the position in the round is what both sides of the conversation
    actually count.

    The source reference is not lost. It is in the expansion, under "In the customer's
    file", with the sheet, the row and the cell -- which is where it belongs, because it is
    how to find the question in *their* document rather than how to name it in ours.
    """
    return {question.question_id: f"Q{index}" for index, question in enumerate(questions, start=1)}


# ---------------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------------


def build_thread(
    *,
    review: Review,
    rounds: Sequence[Round],
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
    artifacts: Sequence[dict[str, Any]] = (),
    truncated: bool = False,
) -> Thread:
    """Compose one review's thread. Pure: no I/O, no clock, no model.

    ``events`` may arrive in any order -- ``for_review`` applies none, and Firestore
    returns auto-id documents in id order, which is arbitrary -- so they are sorted here
    rather than assumed sorted. That is the defect that would otherwise appear only on a
    review large enough for Firestore to page.
    """
    ordered = sorted(events, key=_at)
    grouped = _by_kind(ordered)
    labels = question_labels(questions)
    live = review.state in _IN_FLIGHT

    posts: list[Post] = []
    posts.extend(_arrival(review, rounds, grouped, questions))
    posts.extend(_judgement(grouped))
    posts.extend(_triage(grouped, questions, live))
    posts.extend(_drafting(grouped, questions, answers, live))
    posts.extend(_armor(grouped, labels))
    posts.extend(_denials(grouped))
    posts.extend(_verification(grouped, labels))
    posts.extend(_consistency(grouped, labels))
    posts.extend(_assembly(grouped, answers, labels, review.customer))
    posts.extend(_decisions(grouped, labels))
    posts.extend(_conversation(grouped))
    posts.extend(_resumed(grouped))
    posts.extend(_delivery(grouped, artifacts))
    posts.extend(_closed(grouped))

    posts = [replace(post, details=kept(post.details)) for post in posts]
    posts.sort(key=lambda post: (post.at, post.post_id))
    participants = tuple(dict.fromkeys(post.actor for post in posts))
    return Thread(
        review_id=review.review_id,
        posts=tuple(posts),
        truncated=truncated,
        events_read=len(events),
        participants=participants,
        run_id=next((str(event["run_id"]) for event in ordered if event.get("run_id")), None),
        arrived_by_email=bool(
            grouped.get("review_started_by_email") or grouped.get("follow_up_started_by_email")
        ),
    )


# -- 1. how the work arrived ---------------------------------------------------------


def _arrival(
    review: Review,
    rounds: Sequence[Round],
    grouped: dict[str, list[dict[str, Any]]],
    questions: Sequence[Question],
) -> list[Post]:
    """Who brought the work in, and what they concluded about it.

    Three shapes, and the difference between them is the whole inbound story: a review
    that arrived by email is posted by ``InboxAgent`` with its classification attached, a
    review someone started here says so instead, and a review that has published work but
    heard nothing back says *that*, rather than rendering an empty thread that reads like
    a review with nothing in it.
    """
    posts: list[Post] = []

    for kind in ("review_started_by_email", "follow_up_started_by_email"):
        for event in grouped.get(kind, []):
            posts.append(_inbound_post(review, questions, event, kind))

    if posts:
        return posts

    intake = [
        event
        for event in grouped.get("stage_completed", [])
        if _detail(event).get("stage") == "intake_document"
    ]
    for event in intake:
        detail = _detail(event)
        count = int(detail.get("questions") or len(questions))
        dropped = int(detail.get("dropped_over_ceiling") or 0)
        gcs = str(detail.get("gcs_uri") or "")
        rows = [
            Row("Questions parsed", str(count)),
            Row("Round", str(detail.get("round_id") or ""), mono=True),
            Row("Framework", review.framework.value.upper()),
            Row("Residency demanded", review.residency.value.upper()),
        ]
        if gcs:
            rows.append(Row("Source", gcs, mono=True))
        rows.append(Row("Deduplicated on", str(detail.get("dedup_key") or "—"), mono=True))
        posts.append(
            Post(
                post_id=f"arrival-intake-{_at(event)}",
                actor="Orchestrator",
                kind="arrival",
                at=_at(event),
                summary=(
                    f"{review.customer} — {review.framework.value.upper()}, "
                    f"{count} {_plural(count, 'question')} parsed."
                ),
                lines=(
                    (
                        f"{dropped} questions past the per-round ceiling were not taken "
                        "into this round.",
                    )
                    if dropped
                    else ()
                ),
                details=(Detail("The questionnaire", tuple(rows)),),
            )
        )

    if posts:
        return posts

    first_round = min(rounds, key=lambda item: item.ordinal, default=None)
    return [
        Post(
            post_id="arrival-pending",
            actor="Orchestrator",
            kind="pending",
            at=review.created_at.isoformat(),
            summary="Work has been published. No stage has reported yet.",
            lines=(
                "The review advances by message delivery, so this fills in as the "
                "dispatcher picks the work up.",
            ),
            details=(
                Detail(
                    "What exists so far",
                    (
                        Row("Review", review.review_id, mono=True),
                        Row("State", _humanise(review.state.value)),
                        Row(
                            "Round",
                            first_round.round_id if first_round else "not opened yet",
                            mono=first_round is not None,
                        ),
                    ),
                ),
            ),
            working=True,
        )
    ]


def _inbound_post(
    review: Review, questions: Sequence[Question], event: dict[str, Any], kind: str
) -> Post:
    detail = _detail(event)
    sender = str(detail.get("sender") or "an unknown sender")
    subject = str(detail.get("subject") or "")
    attachments = [str(name) for name in detail.get("attachments") or []]
    framework = str(detail.get("framework") or review.framework.value).upper()
    deadline = str(detail.get("deadline") or "")
    dormant = detail.get("dormant_days")

    summary = f"Questionnaire from {sender} — {framework}"
    if questions:
        summary += f", {len(questions)} questions"
    if deadline:
        summary += f", response requested by {deadline}"

    lines: list[str] = []
    if kind == "follow_up_started_by_email" and dormant is not None:
        ordinal = detail.get("ordinal", review.current_round)
        lines.append(
            f"A reply on the same thread after {dormant} days. Round {ordinal} opens "
            "against the commitments made in the last one."
        )

    email_rows = [
        Row("From", sender, mono=True),
        Row("Subject", subject or "—"),
        Row(
            "Attachment",
            ", ".join(attachments) if attachments else "none",
            mono=bool(attachments),
        ),
        Row("Customer", str(detail.get("customer") or review.customer)),
        Row("Framework", framework),
    ]
    if deadline:
        email_rows.append(Row("Deadline the customer named", deadline))

    verdict = "a security review" if detail.get("is_security_review") else "not a security review"
    classification = [
        Row("Verdict", verdict),
        Row("Reached by", str(detail.get("decided_by") or "model")),
        Row("Because", str(detail.get("reason") or "—")),
    ]
    signals = [str(signal) for signal in detail.get("signals") or []]
    if signals:
        classification.append(Row("Signals", ", ".join(signals)))
    if detail.get("armor_blocked"):
        classification.append(
            Row("Model Armor", "blocked the message body before the classifier read it")
        )

    details = [
        Detail("The email", tuple(email_rows)),
        Detail("What I classified it as, and why", tuple(classification)),
    ]
    gcs = str(detail.get("gcs_uri") or "")
    if gcs:
        details.append(Detail("The attachment", (Row("Stored at", gcs, mono=True),)))

    return Post(
        post_id=f"arrival-{kind}-{_at(event)}",
        actor=_actor(event, "InboxAgent"),
        kind="arrival",
        at=_at(event),
        summary=summary,
        lines=tuple(lines),
        details=tuple(details),
    )


# -- 2. the orchestrator's own judgement ---------------------------------------------


def _judged_by(decided_by: str) -> str:
    """How `decided_by` reads to someone who is not holding the orchestrator's source.

    The value is `"model"` when the judgement call parsed and `"fallback:<why>"` when it did
    not. Both are worth showing and the second is worth showing *more*: a system that says
    it could not read its own answer and took the cautious branch is making a stronger claim
    about itself than one that only ever reports success.
    """
    if not decided_by:
        return ""
    if decided_by == "model":
        return "decided by the orchestrator"
    return f"fell back to the cautious branch ({decided_by.removeprefix('fallback:')})"


def _judgement(grouped: dict[str, list[dict[str, Any]]]) -> list[Post]:
    """The orchestrator's three judgement calls: the plan, the retries, release-or-hold.

    These are the events that make autonomy legible rather than asserted: a system that
    picked a plan and can say why it picked it, as opposed to one that ran whatever was
    hard-coded. Each was already on the trail and each was almost invisible in the thread --
    the plan's reason sat in an expansion, a retry that retried *nothing* summarised as
    "Retried a stage.", and release-or-hold rendered as a tally with the judgement and its
    reason dropped. A reader who cannot see these sees a pipeline rather than a fleet.

    Every post here is absent when its event is absent. A run whose orchestrator never spoke
    shows nothing, rather than a row saying it did nothing.
    """
    posts: list[Post] = []

    # -- judgement 1: the plan ---------------------------------------------------------
    for event in grouped.get("plan_selected", []):
        detail = _detail(event)
        plan = str(detail.get("plan") or detail.get("selected") or detail.get("pipeline") or "")
        reason = _truncate(str(detail.get("reason") or ""), 120)
        # The reason goes on the summary line rather than into the expansion. It is the half
        # that carries the judgement; the plan name on its own is a label.
        summary = f"Plan: {plan}." if plan else "Selected a plan for this round."
        if reason and reason != "(no reason given)":
            summary = f"{summary[:-1]} — {reason}"
        posts.append(
            Post(
                post_id=f"plan-{_at(event)}",
                actor=_actor(event, "Orchestrator"),
                kind="plan",
                at=_at(event),
                summary=_truncate(summary),
                lines=tuple(
                    line for line in (_judged_by(str(detail.get("decided_by") or "")),) if line
                ),
                details=(Detail("The plan, and why", _pairs(detail)),),
            )
        )

    # -- judgement 2: the retries ------------------------------------------------------
    for event in grouped.get("retry_decided", []):
        detail = _detail(event)
        candidates = int(detail.get("candidates") or 0)
        retrying = int(detail.get("retrying") or 0)
        # Retrying nothing is a decision, and it is the one the old summary hid. It is also
        # the safe branch: an un-retried question is already flagged for a person, where a
        # blind retry is how a transient blip becomes a loop.
        if retrying:
            summary = f"Retrying {retrying} of {candidates} weak {_plural(candidates, 'answer')}."
        elif candidates:
            summary = (
                f"Retried none of {candidates} weak {_plural(candidates, 'answer')} — "
                "they stay flagged for a person."
            )
        else:
            summary = "Nothing needed retrying."
        posts.append(
            Post(
                post_id=f"retry-{_at(event)}-{_question_id(event)}",
                actor=_actor(event, "Orchestrator"),
                kind="plan",
                at=_at(event),
                summary=_truncate(str(detail.get("reason") or summary)),
                lines=tuple(
                    line for line in (_judged_by(str(detail.get("decided_by") or "")),) if line
                ),
                details=(Detail("The decision", _pairs(detail)),),
            )
        )

    # -- judgement 3: release or hold --------------------------------------------------
    #
    # `run_completed` carries both the tallies and the judgement. `_closed` renders the
    # tallies; this renders the judgement, which is the half a reader is actually asking
    # about. The injected run's "a guardrail fired repeatedly across multiple questions" is
    # the single best line this system produces and it was not on screen anywhere.
    for event in grouped.get("run_completed", []):
        detail = _detail(event)
        if "release" not in detail:
            continue
        widened = int(detail.get("widened") or 0)
        reason = _truncate(str(detail.get("reason") or ""), 140)
        verdict = (
            "Released the round." if bool(detail.get("release")) else "Held the round for a person."
        )
        if widened:
            verdict = (
                f"{verdict[:-1]} and widened {widened} {_plural(widened, 'answer')} to a person."
            )
        posts.append(
            Post(
                post_id=f"released-{_at(event)}",
                actor=_actor(event, "Orchestrator"),
                kind="plan",
                at=_at(event),
                summary=_truncate(f"{verdict} {reason}".strip()),
                lines=tuple(
                    line for line in (_judged_by(str(detail.get("decided_by") or "")),) if line
                ),
                details=(Detail("Release or hold", _pairs(detail)),),
            )
        )
    return posts


# -- 3. triage -----------------------------------------------------------------------


def _triage(
    grouped: dict[str, list[dict[str, Any]]],
    questions: Sequence[Question],
    live: bool,
) -> list[Post]:
    events = grouped.get("question_triaged", [])
    if not events:
        return []

    # Counted from the questions collection, which is what triage wrote. The events say
    # which model made each call; they are not the tally.
    by_department = Counter(question.department.value for question in questions)
    models = Counter(str(_detail(event).get("model") or "") for event in events)
    routed = sum(
        count for dept, count in by_department.items() if dept != Department.UNASSIGNED.value
    )
    spread = " · ".join(
        f"{by_department[dept]} {dept}" for dept in _DEPARTMENT_ORDER if by_department.get(dept)
    )
    departments = len([dept for dept in _DEPARTMENT_ORDER[:3] if by_department.get(dept)])
    unassigned = by_department.get("unassigned", 0)

    return [
        Post(
            post_id="triage",
            actor=_actor(events[0], "TriageAgent"),
            kind="triage",
            at=_at(events[0]),
            through=_at(events[-1]),
            events=len(events),
            summary=(
                f"Routed {routed} {_plural(routed, 'question')} to {departments} "
                f"{_plural(departments, 'department')} — {spread}."
            ),
            lines=(
                (
                    f"{unassigned} could not be placed and read the shared corpus, where a "
                    "citation still has to be found or the answer is flagged.",
                )
                if unassigned
                else ()
            ),
            details=(
                Detail(
                    "Where the questions went",
                    tuple(
                        Row(dept.capitalize(), f"{by_department[dept]} questions")
                        for dept in _DEPARTMENT_ORDER
                        if by_department.get(dept)
                    ),
                    note=(
                        "A department is an access boundary, not a label: each maps to its "
                        "own datastore and its own agent identity."
                    ),
                ),
                Detail(
                    "What made the call",
                    tuple(
                        Row(model or "unrecorded", f"{count} calls", mono=bool(model))
                        for model, count in models.most_common()
                    ),
                    note=(
                        "Triage runs on the cheap model. The expensive one is spent on "
                        "drafting, where the words end up in front of a customer."
                    ),
                ),
            ),
            working=live and len(events) < len(questions),
            actions=(Action("questions", "Open the grid", len(questions)),),
        )
    ]


# -- 4. drafting, one post per department --------------------------------------------


def _drafting(
    grouped: dict[str, list[dict[str, Any]]],
    questions: Sequence[Question],
    answers: Sequence[Answer],
    live: bool,
) -> list[Post]:
    """One post per department that drafted, with a counter that rises while it runs.

    The counters are why this is one post per department rather than one per answer.
    Three engines drafting in parallel is a fleet doing work; 263 individual "answered
    Q94" posts is the 312-row wall in a different shape.
    """
    drafted = grouped.get("answer_drafted", [])
    retrieved = grouped.get("evidence_retrieved", [])
    expanded = grouped.get("query_expanded", [])
    if not drafted and not retrieved:
        return []

    by_question = {question.question_id: question for question in questions}

    def department_of(question_id: str) -> str:
        question = by_question.get(question_id)
        return question.department.value if question else "unassigned"

    answers_by_department: dict[str, list[Answer]] = defaultdict(list)
    for answer in answers:
        answers_by_department[department_of(answer.question_id)].append(answer)

    assigned: Counter[str] = Counter(question.department.value for question in questions)

    first_event: dict[str, str] = {}
    last_event: dict[str, str] = {}
    event_count: Counter[str] = Counter()
    for event in drafted:
        department = department_of(_question_id(event))
        first_event.setdefault(department, _at(event))
        last_event[department] = _at(event)
        event_count[department] += 1

    passages: dict[str, list[int]] = defaultdict(list)
    documents: dict[str, Counter[str]] = defaultdict(Counter)
    for event in retrieved:
        department = department_of(_question_id(event))
        detail = _detail(event)
        passages[department].append(int(detail.get("count") or 0))
        for document in detail.get("documents") or []:
            documents[department][str(document)] += 1

    expansions: dict[str, list[str]] = defaultdict(list)
    for event in expanded:
        department = department_of(_question_id(event))
        for query in _detail(event).get("queries") or []:
            expansions[department].append(str(query))

    # How each partition actually ran. One row per attempt, because a partition that was
    # resumed twice ran three times, and averaging that away would hide the retry.
    attempts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _stages(grouped, "draft_answer"):
        detail = _detail(event)
        department = str(detail.get("department") or detail.get("partition") or "unassigned")
        attempts[department].append(detail)

    posts: list[Post] = []
    for department in _DEPARTMENT_ORDER:
        produced = answers_by_department.get(department, [])
        total = assigned.get(department, len(produced))
        if not produced and department not in first_event:
            continue

        statuses = Counter(answer.status.value for answer in produced)
        confidences = Counter(answer.confidence.value for answer in produced)
        cited = sum(1 for answer in produced if answer.citations)
        working = live and len(produced) < total
        counts = passages.get(department, [])
        mean_passages = (sum(counts) / len(counts)) if counts else 0.0

        lines: list[str] = []
        uncited = len(produced) - cited
        if uncited:
            lines.append(
                f"{uncited} carry no citation and are flagged rather than answered — "
                "the corpus did not support them."
            )

        top_documents = documents.get(department, Counter()).most_common(SAMPLE_CEILING)
        document_rows, document_note = _sample(
            tuple(
                Row(_truncate(name, 90), f"{count} questions", mono=True)
                for name, count in top_documents
            ),
            len(documents.get(department, Counter())),
            "document",
        )
        expansion_rows, expansion_note = _sample(
            tuple(
                Row("Query", _truncate(query, 110), mono=True)
                for query in expansions.get(department, [])[:SAMPLE_CEILING]
            ),
            len(expansions.get(department, [])),
            "query",
        )

        details = [
            Detail(
                "What it produced",
                tuple(
                    Row(_humanise(status).capitalize(), str(count))
                    for status, count in statuses.most_common()
                ),
            ),
            Detail(
                "Confidence, computed from the retrieval rather than asked of a model",
                tuple(
                    Row(level.capitalize(), str(confidences[level]))
                    for level in ("high", "medium", "low")
                    if confidences.get(level)
                ),
            ),
            Detail(
                "Retrieval",
                (
                    Row("Questions retrieved for", str(len(counts))),
                    Row("Mean passages per question", f"{mean_passages:.1f}", mono=True),
                    Row("Answers carrying a citation", f"{cited} of {len(produced)}"),
                ),
                note=(
                    f"Reading the {department} corpus only. Another department's datastore "
                    "is refused to this identity by IAM, not by instruction."
                    if department != "unassigned"
                    else "Unassigned questions read the shared corpus only."
                ),
            ),
        ]
        if document_rows:
            details.append(Detail("Documents it stood on", document_rows, note=document_note))
        if expansion_rows:
            details.append(
                Detail("Queries it expanded the question into", expansion_rows, note=expansion_note)
            )

        runs = attempts.get(department, [])
        if runs:
            details.append(
                Detail(
                    "How it ran",
                    tuple(
                        Row(f"Attempt {index}", _attempt_line(run), mono=True)
                        for index, run in enumerate(runs, start=1)
                    ),
                    note=(
                        "Each attempt is its own row. A partition that was resumed ran more "
                        "than once, and averaging that away would hide the retry."
                    ),
                )
            )

        at = first_event.get(department) or (produced[0].created_at.isoformat() if produced else "")
        posts.append(
            Post(
                post_id=f"drafting-{department}",
                actor=_DEPARTMENT_AGENT[department],
                kind="drafting",
                at=at,
                through=last_event.get(department),
                events=event_count.get(department, len(produced)),
                summary=(
                    f"{len(produced)} of {total} answered"
                    if working
                    else f"{len(produced)} {_plural(len(produced), 'answer')} drafted"
                ),
                lines=tuple(lines),
                details=tuple(details),
                progress=(Progress(department, len(produced), total),),
                working=working,
            )
        )
    return posts


# -- 5. what was refused --------------------------------------------------------------


def _armor(grouped: dict[str, list[dict[str, Any]]], labels: dict[str, str]) -> list[Post]:
    events = grouped.get("armor_blocked", [])
    if not events:
        return []

    filters = Counter(
        str(name) for event in events for name in _detail(event).get("matched_filters") or []
    )
    surfaces = Counter(str(_detail(event).get("surface") or "unknown") for event in events)
    matched = ", ".join(f"{_humanise(name)} x{count}" for name, count in filters.most_common())

    rows, note = _sample(
        tuple(
            Row(
                labels.get(_question_id(event), "—"),
                _truncate(str(_detail(event).get("excerpt") or ""), 140),
                question_id=_question_id(event) or None,
            )
            for event in events[:SAMPLE_CEILING]
        ),
        len(events),
        "block",
    )

    return [
        Post(
            post_id="armor",
            actor=_actor(events[0], "ArmorGuard"),
            kind="refusal",
            at=_at(events[0]),
            through=_at(events[-1]),
            events=len(events),
            summary=(
                f"Refused {len(events)} {_plural(len(events), 'input')} before a model read "
                f"{_plural(len(events), 'it', 'them')}"
                f"{f' — {matched}' if matched else ''}."
            ),
            lines=(
                "The run continued on every other question. One poisoned cell must not "
                "fail a whole review.",
            ),
            details=(
                Detail(
                    "Where it fired",
                    tuple(Row(surface, str(count)) for surface, count in surfaces.most_common()),
                ),
                Detail("What was blocked", rows, note=note),
            ),
        )
    ]


def _denials(grouped: dict[str, list[dict[str, Any]]]) -> list[Post]:
    events = grouped.get("tool_denied", [])
    if not events:
        return []

    by_agent = Counter(_actor(event, "an agent") for event in events)
    who = ", ".join(f"{agent} x{count}" for agent, count in by_agent.most_common())
    rows, note = _sample(
        tuple(
            Row(
                f"{_actor(event, 'agent')} → {_detail(event).get('tool_name') or 'a tool'}",
                _truncate(str(_detail(event).get("reason") or "")),
                question_id=_question_id(event) or None,
            )
            for event in events[:SAMPLE_CEILING]
        ),
        len(events),
        "refusal",
    )

    return [
        Post(
            post_id="denied",
            actor="ToolInterceptor",
            kind="refusal",
            at=_at(events[0]),
            through=_at(events[-1]),
            events=len(events),
            summary=(
                f"Refused {len(events)} cross-department "
                f"{_plural(len(events), 'access attempt')} — {who}."
            ),
            details=(Detail("Each refusal, and its reason", rows, note=note),),
        )
    ]


# -- 6. the verifier ------------------------------------------------------------------


def _verification(grouped: dict[str, list[dict[str, Any]]], labels: dict[str, str]) -> list[Post]:
    """The check that the work is not being marked by whoever did it.

    A run in which the verifier never reported produces no post at all. That is the
    important half: an unperformed check must not render as a passed one, and a post
    reading "0 unsupported" over a run where nothing was checked would do exactly that.
    """
    events = grouped.get("answer_verified", [])
    if not events:
        return []

    verdicts = Counter(str(_detail(event).get("verdict") or "unknown") for event in events)
    returned = [
        event
        for event in events
        if _detail(event).get("verdict") in {"unsupported", "partially_supported"}
    ]

    parts = [f"{verdicts.get('supported', 0)} supported"]
    if verdicts.get("partially_supported"):
        parts.append(f"{verdicts['partially_supported']} partially")
    if verdicts.get("unsupported"):
        parts.append(f"{verdicts['unsupported']} unsupported")
    if verdicts.get("unknown"):
        parts.append(f"{verdicts['unknown']} could not be checked")

    lines: list[str] = []
    if returned:
        lines.append(
            f"{len(returned)} went back to the drafting agent with the claim the passages "
            "did not carry."
        )
    if verdicts.get("unknown"):
        lines.append(
            f"{verdicts['unknown']} report as unchecked rather than as passed. An "
            "unperformed check is not a passed one."
        )

    claim_rows, claim_note = _sample(
        tuple(
            Row(
                labels.get(_question_id(event), "—"),
                _truncate(
                    "; ".join(
                        str(claim) for claim in _detail(event).get("unsupported_claims") or []
                    )
                    or str(_detail(event).get("reason") or ""),
                    200,
                ),
                question_id=_question_id(event) or None,
            )
            for event in returned[:SAMPLE_CEILING]
        ),
        len(returned),
        "answer",
    )

    # The RAW actor here, not the byline. This block is the evidence for separation of
    # duties, and the evidence is the credential -- an engine resource name is exactly what
    # an auditor needs to compare against the drafting identity beside it.
    identities = sorted({str(event.get("actor") or "VerifierAgent") for event in events})
    drafters = sorted({str(_detail(event).get("drafted_by") or "") for event in events} - {""})

    details = [
        Detail(
            "The verdict distribution",
            tuple(
                Row(_humanise(verdict).capitalize(), str(count))
                for verdict, count in verdicts.most_common()
            ),
        )
    ]
    if claim_rows:
        details.append(
            Detail("What they claimed that no passage said", claim_rows, note=claim_note)
        )
    details.append(
        Detail(
            "Separation of duties",
            (
                *(Row("Verified by", name, mono=True) for name in identities),
                *(Row("Drafted by", name, mono=True) for name in drafters),
            ),
            note=(
                "The verifying identity is refused a verdict when it equals the drafting "
                "one. A reviewer who is also the author is not a reviewer."
            ),
        )
    )

    return [
        Post(
            post_id="verification",
            actor=_actor(events[0], "VerifierAgent"),
            kind="verification",
            at=_at(events[0]),
            through=_at(events[-1]),
            events=len(events),
            summary=(
                f"Checked {len(events)} {_plural(len(events), 'answer')} against the "
                f"passages they cite — {' · '.join(parts)}."
            ),
            lines=tuple(lines[:2]),
            details=tuple(details),
        )
    ]


# -- 7. consistency against prior commitments -----------------------------------------


def _consistency(grouped: dict[str, list[dict[str, Any]]], labels: dict[str, str]) -> list[Post]:
    events = grouped.get("consistency_checked", [])
    if not events:
        return []

    verdicts = Counter(str(_detail(event).get("verdict") or "unknown") for event in events)
    constrained = [event for event in events if _detail(event).get("constrained")]
    contradictions = [event for event in events if _detail(event).get("verdict") == "contradiction"]
    shown = constrained or contradictions

    rows, note = _sample(
        tuple(
            Row(
                labels.get(_question_id(event), "—"),
                _truncate(str(_detail(event).get("prior_statement") or ""), 200),
                question_id=_question_id(event) or None,
            )
            for event in shown[:SAMPLE_CEILING]
        ),
        len(shown),
        "answer",
    )

    summary = f"Checked {len(events)} {_plural(len(events), 'answer')} against prior commitments"
    summary += (
        f" — {len(constrained)} redrafted under one." if constrained else " — none contradicted."
    )

    details = [
        Detail(
            "Verdicts",
            tuple(
                Row(_humanise(verdict).capitalize(), str(count))
                for verdict, count in verdicts.most_common()
            ),
        )
    ]
    if rows:
        details.append(Detail("The commitment that constrained the draft", rows, note=note))

    return [
        Post(
            post_id="consistency",
            actor=_actor(events[0], "Orchestrator"),
            kind="consistency",
            at=_at(events[0]),
            through=_at(events[-1]),
            events=len(events),
            summary=summary,
            details=tuple(details),
        )
    ]


# -- 8. assembly and what a human must see ---------------------------------------------


def _assembly(
    grouped: dict[str, list[dict[str, Any]]],
    answers: Sequence[Answer],
    labels: dict[str, str],
    customer: str = "the customer",
) -> list[Post]:
    events = grouped.get("human_required", [])
    if not events:
        return []

    # Anchored at the stage that assembled, not at the first escalation. `human_required`
    # is written per answer *during* drafting, so its earliest event is minutes before the
    # departments finish -- and a post reading "round assembled" above three agents still
    # drafting is a thread that describes an order of events that did not happen.
    assembled = _stages(grouped, "assemble_round")
    at = _at(assembled[-1]) if assembled else _at(events[-1])

    # Counted from the answers, not from the events: an answer already approved is no
    # longer pending, and a count taken from `human_required` alone would keep claiming
    # 43 after somebody had cleared forty of them.
    pending = [answer for answer in answers if answer.status is AnswerStatus.NEEDS_HUMAN]
    reasons = Counter(str(_detail(event).get("reason") or "unrecorded") for event in events)

    rows, note = _sample(
        tuple(
            Row(
                labels.get(answer.question_id, "—"),
                _truncate(answer.text, 140),
                question_id=answer.question_id,
            )
            for answer in pending[:SAMPLE_CEILING]
        ),
        len(pending),
        "answer",
    )

    details = [
        Detail(
            "Why each was held",
            tuple(
                Row(_humanise(reason).capitalize(), str(count))
                for reason, count in reasons.most_common()
            ),
        )
    ]
    if rows:
        details.append(Detail("Waiting on you", rows, note=note))
    if assembled:
        details.append(Detail("The round as assembled", _pairs(_detail(assembled[-1]))))

    return [
        Post(
            post_id="assembly",
            actor=_actor(events[0], "AssemblerAgent"),
            kind="assembly",
            at=at,
            through=None,
            events=len(events),
            summary=(
                f"{len(pending)} {_plural(len(pending), 'answer')} need you before this can "
                f"go back to {customer}."
                if pending
                else (
                    f"Round assembled. All {len(events)} answers that needed a person have had one."
                )
            ),
            lines=(
                (
                    "Everything else is drafted and cited. Approve all and I will close the "
                    "round, build the pack and reply on the customer's own thread.",
                )
                if pending
                else ()
            ),
            details=tuple(details),
            # Two ways forward, the cheap one first. Sixty-three individual approvals is not
            # a gate, it is a chore, and a chore is what people click through without
            # reading -- which is a weaker control than one deliberate decision signed with
            # a name. Every answer is still recorded individually on the trail.
            actions=(
                (
                    Action("approve_all", "Approve all and send", len(pending)),
                    Action("approve", "Review them first", len(pending)),
                )
                if pending
                else ()
            ),
        )
    ]


# -- 9. what people did -----------------------------------------------------------------


def _decisions(grouped: dict[str, list[dict[str, Any]]], labels: dict[str, str]) -> list[Post]:
    """Human decisions, grouped by the person who made them.

    Grouped rather than one post per click, because clearing a queue of forty produces
    forty events in four minutes and reads as one action to everyone involved.
    """
    posts: list[Post] = []
    by_actor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in grouped.get("human_decision", []):
        by_actor[_actor(event, "a person")].append(event)

    for actor, batch in by_actor.items():
        approved = [event for event in batch if _detail(event).get("approved")]
        rejected = [event for event in batch if not _detail(event).get("approved")]
        edited = [event for event in batch if _detail(event).get("edited")]

        parts: list[str] = []
        if approved:
            parts.append(f"approved {len(approved)}")
        if rejected:
            parts.append(f"rejected {len(rejected)}")
        if edited:
            parts.append(f"edited {len(edited)}")

        rows, note = _sample(
            tuple(
                Row(
                    labels.get(_question_id(event), "—"),
                    "approved" if _detail(event).get("approved") else "rejected",
                    question_id=_question_id(event) or None,
                )
                for event in batch[:SAMPLE_CEILING]
            ),
            len(batch),
            "decision",
        )

        posts.append(
            Post(
                post_id=f"decision-{actor}-{_at(batch[0])}",
                actor=actor,
                kind="decision",
                at=_at(batch[0]),
                through=_at(batch[-1]),
                events=len(batch),
                summary=f"{', '.join(parts).capitalize()}.",
                details=(Detail("Each decision", rows, note=note),),
            )
        )

    for event in grouped.get("approval_requested", []):
        detail = _detail(event)
        posts.append(
            Post(
                post_id=f"approval-requested-{_at(event)}",
                actor=_actor(event, "AssemblerAgent"),
                kind="escalation",
                at=_at(event),
                summary=(
                    f"Asked {detail.get('to') or 'the compliance owner'} to look at the "
                    f"{detail.get('pending') or ''} answers being held."
                ),
                details=(Detail("The request", _pairs(detail)),),
            )
        )
    return posts


# -- 10. the conversation ----------------------------------------------------------------


def _conversation(grouped: dict[str, list[dict[str, Any]]]) -> list[Post]:
    """Questions a person asked in the thread, and what the trail answered.

    Both sides are audit events, so a question asked three weeks ago is still here with
    the answer that was given at the time and the facts it was built from. The thread has
    no separate message store, and that is deliberate: a conversation about a compliance
    decision belongs in the compliance record.
    """
    posts: list[Post] = []
    for event in grouped.get("human_asked", []):
        posts.append(
            Post(
                post_id=f"asked-{_at(event)}",
                actor=_actor(event, "You"),
                kind="asked",
                at=_at(event),
                summary=str(_detail(event).get("question") or ""),
            )
        )
    for event in grouped.get("orchestrator_answered", []):
        detail = _detail(event)
        posts.append(
            Post(
                post_id=f"answered-{_at(event)}",
                actor=_actor(event, "Orchestrator"),
                kind="answered",
                at=_at(event),
                summary=str(detail.get("answer") or ""),
                lines=tuple(str(line) for line in detail.get("lines") or [])[:2],
                details=_rehydrate(detail.get("details")),
            )
        )
    return posts


def _rehydrate(blocks: Any) -> tuple[Detail, ...]:
    """Read back detail blocks that were stored on an event when the answer was composed.

    The answer is built once, at the time it is asked, and stored whole. Recomposing it
    on every read would mean a thread that answers the same question differently in
    January and in June, which is the opposite of what an audit trail is for.
    """
    if not isinstance(blocks, list):
        return ()
    return tuple(
        Detail(
            str(block.get("heading") or ""),
            tuple(
                Row(
                    str(row.get("label") or ""),
                    str(row.get("value") or ""),
                    mono=bool(row.get("mono")),
                    question_id=(str(row["question_id"]) if row.get("question_id") else None),
                )
                for row in block.get("rows") or []
                if isinstance(row, dict)
            ),
            note=str(block.get("note") or ""),
        )
        for block in blocks
        if isinstance(block, dict)
    )


# -- 11. what left the system --------------------------------------------------------


def _delivery(
    grouped: dict[str, list[dict[str, Any]]], artifacts: Sequence[dict[str, Any]]
) -> list[Post]:
    posts: list[Post] = []
    for event in grouped.get("export_produced", []):
        detail = _detail(event)
        rows_count = int(detail.get("rows") or detail.get("answered") or 0)
        cited = int(detail.get("cited") or 0)
        sendable = int(detail.get("sendable") or 0)
        posts.append(
            Post(
                post_id=f"export-{_at(event)}",
                actor=_actor(event, "AssemblerAgent"),
                kind="artifact",
                at=_at(event),
                summary=(
                    f"{_EXPORT_NAME.get(str(detail.get('format') or ''), 'Pack')} built — "
                    f"{rows_count} {_plural(rows_count, 'row')}, {cited} cited, "
                    f"{sendable} clear to send."
                ),
                details=(
                    Detail(
                        "What is in it",
                        (
                            Row("Format", str(detail.get("format") or "")),
                            Row("Rows", str(rows_count)),
                            Row("Carrying a citation", str(cited)),
                            Row("Approved by a person", str(detail.get("human_approved") or 0)),
                            Row("Clear to send", str(sendable)),
                            Row("Size", f"{int(detail.get('bytes') or 0):,} bytes", mono=True),
                        ),
                        note=(
                            "Only an answer a person approved is marked clear to send. A "
                            "draft is labelled a draft in both formats."
                        ),
                    ),
                ),
                actions=(Action("export", "Download", 0),),
            )
        )

    for event in grouped.get("pack_delivered", []):
        detail = _detail(event)
        stored = [item for item in detail.get("artifacts") or [] if isinstance(item, dict)]
        details = [
            Detail(
                "The reply",
                (
                    Row("Authorised by", _actor(event, ""), mono=True),
                    Row("Gmail message", str(detail.get("gmail_message_id") or ""), mono=True),
                    Row("Questions", str(detail.get("questions") or "")),
                    Row("Approved by a person", str(detail.get("human_approved") or 0)),
                ),
            )
        ]
        if stored:
            details.append(
                Detail(
                    "Filed to Drive",
                    tuple(
                        Row(
                            str(item.get("name") or item.get("kind") or "file"),
                            str(item.get("link") or ""),
                            mono=True,
                        )
                        for item in stored
                    ),
                )
            )
        posts.append(
            Post(
                post_id=f"delivered-{_at(event)}",
                actor=_actor(event, "a person"),
                kind="delivery",
                at=_at(event),
                summary=(
                    "Sent the pack back to the customer on the original thread — "
                    f"{detail.get('sendable') or 0} answers cleared."
                ),
                lines=(
                    "Authorised by a named person. The fleet does not email a customer on its own.",
                ),
                details=tuple(details),
                actions=(Action("artifacts", "Artifacts", len(artifacts)),),
            )
        )
    return posts


# -- 12. the round closing -------------------------------------------------------------


def _resumed(grouped: dict[str, list[dict[str, Any]]]) -> list[Post]:
    """The fleet picking the work back up after a person cleared something.

    The half of the loop that is easy to leave invisible. A human approves, and the round
    resumes because a message was published -- not because anyone pressed run. Without
    this post the thread shows the decision and then the outcome, with the mechanism
    between them missing.
    """
    posts: list[Post] = []
    for event in _stages(grouped, "resume_after_human"):
        detail = _detail(event)
        still = detail.get("still_pending")
        posts.append(
            Post(
                post_id=f"resumed-{_at(event)}",
                actor="Orchestrator",
                kind="resumed",
                at=_at(event),
                summary=(
                    f"Resumed on {detail.get('resolved_by') or 'that decision'}."
                    + (
                        f" {still} {_plural(int(still), 'answer')} still held."
                        if still
                        else " Nothing further is held."
                    )
                ),
                details=(Detail("What resumed, and what is left", _pairs(detail)),),
            )
        )
    return posts


def _closed(grouped: dict[str, list[dict[str, Any]]]) -> list[Post]:
    """The round closing, from whichever of the two records exists.

    `run_completed` is the fleet's own event and carries the tallies. `close_round` is the
    dispatcher stage. A run can produce either, so both are read and neither is assumed.
    """
    from_stage = [
        Post(
            post_id=f"closed-{_at(event)}",
            actor="Orchestrator",
            kind="closed",
            at=_at(event),
            summary="Round closed.",
            details=(Detail("The round", _pairs(_detail(event))),),
        )
        for event in _stages(grouped, "close_round")
    ]
    return from_stage + [
        Post(
            post_id=f"completed-{_at(event)}",
            actor=_actor(event, "Orchestrator"),
            kind="closed",
            at=_at(event),
            summary=(
                f"Round closed — {_detail(event).get('answered') or 0} answered, "
                f"{_detail(event).get('flagged') or 0} flagged, "
                f"{_detail(event).get('blocked') or 0} blocked."
            ),
            details=(Detail("The round", _pairs(_detail(event))),),
        )
        for event in grouped.get("run_completed", [])
    ]
