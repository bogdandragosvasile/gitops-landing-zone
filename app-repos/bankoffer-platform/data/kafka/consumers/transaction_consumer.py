"""Kafka consumer for the ``bank.transactions`` topic.

Deserialises Avro or JSON messages, validates them against a Pydantic
schema, and forwards valid records to the :class:`TransactionNormalizer`.
"""

from __future__ import annotations

import io
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import fastavro
from kafka import KafkaConsumer
from pydantic import BaseModel, Field, field_validator

from data.kafka.consumers.normalizer import TransactionNormalizer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Avro schema (embedded; in production this comes from a Schema Registry)
# ---------------------------------------------------------------------------

_AVRO_SCHEMA = fastavro.parse_schema(
    {
        "type": "record",
        "name": "BankTransaction",
        "namespace": "com.bank.transactions",
        "fields": [
            {"name": "transaction_id", "type": "string"},
            {"name": "customer_id", "type": "string"},
            {"name": "amount", "type": "double"},
            {"name": "currency", "type": "string"},
            {"name": "merchant_name", "type": "string"},
            {"name": "merchant_category_code", "type": "string"},
            {"name": "transaction_type", "type": "string"},
            {"name": "timestamp", "type": "string"},
            {"name": "channel", "type": "string"},
            {"name": "status", "type": "string"},
        ],
    }
)


# ---------------------------------------------------------------------------
# Pydantic validation model
# ---------------------------------------------------------------------------


class TransactionStatus(str, Enum):
    COMPLETED = "completed"
    PENDING = "pending"
    REVERSED = "reversed"
    DECLINED = "declined"


class TransactionType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"
    TRANSFER = "transfer"
    PAYMENT = "payment"
    WITHDRAWAL = "withdrawal"


class RawTransaction(BaseModel):
    """Schema for a single bank transaction as received from Kafka."""

    transaction_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    amount: Decimal = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3)
    merchant_name: str = Field(..., min_length=1)
    merchant_category_code: str = Field(..., min_length=1, max_length=10)
    transaction_type: TransactionType
    timestamp: datetime
    channel: str = Field(..., min_length=1)
    status: TransactionStatus

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------

_ENCODING_AVRO = "avro"
_ENCODING_JSON = "json"


@dataclass
class TransactionConsumer:
    """Consumes ``bank.transactions`` and forwards validated records to the
    normalizer pipeline.

    The consumer auto-detects Avro vs JSON encoding per message (Avro
    messages carry the Confluent wire-format magic byte ``0x00``).
    """

    kafka_bootstrap: str = field(
        default_factory=lambda: os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
    )
    kafka_group_id: str = "transaction-consumer"
    kafka_topic: str = "bank.transactions"

    normalizer: TransactionNormalizer = field(
        default_factory=TransactionNormalizer
    )

    _consumer: KafkaConsumer | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    # metrics (replace with Prometheus counters in production)
    _stats: dict[str, int] = field(
        default_factory=lambda: {
            "consumed": 0,
            "valid": 0,
            "invalid": 0,
            "deserialization_errors": 0,
        },
        init=False,
    )

    # -- lifecycle -------------------------------------------------------

    def _build_consumer(self) -> KafkaConsumer:
        return KafkaConsumer(
            self.kafka_topic,
            bootstrap_servers=self.kafka_bootstrap,
            group_id=self.kafka_group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            # raw bytes — we handle deserialization ourselves
            value_deserializer=None,
        )

    def run(self) -> None:
        """Blocking event loop.  Shuts down on SIGINT / SIGTERM."""
        signal.signal(signal.SIGINT, lambda *_: self._stop())
        signal.signal(signal.SIGTERM, lambda *_: self._stop())

        self._consumer = self._build_consumer()
        self._running = True
        logger.info(
            "TransactionConsumer started — consuming from %s", self.kafka_topic
        )

        try:
            while self._running:
                batch = self._consumer.poll(timeout_ms=1000, max_records=100)
                for _tp, messages in batch.items():
                    for msg in messages:
                        self._handle(msg.value)
                if batch:
                    self._consumer.commit()
        finally:
            self._consumer.close()
            logger.info("TransactionConsumer shut down — stats: %s", self._stats)

    def _stop(self) -> None:
        self._running = False

    # -- message handling ------------------------------------------------

    def _handle(self, raw: bytes) -> None:
        self._stats["consumed"] += 1

        record = self._deserialize(raw)
        if record is None:
            return

        try:
            txn = RawTransaction.model_validate(record)
        except Exception:
            self._stats["invalid"] += 1
            logger.warning("Schema validation failed for record: %s", record)
            return

        self._stats["valid"] += 1
        normalized = self.normalizer.normalize(txn)
        logger.debug("Normalised transaction %s", normalized.transaction_id)

    # -- deserialization -------------------------------------------------

    def _deserialize(self, raw: bytes) -> dict[str, Any] | None:
        encoding = self._detect_encoding(raw)
        try:
            if encoding == _ENCODING_AVRO:
                return self._decode_avro(raw)
            return self._decode_json(raw)
        except Exception:
            self._stats["deserialization_errors"] += 1
            logger.exception("Failed to deserialize message")
            return None

    @staticmethod
    def _detect_encoding(raw: bytes) -> str:
        """Confluent Avro messages start with magic byte 0x00."""
        if len(raw) > 5 and raw[0] == 0:
            return _ENCODING_AVRO
        return _ENCODING_JSON

    @staticmethod
    def _decode_avro(raw: bytes) -> dict[str, Any]:
        # Skip Confluent wire format header (1 magic + 4 schema-id bytes)
        payload = raw[5:]
        reader = fastavro.schemaless_reader(
            io.BytesIO(payload), _AVRO_SCHEMA
        )
        return reader  # type: ignore[return-value]

    @staticmethod
    def _decode_json(raw: bytes) -> dict[str, Any]:
        return json.loads(raw.decode("utf-8"))


# -- entrypoint ----------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    consumer = TransactionConsumer()
    consumer.run()


if __name__ == "__main__":
    main()
