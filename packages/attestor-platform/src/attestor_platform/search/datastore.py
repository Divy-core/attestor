"""Vertex AI Search (Discovery Engine): one datastore per department.

The per-department split is what makes scoping physically real rather than a prompt
instruction. `SecurityAgent` is bound to the security datastore; it cannot retrieve
from legal because it is not pointed at legal, not because it was asked nicely.

Serverless vector search, deliberately: no embeddings pipeline, no pgvector, no
always-on cluster. This matches the hackathon's own cost guidance and deletes about a
day of work.
"""

from __future__ import annotations

import logging

from google.api_core import exceptions as gexc
from google.cloud import discoveryengine_v1 as de

from attestor_core.domain import Department, Evidence
from attestor_platform.config import project_id

logger = logging.getLogger(__name__)

#: Discovery Engine data stores live in a multi-region, not a specific region.
SEARCH_LOCATION = "global"
DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_SCORE = 1.0

DEPARTMENT_DATASTORES: dict[Department, str] = {
    Department.SECURITY: "attestor-corpus-security",
    Department.LEGAL: "attestor-corpus-legal",
    Department.ENGINEERING: "attestor-corpus-engineering",
}


def datastore_id(department: Department) -> str:
    """The datastore backing one department's corpus."""
    try:
        return DEPARTMENT_DATASTORES[department]
    except KeyError as exc:
        raise ValueError(f"no corpus datastore for department {department.value!r}") from exc


class CorpusSearch:
    """Query one department's corpus. Construction binds the department."""

    def __init__(
        self,
        department: Department,
        project: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.department = department
        self.project = project or project_id()
        self.datastore = datastore_id(department)
        self._timeout = timeout
        self._client = de.SearchServiceClient()

    @property
    def serving_config(self) -> str:
        return (
            f"projects/{self.project}/locations/{SEARCH_LOCATION}"
            f"/collections/default_collection/dataStores/{self.datastore}"
            f"/servingConfigs/default_config"
        )

    def query(self, text: str, page_size: int = 5) -> list[Evidence]:
        """Search this department's corpus.

        Returns `Evidence`, not `Citation`: evidence is what search returned, a
        citation is evidence an agent chose to stand behind. Collapsing the two would
        make every retrieved chunk look like a claim we have made.
        """
        # Snippets only. `extractive_content_spec` is an ENTERPRISE-edition feature and a
        # standard-edition data store rejects the whole request with:
        #   400 FAILED_PRECONDITION: Cannot use enterprise edition features (website
        #   search, multi-modal search, extractive answers/segments, etc.) in a standard
        #   edition search engine.
        # Note it fails the request outright rather than degrading, so asking for
        # extractive answers "just in case" costs you every result. Enterprise edition
        # would also need an engine/app serving config rather than a data store one, and
        # it is a paid tier we do not need: snippets carry enough text to cite.
        spec = de.SearchRequest.ContentSearchSpec(
            snippet_spec=de.SearchRequest.ContentSearchSpec.SnippetSpec(return_snippet=True),
        )
        request = de.SearchRequest(
            serving_config=self.serving_config,
            query=text,
            page_size=page_size,
            content_search_spec=spec,
        )
        try:
            response = self._client.search(request=request, timeout=self._timeout)
        except gexc.GoogleAPIError as exc:
            logger.warning(
                "corpus search failed",
                extra={"department": self.department.value, "datastore": self.datastore},
                exc_info=exc,
            )
            return []

        results: list[Evidence] = []
        for rank, result in enumerate(response.results):
            doc = result.document
            data = dict(doc.derived_struct_data or {})
            title = str(data.get("title") or doc.id)
            link = str(data.get("link") or doc.name)
            content = ""
            snippets = data.get("snippets") or []
            if snippets:
                content = str(dict(snippets[0]).get("snippet", ""))
            if not content:
                extractive = data.get("extractive_answers") or []
                if extractive:
                    content = str(dict(extractive[0]).get("content", ""))
            # Discovery Engine does not return a normalised relevance score on this
            # surface, so rank is converted into one. Recorded honestly rather than
            # invented: position 1 -> 0.95, decaying by 0.1 per position.
            score = max(0.0, min(_MAX_SCORE, 0.95 - (rank * 0.1)))
            results.append(
                Evidence(
                    document_uri=link,
                    document_title=title,
                    content=content or title,
                    score=score,
                    department=self.department,
                )
            )
        return results
