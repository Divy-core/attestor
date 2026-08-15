"""Model Armor sanitize client and the long-text chunker.

Built against the response shape measured in `docs/proof/PHASE-0-DISCOVERY.md`, not
against documentation.

Two things here are load-bearing:

1. **The regional endpoint.** Model Armor's regional operations are served from
   `modelarmor.<region>.rep.googleapis.com`. The global host answers `global` only and
   returns `403 PERMISSION_DENIED: Read access to project ... was denied` for every
   regional location -- even to a project Owner with the API enabled. That error names
   permissions and means endpoints.

2. **The 512-token cap.** The prompt-injection and jailbreak filter only inspects the
   first ~512 tokens of a prompt. A questionnaire cell, a policy document, or a
   retrieved passage is routinely longer, so anything past that window is unscreened.
   `screen_long_text` chunks with overlap and fans out concurrently. A chunker that
   only catches injections in the first chunk looks like it works, which is strictly
   worse than not having one.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import google.auth
import google.auth.transport.requests

from attestor_core.policy import ArmorVerdict
from attestor_platform.config import default_region, model_armor_endpoint, project_id

logger = logging.getLogger(__name__)

INGRESS_TEMPLATE = "attestor-strict-ingress"

#: Every external call has an explicit timeout. A guardrail that hangs is an outage.
DEFAULT_TIMEOUT_SECONDS = 30.0

# --- chunking ---------------------------------------------------------------------

#: The filter's documented inspection window, in tokens.
INJECTION_TOKEN_LIMIT = 512
#: Chunk size in tokens, kept under the cap with headroom.
CHUNK_TOKENS = 450
#: Overlap between consecutive chunks. An injection straddling a boundary would be
#: split into two harmless-looking halves without this.
OVERLAP_TOKENS = 50
#: Rough characters-per-token for English prose. We chunk on whitespace using this
#: estimate rather than importing a tokenizer: the cost of being wrong is a slightly
#: smaller chunk, and a tokenizer dependency in the request path is not worth it.
CHARS_PER_TOKEN = 4

#: Bounded fan-out. Unbounded concurrency against a quota-limited API is how a 400-page
#: document turns into a 429 storm.
MAX_CONCURRENCY = 8


@dataclass(frozen=True)
class ChunkVerdict:
    """One chunk's result, with enough location detail for the UI to point at it."""

    index: int
    start_char: int
    end_char: int
    verdict: ArmorVerdict
    matched_filters: tuple[str, ...] = ()
    excerpt: str = ""


@dataclass
class LongTextVerdict:
    """Aggregate across every chunk, plus the per-chunk detail.

    Both are returned deliberately: policy branches on the aggregate, and the UI needs
    the per-chunk detail to show *where* in a long document the injection was.
    """

    verdict: ArmorVerdict
    chunks: list[ChunkVerdict] = field(default_factory=list)

    @property
    def matched_chunks(self) -> list[ChunkVerdict]:
        return [c for c in self.chunks if c.verdict.matched]

    @property
    def first_match(self) -> ChunkVerdict | None:
        matches = self.matched_chunks
        return matches[0] if matches else None


