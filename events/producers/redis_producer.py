"""Redis Streams event producer for the Autonomous PMO event bus.

Publishes DeliveryEvent objects to the stream `events:{project_id}`.
Each message includes: event_id, event_type, source, tenant_id, timestamp,
and the full JSON-serialised payload.

Usage:
    producer = RedisEventProducer(redis_client)
    message_id = producer.publish(event)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from events.schemas.event_types import DeliveryEvent

logger = logging.getLogger(__name__)

# Redis Stream key pattern
_STREAM_KEY_PREFIX = "events"
# Maximum number of entries to keep in the stream per project (MAXLEN ~)
_STREAM_MAXLEN = 10_000


class RedisEventProducer:
    """Publishes DeliveryEvent objects to per-project Redis Streams.

    The client is expected to be a redis.Redis (sync) or redis.asyncio.Redis
    (async) instance. This implementation is synchronous; wrap in an executor
    for async contexts.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    def publish(self, event: DeliveryEvent) -> Optional[str]:
        """Push a DeliveryEvent onto the stream for its project.

        Args:
            event: A fully-populated DeliveryEvent instance.

        Returns:
            The Redis message ID string (e.g. "1526919030474-55") on success,
            or None if the publish failed (error is logged, not raised).

        Stream key: ``events:{project_id}``
        Message fields include ``event_id`` so consumers can deduplicate.
        """
        stream_key = f"{_STREAM_KEY_PREFIX}:{event.project_id}"
        fields = self._build_fields(event)

        try:
            message_id = self._redis.xadd(
                stream_key,
                fields,
                maxlen=_STREAM_MAXLEN,
                approximate=True,
            )
            logger.debug(
                "RedisEventProducer: published event_id=%s to stream=%s message_id=%s",
                event.event_id,
                stream_key,
                message_id,
            )
            return message_id
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "RedisEventProducer: failed to publish event_id=%s to stream=%s: %s",
                event.event_id,
                stream_key,
                exc,
            )
            return None

    def publish_many(self, events: list[DeliveryEvent]) -> list[Optional[str]]:
        """Publish multiple events. Returns list of message IDs (None on failure)."""
        return [self.publish(event) for event in events]

    # ---- Internal helpers ----

    @staticmethod
    def _build_fields(event: DeliveryEvent) -> dict:
        """Serialise a DeliveryEvent to a flat string dict for XADD."""
        return {
            "event_id": event.event_id,
            "event_type": str(event.event_type),
            "project_id": event.project_id,
            "source": event.source,
            "tenant_id": event.tenant_id,
            "timestamp": event.timestamp.isoformat(),
            "payload": json.dumps(event.payload, default=str),
        }
