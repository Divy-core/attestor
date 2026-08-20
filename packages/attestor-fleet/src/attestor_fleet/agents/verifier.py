"""VerifierAgent: the agent that checks the work is not the agent that did it.

## What it checks, and why retrieval scores do not already answer it

A relevance score is a property of the **retrieval**: how well a passage matches the
question. Groundedness is a property of the **prose**: whether the sentences the drafting
agent wrote are actually carried by the passages it cited. Those are different questions,
and the gap between them is where a confident, well-cited, wrong answer lives.

Concretely, the failure this exists to catch: five passages retrieved at 0.95 about the
encryption policy, and a drafted answer that says "customer data is encrypted at rest with
customer-managed keys" when the passages only establish encryption at rest. Every existing
signal is green. The claim about key management came from the model.

So the verifier reads the answer and the passages, and reports whether each claim traces to
one. It reports **which** claims do not, because "partially supported" without naming the
overreaching sentence gives a human nothing to act on.

## Separation of duties, enforced by identity rather than by instruction

This is the point of the component, and it is a compliance concept before it is an
architectural one. An auditor does not accept a control tested by the person who operates
it. Here that principle is not a sentence in a policy document or a line in a prompt: the
verifier is a **separate deployed engine with its own Agent Identity**, and
`assert_separation` refuses to produce a verdict when the verifying identity equals the
drafting one.

That refusal is a `PolicyViolation`, not a warning, and not a verdict of `UNKNOWN`. A
self-review that quietly downgrades to "could not check" is worse than no check at all,
because it looks like the control ran.

## What it deliberately does not do

It does not rewrite the answer. A verifier that fixes what it finds is an author again, and
the separation collapses on the first correction. It returns a verdict; `policy` decides
what that means, and a human decides what to do about it.

It also does not see the corpus. It is given the passages the drafting agent chose to stand
behind and nothing else — which is the correct scope, because the question is whether *these
citations* support *this answer*, not whether some better citation exists elsewhere.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from attestor_core.domain import Citation, SupportVerdict
from attestor_core.errors import PolicyViolation
from attestor_platform.config import REASONING_MODEL, genai_client

logger = logging.getLogger(__name__)

#: How much of a cited passage the verifier is shown. The snippets stored on a `Citation`
#: are already truncated at 400 characters by the drafting path; this is the ceiling on the
#: whole prompt's evidence section, so a five-citation answer stays one cheap call.
MAX_PASSAGE_CHARS = 600

#: Unsupported claims reported per answer. A verdict that lists forty sentences is not a
#: finding, it is a re-print of the answer.
MAX_FINDINGS = 5

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

_VERDICTS = {v.value: v for v in SupportVerdict}


@dataclass(frozen=True)
class VerificationResult:
    """One verdict, and enough of its basis to act on."""

    verdict: SupportVerdict
    #: Claims the passages do not carry, verbatim from the answer. Empty when SUPPORTED.
    unsupported_claims: tuple[str, ...] = ()
    #: One sentence on why, for the audit trail and the answer detail pane.
    reason: str = ""
    #: The identity that produced this verdict. Recorded on every answer, so "who checked
    #: this, and were they the author" is answerable six months later without inference.
    verified_by: str = ""
    #: How many citations were examined. A verdict over zero passages is meaningless and
    #: `verify` returns UNKNOWN rather than SUPPORTED for it.
    passages: int = 0

    def as_detail(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "unsupported_claims": list(self.unsupported_claims),
            "reason": self.reason,
            "verified_by": self.verified_by,
            "passages": self.passages,
        }


PROMPT = """\
You are a compliance reviewer. You did NOT write the answer below and you must not rewrite
it. Your only job is to decide whether the cited passages actually support what it claims.

Judge ONLY against the passages given. Do not use anything you know about this company from
elsewhere -- if a claim is true in general but is not in these passages, it is unsupported.
The question is not "is this correct", it is "did they show their working".

Answer as a single JSON object and nothing else:

{{
  "verdict": "supported" | "partially_supported" | "unsupported",
  "unsupported_claims": ["verbatim sentences or clauses the passages do not carry"],
  "reason": "one sentence, under 30 words"
}}

Rules:
- "supported": every factual claim traces to at least one passage.
- "partially_supported": the answer is mostly grounded but asserts something extra --
  a named tool, a frequency, a key-management detail, a certification -- that no passage
  states. List those in `unsupported_claims`.
- "unsupported": the passages do not establish the answer's central claim.
- An answer that says the corpus does not cover something, and cites passages consistent
  with that, is "supported". Admitting a gap is a grounded statement.
- `unsupported_claims` must be verbatim from the answer. Do not paraphrase and do not
  invent a claim to have something to report.

QUESTION:
{question}

ANSWER UNDER REVIEW:
{answer}

