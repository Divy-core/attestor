"""The one path whose effect leaves the building.

Every other stage writes to Firestore, publishes a message, or calls a model. This one sends
an email to a person outside the company, and there is no undo. What is pinned here is the
set of ways that could happen when it should not, or happen without a record of who allowed
it:

* an envelope with no named human on it;
* a send to a review that never arrived by email, and therefore to an invented recipient;
* the pack reaching a customer before Attestor has a copy of what it sent;
* a delivery attributed to "Dispatcher" rather than to the person who authorised it.

The last one is the quietest and the most damaging. "Who authorised sending this, and when"
is the single most audit-relevant fact this system produces.
"""

from __future__ import annotations

from typing import Any

import pytest

from attestor_core.domain import Answer, Citation, Question, Review, Round
from attestor_core.domain.enums import (
    AnswerStatus,
    Confidence,
    Department,
    Framework,
    Residency,
    ReviewState,
)
from attestor_core.errors import ContractViolation
from attestor_core.protocol import WorkEnvelope, WorkKind
from attestor_platform.drive.client import folder_name_for
from dispatcher.handlers import HandlerRegistry, _covering_note

REVIEW = Review(
    review_id="rev-northwind",
    customer="Northwind Traders",
    framework=Framework.CAIQ,
    residency=Residency.US,
    current_round=1,
    state=ReviewState.AWAITING_HUMAN,
)
ROUND = Round(
    round_id="rev-northwind-r1",
    review_id="rev-northwind",
    ordinal=1,
    state=ReviewState.AWAITING_HUMAN,
)


def _question(text: str) -> Question:
    return Question.from_text(text, department=Department.SECURITY)


def _answer(question: Question, status: AnswerStatus = AnswerStatus.DRAFTED) -> Answer:
    return Answer(
        question_id=question.question_id,
        round_id=ROUND.round_id,
        text="Yes, all customer data is encrypted at rest.",
        citations=[
            Citation(
                document_uri="gs://corpus/security/encryption.md",
                document_title="Encryption Policy",
                section="3.1",
                snippet="All customer data is encrypted at rest.",
                retrieval_score=0.84,
            )
        ],
        confidence=Confidence.HIGH,
        status=status,
        authored_by="SecurityAgent",
    )


# ---------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------


class _FakeDrive:
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, int, str]] = []
        self.folders: list[str] = []

    def folder_for_customer(self, customer: str) -> str:
        self.folders.append(customer)
        return "folder-1"

    def upload(self, name: str, payload: bytes, mime_type: str, parent: str | None = None) -> Any:
        del parent
        self.uploaded.append((name, len(payload), mime_type))
        return type(
            "DriveFile",
            (),
            {
                "file_id": f"drive-{len(self.uploaded)}",
                "name": name,
                "mime_type": mime_type,
                "web_view_link": f"https://drive.example/{len(self.uploaded)}",
                "size_bytes": len(payload),
                "as_detail": lambda self: {"file_id": self.file_id, "name": self.name},
            },
        )()


class _FakeGmail:
    def __init__(self, fail: bool = False) -> None:
        self.address = "trust@attestor.example"
        self.sent: list[dict[str, Any]] = []
        self._fail = fail

    def send_reply(self, **kwargs: Any) -> str:
        if self._fail:
            raise RuntimeError("gmail unavailable")
        self.sent.append(kwargs)
        return "sent-1"


class _FakeInboxState:
    def __init__(self, thread: dict[str, Any] | None) -> None:
        self._thread = thread

    def thread_for_review(self, review_id: str) -> dict[str, Any] | None:
        del review_id
        return self._thread


class _FakeArtifacts:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def put(self, review_id: str, round_id: str, kind: str, **fields: Any) -> None:
        self.rows.append({"review_id": review_id, "round_id": round_id, "kind": kind, **fields})


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_safe(self, event: dict[str, Any]) -> str:
        self.events.append(event)
        return "evt"


