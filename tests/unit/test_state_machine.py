"""Every legal transition, and a representative sample of illegal ones.

Targets 100% branch coverage on `attestor_core.state.machine`.
"""

from __future__ import annotations

import pytest

from attestor_core.errors import IllegalTransition
from attestor_core.state import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    ReviewState,
    is_legal,
    legal_targets,
    transition,
)

S = ReviewState

HAPPY_PATH = [
    (S.INTAKE, S.TRIAGING),
    (S.TRIAGING, S.DRAFTING),
    (S.DRAFTING, S.AWAITING_EVIDENCE),
    (S.AWAITING_EVIDENCE, S.DRAFTING),
    (S.DRAFTING, S.AWAITING_HUMAN),
    (S.AWAITING_EVIDENCE, S.AWAITING_HUMAN),
    (S.DRAFTING, S.ASSEMBLING),
    (S.AWAITING_HUMAN, S.DRAFTING),
    (S.AWAITING_HUMAN, S.ASSEMBLING),
    (S.ASSEMBLING, S.DELIVERED),
    (S.DELIVERED, S.FOLLOW_UP),
    (S.FOLLOW_UP, S.TRIAGING),
]

ILLEGAL_SAMPLE = [
    (S.INTAKE, S.DELIVERED),  # cannot skip the entire pipeline
    (S.INTAKE, S.DRAFTING),  # must triage first
    (S.TRIAGING, S.DELIVERED),
    (S.DELIVERED, S.DRAFTING),  # a delivered round is closed; round 2 is a new round
    (S.DELIVERED, S.ASSEMBLING),
    (S.ASSEMBLING, S.TRIAGING),
    (S.FOLLOW_UP, S.DELIVERED),
    (S.DRAFTING, S.INTAKE),  # no going back to intake
    (S.ASSEMBLING, S.AWAITING_EVIDENCE),
]


class TestLegalEdges:
    @pytest.mark.parametrize(("src", "dst"), HAPPY_PATH)
    def test_happy_path_edges_are_legal(self, src: ReviewState, dst: ReviewState) -> None:
        assert is_legal(src, dst)
        assert transition(src, dst) is dst

    @pytest.mark.parametrize("src", [s for s in ReviewState if s not in TERMINAL_STATES])
    def test_any_non_terminal_state_can_block(self, src: ReviewState) -> None:
        if src is S.BLOCKED:
            pytest.skip("blocked -> blocked is deliberately excluded")
        assert transition(src, S.BLOCKED) is S.BLOCKED

    @pytest.mark.parametrize("src", [s for s in ReviewState if s not in TERMINAL_STATES])
    def test_any_non_terminal_state_can_fail(self, src: ReviewState) -> None:
        assert transition(src, S.FAILED) is S.FAILED

    def test_blocked_to_blocked_is_illegal(self) -> None:
        assert not is_legal(S.BLOCKED, S.BLOCKED)


class TestIllegalEdges:
    @pytest.mark.parametrize(("src", "dst"), ILLEGAL_SAMPLE)
    def test_illegal_edges_raise(self, src: ReviewState, dst: ReviewState) -> None:
        assert not is_legal(src, dst)
        with pytest.raises(IllegalTransition, match="illegal transition"):
            transition(src, dst)

    def test_error_lists_the_legal_targets(self) -> None:
        with pytest.raises(IllegalTransition) as exc:
            transition(S.INTAKE, S.DELIVERED)
        assert "legal targets are" in str(exc.value)
        assert "triaging" in str(exc.value)

    def test_error_carries_correlation_context(self) -> None:
        with pytest.raises(IllegalTransition) as exc:
            transition(S.INTAKE, S.DELIVERED, review_id="rev-42")
        assert exc.value.review_id == "rev-42"
        assert exc.value.context["current_state"] == "intake"
        assert exc.value.context["target_state"] == "delivered"


class TestTerminal:
    def test_failed_is_terminal(self) -> None:
        assert S.FAILED in TERMINAL_STATES

    @pytest.mark.parametrize("dst", list(ReviewState))
    def test_nothing_leaves_failed(self, dst: ReviewState) -> None:
        with pytest.raises(IllegalTransition, match="terminal"):
            transition(S.FAILED, dst)


class TestUnblocking:
    def test_blocked_resumes_into_the_state_it_came_from(self) -> None:
        assert transition(S.BLOCKED, S.DRAFTING, blocked_from=S.DRAFTING) is S.DRAFTING

    def test_blocked_cannot_teleport_elsewhere(self) -> None:
        """Otherwise `blocked` becomes a universal escape hatch."""
        with pytest.raises(IllegalTransition, match="may only resume into"):
            transition(S.BLOCKED, S.DELIVERED, blocked_from=S.DRAFTING)

    def test_blocked_may_always_fail(self) -> None:
        assert transition(S.BLOCKED, S.FAILED, blocked_from=S.DRAFTING) is S.FAILED

    def test_blocked_without_origin_permits_any_legal_target(self) -> None:
        """When we did not record where it blocked, we cannot enforce the return."""
        assert transition(S.BLOCKED, S.ASSEMBLING) is S.ASSEMBLING


class TestTable:
    def test_legal_targets_matches_the_table(self) -> None:
        for state in ReviewState:
            expected = {dst for src, dst in LEGAL_TRANSITIONS if src is state}
            assert legal_targets(state) == expected

    def test_round_two_loop_is_closed(self) -> None:
        """delivered -> follow_up -> triaging is what makes round N+1 possible."""
        assert transition(S.DELIVERED, S.FOLLOW_UP) is S.FOLLOW_UP
        assert transition(S.FOLLOW_UP, S.TRIAGING) is S.TRIAGING