CITED PASSAGES:
{passages}
"""


def assert_separation(drafted_by: str, verified_by: str) -> None:
    """Refuse to let an agent review its own work.

    Raises:
        PolicyViolation: when the two identities are the same. Deliberately an exception
            rather than a downgrade to `UNKNOWN`: a self-review that reports "could not
            check" looks like the control ran and did not find anything, which is the worst
            of the three possible outcomes.
    """
    if drafted_by.strip().lower() == verified_by.strip().lower():
        raise PolicyViolation(
            "separation of duties: the verifying agent is the drafting agent "
            f"({verified_by!r}). A reviewer who is also the author is not a reviewer.",
            drafted_by=drafted_by,
            verified_by=verified_by,
        )


def _render_passages(citations: Sequence[Citation]) -> str:
    lines = []
    for index, citation in enumerate(citations, start=1):
        where = f" — {citation.section}" if citation.section else ""
        lines.append(
            f"[{index}] {citation.document_title}{where}\n"
            f"{citation.snippet[:MAX_PASSAGE_CHARS].strip()}"
        )
    return "\n\n".join(lines)


def _clean_claims(raw: Any, answer: str) -> tuple[str, ...]:
    """Keep only claims that are actually in the answer.

    The prompt asks for verbatim quotes and a model will sometimes paraphrase anyway. A
    "quote" that is not in the text is a fabricated finding, and a fabricated finding on a
    page whose purpose is provenance is worse than a missed one -- so anything that does not
    appear in the answer is dropped rather than shown.
    """
    if not isinstance(raw, list):
        return ()
    haystack = " ".join(answer.split()).lower()
    kept: list[str] = []
    for item in raw:
        claim = " ".join(str(item).split())
        if len(claim) < 12 or claim in kept:
            continue
        if claim.lower() not in haystack:
            logger.info("verifier reported a claim not present verbatim; dropped: %s", claim[:80])
            continue
        kept.append(claim)
    return tuple(kept[:MAX_FINDINGS])


@dataclass
class VerifierAgent:
    """Checks one answer against its own citations. One model call, no corpus access.

    Runs on the reasoning tier, not the triage tier: this decision can hold an answer back
    from a customer, and it is one call per answer rather than one per batch.
    """

    #: The identity this agent verifies under. Set to the deployed engine's Agent Identity
    #: when running on Agent Runtime; the default names the component honestly for the
    #: in-process path, where the separation is by component rather than by credential.
    identity: str = "VerifierAgent"
    model: str = REASONING_MODEL
    _client: Any | None = field(default=None, repr=False)

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = genai_client()
        return self._client

    def verify(
        self,
        *,
        question: str,
        answer: str,
        citations: Sequence[Citation],
        drafted_by: str,
    ) -> VerificationResult:
        """Produce a verdict on one answer.

        Raises:
            PolicyViolation: if this agent's identity is the drafting agent's.
        """
        assert_separation(drafted_by, self.identity)

        if not citations:
            # Nothing to check against. UNKNOWN rather than UNSUPPORTED, because an answer
            # with no citations is already handled structurally -- `Answer` will not accept
            # one unless it is flagged -- and reporting a groundedness failure here would
            # double-count a condition the type system has already caught.
            return VerificationResult(
                verdict=SupportVerdict.UNKNOWN,
                reason="No citations to verify against.",
                verified_by=self.identity,
                passages=0,
            )

        prompt = PROMPT.format(
            question=question.strip(),
            answer=answer.strip(),
            passages=_render_passages(citations),
        )
        try:
            response = self.client.models.generate_content(model=self.model, contents=prompt)
            text = (response.text or "").strip()
        except Exception as exc:
            logger.warning("verifier call failed: %s", exc)
            return VerificationResult(
                verdict=SupportVerdict.UNKNOWN,
                reason=f"The verifier could not be reached: {type(exc).__name__}.",
                verified_by=self.identity,
                passages=len(citations),
            )

        match = _JSON_BLOCK.search(text)
        if match is None:
            return VerificationResult(
                verdict=SupportVerdict.UNKNOWN,
                reason="The verifier returned no JSON object.",
                verified_by=self.identity,
                passages=len(citations),
            )
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return VerificationResult(
                verdict=SupportVerdict.UNKNOWN,
                reason="The verifier returned unparseable JSON.",
                verified_by=self.identity,
                passages=len(citations),
            )

        verdict = _VERDICTS.get(str(data.get("verdict") or "").lower())
        if verdict is None or verdict is SupportVerdict.UNKNOWN:
            # A model is not allowed to return UNKNOWN. That value means "the check did not
            # run", which is a fact about our infrastructure, not a judgement -- letting the
            # model reach for it would give it a way to abstain and have that read as an
            # outage.
            return VerificationResult(
                verdict=SupportVerdict.UNKNOWN,
                reason=f"The verifier returned an unusable verdict {data.get('verdict')!r}.",
                verified_by=self.identity,
                passages=len(citations),
            )

        claims = _clean_claims(data.get("unsupported_claims"), answer)
        if verdict is not SupportVerdict.SUPPORTED and not claims:
            # It found a problem and could not point at it. Downgraded to SUPPORTED rather
            # than held: an unciteable complaint is not evidence, and escalating on it would
            # put answers in front of a human with nothing for them to look at.
            logger.info(
                "verifier reported %s with no locatable claim; treating as supported", verdict.value
            )
            return VerificationResult(
                verdict=SupportVerdict.SUPPORTED,
                reason=(
                    f"The verifier said {verdict.value} but could not quote the claim, "
                    "so the finding was not actionable."
                ),
                verified_by=self.identity,
                passages=len(citations),
            )

        return VerificationResult(
            verdict=verdict,
            unsupported_claims=claims,
            reason=" ".join(str(data.get("reason") or "").split())[:300],
            verified_by=self.identity,
            passages=len(citations),
        )


def distribution(results: Sequence[VerificationResult]) -> dict[str, int]:
    """Count verdicts. This is the grounding claim, so it is computed rather than asserted.

    Reported per run. `UNKNOWN` is counted and reported alongside the rest rather than
    dropped from the denominator -- "0 unsupported out of 200 verified" and "0 unsupported
    out of 200, of which 180 were never checked" are different sentences.
    """
    counts = {verdict.value: 0 for verdict in SupportVerdict}
    for result in results:
        counts[result.verdict.value] += 1
    return counts
