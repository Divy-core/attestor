"""What an export contains, decided once and rendered twice.

The XLSX and the PDF must agree. If the workbook says an answer may be sent and the evidence
pack says it is held for a human, one of them is lying to a customer — so the release
decision is made here, in one function, and both renderers read the result.

## Three tiers, not two

The first version of this module had two: approved by a human, or not. A test that walked
every ``AnswerStatus`` refused it, and the refusal was right for a better reason than the
missing enum member it actually caught.

``DRAFTED`` is the status ``ReviewPipeline`` assigns to an answer that retrieved supporting
passages, scored confidently against them, and did not contradict a prior commitment. It is
not "unreviewed and therefore suspect" — it is the *normal successful outcome*, and it is
the outcome the whole architecture exists to produce. Escalation to a human is what happens
when that fails. So a two-tier model would have told a customer that 189 of 189 answers were
unfit to send, which is not what the system determined about any of them.

The three tiers are: a human signed it; the system stands behind it because it cites its
sources; or it must not be sent, with the reason named. That is a description of what
Attestor does rather than a simplification of it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from attestor_core.domain import Answer, AnswerStatus, Question, Review, Round

#: Stated on the cover of both formats. The customer is told the rule rather than left to
#: infer it from a column of words.
RELEASE_RULE = (
    "Every question is listed and nothing is omitted. An answer marked approved was signed "
    "off by a named human. An answer marked drafted with citations is grounded in retrieved "
    "evidence and was not individually reviewed — the passages it relies on are listed "
    "beside it, with the document, section and relevance score, so the claim can be checked "
    "rather than trusted. An answer marked held, no evidence, quarantined or rejected must "
    "not be sent to a customer as an answer."
)


class ReleaseState(StrEnum):
    """Whether one answer may be sent to the customer, and on whose word.

    Deliberately coarser than ``AnswerStatus``: the customer does not need the internal state
    machine, they need to know what stands behind each answer. The mapping is total, so a
    status added later fails loudly rather than silently becoming sendable.
    """

    APPROVED = "Yes — approved by a named human"
    #: Cited, confident, not escalated. The pipeline's normal successful outcome.
    SYSTEM_BACKED = "Yes — drafted with citations, not individually reviewed"
    HELD = "No — held for a human"
    NO_EVIDENCE = "No — no supporting evidence found"
    QUARANTINED = "No — quarantined by the guardrail"
    REJECTED = "No — rejected by a human"
    UNANSWERED = "No — not answered in this round"
    #: A status that says the answer is fine, carrying no citations to show for it. The
    #: `Answer` validator forbids this shape, so reaching it means the validator was
    #: bypassed — and the export refuses to call it sendable rather than trusting the field.
    UNSUPPORTED = "No — claims support it does not have"

    @property
    def sendable(self) -> bool:
        """May this answer go back to the customer as an answer?"""
        return self in {ReleaseState.APPROVED, ReleaseState.SYSTEM_BACKED}

    @property
    def human_approved(self) -> bool:
        return self is ReleaseState.APPROVED


def release_state(answer: Answer | None) -> ReleaseState:
    """Map one answer onto what a customer may do with it.

    Reads the citations as well as the status, for the same reason the console's
    ``lib/states.ts`` does: an answer with no citations is never presented as supported,
    whatever its status field says. The status is a claim; the citations are the evidence for
    it, and where they disagree the evidence wins.

    Raises:
        ValueError: on an ``AnswerStatus`` this function does not handle. A new status that
            defaulted to sendable would put an unreviewed answer into a customer's hands
            under a signature nobody gave, so the failure is loud.
    """
    if answer is None:
        return ReleaseState.UNANSWERED

    match answer.status:
        case AnswerStatus.APPROVED:
            claimed = ReleaseState.APPROVED
        case AnswerStatus.DRAFT | AnswerStatus.DRAFTED | AnswerStatus.DELIVERED:
            claimed = ReleaseState.SYSTEM_BACKED
        case AnswerStatus.NEEDS_HUMAN:
            claimed = ReleaseState.HELD
        case AnswerStatus.FLAGGED_NO_EVIDENCE:
            claimed = ReleaseState.NO_EVIDENCE
        case AnswerStatus.QUARANTINED:
            claimed = ReleaseState.QUARANTINED
        case AnswerStatus.REJECTED:
            claimed = ReleaseState.REJECTED
        case _:  # pragma: no cover - exhaustive over the enum as it stands
            raise ValueError(f"no release mapping for answer status {answer.status!r}")

    if claimed.sendable and not answer.citations:
        return ReleaseState.UNSUPPORTED
    return claimed


@dataclass(frozen=True)
class ExportRow:
    """One question, its answer if it has one, and the release decision."""

    question: Question
    answer: Answer | None
    release: ReleaseState

    @property
    def citation_count(self) -> int:
        return len(self.answer.citations) if self.answer else 0

    @property
    def text(self) -> str:
        return self.answer.text.strip() if self.answer else ""


@dataclass
class ExportBundle:
    """Everything both renderers need, and nothing either of them has to look up.

    Built by ``build_bundle`` from repository reads. Neither renderer touches Firestore, GCS
    or the network, which is what makes them testable without credentials.
    """

    review: Review
    round_: Round
    rows: list[ExportRow]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Where this file was produced, shown on the cover. A ``.run.app`` origin in the footer
    #: of a returned spreadsheet is the least ceremonious possible proof of where the system
    #: runs.
    origin: str = ""

    @property
    def counts(self) -> dict[ReleaseState, int]:
        tally: dict[ReleaseState, int] = {}
        for row in self.rows:
            tally[row.release] = tally.get(row.release, 0) + 1
        return tally

    @property
    def cited(self) -> int:
        return sum(1 for row in self.rows if row.citation_count)

    @property
    def answered(self) -> int:
        return sum(1 for row in self.rows if row.answer is not None)

    @property
    def sendable(self) -> int:
        return sum(1 for row in self.rows if row.release.sendable)

    @property
    def human_approved(self) -> int:
        return sum(1 for row in self.rows if row.release.human_approved)

    def filename(self, extension: str) -> str:
        """A filename a person can find again in their downloads folder."""
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in self.review.customer)
        safe = "-".join(part for part in safe.split("-") if part).lower() or "review"
        return f"attestor-{safe}-{self.round_.round_id}.{extension}"


def build_bundle(
    review: Review,
    round_: Round,
    questions: Iterable[Question],
    answers: Iterable[Answer],
    *,
    origin: str = "",
) -> ExportBundle:
    """Join questions to answers in the questionnaire's own order.

    Ordering is by ``source_ref`` — sheet, then row — rather than by anything derived from
    our side. The customer sent a document with an order and expects it back in that order;
    question ids are content hashes and sort meaninglessly. Questions with no source
    reference sort last rather than being dropped.
    """
    by_question = {a.question_id: a for a in answers}

    def sort_key(question: Question) -> tuple[int, str, int]:
        reference = question.source_ref
        if reference is None or reference.row is None:
            return (1, "", 0)
        return (0, reference.sheet or "", reference.row)

    rows = [
        ExportRow(
            question=question,
            answer=by_question.get(question.question_id),
            release=release_state(by_question.get(question.question_id)),
        )
        for question in sorted(questions, key=sort_key)
    ]
    return ExportBundle(review=review, round_=round_, rows=rows, origin=origin)
