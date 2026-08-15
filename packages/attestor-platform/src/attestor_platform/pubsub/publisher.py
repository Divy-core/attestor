"""Pub/Sub publisher with deterministic dedup keys.

The key is computed in `attestor_core.protocol.WorkEnvelope.for_work` from the work
itself, never randomly. It is published as a message attribute as well as inside the
body so the dispatcher can dedupe without parsing the payload.
"""

from __future__ import annotations

import json
import logging

from google.cloud import pubsub_v1  # type: ignore[attr-defined]

from attestor_core.protocol import WorkEnvelope
from attestor_platform.config import project_id

logger = logging.getLogger(__name__)

WORK_TOPIC = "attestor.work"
DEFAULT_TIMEOUT_SECONDS = 30.0


class WorkPublisher:
    """Publishes `WorkEnvelope`s onto the work topic."""

    def __init__(
        self,
        project: str | None = None,
        topic: str = WORK_TOPIC,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.project = project or project_id()
        self.topic = topic
        self._timeout = timeout
        self._client = pubsub_v1.PublisherClient()
        self._path = self._client.topic_path(self.project, topic)

    def publish(self, envelope: WorkEnvelope) -> str:
        """Publish one envelope; returns the Pub/Sub message id.

        `dedup_key` travels as an attribute so the dispatcher can drop a redelivery
        before deserialising anything.
        """
        data = envelope.model_dump_json().encode("utf-8")
        future = self._client.publish(
            self._path,
            data,
            dedup_key=envelope.dedup_key,
            kind=envelope.kind.value,
            review_id=envelope.review_id,
            run_id=envelope.run_id,
        )
        message_id = str(future.result(timeout=self._timeout))
        logger.info(
            "published work",
            extra={
                "kind": envelope.kind.value,
                "dedup_key": envelope.dedup_key,
                "review_id": envelope.review_id,
                "run_id": envelope.run_id,
                "pubsub_message_id": message_id,
            },
        )
        return message_id

    @staticmethod
    def decode(data: bytes) -> WorkEnvelope:
        """Parse a received message body back into an envelope."""
        return WorkEnvelope.model_validate(json.loads(data.decode("utf-8")))
