"""Worker entry point - consumes Kafka events and runs the ML pipeline."""

import json
import logging
import os
import signal
import sys
from typing import Any

from kafka import KafkaConsumer, KafkaProducer

from services.worker.profiler import CustomerProfiler
from services.worker.scorer import ProductScorer
from services.worker.ranker import OfferRanker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CONSUME_TOPIC = "offer.events"
PRODUCE_TOPIC = "notification.events"
CONSUMER_GROUP = "offer-worker-group"

# Graceful shutdown
_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info("Received signal %s, initiating graceful shutdown...", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


def _create_consumer() -> KafkaConsumer:
    return KafkaConsumer(
        CONSUME_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        max_poll_interval_ms=300000,
        session_timeout_ms=30000,
    )


def _create_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        acks="all",
        retries=3,
    )


def _process_event(
    event: dict[str, Any],
    profiler: CustomerProfiler,
    scorer: ProductScorer,
    ranker: OfferRanker,
    producer: KafkaProducer,
) -> None:
    """Run the full profiler -> scorer -> ranker pipeline for one event."""
    customer_id = event.get("customer_id")
    if not customer_id:
        logger.warning("Event missing customer_id, skipping: %s", event)
        return

    transactions = event.get("transactions", [])
    demographics = event.get("demographics", {})

    logger.info(
        "Processing event for customer %s with %d transactions",
        customer_id,
        len(transactions),
    )

    # Step 1: Profile the customer
    profile = profiler.build_profile(
        customer_id=customer_id,
        transactions=transactions,
        demographics=demographics,
    )
    logger.info(
        "Profile built for %s: life_stage=%s, risk_score=%.1f",
        customer_id,
        profile.life_stage,
        profile.risk_score,
    )

    # Step 2: Score products against the profile
    scored_products = scorer.score(profile)
    logger.info(
        "Scored %d products for customer %s",
        len(scored_products),
        customer_id,
    )

    # Step 3: Rank and filter offers
    ranked_offers = ranker.rank(scored_products, profile)
    logger.info(
        "Ranked %d offers for customer %s",
        len(ranked_offers),
        customer_id,
    )

    # Publish notification events for each top offer
    for offer in ranked_offers:
        notification = {
            "offer_id": offer["offer_id"],
            "product_name": offer["product_name"],
            "personalization_reason": offer["personalization_reason"],
            "cta_url": offer["cta_url"],
            "channel": offer.get("channel", "push"),
            "customer_id": customer_id,
        }
        producer.send(
            PRODUCE_TOPIC,
            key=customer_id.encode("utf-8"),
            value=notification,
        )

    producer.flush(timeout=10)
    logger.info("Published %d notifications for customer %s", len(ranked_offers), customer_id)


def run():
    """Main consumer loop."""
    logger.info("Starting worker, consuming from topic '%s'...", CONSUME_TOPIC)

    profiler = CustomerProfiler()
    scorer = ProductScorer()
    ranker = OfferRanker()

    consumer = _create_consumer()
    producer = _create_producer()

    try:
        while _running:
            records = consumer.poll(timeout_ms=1000, max_records=10)

            for topic_partition, messages in records.items():
                for message in messages:
                    try:
                        _process_event(message.value, profiler, scorer, ranker, producer)
                    except Exception:
                        logger.exception(
                            "Failed to process event at offset %d on %s",
                            message.offset,
                            topic_partition,
                        )
                        # Continue processing other messages; dead-letter in production

                # Commit after processing the batch for this partition
                consumer.commit()

    except Exception:
        logger.exception("Fatal error in consumer loop")
        sys.exit(1)
    finally:
        logger.info("Closing consumer and producer...")
        consumer.close()
        producer.close()
        logger.info("Worker shut down cleanly.")


if __name__ == "__main__":
    run()
