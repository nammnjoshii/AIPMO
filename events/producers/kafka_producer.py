"""Kafka event producer — Enterprise Hardening (Phase 2+ upgrade from Redis Streams).

Replaces RedisEventProducer for Phase 2+ deployments where event volume
justifies Kafka's partitioning, replay, and operational tooling.

DECISION LOG (DECISIONS.md — 2026-03-25):
  Redis Streams is Phase 1. Kafka is the Phase 2+ upgrade path.
  Replacing only this file and kafka_consumer.py is sufficient — no
  orchestrator changes required (interface is identical to Redis producer).

Topic naming: ``pmo.events.{project_id}``
Partitioning: by project_id key — ensures event ordering per project.
Serialization: JSON (same as Redis producer — no schema registry for Phase 2).

Usage:
    producer = KafkaEventProducer(bootstrap_servers="localhost:9092")
    message_id = producer.publish(event)

Config via environment variables:
    KAFKA_BOOTSTRAP_SERVERS   (default: localhost:9092)
    KAFKA_SECURITY_PROTOCOL   (default: PLAINTEXT; set SASL_SSL for production)
    KAFKA_SASL_MECHANISM      (default: none; PLAIN or SCRAM-SHA-256 for prod)
    KAFKA_SASL_USERNAME       (required when SASL enabled)
    KAFKA_SASL_PASSWORD       (required when SASL enabled)
    KAFKA_PRODUCER_ACKS       (default: all — strongest durability guarantee)
    KAFKA_PRODUCER_RETRIES    (default: 5)
    KAFKA_TOPIC_PREFIX        (default: pmo.events)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent

logger = logging.getLogger(__name__)

_DEFAULT_BOOTSTRAP = "localhost:9092"
_TOPIC_PREFIX = os.environ.get("KAFKA_TOPIC_PREFIX", "pmo.events")


def _build_producer_config() -> Dict[str, Any]:
    """Build kafka-python producer configuration from environment variables."""
    config: Dict[str, Any] = {
        "bootstrap_servers": os.environ.get(
            "KAFKA_BOOTSTRAP_SERVERS", _DEFAULT_BOOTSTRAP
        ).split(","),
        "acks": os.environ.get("KAFKA_PRODUCER_ACKS", "all"),
        "retries": int(os.environ.get("KAFKA_PRODUCER_RETRIES", "5")),
        "max_in_flight_requests_per_connection": 1,  # preserve ordering
        "enable_idempotence": True,                  # exactly-once semantics
        "value_serializer": lambda v: json.dumps(v, default=str).encode("utf-8"),
        "key_serializer": lambda k: k.encode("utf-8") if k else None,
        "compression_type": "gzip",
    }

    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    if protocol != "PLAINTEXT":
        config["security_protocol"] = protocol
        mechanism = os.environ.get("KAFKA_SASL_MECHANISM", "PLAIN")
        config["sasl_mechanism"] = mechanism
        config["sasl_plain_username"] = os.environ.get("KAFKA_SASL_USERNAME", "")
        config["sasl_plain_password"] = os.environ.get("KAFKA_SASL_PASSWORD", "")

    return config


class KafkaEventProducer:
    """Publishes DeliveryEvent objects to per-project Kafka topics.

    Interface is intentionally identical to RedisEventProducer —
    upgrading from Redis to Kafka requires only swapping this class.

    Topic: ``pmo.events.{project_id}``
    Key:   project_id (ensures partition-level ordering per project)
    """

    def __init__(
        self,
        bootstrap_servers: Optional[str] = None,
        producer=None,
    ) -> None:
        """Initialize producer.

        Args:
            bootstrap_servers: Comma-separated Kafka brokers. Falls back to
                KAFKA_BOOTSTRAP_SERVERS env var, then localhost:9092.
            producer: Optional pre-built KafkaProducer (for testing).
        """
        if bootstrap_servers:
            os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", bootstrap_servers)
        self._producer = producer
        self._initialized = False

    def _ensure_producer(self) -> bool:
        """Lazily initialize the KafkaProducer."""
        if self._producer is not None:
            return True
        try:
            from kafka import KafkaProducer  # type: ignore
            config = _build_producer_config()
            self._producer = KafkaProducer(**config)
            self._initialized = True
            logger.info(
                "KafkaEventProducer: connected to %s",
                os.environ.get("KAFKA_BOOTSTRAP_SERVERS", _DEFAULT_BOOTSTRAP),
            )
            return True
        except ImportError:
            logger.error(
                "KafkaEventProducer: kafka-python not installed. "
                "Run: pip install kafka-python"
            )
            return False
        except Exception as e:
            logger.error("KafkaEventProducer: failed to connect: %s", e)
            return False

    def publish(self, event: DeliveryEvent) -> Optional[str]:
        """Publish a DeliveryEvent to its project topic.

        Args:
            event: Fully-populated DeliveryEvent instance.

        Returns:
            String "<partition>-<offset>" on success (analogous to Redis message ID),
            or None on failure (logged, not raised).
        """
        if not self._ensure_producer():
            return None

        topic = f"{_TOPIC_PREFIX}.{event.project_id}"
        value = self._build_value(event)
        key = event.project_id

        try:
            future = self._producer.send(topic, key=key, value=value)
            record_metadata = future.get(timeout=10)
            message_id = f"{record_metadata.partition}-{record_metadata.offset}"
            logger.debug(
                "KafkaEventProducer: published event_id=%s topic=%s id=%s",
                event.event_id,
                topic,
                message_id,
            )
            return message_id
        except Exception as e:
            logger.error(
                "KafkaEventProducer: failed to publish event_id=%s topic=%s: %s",
                event.event_id,
                topic,
                e,
            )
            return None

    def publish_many(self, events: List[DeliveryEvent]) -> List[Optional[str]]:
        """Publish multiple events. Returns list of message IDs (None on failure)."""
        return [self.publish(event) for event in events]

    def flush(self, timeout: float = 30.0) -> None:
        """Flush all buffered messages. Call before shutdown."""
        if self._producer:
            try:
                self._producer.flush(timeout=timeout)
            except Exception as e:
                logger.warning("KafkaEventProducer: flush error: %s", e)

    def close(self) -> None:
        """Close the producer cleanly."""
        if self._producer:
            try:
                self._producer.close(timeout=10)
                logger.info("KafkaEventProducer: closed")
            except Exception as e:
                logger.warning("KafkaEventProducer: close error: %s", e)
            finally:
                self._producer = None

    @staticmethod
    def _build_value(event: DeliveryEvent) -> Dict[str, Any]:
        """Serialize DeliveryEvent to Kafka message value dict."""
        return {
            "event_id": event.event_id,
            "event_type": str(event.event_type),
            "project_id": event.project_id,
            "source": event.source,
            "tenant_id": event.tenant_id,
            "timestamp": event.timestamp.isoformat(),
            "payload": event.payload,
        }

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
        except Exception as e:
            logger.warning("KafkaEventProducer.health_check failed: %s", e)
            return False
