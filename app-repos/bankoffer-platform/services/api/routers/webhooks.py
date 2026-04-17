"""Webhooks router - receives bank transaction events and publishes to Kafka."""

import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Header, Request

from services.api.models import WebhookPayload

logger = logging.getLogger(__name__)

router = APIRouter()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
KAFKA_TOPIC = "bank.transactions"


def _get_kafka_producer():
    """Lazily initialize and return a Kafka producer singleton."""
    from kafka import KafkaProducer

    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    return KafkaProducer(
        bootstrap_servers=bootstrap_servers.split(","),
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        acks="all",
        retries=3,
        max_in_flight_requests_per_connection=1,
    )


_producer = None


def get_kafka_producer():
    global _producer
    if _producer is None:
        _producer = _get_kafka_producer()
    return _producer


def verify_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Verify the HMAC-SHA256 webhook signature."""
    if not secret:
        logger.warning("WEBHOOK_SECRET not configured; skipping signature verification")
        return True

    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)


@router.post(
    "/transactions",
    status_code=202,
    summary="Receive bank transaction webhook",
    description="Accepts transaction events from the bank core system and publishes them to Kafka.",
)
async def receive_transactions(
    request: Request,
    x_webhook_signature: str = Header(..., description="HMAC-SHA256 signature"),
):
    """Validate the webhook signature and publish transactions to Kafka."""
    body = await request.body()

    # Parse and validate the payload
    try:
        payload_data = json.loads(body)
        payload = WebhookPayload.model_validate(payload_data)
    except Exception as e:
        logger.warning("Invalid webhook payload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid payload format")

    # Verify signature
    if not verify_signature(body, x_webhook_signature, WEBHOOK_SECRET):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Publish each transaction to Kafka
    producer = get_kafka_producer()
    published_count = 0

    for txn in payload.transactions:
        try:
            producer.send(
                KAFKA_TOPIC,
                key=txn.customer_id.encode("utf-8"),
                value=txn.model_dump(mode="json"),
            )
            published_count += 1
        except Exception as e:
            logger.error(
                "Failed to publish transaction %s to Kafka: %s",
                txn.transaction_id,
                e,
            )

    producer.flush(timeout=10)

    logger.info(
        "Published %d/%d transactions from webhook event %s",
        published_count,
        len(payload.transactions),
        payload.event_type,
    )

    return {
        "status": "accepted",
        "transactions_published": published_count,
        "total_transactions": len(payload.transactions),
    }
