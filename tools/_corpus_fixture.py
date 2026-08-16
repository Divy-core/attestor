"""Plant and remove a temporary document in a department corpus.

Two verification harnesses need this — the tool-poisoning test and the consistency
fault injection — and both need it to be genuinely real: staged to the same bucket,
indexed into the same datastore, retrieved through the same search path. A fake that
skips the indexing would prove nothing about the defence it claims to demonstrate.

Removal runs in a `finally` in both callers and deletes only what was planted, matched by
the exact URI staged, never by pattern.
"""

from __future__ import annotations

import time

from attestor_core.domain import Department

#: Prefix so a planted document is obvious in a bucket listing and sorts to the end.
FIXTURE_PREFIX = "zz-fixture-"

#: How long to wait for a freshly imported document to become searchable.
INDEX_POLL_ATTEMPTS = 6
INDEX_POLL_SECONDS = 20


def fixture_uri(project_id: str, department: Department, stem: str) -> str:
    return f"gs://{project_id}-corpus/{department.value}/{FIXTURE_PREFIX}{stem}.txt"


def plant(project_id: str, department: Department, stem: str, text: str) -> str:
    """Stage a document to GCS and import it into the department's datastore."""
    from google.cloud import discoveryengine_v1 as de
    from google.cloud import storage  # type: ignore[attr-defined]

    from attestor_platform.search.datastore import SEARCH_LOCATION, datastore_id

    uri = fixture_uri(project_id, department, stem)
    bucket_name = f"{project_id}-corpus"
    object_name = uri.split(f"{bucket_name}/", 1)[1]

    blob = storage.Client(project=project_id).bucket(bucket_name).blob(object_name)
    blob.metadata = {"attestor_test_fixture": "true"}
    blob.upload_from_string(text, content_type="text/plain")
    print(f"  staged        : {uri}")

    parent = (
        f"projects/{project_id}/locations/{SEARCH_LOCATION}/collections/default_collection"
        f"/dataStores/{datastore_id(department)}/branches/default_branch"
    )
    operation = de.DocumentServiceClient().import_documents(
        request=de.ImportDocumentsRequest(
            parent=parent,
            gcs_source=de.GcsSource(input_uris=[uri], data_schema="content"),
            reconciliation_mode=de.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
        )
    )
    result = operation.result(timeout=1800)  # type: ignore[no-untyped-call]
    failures = list(result.error_samples)
    if failures:
        # The import LRO reports SUCCESS while indexing nothing -- see PROGRESS.md.
        raise RuntimeError(f"fixture import failed: {failures[0].message[:200]}")
    print(f"  indexed       : into {datastore_id(department)}")
    return uri


def wait_until_retrievable(department: Department, question: str, uri: str) -> bool:
    """Poll until the planted document comes back from search, or give up.

    A test that runs before the index has caught up proves nothing either way, so the
    caller is told which it was rather than being handed a silent false negative.
    """
    from attestor_platform.search import ExpandingCorpusSearch

    search = ExpandingCorpusSearch(department)
    stem = uri.rsplit("/", 1)[-1].removesuffix(".txt")
    for attempt in range(INDEX_POLL_ATTEMPTS):
        evidence = search.retrieve(question).evidence
        if any(stem in item.document_uri for item in evidence):
            return True
        print(f"  (not yet indexed, attempt {attempt + 1}/{INDEX_POLL_ATTEMPTS}; waiting)")
        time.sleep(INDEX_POLL_SECONDS)
    return False


def remove(project_id: str, department: Department, uri: str) -> list[str]:
    """Delete the planted document from the datastore and the bucket."""
    from google.cloud import discoveryengine_v1 as de
    from google.cloud import storage  # type: ignore[attr-defined]

    from attestor_platform.search.datastore import SEARCH_LOCATION, datastore_id

    removed: list[str] = []
    parent = (
        f"projects/{project_id}/locations/{SEARCH_LOCATION}/collections/default_collection"
        f"/dataStores/{datastore_id(department)}/branches/default_branch"
    )
    client = de.DocumentServiceClient()
    for document in client.list_documents(request=de.ListDocumentsRequest(parent=parent)):
        if document.content and document.content.uri == uri:
            client.delete_document(request=de.DeleteDocumentRequest(name=document.name))
            removed.append(document.name)

    bucket_name = f"{project_id}-corpus"
    blob = (
        storage.Client(project=project_id)
        .bucket(bucket_name)
        .get_blob(uri.split(f"{bucket_name}/", 1)[1])
    )
    if blob is not None:
        blob.delete()
        removed.append(uri)
    return removed
