"""Vertex AI Search adapters. One datastore per department is the access boundary."""

from attestor_platform.search.datastore import (
    DEPARTMENT_DATASTORES,
    SEARCH_LOCATION,
    CorpusSearch,
    datastore_id,
)

__all__ = ["DEPARTMENT_DATASTORES", "SEARCH_LOCATION", "CorpusSearch", "datastore_id"]
