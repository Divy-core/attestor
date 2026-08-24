"""Turning a line a person typed into work the fleet will actually do.

## Why this is a parser and not a model call

`attestor_platform.thread.answering` refuses to call a model because an answer about the
audit trail has to be checkable. The same argument applies twice as hard here, for a
different reason: this side does not answer, it **acts**. A fuzzy classifier that is right
95% of the time is a system that emails a customer without being asked one time in twenty.

So a command is recognised by a pattern or it is not recognised at all. Nothing is inferred
from resemblance, no threshold is tuned, and text that matches nothing falls through to the
answering path, where the worst outcome is a reply saying the record does not hold that.

## The two tiers

`Command.irreversible` marks the ones whose effect leaves the building. Those require a
named person and a second call carrying an explicit confirmation, and the control plane
refuses them without one. Everything else dispatches on the first call.

Two things follow from that flag and both are load-bearing. Irreversible commands are
matched **only** on a full, unambiguous phrase -- "send the pack", not "send" -- and they
are excluded from anything that resolves by similarity, so no palette match and no
near-miss can ever reach one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Action(StrEnum):
    """What the fleet is being asked to do.

    Three, and the shortlist was cut by the protocol rather than by taste. `WorkKind` is
    frozen and its payloads use `extra="forbid"`, so a command can only exist here if some
    existing kind can carry it:

    * **A follow-up round** needs `OpenFollowUpPayload.gcs_uri`. A round two *is* a second
      questionnaire, so there is nothing to open without one -- which is why attaching a
      file to an open conversation opens the next round, and no text command does.
    * **Filing to Drive alone** has no kind. `DELIVER_PACK` writes to Drive *and* emails,
      and a command called "file this to Drive" that also emailed a customer would be the
      worst kind of surprise this system could produce.
    """

    #: Reply to the customer on the thread the questionnaire arrived on, with the pack
    #: attached and a copy written to Drive. Leaves the building.
    SEND_PACK = "send_pack"
    #: Draft one named question again, from scratch.
    REDRAFT = "redraft"
    #: Build the workbook and the evidence pack. Produces files, sends nothing.
    EXPORT = "export"


@dataclass(frozen=True)
class Command:
    """One recognised instruction, with everything the dispatcher needs to act on it."""

    action: Action
    #: Exactly what the person typed. Recorded on the trail verbatim.
    text: str
    #: The question this command names, when it names one. `redraft` requires it.
    question_id: str | None = None
    #: A human handle for that question, for the confirmation prompt.
    question_label: str = ""

    @property
    def irreversible(self) -> bool:
        """Whether the effect leaves the building and cannot be taken back."""
        return self.action in _IRREVERSIBLE

    @property
    def prompt(self) -> str:
        """What the person is asked to confirm, in the terms of the effect."""
        return _PROMPTS[self.action].format(question=self.question_label or "that question")

    def as_detail(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "text": self.text,
            "question_id": self.question_id,
            "question_label": self.question_label,
            "irreversible": self.irreversible,
        }


#: The effects that reach a customer. Everything here needs a name and a confirmation.
_IRREVERSIBLE: frozenset[Action] = frozenset({Action.SEND_PACK})

_PROMPTS: dict[Action, str] = {
    Action.SEND_PACK: (
        "This emails the customer on the thread the questionnaire arrived on, with the "
        "workbook and the evidence pack attached, and writes a copy to Drive. It cannot "
        "be recalled."
    ),
    Action.REDRAFT: "This drafts {question} again from scratch, replacing the current answer.",
    Action.EXPORT: "This builds the workbook and the evidence pack.",
}


#: Patterns, in order. First match wins, and every one is anchored on a verb.
#:
#: Deliberately literal. "send" alone does not match `SEND_PACK` -- the phrase has to name
#: what is being sent, so that a half-typed line or a stray word cannot reach the one action
#: that emails somebody.
_PATTERNS: tuple[tuple[re.Pattern[str], Action], ...] = (
    (
        re.compile(r"^\s*send\s+(?:the\s+|this\s+)?(?:pack|answers|workbook|reply)\b", re.I),
        Action.SEND_PACK,
    ),
    (re.compile(r"^\s*(?:reply|respond)\s+to\s+the\s+customer\b", re.I), Action.SEND_PACK),
    # No `answer` here, though it reads like a synonym. "answer Q5" is an instruction and
    # "what did we answer for Q5" is a question, and the difference between them is not
    # something a prefix match can see. Losing a synonym costs a person one word; getting
    # it wrong throws away a drafted answer they were asking about.
    (re.compile(r"^\s*(?:re-?run|re-?draft|redo)\s+", re.I), Action.REDRAFT),
    (re.compile(r"^\s*export\b", re.I), Action.EXPORT),
    (re.compile(r"^\s*(?:build|make)\s+the\s+(?:pack|export|workbook)\b", re.I), Action.EXPORT),
)


def parse(text: str, *, resolve_question: Any = None) -> Command | None:
    """Recognise an instruction, or return `None` so the answering path takes it.

    `resolve_question` is `attestor_platform.thread.answering.resolve_reference` when the
    caller has a round to resolve against. It is a parameter rather than an import so this
    module stays free of the answering module's own dependencies and can be tested with a
    stub.

    A `redraft` that names no question returns `None` rather than a half-formed command.
    "re-run" with nothing after it is not an instruction, and treating it as one would mean
    guessing which of 312 answers to throw away.
    """
    line = text.strip()
    if not line:
        return None

    for pattern, action in _PATTERNS:
        if not pattern.search(line):
            continue
        if action is not Action.REDRAFT:
            return Command(action=action, text=line)
        if resolve_question is None:
            return None
        question = resolve_question(line)
        if question is None:
            return None
        return Command(
            action=action,
            text=line,
            question_id=question.question_id,
            question_label=_label_for(question, line),
        )
    return None


def _label_for(question: Any, line: str) -> str:
    """The handle the person used, if they used one, so the prompt echoes their words."""
    numbered = re.search(r"(?<![a-z0-9])[Qq]\s*(\d{1,4})(?![a-z0-9])", line)
    if numbered is not None:
        return f"Q{numbered.group(1)}"
    return str(getattr(question, "question_id", ""))[:8]
