"""What a thread is made of.

Plain frozen dataclasses rather than pydantic models, because nothing here is validated
at a boundary — the projection constructs every one of these from data that has already
crossed one. They are serialised to JSON by the control plane with `as_dict`, and the
shapes are mirrored in `services/web/lib/types/thread.ts` by hand, which is stated in
that file rather than hidden: this is a *view* over the audit trail, not the frozen wire
protocol, and it is expected to change while the protocol does not.

## The shape is the design

A post is a **one-line summary** plus a set of **detail blocks** that are hidden until
someone asks for them. That is the whole answer to the problem Phase 8 exists to solve:
the previous interface put 312 question-and-answer rows on one page because it had no way
to be brief without also being unsupported. A collapsed post is brief; the same post
expanded is the retrieval, the passages, the scores, and the events. Nothing is summarised
away — it is one disclosure triangle out of reach.

## Every number here was counted, never asserted

`Post.summary` is composed from figures the projection counted out of the trail and out of
the answers collection. There is no field on this type that a model writes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

#: How many example rows a detail block may carry.
#:
#: A block that lists two hundred question ids is not evidence, it is the wall this phase
#: is removing. The projection lists the first `SAMPLE_CEILING` and says how many more
#: there are, so the count stays true while the block stays readable.
SAMPLE_CEILING = 8


@dataclass(frozen=True)
class Row:
    """One labelled fact inside a detail block."""

    label: str
    value: str
    #: Set when the value is machine output — an id, a score, a resource name, a URI.
    #: The interface renders those in monospace, and prose in monospace is unreadable.
    mono: bool = False
    #: A question this row points at. The interface turns it into a jump to the grid.
    question_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "mono": self.mono,
            "question_id": self.question_id,
        }


@dataclass(frozen=True)
class Detail:
    """One block of the expansion: a heading, some rows, and an optional caveat.

    `note` is for what the rows cannot say on their own — most often that a figure is
    bounded ("the first eight of forty-three") or that a check did not run. It is never
    used for explanatory prose about how the system works.
    """

    heading: str
    rows: tuple[Row, ...] = ()
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "rows": [row.as_dict() for row in self.rows],
            "note": self.note,
        }


#: What an inline control in a post does. Approvals happen in the thread, so the thread
#: has to be able to carry a control rather than a link to a page that carries one.
ActionKind = Literal["approve", "questions", "artifacts", "export", "connect_gmail"]


@dataclass(frozen=True)
class Action:
    """A control rendered inside a post. Never a link to somewhere else to do the work."""

    kind: ActionKind
    label: str
    count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "count": self.count}


@dataclass(frozen=True)
class Progress:
    """A live counter on a working post: `82 · 45 · 38 answered`.

    `total` is what the partition was given, `done` is what it has produced. Both are
    counted from the answers collection rather than from events, because an event stream
    that dropped a write would under-count and the number on screen is the one a viewer
    is watching move.
    """

    label: str
    done: int
    total: int

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "done": self.done, "total": self.total}


@dataclass(frozen=True)
class Post:
    """One participant saying one thing, with its evidence folded underneath.

    `actor` is the agent's own name, because the thread's premise is that the fleet posts
    as itself. `Orchestrator`, `SecurityAgent`, `VerifierAgent` — and a person's name for
    the posts a person made, which is the same field and deliberately so.
    """

    post_id: str
    actor: str
    #: Coarse category, used by the interface for the actor's mark and for filtering.
    #: Not a status: a post is a record of something that happened.
    kind: str
    at: str
    summary: str
    #: Extra summary lines, shown while collapsed. Kept to two at most by the projection.
    lines: tuple[str, ...] = ()
    details: tuple[Detail, ...] = ()
    progress: tuple[Progress, ...] = ()
    actions: tuple[Action, ...] = ()
    #: Set while the work this post describes is still running.
    working: bool = False
    #: The last event folded into this post, when it aggregates more than one.
    through: str | None = None
    #: How many audit events this post stands for. `1` for a post about one event.
    events: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "actor": self.actor,
            "kind": self.kind,
            "at": self.at,
            "summary": self.summary,
            "lines": list(self.lines),
            "details": [detail.as_dict() for detail in self.details],
            "progress": [p.as_dict() for p in self.progress],
            "actions": [a.as_dict() for a in self.actions],
            "working": self.working,
            "through": self.through,
            "events": self.events,
        }


def kept(details: Iterable[Detail]) -> tuple[Detail, ...]:
    """Drop blocks with nothing in them.

    A heading over zero rows is the same defect as a filter chip reading `Denied 0`: a
    control, or here a disclosure, that cannot do anything, sitting beside ones that can.
    Every composer in this package produces its blocks unconditionally and then passes
    them through here, which is one rule in one place rather than an `if` at every site.
    """
    return tuple(detail for detail in details if detail.rows or detail.note)


@dataclass(frozen=True)
class Thread:
    """The projection's whole output for one review."""

    review_id: str
    posts: tuple[Post, ...] = ()
    #: Set when the audit read hit its ceiling. The counts a post quotes come from the
    #: answers collection and stay exact; what degrades is the narrative, and saying so
    #: is better than a thread that silently describes part of a run as all of it.
    truncated: bool = False
    #: Events read, so the figure above is checkable rather than a flag.
    events_read: int = 0
    participants: tuple[str, ...] = field(default_factory=tuple)
    #: The run whose event stream this thread should watch, read off the trail.
    #:
    #: On the thread rather than on the round because a run id is not a property of a
    #: round -- it is on the events a run wrote. Carried here so the page needs one read
    #: instead of two: it was fetching the whole audit trail a second time purely to pick
    #: this string out of it.
    run_id: str | None = None
    #: Whether this review came in on an email thread, and can therefore be replied to.
    arrived_by_email: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "posts": [post.as_dict() for post in self.posts],
            "truncated": self.truncated,
            "events_read": self.events_read,
            "participants": list(self.participants),
            "run_id": self.run_id,
            "arrived_by_email": self.arrived_by_email,
        }
