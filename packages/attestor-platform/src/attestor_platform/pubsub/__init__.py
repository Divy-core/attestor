"""Pub/Sub publisher. Dedup keys are deterministic; see protocol.WorkEnvelope."""

from attestor_platform.pubsub.publisher import WORK_TOPIC, WorkPublisher

__all__ = ["WORK_TOPIC", "WorkPublisher"]
