"""Query expansion for corpus retrieval.

**Never pass raw question text to search.** Measured in Phase 2 against the real
datastore: `"Recovery Time Objective"` returned **0 results** from a datastore whose
`backup-restore-procedure.txt` contains that exact phrase, while
`"recovery objective backup restore"` returned it at 0.95. Interrogative framing and
short phrases retrieve badly; declarative claims and noun phrases retrieve well.

Poor recall is the largest single threat to the product: it produces ungrounded answers,
which the domain model correctly forces to `FLAGGED_NO_EVIDENCE`, which means a demo of a
system that cannot answer its own questionnaire.

So every search goes through `expand()` first. Expansions are cached by `question_id`
because the same question recurs across rounds and re-expanding is pure waste.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from attestor_core.domain import Department, Evidence
from attestor_core.domain.ids import make_question_id
from attestor_platform.config import TRIAGE_MODEL, genai_client
from attestor_platform.search.datastore import CorpusSearch
from attestor_platform.search.relevance import RelevanceScorer
from attestor_platform.search.sections import SectionIndex

logger = logging.getLogger(__name__)

#: How many variants to ask for. Below 3 the expansion adds little; above 5 the extra
#: searches cost more than the recall they buy.
VARIANT_COUNT = 4
EXPANSION_TIMEOUT_SECONDS = 20.0

#: Framework control IDs that may appear in a question: CC7.2, A.8.24, A.17.1, PCI 3.4.
_CONTROL_ID = re.compile(r"\b(?:CC\d+\.\d+|A\.\d+(?:\.\d+)*|PCI\s?\d+(?:\.\d+)*)\b", re.IGNORECASE)

#: Interrogative scaffolding that carries no retrieval signal. Stripped to leave the
#: noun phrases, which is what actually matches document text.
_INTERROGATIVE = re.compile(
    r"^\s*(?:please\s+)?(?:can you|could you|do you|does your|did you|will you|would you|"
    r"have you|has your|are you|is your|what(?:'s| is| are)?|which|how(?: much| many| long|"
    r" frequently| often)?|when|where|who|why|describe|provide|confirm|list|explain|"
    r"summarise|summarize|state)\b[\s,:]*",
    re.IGNORECASE,
)

#: Domain synonym groups. Deliberately hand-curated rather than model-generated: these
#: are the abbreviations a compliance questionnaire actually uses, and a fixed map is
#: free, deterministic, and cannot hallucinate.
_SYNONYMS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"rto", "recovery time objective"}), "recovery time objective restore duration"),
    (frozenset({"rpo", "recovery point objective"}), "recovery point objective data loss window"),
    (frozenset({"mfa", "2fa", "multi-factor", "multifactor"}), "multi-factor authentication"),
    (frozenset({"sso"}), "single sign-on SAML OIDC federation"),
    (frozenset({"dr", "disaster recovery"}), "disaster recovery failover business continuity"),
    (frozenset({"bcp", "business continuity"}), "business continuity disaster recovery"),
    (frozenset({"dpa"}), "data processing agreement processor controller"),
    (
        frozenset({"sccs", "scc", "standard contractual clauses"}),
        "standard contractual clauses transfers",
    ),
    (frozenset({"pii", "personal data"}), "personal data privacy data subject"),
    (frozenset({"sla"}), "service level agreement uptime availability credits"),
    (frozenset({"sdlc"}), "software development lifecycle code review"),
    (frozenset({"sast"}), "static application security testing code scanning"),
    (frozenset({"sca"}), "software composition analysis dependency scanning"),
    (frozenset({"cmek", "bring your own key", "byok"}), "customer managed encryption keys"),
    (frozenset({"iam"}), "identity access management permissions roles"),
    (frozenset({"edr"}), "endpoint detection response antivirus"),
    (frozenset({"waf"}), "web application firewall"),
    (frozenset({"sbom"}), "software bill of materials dependency inventory"),
    (
        frozenset({"pen test", "pentest", "penetration test"}),
        "penetration test security assessment",
    ),
    (frozenset({"soc 2", "soc2"}), "SOC 2 Type II audit report trust services criteria"),
    (
        frozenset({"iso 27001", "iso27001"}),
        "ISO 27001 certification information security management",
    ),
)


@dataclass(frozen=True)
class ExpandedQuery:
    """A question and the variants that will actually be searched."""

    question_id: str
    original: str
    variants: tuple[str, ...]

    @property
    def all_queries(self) -> tuple[str, ...]:
        """Every string to search, original first."""
        return (self.original, *self.variants)


@dataclass
class RetrievalResult:
    """Evidence plus enough detail to explain how it was found."""

    evidence: list[Evidence] = field(default_factory=list)
    #: Which variant surfaced each document, for the trace and for debugging recall.
    matched_by: dict[str, str] = field(default_factory=dict)
    queries_run: tuple[str, ...] = ()


def _strip_interrogative(question: str) -> str:
    """Remove question framing, leaving the noun phrases that actually retrieve."""
    text = question.strip()
    previous = None
    while previous != text:
        previous = text
        text = _INTERROGATIVE.sub("", text)
    return text.rstrip("?.").strip()


def _synonym_variant(question: str) -> str | None:
    """Expand any known abbreviation into its long form and related terms."""
    lowered = question.lower()
    additions: list[str] = []
    for triggers, expansion in _SYNONYMS:
        if any(trigger in lowered for trigger in triggers):
            additions.append(expansion)
    if not additions:
        return None
    # Sorted so the string is byte-stable for a given question -- caching and prompt
    # stability both depend on not shuffling.
    return " ".join(sorted(set(additions)))


def _control_id_variant(question: str) -> str | None:
    """Pull out a framework control ID if the question carries one."""
    found = _CONTROL_ID.findall(question)
    if not found:
        return None
    return " ".join(sorted({f.upper() for f in found}))


def heuristic_variants(question: str) -> list[str]:
    """Deterministic expansions requiring no model call.

    These alone recover most of the loss: the measured failure was interrogative
    framing and abbreviations, both of which are mechanical to fix. The model variant
    is additive, not load-bearing, which keeps expansion cheap and keeps retrieval
    working when the model is unavailable.
    """
    variants: list[str] = []

    stripped = _strip_interrogative(question)
    if stripped and stripped.lower() != question.strip().rstrip("?").lower():
        variants.append(stripped)

    synonyms = _synonym_variant(question)
    if synonyms:
        variants.append(f"{stripped or question} {synonyms}".strip())

    control = _control_id_variant(question)
    if control:
        variants.append(f"{control} {stripped or question}".strip())

    # Deduplicate, preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for variant in variants:
        key = variant.lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(variant)
    return ordered


_EXPANSION_PROMPT = """\
You rewrite vendor security questionnaire questions into search queries for a corpus of \
internal policy documents.

