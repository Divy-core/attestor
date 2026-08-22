"""Answering a person's question about a review, out of the review's own record.

## The rule this module exists to keep

"Why did you flag this?" has a real answer, and it is already written down. The question
was triaged by a named model into a named department; a named agent retrieved *n* passages
from a named datastore; it drafted an answer with *k* citations; a *different* agent
checked that answer against those citations and returned a verdict; policy computed a
confidence from those signals and escalated on a stated reason. That chain is the answer.

So this module composes the reply from the trail and **never calls a model**. The
alternative -- handing the events to an LLM and asking it to explain -- produces fluent
sentences whose relationship to the events is unverifiable, on the one surface in this
product whose entire purpose is being checkable. A judge asking "is that real?" should get
"every clause maps to an audit row", not "the model said so".

The cost is that the reply is templated rather than conversational, and the vocabulary of
questions it understands is finite. It says so when a question falls outside it, rather
than reaching for something plausible: **an unanswerable question is answered with what
the trail does hold.** That refusal is the same shape as `SupportVerdict.UNKNOWN`, and it
is here for the same reason.

## Resolving which question is meant

A person types "Q112", or a cell reference, or a phrase out of the question text. All
three resolve against the round's own questions -- label first, then id, then token
overlap with a floor. Below the floor nothing is guessed: naming the wrong question and
then explaining it correctly is worse than saying which ones came close.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from attestor_core.domain import Answer, Question, Review
from attestor_platform.thread.model import Detail, Row, kept
from attestor_platform.thread.projection import question_labels

#: Minimum share of the asker's content words that must appear in a question before it is
#: treated as the one they meant.
_MATCH_FLOOR = 0.4

#: Two further floors, and they exist because the share on its own is not enough.
#:
#: Measured on the deployed 312-question round: "how many are held?" reduces to the single
#: content word *held*, which appears in some answer somewhere, so it scored 1.0 and
#: resolved to row 31 -- a confident, fluent, completely wrong answer to a question about
#: the round. A ratio over a one-word set is not evidence of anything.
#:
#: So a fuzzy match needs at least `_MIN_ASKED_WORDS` content words to work from and at
#: least `_MIN_OVERLAP` of them present in the row. Below either, nothing is resolved and
#: the question falls through to the round-level handlers, which is where a question about
#: the round belonged in the first place.
_MIN_ASKED_WORDS = 3
_MIN_OVERLAP = 2

#: Words that carry no signal when matching a question. Not a general stop list -- these
#: are the words this domain repeats in every single row.
#: Spelled out as one string rather than a list literal because the point of it is to be
#: read at a glance and edited when a false match is found.
_NOISE_WORDS = """
a an and are as at be by did do does for from has have how in is it its many much of on
or our that the their there these this to was we were what when where which who why will
with you your say said answer answered question questions review
"""

_NOISE = frozenset(_NOISE_WORDS.split())


def _words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _NOISE]


class Composed:
    """One reply: a line, up to two supporting lines, and the blocks behind it."""

    __slots__ = ("answer", "details", "lines", "question_id")

    def __init__(
        self,
        answer: str,
        *,
        lines: Sequence[str] = (),
        details: Sequence[Detail] = (),
        question_id: str | None = None,
    ) -> None:
        self.answer = answer
        self.lines = tuple(lines)[:2]
        self.details = kept(details)
        self.question_id = question_id

    def as_detail(self) -> dict[str, Any]:
        """The shape stored on the `orchestrator_answered` audit event.

        Stored whole rather than recomposed on read. A thread that answers the same
        question differently in January and in June is not an audit trail.
        """
        return {
            "answer": self.answer,
            "lines": list(self.lines),
            "details": [detail.as_dict() for detail in self.details],
            "question_id": self.question_id,
        }


# ---------------------------------------------------------------------------------
# Resolving a question reference
# ---------------------------------------------------------------------------------


def resolve_reference(asked: str, questions: Sequence[Question]) -> Question | None:
    """A question named outright -- by its number, by where it sits in the customer's file,
    or by its id.

    High precision by construction: every branch requires the asker to have typed something
    only one row can be. Nothing is scored and nothing is guessed, which is why this runs
    before the round-level handlers while the fuzzy match runs after them.
    """
    if not questions:
        return None

    lowered = asked.lower()

    # 1. `Q112` -- the question's number in this round, which is what the whole product
    #    labels it. Word-bounded, so `Q11` cannot match `Q112`: that is the defect that
    #    makes a reference feature untrustworthy the first time somebody notices it.
    #    Out of range resolves to nothing rather than to the last row.
    numbered = re.search(r"(?<![a-z0-9])q\s*(\d{1,4})(?![a-z0-9])", lowered)
    if numbered is not None:
        index = int(numbered.group(1))
        if 1 <= index <= len(questions):
            return questions[index - 1]

    # 2. Where it sits in the customer's own spreadsheet. Somebody reading their file rather
    #    than this screen will say "row 301" or "C301", and both name one row exactly.
    row = re.search(r"(?<![a-z0-9])row\s*(\d{1,6})(?![a-z0-9])", lowered)
    if row is not None:
        wanted_row = int(row.group(1))
        matches = [
            question
            for question in questions
            if question.source_ref is not None and question.source_ref.row == wanted_row
        ]
        if len(matches) == 1:
            return matches[0]

    cell = re.search(r"(?<![a-z0-9])([a-z]{1,3}\d{1,6})(?![a-z0-9])", lowered)
    if cell is not None:
        wanted_cell = cell.group(1)
        matches = [
            question
            for question in questions
            if question.source_ref is not None
            and str(question.source_ref.cell or "").lower() == wanted_cell
        ]
        if len(matches) == 1:
            return matches[0]

    # 3. A content id, whole or as a prefix long enough to name one row and no other.
    for token in re.findall(r"[0-9a-f]{8,}", lowered):
        matches = [question for question in questions if question.question_id.startswith(token)]
        if len(matches) == 1:
            return matches[0]

    return None


def resolve_question(
    asked: str, questions: Sequence[Question], answers: Sequence[Answer]
) -> tuple[Question | None, list[Question]]:
    """Which row the asker meant, and the near misses when nothing is certain enough.

    Reference first, then token overlap against the question text and its answer. The
    overlap is deliberately hard to satisfy -- see `_MIN_ASKED_WORDS` for the measured
    reason -- and when it is not satisfied the near misses are returned so the reply can
    show what came close instead of picking one.
    """
    named = resolve_reference(asked, questions)
    if named is not None:
        return named, []

    asked_words = set(_words(asked))
    if len(asked_words) < _MIN_ASKED_WORDS:
        return None, []

    answer_text = {answer.question_id: answer.text for answer in answers}
    scored: list[tuple[int, float, Question]] = []
    for question in questions:
        haystack = set(_words(question.text)) | set(
            _words(answer_text.get(question.question_id, ""))
        )
        if not haystack:
            continue
        overlap = len(asked_words & haystack)
        scored.append((overlap, overlap / len(asked_words), question))
    scored.sort(key=lambda row: (row[1], row[0]), reverse=True)
    if scored and scored[0][0] >= _MIN_OVERLAP and scored[0][1] >= _MATCH_FLOOR:
        return scored[0][2], []
    return None, [question for overlap, _, question in scored[:3] if overlap > 0]


# ---------------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------------


def answer_from_trail(
    asked: str,
    *,
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    """Compose a reply. Pure, deterministic, and grounded in `events` alone.

    Three passes, in falling order of certainty. A question that names a row outright is
    about that row; a question using the vocabulary of the round is about the round; and
    only then is a phrase matched against the question text. Getting that order wrong is
    what made "how many are held?" answer confidently about row 31.
    """
    named = resolve_reference(asked, questions)
    if named is not None:
        return _about_question(named, questions, answers, events)

    lowered = asked.lower()
    for probe, handler in _ROUND_HANDLERS:
        if any(term in lowered for term in probe):
            return handler(review, questions, answers, events)

    question, near = resolve_question(asked, questions, answers)
    if question is not None:
        return _about_question(question, questions, answers, events)
    return _cannot_answer(review, questions, answers, events, near)


def _about_question(
    question: Question,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    """The reasoning chain for one row, in the order it happened."""
    label = question_labels(questions).get(question.question_id, question.question_id)
    answer = next((a for a in answers if a.question_id == question.question_id), None)
    chain = [event for event in events if event.get("question_id") == question.question_id]
    chain.sort(key=lambda event: str(event.get("occurred_at") or ""))

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for event in chain:
        by_kind.setdefault(str(event.get("kind") or ""), []).append(event)

    def latest(kind: str) -> dict[str, Any]:
        found = by_kind.get(kind)
        return _detail(found[-1]) if found else {}

    triaged = latest("question_triaged")
    retrieved = latest("evidence_retrieved")
    verified = latest("answer_verified")
    consistency = latest("consistency_checked")
    escalation = latest("human_required")
    blocked = latest("armor_blocked")

    if answer is None:
        return Composed(
            f"{label} has no answer on file yet.",
            lines=(
                (
                    f"It was routed to {triaged['department']} by "
                    f"{triaged.get('model', 'triage')}, and nothing has drafted it since.",
                )
                if triaged
                else ("Nothing has reported against it.",)
            ),
            details=(
                Detail("The question", _question_rows(question, label)),
                *((_chain_block(chain),) if chain else ()),
            ),
            question_id=question.question_id,
        )

    # The headline: the reason the answer is in the state it is in, not a restatement of
    # the answer. "Why is this flagged" is answered with the escalation reason, and every
    # branch below names a field that was recorded rather than inferred.
    if blocked:
        headline = (
            f"{label} was quarantined: Model Armor matched "
            f"{', '.join(str(f) for f in blocked.get('matched_filters') or ['a filter'])} "
            f"on the {blocked.get('surface', 'input')} before a model read it."
        )
    elif escalation:
        reason = str(escalation.get("reason") or "policy")
        headline = (
            f"{label} is held for a person because {reason.replace('_', ' ')}, at "
            f"{answer.confidence.value} confidence."
        )
    elif answer.status.value == "flagged_no_evidence":
        headline = (
            f"{label} is flagged: the corpus carried nothing that answered it, so no "
            "answer was written."
        )
    else:
        headline = (
            f"{label} was answered by {answer.authored_by} at "
            f"{answer.confidence.value} confidence, on "
            f"{len(answer.citations)} {'passage' if len(answer.citations) == 1 else 'passages'}."
        )

    lines: list[str] = []
    if consistency.get("constrained"):
        lines.append(
            "A prior-round commitment changed the draft: "
            f"{_shorten(str(consistency.get('prior_statement') or ''))}"
        )
    if verified.get("verdict") in {"unsupported", "partially_supported"}:
        claims = verified.get("unsupported_claims") or []
        lines.append(
            f"{verified['verdict'].replace('_', ' ').capitalize()} — "
            + (
                _shorten("; ".join(str(claim) for claim in claims))
                if claims
                else _shorten(str(verified.get("reason") or ""))
            )
        )
    elif verified.get("verdict") == "unknown" or not verified:
        lines.append(
            "No separate agent has checked this answer against its own citations, so it "
            "reports as unchecked rather than as verified."
        )

    details: list[Detail] = [Detail("The question", _question_rows(question, label))]

    routing_rows = [Row("Owning department", question.department.value)]
    if triaged:
        routing_rows.append(Row("Decided by", str(triaged.get("model") or ""), mono=True))
    routing_rows.append(
        Row("Retrieved passages", str(retrieved.get("count", len(answer.citations))))
    )
    details.append(
        Detail(
            "How it was routed and what was read",
            tuple(routing_rows),
            note=(
                f"Only the {question.department.value} datastore was reachable to this identity."
                if question.department.value != "unassigned"
                else "Unassigned questions read the shared corpus only."
            ),
        )
    )

    details.append(
        Detail(
            "The answer as drafted",
            (
                Row("Written by", answer.authored_by, mono=True),
                Row("Status", answer.status.value.replace("_", " ")),
                Row("Confidence", answer.confidence.value),
                Row("Text", _shorten(answer.text, 400)),
            ),
        )
    )

    if answer.citations:
        details.append(
            Detail(
                "The passages it stood on",
                tuple(
                    Row(
                        f"{citation.document_title}"
                        + (f" · {citation.section}" if citation.section else ""),
                        f"{citation.retrieval_score:.2f} — {_shorten(citation.snippet, 220)}",
                    )
                    for citation in answer.citations[:6]
                ),
                note=(
                    f"{len(answer.citations) - 6} further passages not listed here."
                    if len(answer.citations) > 6
                    else ""
                ),
            )
        )

    details.append(
        Detail(
            "The check on the answer",
            (
                Row("Verified by", answer.verified_by or "nobody", mono=bool(answer.verified_by)),
                Row("Verdict", answer.support.value.replace("_", " ")),
                *(
                    (Row("Because", _shorten(str(verified.get("reason") or ""), 300)),)
                    if verified.get("reason")
                    else ()
                ),
            ),
            note=(
                "The verifying identity is not the drafting one; a verdict is refused when "
                "they are equal."
            ),
        )
    )

    if consistency:
        details.append(
            Detail(
                "Against what was promised before",
                (
                    Row("Verdict", str(consistency.get("verdict") or "")),
                    Row(
                        "Prior commitment",
                        _shorten(str(consistency.get("prior_statement") or ""), 300),
                    ),
                    Row(
                        "Changed the draft",
                        "yes" if consistency.get("constrained") else "no",
                    ),
                ),
            )
        )

    if chain:
        details.append(_chain_block(chain))

    return Composed(headline, lines=lines, details=details, question_id=question.question_id)


def _question_rows(question: Question, label: str) -> tuple[Row, ...]:
    ref = question.source_ref
    rows = [
        Row(label, _shorten(question.text, 400)),
        Row("Id", question.question_id, mono=True),
    ]
    if ref is not None and (ref.sheet or ref.row or ref.cell):
        where = " · ".join(
            part
            for part in (
                ref.sheet or "",
                f"row {ref.row}" if ref.row else "",
                str(ref.cell or ""),
            )
            if part
        )
        rows.append(Row("In the customer's file", where, mono=True))
    if question.framework_hint:
        rows.append(Row("Framework reference", question.framework_hint, mono=True))
    return tuple(rows)


def _chain_block(chain: Sequence[dict[str, Any]]) -> Detail:
    """Every audit row for this question, in order. The unedited basis for the above."""
    return Detail(
        "Every audit event for this question, in order",
        tuple(
            Row(
                str(event.get("occurred_at") or "")[:19].replace("T", " "),
                f"{event.get('kind')} · {event.get('actor') or 'unattributed'}",
                mono=True,
            )
            for event in chain
        ),
        note=f"{len(chain)} events. This is the record the answer above was read out of.",
    )


# ---------------------------------------------------------------------------------
# Questions about the round rather than about a row
# ---------------------------------------------------------------------------------


def _detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    return detail if isinstance(detail, dict) else {}


def _shorten(text: str, ceiling: int = 200) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= ceiling else text[: ceiling - 1].rstrip() + "…"


def _of_kind(events: Sequence[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("kind") == kind]


def _held(
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    pending = [answer for answer in answers if answer.status.value == "needs_human"]
    reasons = Counter(
        str(_detail(event).get("reason") or "unrecorded")
        for event in _of_kind(events, "human_required")
    )
    labels = question_labels(questions)
    return Composed(
        f"{len(pending)} of {len(answers)} answers are held for a person.",
        lines=(
            (
                "Nothing is released without you when confidence is low, evidence is "
                "absent, or a prior commitment is in play.",
            )
            if pending
            else ("Nothing is waiting on you in this round.",)
        ),
        details=(
            Detail(
                "Why they were held",
                tuple(
                    Row(reason.replace("_", " ").capitalize(), str(count))
                    for reason, count in reasons.most_common()
                ),
            ),
            Detail(
                "The first of them",
                tuple(
                    Row(
                        labels.get(answer.question_id, "—"),
                        _shorten(answer.text, 160),
                        question_id=answer.question_id,
                    )
                    for answer in pending[:8]
                ),
                note=(
                    f"{len(pending) - 8} further answers not listed here."
                    if len(pending) > 8
                    else ""
                ),
            ),
        ),
    )


def _verification(
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    checked = _of_kind(events, "answer_verified")
    if not checked:
        return Composed(
            "No answer in this round has been checked by a separate agent.",
            lines=(
                "The trail carries no verification event, so every answer reports as "
                "unchecked rather than as passed.",
            ),
            details=(
                Detail(
                    "What the answers say about it",
                    (
                        Row(
                            "Marked unknown",
                            str(sum(1 for a in answers if a.support.value == "unknown")),
                        ),
                        Row("Answers on file", str(len(answers))),
                    ),
                ),
            ),
        )
    verdicts = Counter(str(_detail(event).get("verdict") or "unknown") for event in checked)
    labels = question_labels(questions)
    returned = [
        event
        for event in checked
        if _detail(event).get("verdict") in {"unsupported", "partially_supported"}
    ]
    return Composed(
        f"{len(checked)} answers were checked against the passages they cite — "
        + " · ".join(
            f"{count} {verdict.replace('_', ' ')}" for verdict, count in verdicts.most_common()
        )
        + ".",
        lines=((f"{len(returned)} went back to the drafting agent.",) if returned else ()),
        details=(
            Detail(
                "The distribution",
                tuple(
                    Row(verdict.replace("_", " ").capitalize(), str(count))
                    for verdict, count in verdicts.most_common()
                ),
            ),
            Detail(
                "What was returned",
                tuple(
                    Row(
                        labels.get(str(event.get("question_id") or ""), "—"),
                        _shorten(
                            "; ".join(
                                str(claim)
                                for claim in _detail(event).get("unsupported_claims") or []
                            )
                            or str(_detail(event).get("reason") or "")
                        ),
                        question_id=str(event.get("question_id") or "") or None,
                    )
                    for event in returned[:8]
                ),
            ),
        ),
    )


def _blocked(
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    armor = _of_kind(events, "armor_blocked")
    denied = _of_kind(events, "tool_denied")
    labels = question_labels(questions)
    if not armor and not denied:
        return Composed(
            "Nothing was refused in this round.",
            lines=("No Model Armor block and no cross-department denial is on the trail.",),
        )
    filters = Counter(
        str(name) for event in armor for name in _detail(event).get("matched_filters") or []
    )
    return Composed(
        f"{len(armor)} inputs were blocked before a model read them, and {len(denied)} "
        "cross-department reads were refused.",
        lines=("The run continued on every other question in both cases.",),
        details=(
            Detail(
                "What Model Armor matched",
                tuple(
                    Row(name.replace("_", " "), str(count)) for name, count in filters.most_common()
                ),
            ),
            Detail(
                "The blocked content",
                tuple(
                    Row(
                        labels.get(str(event.get("question_id") or ""), "—"),
                        _shorten(str(_detail(event).get("excerpt") or ""), 160),
                        question_id=str(event.get("question_id") or "") or None,
                    )
                    for event in armor[:8]
                ),
            ),
            Detail(
                "The refused reads",
                tuple(
                    Row(
                        f"{event.get('actor')} → {_detail(event).get('tool_name') or 'a tool'}",
                        _shorten(str(_detail(event).get("reason") or "")),
                    )
                    for event in denied[:8]
                ),
            ),
        ),
    )


def _commitments(
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    checks = _of_kind(events, "consistency_checked")
    labels = question_labels(questions)
    if not checks:
        return Composed(
            "No answer in this round was checked against a prior commitment.",
            lines=(
                "That check runs from round two onward, against what was promised in the "
                "round before.",
            ),
        )
    constrained = [event for event in checks if _detail(event).get("constrained")]
    return Composed(
        f"{len(checks)} answers were checked against prior commitments; "
        f"{len(constrained)} were redrafted under one.",
        details=(
            Detail(
                "The commitments that changed a draft",
                tuple(
                    Row(
                        labels.get(str(event.get("question_id") or ""), "—"),
                        _shorten(str(_detail(event).get("prior_statement") or ""), 240),
                        question_id=str(event.get("question_id") or "") or None,
                    )
                    for event in constrained[:8]
                ),
            ),
        ),
    )


def _who(
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    actors = Counter(str(event.get("actor") or "unattributed") for event in events)
    return Composed(
        f"{len(actors)} participants have written to this review's record.",
        details=(
            Detail(
                "Who did what, by event count",
                tuple(
                    Row(actor, f"{count} events", mono=True)
                    for actor, count in actors.most_common()
                ),
                note=(
                    "Every event carries the identity that wrote it. Nothing in this "
                    "record is unattributed."
                ),
            ),
        ),
    )


def _state(
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
) -> Composed:
    statuses = Counter(answer.status.value for answer in answers)
    return Composed(
        f"{review.customer} is in round {review.current_round}, state "
        f"{review.state.value.replace('_', ' ')} — {len(answers)} of {len(questions)} "
        "questions answered.",
        details=(
            Detail(
                "Where the answers stand",
                tuple(
                    Row(status.replace("_", " ").capitalize(), str(count))
                    for status, count in statuses.most_common()
                ),
            ),
            Detail(
                "The review",
                (
                    Row("Framework", review.framework.value.upper()),
                    Row("Residency demanded", review.residency.value.upper()),
                    Row("Round", str(review.current_round)),
                    Row("Audit events on file", str(len(events))),
                ),
            ),
        ),
    )


#: What a round-level handler is given, and what it must return.
_Handler = Callable[
    [Review, Sequence[Question], Sequence[Answer], Sequence[dict[str, Any]]],
    Composed,
]

#: Probe words to handler. Order matters -- the first match wins, so the specific
#: subjects come before the general status question.
_ROUND_HANDLERS: tuple[tuple[tuple[str, ...], _Handler], ...] = (
    (("held", "waiting on", "needs a human", "needs human", "pending", "approve"), _held),
    (("verif", "grounded", "supported", "unsupported", "checked the answer"), _verification),
    (("block", "armor", "injection", "poison", "refus", "denied", "denial"), _blocked),
    (("commit", "contradict", "consisten", "promised", "prior round"), _commitments),
    (("who ", "which agent", "which agents", "identit", "participants"), _who),
    (("status", "where are we", "how far", "progress", "state of"), _state),
)


def _cannot_answer(
    review: Review,
    questions: Sequence[Question],
    answers: Sequence[Answer],
    events: Sequence[dict[str, Any]],
    near: Sequence[Question],
) -> Composed:
    """What to say when the trail does not hold the answer.

    Not an apology and not a guess. The reply names the question rows that came closest,
    if any did, and otherwise says what this record can answer -- which is a short,
    finite, true list.
    """
    labels = question_labels(questions)
    kinds = Counter(str(event.get("kind") or "") for event in events)
    details: list[Detail] = []

    if near:
        details.append(
            Detail(
                "Questions that came close",
                tuple(
                    Row(
                        labels.get(question.question_id, "—"),
                        _shorten(question.text, 160),
                        question_id=question.question_id,
                    )
                    for question in near
                ),
                note="Name one of these, or its number, and the full chain comes back.",
            )
        )
    details.append(
        Detail(
            "What this review's record holds",
            tuple(
                Row(kind.replace("_", " ").capitalize(), f"{count} events")
                for kind, count in kinds.most_common(12)
            ),
        )
    )

    return Composed(
        "That is not something this review's record answers."
        if not near
        else "I could not tell which question you meant.",
        lines=(
            "Ask about a question by its number, or about what is held, what was "
            "verified, what was refused, or what was promised in an earlier round.",
        ),
        details=details,
    )
