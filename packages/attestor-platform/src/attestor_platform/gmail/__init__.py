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

__all__ = [
    "OAUTH_SECRET",
    "SCOPES",
    "Attachment",
    "GmailClient",
    "HistoryPage",
    "InboundMessage",
    "WatchRegistration",
    "build_questionnaire",
    "parse_message",
    "safe_filename",
    "stage_attachment",
    "stage_body_questions",
]
