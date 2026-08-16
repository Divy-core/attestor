"""Wire contracts. These are frozen, so the tests are the freeze."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from attestor_core.domain.enums import ContradictionVerdict, Department, ReviewState
from attestor_core.domain.models import Review, Round
from attestor_core.errors import ContractViolation
from attestor_core.protocol import (
    EmptyPayload,
    EventType,
    IntakeDocumentPayload,
    WorkEnvelope,
    WorkKind,
    parse_payload,
)
from attestor_core.protocol.events import AttestorEvent, ConsistencyChecked


# ---------------------------------------------------------------------------------
# Amendment 1 -- ReviewState is a domain enum, validated at construction
# ---------------------------------------------------------------------------------
class TestReviewStateIsEnforcedAtConstruction:
    def test_invalid_review_state_raises_at_construction(self) -> None:
        """Not "eventually, if the machine happens to look at it"."""
        with pytest.raises(ValidationError):
            Review(review_id="rev1", customer="Acme", state="not_a_state")  # type: ignore[arg-type]

    def test_invalid_round_state_raises_at_construction(self) -> None:
        with pytest.raises(ValidationError):
            Round(round_id="r1", review_id="rev1", ordinal=1, state="banana")  # type: ignore[arg-type]

    def test_invalid_blocked_from_raises(self) -> None:
        with pytest.raises(ValidationError):
            Review(
                review_id="rev1",
                customer="Acme",
                state=ReviewState.BLOCKED,
                blocked_from="nowhere",  # type: ignore[arg-type]
            )

    def test_valid_state_is_the_enum_not_a_string(self) -> None:
        review = Review(review_id="rev1", customer="Acme", state=ReviewState.DRAFTING)
        assert review.state is ReviewState.DRAFTING

    def test_state_machine_and_domain_share_one_enum(self) -> None:
        """If these ever diverge, the machine is guarding a different type."""
        from attestor_core.state import ReviewState as MachineReviewState

        assert MachineReviewState is ReviewState


# ---------------------------------------------------------------------------------
# Amendment 2 -- the round-2 consistency beat is renderable
# ---------------------------------------------------------------------------------
class TestConsistencyEvents:
    def test_union_has_fourteen_variants(self) -> None:
        assert len(list(EventType)) == 14

    def test_commitment_recorded_round_trips(self) -> None:
        payload = {
            "type": "commitment_recorded",
            "review_id": "rev1",
            "run_id": "run1",
            "seq": 3,
            "commitment_id": "c" * 16,
            "question_id": "q" * 16,
            "statement": "Northwind does not offer on-premises or self-hosted deployment.",
            "round_ordinal": 1,
        }
        event = TypeAdapter(AttestorEvent).validate_python(payload)
        assert event.type is EventType.COMMITMENT_RECORDED

    def test_consistency_checked_round_trips(self) -> None:
        payload = {
            "type": "consistency_checked",
            "review_id": "rev1",
            "run_id": "run2",
            "seq": 9,
            "question_id": "q" * 16,
            "commitment_id": "c" * 16,
            "prior_statement": "Northwind does not offer on-premises deployment.",
            "prior_round_ordinal": 1,
            "verdict": "contradiction",
            "constrained": True,
        }
        event = TypeAdapter(AttestorEvent).validate_python(payload)
        assert isinstance(event, ConsistencyChecked)
        assert event.constrained is True
        assert event.verdict is ContradictionVerdict.CONTRADICTION

    def test_constrained_defaults_false(self) -> None:
        """ "We checked" is the default; "it mattered" must be asserted."""
        event = ConsistencyChecked(
            review_id="rev1",
            run_id="run2",
            seq=1,
            question_id="q" * 16,
            commitment_id="c" * 16,
            prior_statement="x",
            prior_round_ordinal=1,
            verdict=ContradictionVerdict.NO_CONTRADICTION,
        )
        assert event.constrained is False

    def test_discriminator_picks_the_right_variant(self) -> None:
        payload = {
            "type": "tool_denied",
            "review_id": "rev1",
            "run_id": "run1",
            "seq": 0,
            "agent": "SecurityAgent",
            "agent_department": "security",
            "tool_name": "search_corpus",
            "resource_ref": "corpus/legal/dpa.md",
            "decision": "deny",
            "reason": "cross-department",
        }
        event = TypeAdapter(AttestorEvent).validate_python(payload)
        assert event.agent_department is Department.SECURITY


# ---------------------------------------------------------------------------------
# Amendment 3 -- payloads are validated at both ends
# ---------------------------------------------------------------------------------
class TestPayloadValidation:
    def test_valid_intake_payload_parses(self) -> None:
        env = WorkEnvelope.for_work(
            message_id="m1",
            review_id="rev1",
            run_id="run1",
            kind=WorkKind.INTAKE_DOCUMENT,
            payload={"gcs_uri": "gs://bucket/q.xlsx"},
        )
        parsed = parse_payload(env)
        assert isinstance(parsed, IntakeDocumentPayload)
        assert parsed.gcs_uri == "gs://bucket/q.xlsx"

    def test_missing_required_field_fails_at_publish(self) -> None:
        """Not as a KeyError inside a worker three services away."""
        with pytest.raises(ContractViolation, match="IntakeDocumentPayload"):
            WorkEnvelope.for_work(
                message_id="m1",
                review_id="rev1",
                run_id="run1",
                kind=WorkKind.INTAKE_DOCUMENT,
                payload={},
            )

    def test_extra_field_on_a_no_payload_kind_fails(self) -> None:
        with pytest.raises(ContractViolation, match="EmptyPayload"):
            WorkEnvelope.for_work(
                message_id="m1",
                review_id="rev1",
                run_id="run1",
                kind=WorkKind.DRAFT_ANSWER,
                payload={"junk": 1},
            )

    def test_no_payload_kinds_parse_to_empty(self) -> None:
        env = WorkEnvelope.for_work(
            message_id="m1", review_id="rev1", run_id="run1", kind=WorkKind.CLOSE_ROUND
        )
        assert isinstance(parse_payload(env), EmptyPayload)

    def test_follow_up_ordinal_must_be_at_least_two(self) -> None:
        """Round 1 is the initial questionnaire; a follow-up starts at 2."""
        with pytest.raises(ContractViolation):
            WorkEnvelope.for_work(
                message_id="m1",
                review_id="rev1",
                run_id="run1",
                kind=WorkKind.OPEN_FOLLOW_UP,
                payload={"gcs_uri": "gs://b/r2.xlsx", "round_ordinal": 1},
            )

    def test_timer_payload_parses(self) -> None:
        env = WorkEnvelope.for_work(
            message_id="m1",
            review_id="rev1",
            run_id="run1",
            kind=WorkKind.TIMER_FIRED,
            payload={
                "timer_kind": "follow_up_due",
                "scheduled_for": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
            },
        )
        assert parse_payload(env).timer_kind == "follow_up_due"  # type: ignore[attr-defined]

    def test_parse_payload_rejects_a_hand_built_bad_envelope(self) -> None:
        """A message from an older/buggy producer is caught at the consume edge too."""
        env = WorkEnvelope(
            message_id="m1",
            dedup_key="0" * 16,
            review_id="rev1",
            run_id="run1",
            kind=WorkKind.INTAKE_DOCUMENT,
            payload={"wrong": "shape"},
        )
        with pytest.raises(ContractViolation):
            parse_payload(env)

    def test_every_work_kind_has_a_payload_model(self) -> None:
        from attestor_core.protocol import PAYLOAD_MODELS

        missing = [k for k in WorkKind if k not in PAYLOAD_MODELS]
        assert missing == [], f"WorkKind members with no payload model: {missing}"


class TestDedupKeyStability:
    def test_retries_share_a_key(self) -> None:
        """Attempt, run_id, and message_id must not perturb it."""
        first = WorkEnvelope.for_work(
            message_id="m1",
            review_id="rev1",
            run_id="runA",
            kind=WorkKind.DRAFT_ANSWER,
            round_id="r1",
            question_id="a" * 16,
        )
        retry = WorkEnvelope.for_work(
            message_id="m2",
            review_id="rev1",
            run_id="runB",
            kind=WorkKind.DRAFT_ANSWER,
            round_id="r1",
            question_id="a" * 16,
            attempt=4,
        )
        assert first.dedup_key == retry.dedup_key

    def test_different_questions_do_not_share_a_key(self) -> None:
        first = WorkEnvelope.for_work(
            message_id="m1",
            review_id="rev1",
            run_id="runA",
            kind=WorkKind.DRAFT_ANSWER,
            round_id="r1",
            question_id="a" * 16,
        )
        other = WorkEnvelope.for_work(
            message_id="m1",
            review_id="rev1",
            run_id="runA",
            kind=WorkKind.DRAFT_ANSWER,
            round_id="r1",
            question_id="b" * 16,
        )
        assert first.dedup_key != other.dedup_key


class TestPartitionedDedup:
    """ADR-0005. The bug this prevents is invisible when it happens.

    Drafting is partitioned by department, so three messages of one round share
    `review_id`, `round_id`, a null `question_id`, and `kind`. Before `partition` joined
    the key they produced ONE key, the dispatcher acked two of the three as redeliveries,
    and two thirds of the drafting work disappeared with no exception, no dead letter and
    no retry -- just a smaller number at the end of the run.
    """

    @staticmethod
    def _draft(department: str | None, **overrides: object) -> WorkEnvelope:
        kwargs: dict[str, object] = {
            "message_id": f"m-{department}",
            "review_id": "rev-acme-2026-q3",
            "run_id": "run-1",
            "kind": WorkKind.DRAFT_ANSWER,
            "round_id": "rnd-1",
            "partition": department,
        }
        kwargs.update(overrides)
        return WorkEnvelope.for_work(**kwargs)  # type: ignore[arg-type]

    def test_department_partitions_do_not_collide(self) -> None:
        keys = {d: self._draft(d).dedup_key for d in ("security", "legal", "engineering")}
        assert len(set(keys.values())) == 3, keys

    def test_a_redelivered_partition_still_collides(self) -> None:
        """The other half of the contract, and the easy one to break while fixing the
        first: a retry of ONE partition must still be recognised as the same work."""
        first = self._draft("security")
        retry = self._draft("security", message_id="m-retry", run_id="run-9", attempt=4)
        assert first.dedup_key == retry.dedup_key

    def test_partition_is_optional_and_defaults_to_none(self) -> None:
        """Unpartitioned kinds -- intake, assemble, close -- publish exactly as before."""
        envelope = WorkEnvelope.for_work(
            message_id="m1",
            review_id="rev1",
            run_id="runA",
            kind=WorkKind.ASSEMBLE_ROUND,
            round_id="r1",
        )
        assert envelope.partition is None

    def test_partitioned_and_unpartitioned_differ(self) -> None:
        """A `None` partition must not hash the same as the literal string 'None'."""
        unpartitioned = self._draft(None)
        partitioned = self._draft("none")
        assert unpartitioned.dedup_key != partitioned.dedup_key

    def test_partition_survives_a_round_trip(self) -> None:
        original = self._draft("legal")
        restored = WorkEnvelope.model_validate_json(original.model_dump_json())
        assert restored.partition == "legal"
        assert restored.dedup_key == original.dedup_key
