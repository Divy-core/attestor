"""Gmail as an inbound transport and an outbound channel.

The whole point of this package is in `client.py`'s module docstring: a customer's email
arrives, Gmail publishes a change notification to Pub/Sub, and Attestor's existing
Eventarc dispatcher picks it up. Nothing new was needed in the transport — an email
becomes a `WorkEnvelope`, and everything downstream of that runs unchanged.
"""

from attestor_platform.gmail.client import (
    OAUTH_SECRET,
    SCOPES,
    GmailClient,
    HistoryPage,
    WatchRegistration,
)
from attestor_platform.gmail.message import (
    Attachment,
    InboundMessage,
    parse_message,
    safe_filename,
)
from attestor_platform.gmail.staging import (
    build_questionnaire,
    stage_attachment,
    stage_body_questions,
)
from attestor_platform.gmail.watch import (
    DEFAULT_LABEL,
    DEFAULT_TOPIC,
    SCOPE_NOTES,
    TopicCheck,
    WatchRefused,
    WatchStatus,
    check_topic,
)
from attestor_platform.gmail.watch import register as register_watch
from attestor_platform.gmail.watch import status as watch_status
from attestor_platform.gmail.watch import stop as stop_watch

__all__ = [
    "DEFAULT_LABEL",
    "DEFAULT_TOPIC",
    "OAUTH_SECRET",
    "SCOPES",
    "SCOPE_NOTES",
    "Attachment",
    "GmailClient",
    "HistoryPage",
    "InboundMessage",
    "TopicCheck",
    "WatchRefused",
    "WatchRegistration",
    "WatchStatus",
    "build_questionnaire",
    "check_topic",
    "parse_message",
    "register_watch",
    "safe_filename",
    "stage_attachment",
    "stage_body_questions",
    "stop_watch",
    "watch_status",
]
