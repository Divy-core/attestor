"""GCS: uploads, corpus staging, exports.

Uploads use signed URLs so the browser PUTs straight to GCS. A 40MB questionnaire must
never transit the control plane: it would tie up a scale-to-zero instance for the whole
transfer and count against its request timeout.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.cloud import storage  # type: ignore[attr-defined]

from attestor_platform.config import project_id

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
#: Long enough for a slow connection to finish a large upload, short enough that a
#: leaked URL is not a standing grant.
SIGNED_URL_TTL = timedelta(minutes=30)


def bucket_name(project: str, suffix: str) -> str:
    return f"{project}-{suffix}"


class StorageClient:
    """Thin wrapper over the GCS client with our bucket naming baked in."""

    def __init__(
        self, project: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.project = project or project_id()
        self._timeout = timeout
        self._client = storage.Client(project=self.project)

    def _bucket(self, suffix: str) -> storage.Bucket:
        return self._client.bucket(bucket_name(self.project, suffix))

    def signed_upload_url(
        self,
        object_name: str,
        content_type: str,
        bucket_suffix: str = "uploads",
        ttl: timedelta = SIGNED_URL_TTL,
    ) -> tuple[str, str, datetime]:
        """Return (upload_url, gs_uri, expires_at) for a direct browser PUT."""
        blob = self._bucket(bucket_suffix).blob(object_name)
        expires_at = datetime.now(UTC) + ttl
        url = blob.generate_signed_url(
            version="v4",
            expiration=ttl,
            method="PUT",
            content_type=content_type,
        )
        gs_uri = f"gs://{bucket_name(self.project, bucket_suffix)}/{object_name}"
        return url, gs_uri, expires_at

    def upload_text(self, object_name: str, text: str, bucket_suffix: str = "corpus") -> str:
        blob = self._bucket(bucket_suffix).blob(object_name)
        blob.upload_from_string(text, content_type="text/markdown", timeout=self._timeout)
        return f"gs://{bucket_name(self.project, bucket_suffix)}/{object_name}"

    def upload_file(self, object_name: str, path: str, bucket_suffix: str = "corpus") -> str:
        blob = self._bucket(bucket_suffix).blob(object_name)
        blob.upload_from_filename(path, timeout=self._timeout)
        return f"gs://{bucket_name(self.project, bucket_suffix)}/{object_name}"

    def download_text(self, gs_uri: str) -> str:
        bucket, _, name = gs_uri.removeprefix("gs://").partition("/")
        blob = self._client.bucket(bucket).blob(name)
        return str(blob.download_as_text(timeout=self._timeout))

    def download_to_file(self, gs_uri: str, destination: str) -> str:
        """Download binary content to a local path.

        `download_text` cannot be used for a questionnaire: an .xlsx is a zip archive,
        and decoding it as UTF-8 corrupts it before the parser ever sees it.
        """
        bucket, _, name = gs_uri.removeprefix("gs://").partition("/")
        self._client.bucket(bucket).blob(name).download_to_filename(
            destination, timeout=self._timeout
        )
        return destination

    def exists(self, gs_uri: str) -> bool:
        bucket, _, name = gs_uri.removeprefix("gs://").partition("/")
        return bool(self._client.bucket(bucket).blob(name).exists(timeout=self._timeout))


def download_to_temp(gs_uri: str, client: StorageClient | None = None) -> Path:
    """Download an object to a temporary file and return the local path.

    The dispatcher runs on Cloud Run, whose filesystem is an in-memory tmpfs counted
    against the instance's memory. Questionnaires are hundreds of kilobytes, so this is
    fine -- recorded because it would not be for a 200MB artefact.
    """
    storage_client = client if client is not None else StorageClient()
    suffix = Path(gs_uri).suffix or ".bin"
    # `delete=False` on purpose: the caller parses the file after this returns, so it
    # must outlive the handle. Cloud Run's filesystem is a per-instance tmpfs that goes
    # away with the instance, which is the cleanup.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        destination = handle.name
    storage_client.download_to_file(gs_uri, destination)
    return Path(destination)
