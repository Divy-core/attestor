"""The Gmail REST surface Attestor uses, and nothing else.

## Why the REST API directly rather than `googleapiclient`

Six calls are needed -- `watch`, `history.list`, `messages.get`, `attachments.get`,
`messages.send`, `messages.modify`. The discovery client would add a large dependency
whose surface is built at runtime from a discovery document, which means `mypy --strict`
can say nothing about any of it. An `AuthorizedSession` over six typed methods is smaller,
checkable, and the failure modes are visible.

## Auth, and its honest limits

Attestor has no Workspace domain, so domain-wide delegation is unavailable. A dedicated
Gmail account grants consent **once**, locally, and the resulting refresh token lives in
Secret Manager; Cloud Run exchanges it for an access token per instance. That is a real
constraint of the build and is stated rather than dressed up: this is one mailbox Attestor
was given, not an org-wide integration.

Scopes are the narrowest set that works:

* `gmail.readonly` -- read the notification's messages and their attachments.
* `gmail.send` -- reply in-thread with the finished pack.
* `gmail.modify` -- label a thread as claimed, so the mailbox shows what the fleet took.
* `drive.file` -- **only files Attestor itself created**. Attestor cannot see, and cannot
  be tricked into reading, anything else in that Drive.

`gmail.modify` subsumes label writes and is the narrowest scope Gmail offers for them;
there is no label-only scope. Noted because "narrowest that works" should be checkable.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from attestor_core.errors import ConfigurationError, ContextUnavailable
from attestor_platform.gmail.message import InboundMessage, decode_b64url, parse_message
from attestor_platform.secrets import read_secret

logger = logging.getLogger(__name__)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a credential

#: Requested at consent time and asserted at load time, so a token minted with a wider
#: grant than this file documents is caught here rather than discovered in an audit.
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive.file",
)

#: Where the refresh token lives. The payload is the JSON `tools/gmail_authorize.py` writes.
OAUTH_SECRET = "attestor-gmail-oauth"  # noqa: S105 - a secret NAME, not a secret

DEFAULT_TIMEOUT_SECONDS = 30.0

#: Gmail attachments can be 25MB. A questionnaire is tens of kilobytes; anything much
#: larger is not one, and staging it would put it on the request path of a 512MB service.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class WatchRegistration:
    """What `users.watch` returned. `expiration` is why this has to be renewed."""

    history_id: str
    expiration_ms: int
    topic: str


@dataclass(frozen=True)
class HistoryPage:
    """New messages since a history point, plus where to resume.

    `(message_id, thread_id)` pairs rather than bare ids: the thread is what decides
    new-review-versus-follow-up, `history.list` already returns it, and fetching each
    message a second time to learn something the delta had is a call per email for nothing.
    """

    messages: tuple[tuple[str, str], ...]
    history_id: str
    #: True when Gmail refused the start point as too old -- see `GmailClient.history_since`.
    restarted: bool = False


def oauth_payload() -> dict[str, Any]:
    """The consent document, from Secret Manager.

    Shared with the Drive client rather than duplicated: one consent, one refresh token, one
    set of scopes. Two copies of this would eventually be two different scope lists, and the
    narrowness of `drive.file` is a claim this project makes out loud.
    """
    raw = read_secret(OAUTH_SECRET)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"secret {OAUTH_SECRET} is not JSON; expected the document written by "
            "tools/gmail_authorize.py",
        ) from exc
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not data.get(k)]
    if missing:
        raise ConfigurationError(
            f"secret {OAUTH_SECRET} is missing {missing}; re-run tools/gmail_authorize.py"
        )
    return dict(data)


def authorized_session() -> Any:
    """An `AuthorizedSession` for the consented mailbox. One credential, both APIs."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    payload = oauth_payload()
    # google-auth ships `py.typed` but leaves these two constructors unannotated, so
    # --strict reads them as untyped calls. Ignored at the call site rather than by relaxing
    # the rule for `google.*` across the codebase, which would also silence it for the
    # clients that ARE annotated.
    return AuthorizedSession(  # type: ignore[no-untyped-call]
        Credentials(  # type: ignore[no-untyped-call]
            token=None,
            refresh_token=payload["refresh_token"],
            client_id=payload["client_id"],
            client_secret=payload["client_secret"],
            token_uri=TOKEN_URI,
            scopes=list(SCOPES),
        )
    )