class _Repo:
    def __init__(self, value: Any) -> None:
        self._value = value
        self.written: list[Any] = []

    def get(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return self._value

    def put(self, value: Any, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.written.append(value)
        self._value = value


class _Rounds(_Repo):
    def for_review(self, review_id: str) -> list[Round]:
        del review_id
        return [ROUND]


class _Questions:
    def __init__(self, questions: list[Question]) -> None:
        self._questions = questions

    def for_round(self, round_id: str) -> list[Question]:
        del round_id
        return self._questions


class _Answers:
    def __init__(self, answers: list[Answer]) -> None:
        self._answers = answers

    def for_round(self, round_id: str) -> list[Answer]:
        del round_id
        return self._answers


def _registry(
    *,
    thread: dict[str, Any] | None = None,
    gmail: _FakeGmail | None = None,
    source: str | None = None,
) -> tuple[HandlerRegistry, dict[str, Any]]:
    question = _question("Do you encrypt customer data at rest?")
    parts = {
        "reviews": _Repo(REVIEW),
        "rounds": _Rounds(ROUND),
        "questions": _Questions([question]),
        "answers": _Answers([_answer(question)]),
        "audit": _FakeAudit(),
        "publisher": type("P", (), {"publish": lambda self, e: e.dedup_key})(),
        "round_sources": _Repo(source),
        "inbox_state": _FakeInboxState(
            thread
            if thread is not None
            else {"thread_id": "thr-1", "sender": "procurement@northwind.example"}
        ),
        "artifacts": _FakeArtifacts(),
        "drive": _FakeDrive(),
        "gmail": gmail or _FakeGmail(),
    }
    return HandlerRegistry(**parts), parts  # type: ignore[arg-type]


def _envelope(approved_by: str = "divy@attestor.example", note: str = "") -> WorkEnvelope:
    return WorkEnvelope.for_work(
        message_id="m-1",
        review_id=REVIEW.review_id,
        run_id="deliver-1",
        round_id=ROUND.round_id,
        kind=WorkKind.DELIVER_PACK,
        payload={"approved_by": approved_by, "note": note},
    )


# ---------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------


class TestTheHumanGate:
    @pytest.mark.parametrize("approved_by", ["", "   "])
    def test_an_envelope_without_a_named_human_cannot_be_constructed(
        self, approved_by: str
    ) -> None:
        """Structural, not procedural.

        `approved_by` is `Field(min_length=1)` on the payload model, so there is no code path
        in which the handler runs unapproved -- because there is no envelope. The same
        reasoning that put the citation requirement in `Answer`'s validator rather than in a
        prompt.
        """
        with pytest.raises(ContractViolation):
            WorkEnvelope.for_work(
                message_id="m",
                review_id=REVIEW.review_id,
                run_id="r",
                kind=WorkKind.DELIVER_PACK,
                payload={"approved_by": approved_by} if approved_by else {},
            )

    def test_the_audit_event_names_the_person_not_the_service(self) -> None:
        registry, parts = _registry()
        registry.deliver_pack(_envelope(approved_by="divy@attestor.example"))
        delivered = [e for e in parts["audit"].events if e["kind"] == "pack_delivered"]
        assert len(delivered) == 1
        assert delivered[0]["actor"] == "divy@attestor.example"
        # And it is not attributed to the machinery that carried it out.
        assert delivered[0]["actor"] != "Dispatcher"


class TestTheRefusals:
    def test_a_review_that_did_not_arrive_by_email_has_nobody_to_reply_to(self) -> None:
        """Permanent, so it dead-letters. No retry conjures a thread."""
        registry, parts = _registry(thread={})
        with pytest.raises(ContractViolation) as caught:
            registry.deliver_pack(_envelope())
        assert "no email thread" in str(caught.value)
        assert parts["gmail"].sent == []

    def test_nothing_is_sent_when_there_is_no_thread(self) -> None:
        registry, parts = _registry(thread=None if False else {})
        with pytest.raises(ContractViolation):
            registry.deliver_pack(_envelope())
        assert parts["drive"].uploaded == []


class TestOrdering:
    def test_drive_is_written_before_the_email_is_sent(self) -> None:
        """The one ordering that matters, and why.

        If the upload fails, nothing has been sent and the message is retried. If the send
        fails after the upload, the retry re-uploads and re-sends. The reverse order has a
        state in which the customer has the pack and we have no record of what we sent them,
        which is the one outcome a compliance system may not have.
        """
        gmail = _FakeGmail(fail=True)
        registry, parts = _registry(gmail=gmail)
        with pytest.raises(RuntimeError):
            registry.deliver_pack(_envelope())
        # The send failed; the copy exists anyway.
        assert parts["drive"].uploaded
        assert parts["artifacts"].rows
        assert gmail.sent == []

    def test_the_evidence_pack_is_attached_even_without_the_customer_workbook(self) -> None:
        # No `round_sources` record, so the customer's own file cannot be re-opened. The
        # evidence pack needs no source file, so the delivery still happens -- with one
        # attachment rather than two, and never with a zero-length spreadsheet.
        registry, parts = _registry(source=None)
        registry.deliver_pack(_envelope())
        attachments = parts["gmail"].sent[0]["attachments"]
        assert len(attachments) == 1
        assert attachments[0][0].endswith(".pdf")
        assert len(attachments[0][2]) > 0


class TestWhatTheCustomerReads:
    def test_the_note_states_the_numbers_before_the_attachment_is_opened(self) -> None:
        """A covering note that says "please find attached" makes the recipient open a
        312-row spreadsheet to discover that 43 rows need a conversation."""
        bundle = type(
            "Bundle",
            (),
            {"rows": list(range(312)), "sendable": 269, "human_approved": 41},
        )()
        note = _covering_note(REVIEW, bundle, "")
        assert "269 of 312" in note
        assert "41 were reviewed and approved by a named person" in note
        assert "43 are not included as answers" in note

    def test_an_operator_note_is_appended_not_substituted(self) -> None:
        bundle = type(
            "Bundle", (), {"rows": list(range(10)), "sendable": 10, "human_approved": 10}
        )()
        note = _covering_note(REVIEW, bundle, "Happy to walk through any of these on a call.")
        assert "10 of 10" in note
        assert note.rstrip().endswith("Happy to walk through any of these on a call.")

    def test_the_thread_and_recipient_come_from_the_binding_not_from_the_payload(self) -> None:
        # Otherwise an envelope could name its own recipient, and the approval a human gave
        # would be for a different email than the one that went out.
        registry, parts = _registry()
        registry.deliver_pack(_envelope())
        sent = parts["gmail"].sent[0]
        assert sent["thread_id"] == "thr-1"
        assert sent["to"] == "procurement@northwind.example"


class TestDriveFolderNames:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Northwind Traders", "Northwind Traders"),
            ("O'Brien & Co", "O Brien Co"),
            ("' or '1'='1", "or 1 1"),
            ("", "Unknown customer"),
        ],
    )
    def test_a_customer_name_cannot_break_the_drive_query(self, raw: str, expected: str) -> None:
        """The name arrived in an email. A quote terminates the `q=` expression, so the input
        is reduced rather than escaped -- escaping a query language by hand is how injection
        gets in."""
        assert folder_name_for(raw) == expected


