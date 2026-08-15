"""The review state machine.

Illegal transitions raise. They never warn and they are never silently allowed: the
audit trail is the product here, and a state graph that quietly accepts nonsense
produces an audit trail nobody can defend six months later.

Pure stdlib. The transition table is an explicit frozenset of ordered pairs rather
than a graph built at import time, so the legal edges can be read at a glance and
diffed in review.
"""

from __future__ import annotations

from enum import StrEnum

from attestor_core.errors import IllegalTransition


class ReviewState(StrEnum):
    """Where a review is in its lifecycle.

    The happy path::

        intake -> triaging -> drafting -> awaiting_evidence -> awaiting_human
               -> assembling -> delivered -> follow_up -> triaging (round N+1)
    """

    INTAKE = "intake"
    TRIAGING = "triaging"
    DRAFTING = "drafting"
    AWAITING_EVIDENCE = "awaiting_evidence"
    AWAITING_HUMAN = "awaiting_human"
    ASSEMBLING = "assembling"
    DELIVERED = "delivered"
    FOLLOW_UP = "follow_up"

    #: Recoverable halt. Remembers where it came from and can return there.
    BLOCKED = "blocked"
    #: Terminal. Nothing resumes from here.
    FAILED = "failed"


#: States from which nothing may proceed.
TERMINAL_STATES: frozenset[ReviewState] = frozenset({ReviewState.FAILED})

#: The happy path plus the loops back into it, written out edge by edge.
_HAPPY_PATH: frozenset[tuple[ReviewState, ReviewState]] = frozenset(
    {
        (ReviewState.INTAKE, ReviewState.TRIAGING),
        (ReviewState.TRIAGING, ReviewState.DRAFTING),
        (ReviewState.DRAFTING, ReviewState.AWAITING_EVIDENCE),
        (ReviewState.AWAITING_EVIDENCE, ReviewState.DRAFTING),
        (ReviewState.DRAFTING, ReviewState.AWAITING_HUMAN),
        (ReviewState.AWAITING_EVIDENCE, ReviewState.AWAITING_HUMAN),
        (ReviewState.DRAFTING, ReviewState.ASSEMBLING),
        (ReviewState.AWAITING_HUMAN, ReviewState.DRAFTING),
        (ReviewState.AWAITING_HUMAN, ReviewState.ASSEMBLING),
        (ReviewState.ASSEMBLING, ReviewState.DELIVERED),
        (ReviewState.DELIVERED, ReviewState.FOLLOW_UP),
        (ReviewState.FOLLOW_UP, ReviewState.TRIAGING),
    }
)

#: Any non-terminal state may be blocked or failed. Generated rather than written out,
#: because writing 18 near-identical pairs by hand invites an omission.
_EXCEPTIONAL: frozenset[tuple[ReviewState, ReviewState]] = frozenset(
    {(s, ReviewState.BLOCKED) for s in ReviewState if s not in TERMINAL_STATES}
    | {(s, ReviewState.FAILED) for s in ReviewState if s not in TERMINAL_STATES}
) - {(ReviewState.BLOCKED, ReviewState.BLOCKED)}

#: Recovery out of BLOCKED. Which state is legal depends on where it was blocked from,
#: so `transition` checks that dynamically; this permits the edge in principle.
_UNBLOCK: frozenset[tuple[ReviewState, ReviewState]] = frozenset(
    {(ReviewState.BLOCKED, s) for s in ReviewState if s not in TERMINAL_STATES}
) - {(ReviewState.BLOCKED, ReviewState.BLOCKED)}

#: The complete set of legal edges.
LEGAL_TRANSITIONS: frozenset[tuple[ReviewState, ReviewState]] = (
    _HAPPY_PATH | _EXCEPTIONAL | _UNBLOCK
)


def is_legal(current: ReviewState, target: ReviewState) -> bool:
    """Return whether ``current -> target`` is a permitted edge.

    Note this does not consider ``blocked_from``; ``transition`` applies that
    additional constraint.
    """
    return (current, target) in LEGAL_TRANSITIONS


def legal_targets(current: ReviewState) -> frozenset[ReviewState]:
    """Every state reachable from ``current`` in one step."""
    return frozenset(target for source, target in LEGAL_TRANSITIONS if source is current)


def transition(
    current: ReviewState,
    target: ReviewState,
    *,
    blocked_from: ReviewState | None = None,
    review_id: str | None = None,
) -> ReviewState:
    """Move from ``current`` to ``target``, or raise.

    Args:
        current: The state the review is in now.
        target: The state being moved to.
        blocked_from: Required when leaving ``BLOCKED``. A blocked review may only
            resume into the state it was blocked from -- otherwise "blocked" becomes a
            universal escape hatch that can teleport a review anywhere, which defeats
            the point of having a state machine.
        review_id: Correlation id, attached to the raised error.

    Returns:
        ``target``, for convenient chaining.

    Raises:
        IllegalTransition: If the edge is not permitted.
    """
    if current in TERMINAL_STATES:
        raise IllegalTransition(
            f"{current.value!r} is terminal; no transition out of it is legal",
            review_id=review_id,
            current_state=current.value,
            target_state=target.value,
        )

    if not is_legal(current, target):
        allowed = sorted(s.value for s in legal_targets(current))
        raise IllegalTransition(
            f"illegal transition {current.value!r} -> {target.value!r}; "
            f"legal targets are {allowed}",
            review_id=review_id,
            current_state=current.value,
            target_state=target.value,
        )

    if (
        current is ReviewState.BLOCKED
        and target not in {ReviewState.FAILED, ReviewState.BLOCKED}
        and blocked_from is not None
        and target is not blocked_from
    ):
        raise IllegalTransition(
            f"a blocked review may only resume into {blocked_from.value!r}, not {target.value!r}",
            review_id=review_id,
            current_state=current.value,
            target_state=target.value,
            blocked_from=blocked_from.value,
        )

    return target
