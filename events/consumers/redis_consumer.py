"""Redis Streams event consumer for the Autonomous PMO event bus.

Consumer group: ``pmo_processors``
At-least-once delivery via explicit XACK after successful processing.
Exponential backoff on transient errors (max 5 retries, cap 60s).

Usage:
    consumer = RedisEventConsumer(redis_client, project_id="proj_123", handler=my_fn)
    consumer.run()          # blocking
    consumer.run_once()     # process one batch and return (useful for tests)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent, EventType

logger = logging.getLogger(__name__)

_CONSUMER_GROUP = "pmo_processors"
_STREAM_KEY_PREFIX = "events"
_BLOCK_MS = 5_000          # block on XREADGROUP for up to 5 s
_BATCH_SIZE = 10            # messages per XREADGROUP call
_MAX_RETRIES = 5
_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 60.0
_PENDING_IDLE_MS = 30_000  # reclaim pending messages idle > 30 s


class RedisEventConsumer:
    """Consumes DeliveryEvent objects from a per-project Redis Stream.

    Implements consumer group semantics for at-least-once delivery:
    - Messages are ACKed only after the handler returns without exception.
    - Failed messages are retried up to _MAX_RETRIES times with exponential backoff.
    - After max retries the message is ACKed to prevent infinite redelivery, and
      the error is logged for manual review (dead-letter pattern via logging).
    """

    def __init__(
        self,
        redis_client,
        project_id: str,
        handler: Callable[[DeliveryEvent], None],
        consumer_name: str = "consumer-1",
    ) -> None:
        self._redis = redis_client
        self._project_id = project_id
        self._stream_key = f"{_STREAM_KEY_PREFIX}:{project_id}"
        self._handler = handler
        self._consumer_name = consumer_name
        self._running = False
        self._ensure_group()

    # ---- Public API ----

    def run(self) -> None:
        """Block and process events until stop() is called."""
        self._running = True
        logger.info(
            "RedisEventConsumer: starting on stream=%s group=%s consumer=%s",
            self._stream_key,
            _CONSUMER_GROUP,
            self._consumer_name,
        )
        while self._running:
            try:
                self._process_pending()   # reclaim stale pending first
                self._read_and_process()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "RedisEventConsumer: unhandled error in main loop for stream=%s: %s",
                    self._stream_key,
                    exc,
                    exc_info=True,
                )
                time.sleep(_INITIAL_BACKOFF_S)

    def run_once(self) -> int:
        """Process one batch of new messages. Returns number of messages processed."""
        self._ensure_group()
        return self._read_and_process(block_ms=0)

    def stop(self) -> None:
        """Signal the run loop to exit after the current iteration."""
        self._running = False

    # ---- Internal ----

    def _ensure_group(self) -> None:
        """Create the consumer group if it does not exist."""
        try:
            self._redis.xgroup_create(
                self._stream_key, _CONSUMER_GROUP, id="0", mkstream=True
            )
            logger.debug(
                "RedisEventConsumer: created group=%s on stream=%s",
                _CONSUMER_GROUP,
                self._stream_key,
            )
        except Exception as exc:
            # BUSYGROUP means it already exists — safe to ignore
            if "BUSYGROUP" in str(exc):
                return
            logger.warning(
                "RedisEventConsumer: could not create group=%s: %s",
                _CONSUMER_GROUP,
                exc,
            )

    def _read_and_process(self, block_ms: int = _BLOCK_MS) -> int:
        """Read a batch of new messages and process each one."""
        try:
            results = self._redis.xreadgroup(
                _CONSUMER_GROUP,
                self._consumer_name,
                {self._stream_key: ">"},
                count=_BATCH_SIZE,
                block=block_ms,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "RedisEventConsumer: XREADGROUP failed for stream=%s: %s",
                self._stream_key,
                exc,
            )
            return 0

        if not results:
            return 0

        processed = 0
        for _stream, messages in results:
            for message_id, fields in messages:
                self._handle_with_retry(message_id, fields)
                processed += 1
        return processed

    def _process_pending(self) -> None:
        """Reclaim and reprocess messages stuck in PEL (pending entry list)."""
        try:
            pending = self._redis.xautoclaim(
                self._stream_key,
                _CONSUMER_GROUP,
                self._consumer_name,
                min_idle_time=_PENDING_IDLE_MS,
                start_id="0-0",
                count=_BATCH_SIZE,
            )
        except Exception as exc:  # noqa: BLE001
            # xautoclaim requires Redis 6.2+ — fall back silently for older versions
            if "ERR unknown command" in str(exc) or "WRONGTYPE" in str(exc):
                return
            logger.warning(
                "RedisEventConsumer: xautoclaim failed for stream=%s: %s",
                self._stream_key,
                exc,
            )
            return

        if not pending:
            return

        # xautoclaim returns (next_start_id, messages, deleted_ids) in Redis 7+
        # or (next_start_id, messages) in Redis 6.2
        messages = pending[1] if isinstance(pending, (list, tuple)) else []
        for message_id, fields in (messages or []):
            self._handle_with_retry(message_id, fields)

    def _handle_with_retry(self, message_id: Any, fields: Dict[str, Any]) -> None:
        """Dispatch a single message to the handler with exponential backoff retry."""
        attempt = 0
        backoff = _INITIAL_BACKOFF_S

        while attempt <= _MAX_RETRIES:
            try:
                event = self._deserialize(fields)
                self._handler(event)
                self._redis.xack(self._stream_key, _CONSUMER_GROUP, message_id)
                logger.debug(
                    "RedisEventConsumer: ACKed message_id=%s event_id=%s",
                    message_id,
                    fields.get("event_id"),
                )
                return
            except _DeserializationError as exc:
                # Permanent failure — ACK to prevent redelivery, log for review
                logger.error(
                    "RedisEventConsumer: deserialization failure for message_id=%s "
                    "(dead-letter): %s",
                    message_id,
                    exc,
                )
                self._redis.xack(self._stream_key, _CONSUMER_GROUP, message_id)
                return
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt > _MAX_RETRIES:
                    logger.error(
                        "RedisEventConsumer: max retries (%d) exceeded for message_id=%s "
                        "event_id=%s (dead-letter): %s",
                        _MAX_RETRIES,
                        message_id,
                        fields.get("event_id"),
                        exc,
                    )
                    self._redis.xack(self._stream_key, _CONSUMER_GROUP, message_id)
                    return

                logger.warning(
                    "RedisEventConsumer: attempt %d/%d failed for message_id=%s: %s "
                    "— retrying in %.1fs",
                    attempt,
                    _MAX_RETRIES,
                    message_id,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_S)

    @staticmethod
    def _deserialize(fields: Dict[str, Any]) -> DeliveryEvent:
        """Reconstruct a DeliveryEvent from stream message fields.

        Raises:
            _DeserializationError: on missing required fields or unparseable data.
        """
        try:
            payload_raw = fields.get("payload", "{}")
            if isinstance(payload_raw, bytes):
                payload_raw = payload_raw.decode()
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw

            # Decode bytes values from Redis
            def _decode(v: Any) -> Any:
                return v.decode() if isinstance(v, bytes) else v

            return DeliveryEvent(
                event_id=_decode(fields.get("event_id", "")),
                event_type=EventType(_decode(fields.get("event_type", "task.updated"))),
                project_id=_decode(fields.get("project_id", "unknown_project")),
                source=_decode(fields.get("source", "unknown")),
                tenant_id=_decode(fields.get("tenant_id", "default")),
                timestamp=_decode(fields.get("timestamp", "")),
                payload=payload,
            )
        except Exception as exc:
            raise _DeserializationError(str(exc)) from exc


class _DeserializationError(Exception):
    """Raised when a stream message cannot be converted to a DeliveryEvent."""
