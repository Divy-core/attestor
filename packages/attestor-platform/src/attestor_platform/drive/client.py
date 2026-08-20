"""The four Drive calls Attestor makes.

Same reasoning as `gmail/client.py`: four operations against a documented REST surface do
not justify the discovery client, whose runtime-built API `mypy --strict` can say nothing
about. `AuthorizedSession` over typed methods is smaller and checkable.

The credential is the *same one* the Gmail client uses — one consent, one refresh token,
one scope list. Two copies would eventually be two different scope lists, and the narrowness
of `drive.file` is a claim this project makes out loud.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from attestor_core.errors import ContextUnavailable
from attestor_platform.gmail.client import authorized_session

logger = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"

FOLDER_MIME = "application/vnd.google-apps.folder"

DEFAULT_TIMEOUT_SECONDS = 60.0

#: The top-level folder everything lands under. One place for a person to look, and one
#: thing to delete at teardown.
ROOT_FOLDER = "Attestor"

#: Folder names are built from a customer name that arrived in an email. Drive tolerates
#: most characters but a quote terminates the `q=` search expression, so the input is
#: reduced rather than escaped -- escaping a query language by hand is how injection gets in.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9 ._-]+")


@dataclass(frozen=True)
class DriveFile:
    """One file Attestor put in Drive."""

    file_id: str
    name: str
    mime_type: str
    #: The URL a signed-in person opens. Not a public link -- see `DriveClient.upload`.
    web_view_link: str
    size_bytes: int = 0

    def as_detail(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "mime_type": self.mime_type,
            "web_view_link": self.web_view_link,
            "size_bytes": self.size_bytes,
        }


def folder_name_for(customer: str) -> str:
    """A per-customer folder name that cannot break a Drive query."""
    cleaned = _UNSAFE_NAME.sub(" ", customer or "").strip()
    return " ".join(cleaned.split())[:120] or "Unknown customer"


class DriveClient:
    """Create folders, upload files, list what we put there. Nothing else."""

    def __init__(self, session: Any | None = None) -> None:
        self._session = session

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = authorized_session()
        return self._session

    def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(method, url, timeout=DEFAULT_TIMEOUT_SECONDS, **kwargs)
        if response.status_code >= 400:
            raise ContextUnavailable(
                f"drive {method} {url} -> {response.status_code}: {response.text[:400]}",
                status_code=response.status_code,
            )
        return dict(response.json()) if response.content else {}

    # -- folders -----------------------------------------------------------------------

    def ensure_folder(self, name: str, parent: str | None = None) -> str:
        """Find or create one folder, and return its id.

        The search is restricted to folders **this application created** — which is not a
        filter we chose but the only thing `drive.file` can see. A folder the account owner
        made by hand with the same name is invisible here, so a first run creates its own.
        Stated because it looks like a bug the first time it happens.
        """
        safe = folder_name_for(name)
        query = f"mimeType='{FOLDER_MIME}' and name='{safe}' and trashed=false" + (
            f" and '{parent}' in parents" if parent else ""
        )
        found = self._call(
            "GET",
            f"{API}/files",
            params={"q": query, "fields": "files(id,name)", "pageSize": 10},
        )
        files = found.get("files") or []
        if files:
            return str(files[0]["id"])

        body: dict[str, Any] = {"name": safe, "mimeType": FOLDER_MIME}
        if parent:
            body["parents"] = [parent]
        created = self._call("POST", f"{API}/files", json=body, params={"fields": "id"})
        logger.info("created Drive folder %r", safe)
        return str(created["id"])

    def folder_for_customer(self, customer: str) -> str:
        """`Attestor / <customer>`, created on first use."""
        return self.ensure_folder(customer, parent=self.ensure_folder(ROOT_FOLDER))

    # -- files -------------------------------------------------------------------------

    def upload(
        self, name: str, payload: bytes, mime_type: str, parent: str | None = None
    ) -> DriveFile:
        """Put one file in Drive and return what a person needs to open it.

        Multipart upload, because these are workbooks and PDFs of a few hundred kilobytes;
        a resumable session would be three round trips to move something that fits in one.

        **Nothing here shares the file.** The returned link opens for the account that owns
        it and for nobody else. Making a compliance pack world-readable to produce a
        convenient link would publish a customer's security posture to anyone who guessed
        the URL, which is a considerably worse outcome than a link that asks you to sign in.
        """
        metadata: dict[str, Any] = {"name": name, "mimeType": mime_type}
        if parent:
            metadata["parents"] = [parent]

        # `requests`' multipart encoder emits `multipart/form-data`; Drive wants
        # `multipart/related`, and it enforces it. Built explicitly for that reason.
        boundary = "attestor-drive-boundary"
        body = b"".join(
            [
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                json.dumps(metadata).encode("utf-8"),
                f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode(),
                payload,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        created = self._call(
            "POST",
            f"{UPLOAD_API}/files",
            params={"uploadType": "multipart", "fields": "id,name,mimeType,webViewLink,size"},
            data=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )
        file = DriveFile(
            file_id=str(created.get("id") or ""),
            name=str(created.get("name") or name),
            mime_type=str(created.get("mimeType") or mime_type),
            web_view_link=str(created.get("webViewLink") or ""),
            size_bytes=int(created.get("size") or len(payload)),
        )
        logger.info(
            "uploaded %s (%d bytes) to Drive as %s", file.name, file.size_bytes, file.file_id
        )
        return file

    def list_folder(self, folder_id: str, limit: int = 50) -> list[DriveFile]:
        """What Attestor has put in one folder. Used to reconcile the artifacts panel."""
        found = self._call(
            "GET",
            f"{API}/files",
            params={
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": "files(id,name,mimeType,webViewLink,size)",
                "pageSize": limit,
            },
        )
        return [
            DriveFile(
                file_id=str(f.get("id") or ""),
                name=str(f.get("name") or ""),
                mime_type=str(f.get("mimeType") or ""),
                web_view_link=str(f.get("webViewLink") or ""),
                size_bytes=int(f.get("size") or 0),
            )
            for f in found.get("files") or []
        ]