Return exactly {n} lines. One query per line. No numbering, no commentary, no quotes.

Make the queries retrieve well against policy prose:
- Restate the question as a declarative claim, as a policy document would phrase it.
- Use noun phrases; drop interrogative words entirely.
- Expand abbreviations to their full form AND keep the abbreviation.
- Include likely document section headings.

Question: {question}"""


class QueryExpander:
    """Expands questions into retrieval queries, with a per-question cache.

    ``use_model`` defaults to **False**, which is a measured decision rather than a
    convenience. Heuristic expansion alone moved recall@5 from 90% to 95% on the
    63-pair labelled set -- past the 85% gate -- while costing nothing, adding no
    latency, and being fully deterministic. A model call per question would add ~312
    flash-lite calls to a full run and one more failure mode, for no demonstrated
    recall benefit. The model path stays available for tuning if the corpus grows and
    the heuristics stop covering it.
    """

    def __init__(self, use_model: bool = False) -> None:
        self.use_model = use_model
        self._cache: dict[str, ExpandedQuery] = {}

    def expand(self, question: str, question_id: str | None = None) -> ExpandedQuery:
        """Return search variants for ``question``.

        Cached by `question_id`, which is content-derived, so the same question in a
        later round reuses the expansion rather than paying for it again.
        """
        qid = question_id or make_question_id(question)
        cached = self._cache.get(qid)
        if cached is not None:
            return cached

        variants = heuristic_variants(question)

        if self.use_model:
            try:
                variants.extend(self._model_variants(question))
            except Exception as exc:
                logger.warning("query expansion model call failed, using heuristics: %s", exc)

        seen: set[str] = {question.strip().lower()}
        ordered: list[str] = []
        for variant in variants:
            key = variant.strip().lower()
            if key and key not in seen:
                seen.add(key)
                ordered.append(variant.strip())

        expanded = ExpandedQuery(
            question_id=qid, original=question, variants=tuple(ordered[:VARIANT_COUNT])
        )
        self._cache[qid] = expanded
        return expanded

    def _model_variants(self, question: str) -> list[str]:
        """Ask the cheap tier for additional phrasings."""
        client = genai_client()
        response = client.models.generate_content(
            model=TRIAGE_MODEL,
            contents=_EXPANSION_PROMPT.format(n=VARIANT_COUNT, question=question),
        )
        text = response.text or ""
        lines = [line.strip(" -*\t") for line in text.splitlines()]
        return [line for line in lines if line and len(line) > 8][:VARIANT_COUNT]


class ExpandingCorpusSearch:
    """Department-scoped search that expands before querying and reranks after.

    Wraps `CorpusSearch` rather than replacing it: the department binding, and therefore
    the access boundary, stays exactly where it was.
    """

    def __init__(
        self,
        department: Department,
        expander: QueryExpander | None = None,
        search: CorpusSearch | None = None,
        scorer: RelevanceScorer | None = None,
        sections: SectionIndex | bool | None = True,
    ) -> None:
        self.department = department
        self._search = search if search is not None else CorpusSearch(department)
        self._expander = expander if expander is not None else QueryExpander()
        #: Shared across departments by the caller, so one passage is embedded once for
        #: the whole run rather than once per department that retrieves it.
        self._scorer = scorer if scorer is not None else RelevanceScorer()
        #: `True` builds the default index, `None`/`False` disables section reranking
        #: (the recall harness's raw baseline and the unit tests), and an instance is
        #: used as given.
        if sections is True:
            self._sections: SectionIndex | None = SectionIndex(department)
        elif isinstance(sections, SectionIndex):
            self._sections = sections
        else:
            self._sections = None

    def retrieve(
        self,
        question: str,
        question_id: str | None = None,
        top_k: int = 5,
        per_query: int = 5,
    ) -> RetrievalResult:
        """Search every variant, dedupe by document, score by relevance, rank.

        Dedupe key is `(document_uri, section)` so the same document surfaced by three
        variants counts once -- otherwise a document that matches weakly many times would
        outrank one that matches strongly once.

        Scoring happens **after** the union, and against the ORIGINAL question rather
        than the variant that happened to surface the passage. That matters: a variant is
        a retrieval device, and scoring a passage against the expanded query it matched
        would flatter exactly the passages the expansion dragged in.
        """
        expanded = self._expander.expand(question, question_id)
        candidates: dict[tuple[str, str | None], Evidence] = {}
        matched_by: dict[str, str] = {}

        for query in expanded.all_queries:
            for evidence in self._search.query(query, page_size=per_query):
                key = (evidence.document_uri, evidence.section)
                if key not in candidates:
                    candidates[key] = evidence
                    matched_by[evidence.document_uri] = query

        pooled = list(candidates.values())
        scored = self._rerank(question, pooled)

        ranked = sorted(scored, key=lambda e: e.score, reverse=True)[:top_k]
        return RetrievalResult(
            evidence=ranked,
            matched_by={e.document_uri: matched_by[e.document_uri] for e in ranked},
            queries_run=expanded.all_queries,
        )

    def _rerank(self, question: str, pooled: list[Evidence]) -> list[Evidence]:
        """Score each candidate, preferring its best-matching section to its snippet.

        Discovery Engine picks the snippet; it is often the wrong part of the right
        document (`search/sections.py` has the measurement). So each candidate is scored
        against every section of its own document, and the winning section replaces both
        the score and the cited text. When the document cannot be read, the snippet is
        scored instead -- degraded, not broken.
        """
        if self._sections is None:
            scores = self._scorer.score(question, [item.content for item in pooled])
            return [
                item.model_copy(update={"score": score})
                for item, score in zip(pooled, scores, strict=True)
            ]

        # One flat list of passages, one embed call, one cache. Section vectors are
        # reused across every question in the run.
        passages: list[str] = []
        spans: list[tuple[int, int]] = []
        for item in pooled:
            sections = self._sections.sections_for(item.document_uri)
            texts = [section.text for section in sections] or [item.content]
            spans.append((len(passages), len(texts)))
            passages.extend(texts)

        scores = self._scorer.score(question, passages)

        reranked: list[Evidence] = []
        for item, (start, count) in zip(pooled, spans, strict=True):
            window = scores[start : start + count]
            best = max(range(count), key=lambda i: window[i])
            sections = self._sections.sections_for(item.document_uri)
            if sections:
                section = sections[best]
                reranked.append(
                    item.model_copy(
                        update={
                            "score": window[best],
                            "content": section.text,
                            "section": section.heading or None,
                        }
                    )
                )
            else:
                reranked.append(item.model_copy(update={"score": window[best]}))
        return reranked

    def retrieve_raw(self, question: str, top_k: int = 5) -> list[Evidence]:
        """Baseline: the raw question, unexpanded. Used only by the recall harness."""
        return self._search.query(question, page_size=top_k)


def variants_for(question: str, expander: QueryExpander | None = None) -> Sequence[str]:
    """Convenience for tests and the recall harness."""
    return (expander or QueryExpander(use_model=False)).expand(question).all_queries
