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

    def _signing_credentials(self) -> dict[str, str]:
        """Extra arguments that make `generate_signed_url` sign through the IAM API.

        ## Why this is needed at all

        A v4 signed URL is an RSA signature over a canonical request, and signing needs a
        **private key**. Locally that is fine: ADC is a user credential and has one. On Cloud
        Run there is no key — the credentials come from the metadata server and carry only an
        access token — so `generate_signed_url` raises:

            AttributeError: you need a private key to sign credentials. the credentials you
            are currently using <class 'google.auth.compute_engine.credentials.Credentials'>
            just contains a token.

        The fix has two halves and **both** are required, which is the part worth recording:

        1. `roles/iam.serviceAccountTokenCreator` on the service account, granted on the
           account itself so it may impersonate itself (`infra/deploy.sh`).
        2. Passing `service_account_email` and `access_token` here. The library does **not**
           fall back to IAM signing on its own — given only the grant it still raises the
           same AttributeError, because it has no reason to believe an IAM signer is wanted.
           Phase 6.5 granted the permission first, redeployed, and got the identical error;
           the grant was necessary and not sufficient.

        Returns:
            The two extra kwargs when running on a metadata-server credential, or an empty
            dict when the local credential can sign for itself.
        """
        from google.auth import compute_engine, default
        from google.auth.transport.requests import Request

        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        # Type-checked rather than duck-typed. `hasattr(credentials, "service_account_email")`
        # is true for several credential classes that CAN sign locally, and routing those
        # through IAM would add a network call and a permission requirement for nothing.
        if not isinstance(credentials, compute_engine.Credentials):
            return {}
        # The token is what the IAM signBlob call authenticates with, so it has to be fresh.
        # `refresh` is untyped in google-auth; the cast keeps --strict honest about that rather
        # than widening the whole module's typing.
        credentials.refresh(Request())  # type: ignore[no-untyped-call]
        return {
            "service_account_email": str(credentials.service_account_email),
            "access_token": str(credentials.token),
        }

    def signed_upload_url(
        self,
        object_name: str,
        content_type: str,
        bucket_suffix: str = "uploads",
        ttl: timedelta = SIGNED_URL_TTL,
    ) -> tuple[str, str, datetime]:
        """Return (upload_url, gs_uri, expires_at) for a direct browser PUT.

        The signed `content_type` is part of the signature, so the browser's PUT must send
        exactly the same string. `services/web/lib/api/start.ts` derives it from the file
        extension for that reason -- `File.type` is reported inconsistently and is empty for
        .xlsx on some platforms, which would sign for `""` and then PUT something else.
        """
        blob = self._bucket(bucket_suffix).blob(object_name)
        expires_at = datetime.now(UTC) + ttl
        url = blob.generate_signed_url(
            version="v4",
            expiration=ttl,
            method="PUT",
            content_type=content_type,
            **self._signing_credentials(),
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
