"""Kafka event consumer — Enterprise Hardening (Phase 2+ upgrade from Redis Streams).

Replaces RedisEventConsumer for Phase 2+ deployments where event volume
justifies Kafka's partitioning, replay, and operational tooling.

DECISION LOG (DECISIONS.md — 2026-03-25):
  Redis Streams is Phase 1. Kafka is the Phase 2+ upgrade path.
  Replacing only this file and kafka_producer.py is sufficient — no
  orchestrator changes required (interface is identical to Redis consumer).

Consumer group: ``pmo_processors``
At-least-once delivery via manual offset commits (enable_auto_commit=False).
  - Offsets committed only after the handler returns without exception.
  - Equivalent to Redis XACK — same semantic guarantee.
Exponential backoff on transient errors (max 5 retries, cap 60s).
Graceful shutdown via threading.Event (mirrors RedisEventConsumer.stop()).

Topic subscription: ``pmo.events.*`` (via regex) or explicit list via
  KAFKA_CONSUMER_TOPICS env var (comma-separated, e.g. "pmo.events.proj_001").

Config via environment variables:
    KAFKA_BOOTSTRAP_SERVERS   (default: localhost:9092)
    KAFKA_SECURITY_PROTOCOL   (default: PLAINTEXT; set SASL_SSL for production)
    KAFKA_SASL_MECHANISM      (default: none; PLAIN or SCRAM-SHA-256 for prod)
    KAFKA_SASL_USERNAME       (required when SASL enabled)
    KAFKA_SASL_PASSWORD       (required when SASL enabled)
    KAFKA_TOPIC_PREFIX        (default: pmo.events)
    KAFKA_CONSUMER_TOPICS     (optional override — comma-separated topic list)
    KAFKA_CONSUMER_POLL_TIMEOUT_S  (default: 5.0 — seconds to block on poll)
    KAFKA_CONSUMER_BATCH_SIZE      (default: 10 — max records per poll)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent, EventType

logger = logging.getLogger(__name__)

_CONSUMER_GROUP = "pmo_processors"
_TOPIC_PREFIX = os.environ.get("KAFKA_TOPIC_PREFIX", "pmo.events")
_DEFAULT_BOOTSTRAP = "localhost:9092"

_MAX_RETRIES = 5
_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 60.0


def _build_consumer_config(consumer_name: str) -> Dict[str, Any]:
    """Build kafka-python consumer configuration from environment variables."""
    config: Dict[str, Any] = {
        "bootstrap_servers": os.environ.get(
            "KAFKA_BOOTSTRAP_SERVERS", _DEFAULT_BOOTSTRAP
        ).split(","),
        "group_id": _CONSUMER_GROUP,
        "client_id": consumer_name,
        "enable_auto_commit": False,          # manual commit for at-least-once
        "auto_offset_reset": "earliest",      # replay from beginning on new group
        "max_poll_records": int(os.environ.get("KAFKA_CONSUMER_BATCH_SIZE", "10")),
        "value_deserializer": lambda v: json.loads(v.decode("utf-8")),
        "key_deserializer": lambda k: k.decode("utf-8") if k else None,
        "session_timeout_ms": 30_000,
        "heartbeat_interval_ms": 10_000,
        "max_poll_interval_ms": 300_000,      # 5 min max between polls
    }

    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    if protocol != "PLAINTEXT":
        config["security_protocol"] = protocol
        mechanism = os.environ.get("KAFKA_SASL_MECHANISM", "PLAIN")
        config["sasl_mechanism"] = mechanism
        config["sasl_plain_username"] = os.environ.get("KAFKA_SASL_USERNAME", "")
        config["sasl_plain_password"] = os.environ.get("KAFKA_SASL_PASSWORD", "")

    return config


def _resolve_topics() -> Optional[List[str]]:
    """Return explicit topic list from env var, or None to use regex subscription."""
    raw = os.environ.get("KAFKA_CONSUMER_TOPICS", "").strip()
    if raw:
        return [t.strip() for t in raw.split(",") if t.strip()]
    return None


class KafkaEventConsumer:
    """Consumes DeliveryEvent objects from Kafka topics.

    Interface is intentionally identical to RedisEventConsumer —
    upgrading from Redis to Kafka requires only swapping this class.

    Consumer group: ``pmo_processors``
    Topic subscription: ``pmo.events.*`` regex (or explicit list via env var)
    Delivery guarantee: at-least-once — offsets committed after handler success.

    Usage::

        consumer = KafkaEventConsumer(handler=my_fn)
        consumer.run()          # blocking
        consumer.run_once()     # process one poll batch and return (tests)
        consumer.stop()         # signal graceful shutdown
    """

    def __init__(
        self,
        handler: Callable[[DeliveryEvent], None],
        consumer_name: str = "consumer-1",
        consumer=None,
    ) -> None:
        """Initialize consumer.

        Args:
            handler: Callable invoked for each deserialized DeliveryEvent.
            consumer_name: Unique name within the consumer group. Multiple
                instances should use distinct names (e.g. "consumer-1", "consumer-2").
            consumer: Optional pre-built KafkaConsumer (for testing).
        """
        self._handler = handler
        self._consumer_name = consumer_name
        self._consumer = consumer
        self._stop_event = threading.Event()
        self._poll_timeout = float(
            os.environ.get("KAFKA_CONSUMER_POLL_TIMEOUT_S", "5.0")
        )

    # ---- Public API ----

    def run(self) -> None:
        """Block and process events until stop() is called."""
        if not self._ensure_consumer():
            logger.error(
                "KafkaEventConsumer: cannot start — consumer not initialized"
            )
            return

        self._stop_event.clear()
        logger.info(
            "KafkaEventConsumer: starting group=%s consumer=%s",
            _CONSUMER_GROUP,
            self._consumer_name,
        )

        while not self._stop_event.is_set():
            try:
                self._poll_and_process()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "KafkaEventConsumer: unhandled error in main loop consumer=%s: %s",
                    self._consumer_name,
                    exc,
                    exc_info=True,
                )
                # Brief pause to avoid tight-looping on persistent errors
                time.sleep(_INITIAL_BACKOFF_S)

        logger.info(
            "KafkaEventConsumer: stopped consumer=%s", self._consumer_name
        )

    def run_once(self) -> int:
        """Process one poll batch of messages. Returns number processed.

        Useful in tests — does not block waiting for the stop event.
        """
        if not self._ensure_consumer():
            return 0
        return self._poll_and_process(timeout=0)

    def stop(self) -> None:
        """Signal the run loop to exit after the current poll timeout."""
        self._stop_event.set()
        logger.info(
            "KafkaEventConsumer: stop requested for consumer=%s",
            self._consumer_name,
        )

    def close(self) -> None:
        """Stop consuming and close the underlying KafkaConsumer."""
        self.stop()
        if self._consumer:
            try:
                self._consumer.close()
                logger.info("KafkaEventConsumer: closed consumer=%s", self._consumer_name)
            except Exception as exc:
                logger.warning(
                    "KafkaEventConsumer: close error consumer=%s: %s",
                    self._consumer_name,
                    exc,
                )
            finally:
                self._consumer = None

    # ---- Internal ----

    def _ensure_consumer(self) -> bool:
        """Lazily initialize the KafkaConsumer."""
        if self._consumer is not None:
            return True
        try:
            from kafka import KafkaConsumer  # type: ignore

            config = _build_consumer_config(self._consumer_name)
            consumer = KafkaConsumer(**config)

            explicit_topics = _resolve_topics()
            if explicit_topics:
                consumer.subscribe(explicit_topics)
                logger.info(
                    "KafkaEventConsumer: subscribed to topics=%s",
                    explicit_topics,
                )
            else:
                # Regex subscription — matches all per-project topics
                pattern = re.compile(rf"^{re.escape(_TOPIC_PREFIX)}\..+$")
                consumer.subscribe(pattern=pattern)
                logger.info(
                    "KafkaEventConsumer: subscribed to pattern=%s.*",
                    _TOPIC_PREFIX,
                )

            self._consumer = consumer
            logger.info(
                "KafkaEventConsumer: connected to %s group=%s",
                os.environ.get("KAFKA_BOOTSTRAP_SERVERS", _DEFAULT_BOOTSTRAP),
                _CONSUMER_GROUP,
            )
            return True

        except ImportError:
            logger.error(
                "KafkaEventConsumer: kafka-python not installed. "
                "Run: pip install kafka-python"
            )
            return False
        except Exception as exc:
            logger.error(
                "KafkaEventConsumer: failed to initialize consumer=%s: %s",
                self._consumer_name,
                exc,
            )
            return False

    def _poll_and_process(self, timeout: Optional[float] = None) -> int:
        """Poll Kafka for messages and process each one.

        Returns:
            Number of messages processed in this poll cycle.
        """
        poll_timeout = timeout if timeout is not None else self._poll_timeout
        try:
            records = self._consumer.poll(
                timeout_ms=int(poll_timeout * 1000),
                max_records=int(os.environ.get("KAFKA_CONSUMER_BATCH_SIZE", "10")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "KafkaEventConsumer: poll failed consumer=%s: %s",
                self._consumer_name,
                exc,
            )
            return 0

        if not records:
            return 0

        processed = 0
        for _topic_partition, messages in records.items():
            for message in messages:
                self._handle_with_retry(message)
                processed += 1

        return processed

    def _handle_with_retry(self, message: Any) -> None:
        """Dispatch a single Kafka message to the handler with exponential backoff.

        Commits the offset only after the handler returns successfully.
        After max retries, logs a dead-letter error and commits the offset
        to prevent infinite redelivery — same pattern as RedisEventConsumer.
        """
        attempt = 0
        backoff = _INITIAL_BACKOFF_S

        topic = getattr(message, "topic", "unknown")
        partition = getattr(message, "partition", -1)
        offset = getattr(message, "offset", -1)
        message_ref = f"{topic}:{partition}:{offset}"

        while attempt <= _MAX_RETRIES:
            try:
                event = self._deserialize(message)
                self._handler(event)
                # Commit offset only after successful handler invocation
                self._consumer.commit()
                logger.debug(
                    "KafkaEventConsumer: committed offset %s event_id=%s",
                    message_ref,
                    event.event_id,
                )
                return

            except _DeserializationError as exc:
                # Permanent failure — commit to skip, log for manual review
                logger.error(
                    "KafkaEventConsumer: deserialization failure for %s "
                    "(dead-letter): %s",
                    message_ref,
                    exc,
                )
                self._consumer.commit()
                return

            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt > _MAX_RETRIES:
                    logger.error(
                        "KafkaEventConsumer: max retries (%d) exceeded for %s "
                        "(dead-letter): %s",
                        _MAX_RETRIES,
                        message_ref,
                        exc,
                    )
                    self._consumer.commit()
                    return

                logger.warning(
                    "KafkaEventConsumer: attempt %d/%d failed for %s: %s "
                    "— retrying in %.1fs",
                    attempt,
                    _MAX_RETRIES,
                    message_ref,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_S)

    @staticmethod
    def _deserialize(message: Any) -> DeliveryEvent:
        """Reconstruct a DeliveryEvent from a Kafka ConsumerRecord.

        The value is already deserialized from JSON by the KafkaConsumer's
        value_deserializer — we receive a dict directly.

        Raises:
            _DeserializationError: on missing required fields or bad data.
        """
        try:
            value: Dict[str, Any] = message.value
            if not isinstance(value, dict):
                raise ValueError(f"Expected dict, got {type(value).__name__}")

            payload = value.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)

            return DeliveryEvent(
                event_id=value.get("event_id", ""),
                event_type=EventType(value.get("event_type", "task.updated")),
                project_id=value.get("project_id", "unknown_project"),
                source=value.get("source", "unknown"),
                tenant_id=value.get("tenant_id", "default"),
                timestamp=value.get("timestamp", ""),
                payload=payload,
            )
        except Exception as exc:
            raise _DeserializationError(str(exc)) from exc

    def health_check(self) -> bool:
        """Test Kafka broker connectivity.

        Returns:
            True if brokers are reachable. False on any error.
        """
        try:
            from kafka import KafkaAdminClient  # type: ignore

            admin = KafkaAdminClient(
                bootstrap_servers=os.environ.get(
                    "KAFKA_BOOTSTRAP_SERVERS", _DEFAULT_BOOTSTRAP
                ).split(","),
                request_timeout_ms=3000,
            )
            admin.list_topics()
            admin.close()
            return True
        except Exception as exc:
            logger.warning(
                "KafkaEventConsumer.health_check failed consumer=%s: %s",
                self._consumer_name,
                exc,
            )
            return False


class _DeserializationError(Exception):
    """Raised when a Kafka message cannot be converted to a DeliveryEvent."""