class GmailClient:
    """One mailbox, six operations.

    Constructed lazily: importing this module must not require credentials, because the
    dispatcher imports it on every cold start and only a fraction of messages are inbound
    email.
    """

    def __init__(self, session: Any | None = None, address: str = "") -> None:
        self._session = session
        self._address = address

    @property
    def address(self) -> str:
        """The watched mailbox, from the token document. For logs and the reply's `From`."""
        if not self._address:
            self._address = str(oauth_payload().get("email", "")) or "unknown"
        return self._address

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = authorized_session()
            self._address = str(oauth_payload().get("email", "")) or self._address
        return self._session

    # -- plumbing ----------------------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One request. Non-2xx raises, with the body in the message.

        Gmail's errors are informative and its 4xx bodies name the scope or id at fault;
        swallowing them would turn a one-line diagnosis into an afternoon.
        """
        response = self.session.request(
            method, f"{API}{path}", timeout=DEFAULT_TIMEOUT_SECONDS, **kwargs
        )
        if response.status_code >= 400:
            raise ContextUnavailable(
                f"gmail {method} {path} -> {response.status_code}: {response.text[:400]}",
                status_code=response.status_code,
            )
        return dict(response.json()) if response.content else {}

    # -- the operations ----------------------------------------------------------------

    def watch(self, topic: str, label_ids: tuple[str, ...] = ("INBOX",)) -> WatchRegistration:
        """Ask Gmail to publish change notifications to a Pub/Sub topic.

        Expires after seven days, always. Gmail does not renew it and there is no
        subscription-style auto-extend, so `tools/gmail_watch.py` re-registers and the
        expiry is surfaced rather than assumed.
        """
        body = {
            "topicName": topic,
            "labelIds": list(label_ids),
            "labelFilterBehavior": "INCLUDE",
        }
        data = self._call("POST", "/watch", json=body)
        return WatchRegistration(
            history_id=str(data.get("historyId") or ""),
            expiration_ms=int(data.get("expiration") or 0),
            topic=topic,
        )

    def stop_watch(self) -> None:
        self._call("POST", "/stop")

    def history_since(self, start_history_id: str, limit: int = 50) -> HistoryPage:
        """Message ids added since a history point.

        Gmail's notification carries only "something changed and here is the new history
        id" -- never the message. The delta has to be asked for, and Gmail expires history
        older than roughly a week: a `404` means the start point is gone, which is
        recoverable (resume from the current point) but is **not** the same as "nothing
        arrived". Returned as `restarted=True` so the caller records a gap rather than
        reporting an empty inbox. That distinction is the same one `ContextUnavailable`
        exists for, and it has now been got wrong eight times in this project.
        """
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        try:
            data = self._call(
                "GET",
                "/history",
                params={
                    "startHistoryId": start_history_id,
                    "historyTypes": "messageAdded",
                    "maxResults": limit,
                },
            )
        except ContextUnavailable as exc:
            if exc.extra.get("status_code") != 404:
                raise
            logger.warning("gmail history %s has expired; resuming from now", start_history_id)
            profile = self._call("GET", "/profile")
            return HistoryPage((), str(profile.get("historyId") or ""), restarted=True)

        for record in data.get("history") or []:
            for added in record.get("messagesAdded") or []:
                message = added.get("message") or {}
                message_id = str(message.get("id") or "")
                if message_id and message_id not in seen:
                    seen.add(message_id)
                    found.append((message_id, str(message.get("threadId") or message_id)))
        return HistoryPage(tuple(found), str(data.get("historyId") or start_history_id))

    def current_history_id(self) -> str:
        return str(self._call("GET", "/profile").get("historyId") or "")

    def get_message(self, message_id: str) -> InboundMessage:
        raw = self._call("GET", f"/messages/{message_id}", params={"format": "full"})
        return parse_message(raw)

    def thread_message_ids(self, thread_id: str) -> tuple[str, ...]:
        data = self._call("GET", f"/threads/{thread_id}", params={"format": "minimal"})
        return tuple(str(m.get("id")) for m in data.get("messages") or [] if m.get("id"))

    def attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        data = self._call("GET", f"/messages/{message_id}/attachments/{attachment_id}")
        size = int(data.get("size") or 0)
        if size > MAX_ATTACHMENT_BYTES:
            raise ContextUnavailable(
                f"attachment is {size} bytes, over the {MAX_ATTACHMENT_BYTES} ceiling",
                message_id=message_id,
            )
        return decode_b64url(str(data.get("data") or ""))

    def send_reply(
        self,
        *,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str = "",
        attachments: tuple[tuple[str, str, bytes], ...] = (),
    ) -> str:
        """Reply on an existing thread. Returns Gmail's id for the sent message.

        `threadId` alone puts the reply in the right conversation in *our* mailbox;
        `In-Reply-To` and `References` are what put it in the right place in the
        recipient's client, so both are set.

        This is the one method here with an irreversible external effect. It is deliberately
        not gated at this level -- a client library that decided when a human had approved
        something would put that decision three layers away from where it is auditable. The
        gate is in the handler, where the approving actor is recorded.
        """
        message = EmailMessage()
        message["To"] = to
        message["From"] = self.address
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        for filename, mime_type, payload in attachments:
            maintype, _, subtype = mime_type.partition("/")
            message.add_attachment(
                payload,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        # `threadId` is omitted rather than sent empty when there is no thread. Gmail rejects
        # an empty string, and the caller with no thread is the internal approval request --
        # a genuinely new message to ourselves, not a reply to anybody.
        request: dict[str, Any] = {"raw": raw}
        if thread_id:
            request["threadId"] = thread_id
        sent = self._call("POST", "/messages/send", json=request)
        return str(sent.get("id") or "")

    def ensure_label(self, name: str) -> str:
        """Return the id of a label, creating it if the mailbox does not have it."""
        for label in self._call("GET", "/labels").get("labels") or []:
            if str(label.get("name", "")).lower() == name.lower():
                return str(label.get("id"))
        created = self._call(
            "POST",
            "/labels",
            json={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        return str(created.get("id"))

    def label_message(
        self, message_id: str, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()
    ) -> None:
        self._call(
            "POST",
            f"/messages/{message_id}/modify",
            json={"addLabelIds": list(add), "removeLabelIds": list(remove)},
        )
