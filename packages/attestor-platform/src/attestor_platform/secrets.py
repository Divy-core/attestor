"""Secret Manager reads, behind one function.

One place because there is one thing worth being careful about: a secret that is absent
must fail loudly at the point of use, with the resource name in the message, rather than
resolving to an empty string that produces a 401 three calls later and reads like a
credential problem on the other side.
"""

from __future__ import annotations

import functools
import logging

from google.api_core import exceptions as gexc

from attestor_core.errors import ConfigurationError
from attestor_platform.config import project_id

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20.0


@functools.lru_cache(maxsize=8)
def read_secret(name: str, version: str = "latest", project: str | None = None) -> str:
    """Return the payload of one secret version, as text.

    Cached per process. A Cloud Run instance that re-read the OAuth refresh token on every
    inbound message would add a Secret Manager round trip to every email, and the token
    does not change within the life of an instance -- when it does, the instance is
    replaced, because rotating it is a deploy.

    Raises:
        ConfigurationError: If the secret or version does not exist, or access is denied.
            Never returns an empty string for a missing secret.
    """
    from google.cloud import secretmanager

    resource = f"projects/{project or project_id()}/secrets/{name}/versions/{version}"
    try:
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(
            request={"name": resource}, timeout=DEFAULT_TIMEOUT_SECONDS
        )
    except (gexc.NotFound, gexc.PermissionDenied, gexc.FailedPrecondition) as exc:
        raise ConfigurationError(
            f"secret {resource} could not be read: {type(exc).__name__}: {exc}",
            secret=resource,
        ) from exc
    payload = response.payload.data.decode("utf-8")
    if not payload.strip():
        raise ConfigurationError(f"secret {resource} exists but is empty", secret=resource)
    return payload
