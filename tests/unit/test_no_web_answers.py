"""Attestor answers from its own corpus, and a test is the only thing that keeps it that way.

A questionnaire asks *"do you encrypt customer data at rest?"*. That is a fact about the
company Attestor works for, and the only place it legitimately exists is that company's own
corpus. An agent that searched the web for it would be guessing about its own employer.

The failure mode is what makes this worth a test rather than a convention. It is not a blank
or an error: it is a fluent, well-cited answer sourced from a competitor's trust page or a
five-year-old blog post, returned to a customer under this company's name, with a citation
that makes it look *more* trustworthy than the honest refusal it replaced. Every
`flagged_no_evidence` in the run of record -- 135 of 312 -- is a place where a web tool would
have produced one of those instead.

Research about the **customer** at intake is allowed. The boundary is the destination, not
the tool: it may shape how work is routed, and may never become the text of an answer or a
citation on one.
"""

from __future__ import annotations

import ast
import pathlib
import re

from attestor_fleet.tools import ANSWERING_AGENTS, WEB_TOOL_MARKERS

FLEET = pathlib.Path(__file__).resolve().parents[2] / "packages" / "attestor-fleet" / "src"
DISPATCHER = pathlib.Path(__file__).resolve().parents[2] / "services" / "dispatcher" / "src"


#: The file that *declares* the markers, which necessarily contains all of them.
MARKER_DECLARATION = FLEET / "attestor_fleet" / "tools" / "__init__.py"


def sources() -> list[pathlib.Path]:
    """Every Python file that could bind a tool to an agent.

    Excluding the one that defines the marker list, which would otherwise be the only
    thing this test ever finds.
    """
    found = [*FLEET.rglob("*.py"), *DISPATCHER.rglob("*.py")]
    return sorted(p for p in found if p.resolve() != MARKER_DECLARATION.resolve())


def code_of(path: pathlib.Path) -> str:
    """Source with docstrings and comments removed.

    The prohibition is written down in several places, in prose, using the very words this
    test looks for. Scanning raw text would fail on the paragraph explaining the rule, so
    the AST is unparsed back to code with every docstring stripped -- what is left is only
    what executes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestNoAgentCanReachTheWebForAnAnswer:
    def test_no_web_tool_is_bound_anywhere_in_the_fleet(self) -> None:
        """The fleet is small enough to assert this over the whole package rather than
        agent by agent, and the whole package is the right scope: a tool bound to the
        orchestrator is one `sub_agents` edge away from every drafter."""
        offenders: list[str] = []
        for path in sources():
            code = code_of(path).lower()
            for marker in WEB_TOOL_MARKERS:
                if marker in code:
                    offenders.append(f"{path.name}: {marker}")
        assert offenders == [], (
            "A web-reaching tool appeared in the fleet. Attestor answers from its own "
            "corpus; see attestor_fleet.tools for the rule and where research is "
            f"allowed instead. Found: {offenders}"
        )

    def test_the_answering_agents_are_the_ones_this_protects(self) -> None:
        """A regression guard on the list itself, so removing an agent from it is a
        deliberate edit rather than something that happens while renaming a class."""
        assert set(ANSWERING_AGENTS) == {
            "SecurityAgent",
            "LegalAgent",
            "EngineeringAgent",
            "EvidenceAgent",
            "VerifierAgent",
        }

    def test_the_root_agent_holds_exactly_three_tools(self) -> None:
        """The orchestrator judges and the tools execute. Three: run a pipeline, retry a
        set of questions, finalise. A fourth is where a web tool would arrive."""
        source = (FLEET / "attestor_fleet" / "orchestrator.py").read_text(encoding="utf-8")
        assert (
            "tools=[session.execute_pipeline, session.retry_questions, session.finalise_run]"
            in source
        )


class TestTheRuleIsWrittenDownWhereATooolWouldBeAdded:
    """The prose is checked on meaning rather than on an exact phrase, because a docstring
    that has to be reflowed by hand to keep a test green is a docstring nobody edits."""

    def test_the_tools_package_states_the_prohibition_and_the_exception(self) -> None:
        raw = (FLEET / "attestor_fleet" / "tools" / "__init__.py").read_text(encoding="utf-8")
        text = re.sub(r"[\s*]+", " ", raw).lower()
        assert "never reaches the web to answer a question" in text
        assert "guessing about its own employer" in text
        # And the exception, so the rule cannot be read as "no research at all".
        assert "learning about the customer at intake" in text
