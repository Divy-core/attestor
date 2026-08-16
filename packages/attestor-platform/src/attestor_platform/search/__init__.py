"""Vertex AI Search adapters. One datastore per department is the access boundary."""

from attestor_platform.search.datastore import (
    DEPARTMENT_DATASTORES,
    SEARCH_LOCATION,
    CorpusSearch,
    SearchUnavailable,
    clean_snippet,
    datastore_id,
)
from attestor_platform.search.expansion import (
    ExpandedQuery,
    ExpandingCorpusSearch,
    QueryExpander,
    RetrievalResult,
    heuristic_variants,
)

__all__ = [
    "DEPARTMENT_DATASTORES",
    "SEARCH_LOCATION",
    "CorpusSearch",
    "ExpandedQuery",
    "ExpandingCorpusSearch",
    "QueryExpander",
    "RetrievalResult",
    "SearchUnavailable",
    "clean_snippet",
    "datastore_id",
    "heuristic_variants",
]
