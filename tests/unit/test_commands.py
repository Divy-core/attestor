"""The command grammar, and the four ways a text box that acts can hurt somebody.

`answering.py` refuses to call a model because an answer has to be checkable. This side
does not answer, it **acts**, and the argument is stronger: a fuzzy classifier that is
right 95% of the time emails a customer unasked one time in twenty.

So these tests are about what the parser *declines* to do. The happy path is three regexes.

1. **Nothing is recognised by resemblance.** A near-miss falls through to the answering
   path, where the worst outcome is a reply saying the record does not hold that.
2. **A question is never an instruction.** "what did we answer for Q5" must not redraft Q5.
3. **Irreversible needs the whole phrase.** "send" alone reaches nothing.
4. **A redraft that names no question is not a redraft.** Guessing which of 312 answers to
   throw away is not a reasonable thing to do with an ambiguous line.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from attestor_platform.thread.commands import Action, Command, parse


@dataclass(frozen=True)
class FakeQuestion:
    question_id: str = "0123456789abcdef"


def resolver(_line: str) -> FakeQuestion:
    return FakeQuestion()


def none_resolver(_line: str) -> None:
    return None


class TestTheThingsThatDispatch:
    @pytest.mark.parametrize(
        "line",
        [
            "send the pack",
            "Send the pack",
            "send this pack",
            "send the answers",
            "send the workbook",
            "send the reply",
            "reply to the customer",
            "respond to the customer with the pack",
        ],
    )
    def test_send_is_recognised_on_a_whole_phrase(self, line: str) -> None:
        command = parse(line)
        assert command is not None
        assert command.action is Action.SEND_PACK
        assert command.irreversible is True

    @pytest.mark.parametrize("line", ["export", "export the round", "build the pack"])
    def test_export_is_recognised(self, line: str) -> None:
        command = parse(line)
        assert command is not None
        assert command.action is Action.EXPORT
        assert command.irreversible is False

    @pytest.mark.parametrize("line", ["re-run Q112", "rerun Q112", "redraft Q112", "redo Q112"])
    def test_redraft_is_recognised_and_carries_the_question(self, line: str) -> None:
        command = parse(line, resolve_question=resolver)
        assert command is not None
        assert command.action is Action.REDRAFT
        assert command.question_id == "0123456789abcdef"
        assert command.question_label == "Q112"
        assert command.irreversible is False

    def test_the_typed_line_is_kept_verbatim(self) -> None:
        """It goes on the audit trail as the person wrote it, not as the parser read it."""
        command = parse("  Send the pack, please  ")
        assert command is not None
        assert command.text == "Send the pack, please"


class TestTheThingsThatMustNotDispatch:
    @pytest.mark.parametrize(
        "line",
        [
            "send",
            "send it",
            "send",
            "can you send the pack later",
            "should I send the pack",
            "who sent the pack",
            "why was the pack sent",
        ],
    )
    def test_a_partial_or_indirect_send_reaches_nothing(self, line: str) -> None:
        """Every one of these falls through to the answering path, which cannot email."""
        assert parse(line) is None

    @pytest.mark.parametrize(
        "line",
        [
            "what did we answer for Q5",
            "answer for Q5",
            "why is Q112 held",
            "who approved Q47",
            "what's outstanding",
            "how many are held",
        ],
    )
    def test_a_question_is_never_an_instruction(self, line: str) -> None:
        assert parse(line, resolve_question=resolver) is None

    def test_a_redraft_naming_no_question_is_not_a_redraft(self) -> None:
        """ "re-run" with nothing resolvable after it would mean guessing a row to discard."""
        assert parse("re-run it", resolve_question=none_resolver) is None
        assert parse("re-run everything", resolve_question=none_resolver) is None

    def test_a_redraft_with_no_resolver_at_all_is_refused(self) -> None:
        assert parse("re-run Q112") is None

    @pytest.mark.parametrize("line", ["", "   ", "\n"])
    def test_empty_input_is_nothing(self, line: str) -> None:
        assert parse(line) is None

    def test_the_only_irreversible_action_is_sending(self) -> None:
        """A regression guard on the flag the confirmation gate reads."""
        irreversible = {a for a in Action if Command(action=a, text="x").irreversible}
        assert irreversible == {Action.SEND_PACK}


class TestEveryActionCanBeConfirmedInWords:
    @pytest.mark.parametrize("action", list(Action))
    def test_each_prompt_describes_the_effect(self, action: Action) -> None:
        prompt = Command(action=action, text="x", question_label="Q9").prompt
        assert prompt.startswith("This ")
        assert "{" not in prompt

    def test_the_send_prompt_says_it_cannot_be_recalled(self) -> None:
        assert "cannot be recalled" in Command(action=Action.SEND_PACK, text="x").prompt

    def test_a_redraft_prompt_echoes_the_handle_the_person_used(self) -> None:
        assert "Q9" in Command(action=Action.REDRAFT, text="x", question_label="Q9").prompt


class TestWhatGoesOnTheTrail:
    def test_the_detail_carries_the_action_the_text_and_the_flag(self) -> None:
        detail = Command(
            action=Action.REDRAFT, text="re-run Q9", question_id="abc", question_label="Q9"
        ).as_detail()
        assert detail == {
            "action": "redraft",
            "text": "re-run Q9",
            "question_id": "abc",
            "question_label": "Q9",
            "irreversible": False,
        }
