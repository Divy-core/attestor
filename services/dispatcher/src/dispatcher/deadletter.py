"""Dead-lettering, with the failure recorded rather than merely moved.

Pub/Sub can dead-letter on its own once `maxDeliveryAttempts` is reached, and that is
configured as a backstop. It is not sufficient on its own: the platform moves the message
to another topic and tells nobody. A review stops advancing, the UI shows a round stuck in
`drafting`, and the only evidence lives in a topic no one is subscribed to.

So the dispatcher dead-letters *first*, at a lower attempt count than Pub/Sub's, and the
audit event is written **before** the publish. If the publish then fails, the record of
the failure still exists — which is the ordering that matters, because the audit trail is
the deliverable and the DLQ topic is a convenience.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from attestor_core.protocol import WorkEnvelope
from attestor_platform.firestore import AuditEventRepository
from attestor_platform.pubsub import WorkPublisher

logger = logging.getLogger(__name__)

DEAD_LETTER_TOPIC = os.environ.get("ATTESTOR_DLQ_TOPIC", "attestor.deadletter")

#: Audit event kind. Not in the SSE protocol -- the wire protocol is frozen at 14
#: variants and a dead letter is an operational record, not a UI event.
WORK_DEAD_LETTERED = "work_dead_lettered"


class DeadLetterSink:
    """Records a terminally failed message, then forwards it to the DLQ topic."""

    def __init__(
        self,
        audit: AuditEventRepository | None = None,
        publisher: WorkPublisher | None = None,
        topic: str = DEAD_LETTER_TOPIC,
    ) -> None:
        self._audit = audit
        self._publisher = publisher
        self._topic = topic

    # Both dependencies are lazy so that importing the module -- which the tests and the
    # local harness do -- never requires credentials.
    def _audit_repo(self) -> AuditEventRepository:
        if self._audit is None:
            self._audit = AuditEventRepository()
        return self._audit

    def _publish_client(self) -> WorkPublisher:
        if self._publisher is None:
            self._publisher = WorkPublisher(topic=self._topic)
        return self._publisher

    def record(
        self,
        envelope: WorkEnvelope,
        error: Exception,
        *,
        attempt: int,
        permanent: bool,
    ) -> None:
        """Audit the failure, then forward the message.

        Audit first. A dead letter nobody can see is the failure mode this exists to
        prevent, so the durable record is written before the convenience copy.
        """
        detail: dict[str, Any] = {
            "kind": envelope.kind.value,
            "dedup_key": envelope.dedup_key,
            "partition": envelope.partition,
            "attempt": attempt,
            "permanent": permanent,
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
            "dead_lettered_at": datetime.now(UTC).isoformat(),
        }
        # `append_safe` never raises: a failure to write the audit record must not mask
        # the original failure, which is the thing worth reporting.
        self._audit_repo().append_safe(
            {
                "kind": WORK_DEAD_LETTERED,
                "review_id": envelope.review_id,
                "run_id": envelope.run_id,
                "question_id": envelope.question_id,
                "actor": "Dispatcher",
                "detail": detail,
            }
        )

        try:
            self._publish_client().publish(envelope)
        except Exception as exc:
            # The audit record is already durable, so this is a degraded outcome rather
            # than a lost one.
            logger.error(
                "dead-letter publish failed; the audit record stands: %s",
                exc,
                extra={"dedup_key": envelope.dedup_key},
            )

    def record_unparseable(self, body: dict[str, Any], error: Exception) -> None:
        """A push body that never became an envelope.

        There is no `review_id` to correlate against, which is precisely why this is
        recorded loudly: it means a producer is publishing something malformed, and no
        review will ever show a symptom.
        """
        message = body.get("message") or {}
        self._audit_repo().append_safe(
            {
                "kind": WORK_DEAD_LETTERED,
                "review_id": "unknown",
                "run_id": "unknown",
                "actor": "Dispatcher",
                "detail": {
                    "unparseable": True,
                    "error_type": type(error).__name__,
                    "error": str(error)[:1000],
                    "pubsub_message_id": message.get("messageId"),
                    "subscription": body.get("subscription"),
                    "dead_lettered_at": datetime.now(UTC).isoformat(),
                },
            }
        )
        logger.error("unparseable push message dead-lettered: %s", error)