class TestTheApprovalRequest:
    """A durable pause is only a feature if somebody finds out about it.

    Before this, a review stopped at `awaiting_human` and stayed there until a person
    happened to open the console -- which is the "nobody logs into a dashboard to check
    whether their questionnaire is done" problem the phase brief opens with, reproduced
    inside our own product.
    """

    def _registry_for_pause(self, gmail: _FakeGmail) -> tuple[HandlerRegistry, _FakeAudit]:
        question = _question("Do you encrypt customer data at rest?")
        audit = _FakeAudit()
        registry = HandlerRegistry(
            # DRAFTING, not AWAITING_HUMAN: `assemble_round` is what performs that move, and
            # a fixture already in the target state makes the transition illegal.
            reviews=_Repo(REVIEW.model_copy(update={"state": ReviewState.DRAFTING})),  # type: ignore[arg-type]
            rounds=_Rounds(ROUND),  # type: ignore[arg-type]
            questions=_Questions([question]),  # type: ignore[arg-type]
            answers=_Answers([_answer(question, AnswerStatus.NEEDS_HUMAN)]),  # type: ignore[arg-type]
            audit=audit,  # type: ignore[arg-type]
            publisher=type("P", (), {"publish": lambda self, e: e.dedup_key})(),  # type: ignore[arg-type]
            gmail=gmail,  # type: ignore[arg-type]
        )
        return registry, audit

    def _assemble(self) -> WorkEnvelope:
        return WorkEnvelope.for_work(
            message_id="m",
            review_id=REVIEW.review_id,
            run_id="run-1",
            round_id=ROUND.round_id,
            kind=WorkKind.ASSEMBLE_ROUND,
        )

    def test_pausing_for_a_human_emails_the_human(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATTESTOR_CONSOLE_URL", "https://attestor.example")
        gmail = _FakeGmail()
        registry, audit = self._registry_for_pause(gmail)
        result = registry.assemble_round(self._assemble())

        assert result.detail["awaiting_human"] == 1
        assert len(gmail.sent) == 1
        sent = gmail.sent[0]
        # To ourselves, not to the customer. Mailing the customer to say their questionnaire
        # needs internal review would be a different and much worse email.
        assert sent["to"] == gmail.address
        assert "need review" in sent["subject"]
        assert "https://attestor.example/reviews/rev-northwind?view=queue" in sent["body"]
        assert [e["kind"] for e in audit.events if e["kind"] == "approval_requested"]

    def test_it_is_a_new_message_and_not_a_reply_to_a_thread(self) -> None:
        # The internal request has no thread. Gmail rejects an empty `threadId`, so the
        # client omits it -- and this asserts the handler asks for that shape.
        gmail = _FakeGmail()
        registry, _ = self._registry_for_pause(gmail)
        registry.assemble_round(self._assemble())
        assert gmail.sent[0]["thread_id"] == ""

    def test_a_mail_failure_does_not_fail_the_pause(self) -> None:
        """The pause is the product working. A notification is a convenience on top of it."""
        gmail = _FakeGmail(fail=True)
        registry, audit = self._registry_for_pause(gmail)
        result = registry.assemble_round(self._assemble())

        assert result.state is ReviewState.AWAITING_HUMAN
        assert result.detail["awaiting_human"] == 1
        # And nothing claims the request was sent.
        assert not [e for e in audit.events if e["kind"] == "approval_requested"]