def chunk_text(
    text: str,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[tuple[int, int]]:
    """Split ``text`` into overlapping (start, end) character ranges.

    Splits on whitespace boundaries so a chunk never bisects a word, which would both
    read badly in the UI and could hide a keyword from the filter.

    Returns:
        Character ranges covering the whole string. A short string yields one range.
    """
    if not text:
        return []

    size = chunk_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    stride = max(1, size - overlap)

    if len(text) <= size:
        return [(0, len(text))]

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Back off to the last whitespace so we do not split a word.
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        ranges.append((start, end))
        if end >= len(text):
            break
        start += stride
    return ranges


def _strictest(verdicts: list[ArmorVerdict]) -> ArmorVerdict:
    """Aggregate to the strictest verdict across chunks.

    A document is exactly as safe as its most dangerous chunk. Any flag anywhere wins.
    """
    if not verdicts:
        return ArmorVerdict()
    return ArmorVerdict(
        matched=any(v.matched for v in verdicts),
        prompt_injection=any(v.prompt_injection for v in verdicts),
        sensitive_data=any(v.sensitive_data for v in verdicts),
        responsible_ai=any(v.responsible_ai for v in verdicts),
        malicious_uri=any(v.malicious_uri for v in verdicts),
        execution_failed=any(v.execution_failed for v in verdicts),
    )


def parse_sanitize_response(payload: dict[str, Any]) -> tuple[ArmorVerdict, tuple[str, ...]]:
    """Map a raw sanitize response onto the policy-facing verdict.

    This is the only place that knows Google's wire field names. `core.policy` decides
    on `ArmorVerdict`, so a change to the response format touches this function alone.

    Field names measured in Phase 0:
        sanitizationResult.filterMatchState                       MATCH_FOUND|NO_MATCH_FOUND
        ...filterResults.pi_and_jailbreak.piAndJailbreakFilterResult.matchState
        ...filterResults.sdp.sdpFilterResult.inspectResult.matchState
        ...filterResults.rai.raiFilterResult.matchState
        ...filterResults.malicious_uris.maliciousUriFilterResult.matchState
    """
    if "_http_error" in payload or "_error" in payload:
        return ArmorVerdict(matched=True, execution_failed=True), ("execution_failed",)

    result = payload.get("sanitizationResult", {})
    filters = result.get("filterResults", {})
    matched_overall = result.get("filterMatchState") == "MATCH_FOUND"

    def _match(*path: str) -> bool:
        node: Any = filters
        for key in path:
            if not isinstance(node, dict):
                return False
            node = node.get(key, {})
        return isinstance(node, dict) and node.get("matchState") == "MATCH_FOUND"

    injection = _match("pi_and_jailbreak", "piAndJailbreakFilterResult")
    sdp = _match("sdp", "sdpFilterResult", "inspectResult")
    rai = _match("rai", "raiFilterResult")
    uri = _match("malicious_uris", "maliciousUriFilterResult")
    csam = _match("csam", "csamFilterFilterResult")

    names: list[str] = []
    if injection:
        names.append("prompt_injection")
    if sdp:
        names.append("sensitive_data")
    if rai:
        names.append("responsible_ai")
    if uri:
        names.append("malicious_uri")
    if csam:
        names.append("csam")

    verdict = ArmorVerdict(
        matched=matched_overall or bool(names),
        prompt_injection=injection,
        sensitive_data=sdp,
        # CSAM is treated as a responsible-AI class match for policy purposes; either
        # way it never reaches a model.
        responsible_ai=rai or csam,
        malicious_uri=uri,
        execution_failed=result.get("invocationResult") not in (None, "SUCCESS"),
    )
    return verdict, tuple(names)


class ArmorClient:
    """Thin client over the Model Armor sanitize API."""

    def __init__(
        self,
        project: str | None = None,
        region: str | None = None,
        template: str = INGRESS_TEMPLATE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.project = project or project_id()
        self.region = region or default_region()
        self.template = template
        self.timeout = timeout
        self._endpoint = model_armor_endpoint(self.region)
        self._credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )

    def _token(self) -> str:
        if not self._credentials.valid:
            self._credentials.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]
        return str(self._credentials.token)

    def _post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        url = (
            f"{self._endpoint}/v1/projects/{self.project}"
            f"/locations/{self.region}/templates/{self.template}:{method}"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return dict(json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            logger.warning("armor: HTTP %s from %s: %s", exc.code, method, detail[:300])
            return {"_http_error": exc.code, "_body": detail}
        except (TimeoutError, OSError) as exc:
            # Fails closed: policy maps execution_failed to DENY.
            logger.warning("armor: transport failure on %s: %s", method, exc)
            return {"_error": str(exc)}

    def screen(self, text: str) -> tuple[ArmorVerdict, tuple[str, ...]]:
        """Screen a single string that fits inside the filter window."""
        return parse_sanitize_response(
            self._post("sanitizeUserPrompt", {"user_prompt_data": {"text": text}})
        )

    def screen_model_response(self, text: str) -> tuple[ArmorVerdict, tuple[str, ...]]:
        """Screen generated output before it leaves the system (egress)."""
        return parse_sanitize_response(
            self._post("sanitizeModelResponse", {"model_response_data": {"text": text}})
        )

    def screen_long_text(
        self,
        text: str,
        *,
        max_concurrency: int = MAX_CONCURRENCY,
        egress: bool = False,
    ) -> LongTextVerdict:
        """Screen text of any length by chunking with overlap and fanning out.

        The prompt-injection filter inspects only the first ~512 tokens, so a single
        call on a long document leaves everything past that window unscreened. This
        chunks at ~450 tokens with ~50 tokens of overlap, screens the chunks
        concurrently under a bounded semaphore, and aggregates to the strictest verdict.

        Returns:
            The aggregate verdict plus per-chunk detail, so the UI can show which part
            of the document tripped the filter.
        """
        ranges = chunk_text(text)
        if not ranges:
            return LongTextVerdict(verdict=ArmorVerdict())

        screen = self.screen_model_response if egress else self.screen

        def _one(indexed: tuple[int, tuple[int, int]]) -> ChunkVerdict:
            index, (start, end) = indexed
            fragment = text[start:end]
            verdict, names = screen(fragment)
            excerpt = fragment[:280] if verdict.matched else ""
            return ChunkVerdict(
                index=index,
                start_char=start,
                end_char=end,
                verdict=verdict,
                matched_filters=names,
                excerpt=excerpt,
            )

        workers = max(1, min(max_concurrency, len(ranges)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            chunks = sorted(pool.map(_one, enumerate(ranges)), key=lambda c: c.index)

        aggregate = _strictest([c.verdict for c in chunks])
        if aggregate.matched:
            first = next((c for c in chunks if c.verdict.matched), None)
            logger.warning(
                "armor: match in long text",
                extra={
                    "chunks": len(chunks),
                    "matched_chunks": sum(1 for c in chunks if c.verdict.matched),
                    "first_match_index": first.index if first else None,
                    "first_match_char": first.start_char if first else None,
                },
            )
        return LongTextVerdict(verdict=aggregate, chunks=chunks)
