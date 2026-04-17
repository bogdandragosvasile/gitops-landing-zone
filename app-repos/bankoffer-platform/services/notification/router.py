"""Notification router that consumes Kafka events and dispatches to channel adapters."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from dataclasses import dataclass, field
from typing import Any

from kafka import KafkaConsumer
from pydantic import BaseModel, Field, HttpUrl

from services.notification.adapters.email import EmailAdapter
from services.notification.adapters.inapp import InAppAdapter
from services.notification.adapters.push import PushAdapter
from services.notification.preference_service import PreferenceService

logger = logging.getLogger(__name__)


class NotificationPayload(BaseModel):
    """Schema for every notification event consumed from Kafka."""

    offer_id: str = Field(..., description="Unique offer identifier")
    product_name: str = Field(..., description="Name of the banking product")
    personalization_reason: str = Field(
        ..., description="Why this offer is relevant for the customer"
    )
    cta_url: HttpUrl = Field(..., description="Call-to-action deep link")
    channel: str = Field(
        ..., description="Preferred delivery channel: push | email | in_app"
    )
    customer_id: str = Field(..., description="Target customer identifier")
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class NotificationRouter:
    """Consumes ``notification.events`` from Kafka and routes each message to
    the appropriate channel adapter based on customer preference, frequency
    caps, and quiet-hour rules.

    Usage::

        router = NotificationRouter(
            kafka_bootstrap="kafka:9092",
            kafka_group_id="notification-router",
        )
        asyncio.run(router.run())
    """

    kafka_bootstrap: str = "kafka:9092"
    kafka_group_id: str = "notification-router"
    kafka_topic: str = "notification.events"
    kafka_auto_offset_reset: str = "earliest"

    preference_service: PreferenceService = field(default_factory=PreferenceService)
    push_adapter: PushAdapter = field(default_factory=PushAdapter)
    email_adapter: EmailAdapter = field(default_factory=EmailAdapter)
    inapp_adapter: InAppAdapter = field(default_factory=InAppAdapter)

    _consumer: KafkaConsumer | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    # -- lifecycle -------------------------------------------------------

    def _build_consumer(self) -> KafkaConsumer:
        return KafkaConsumer(
            self.kafka_topic,
            bootstrap_servers=self.kafka_bootstrap,
            group_id=self.kafka_group_id,
            auto_offset_reset=self.kafka_auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )

    async def run(self) -> None:
        """Main event loop. Blocks until a shutdown signal is received."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        self._consumer = self._build_consumer()
        self._running = True
        logger.info(
            "NotificationRouter started — consuming from %s", self.kafka_topic
        )

        try:
            while self._running:
                raw_messages = self._consumer.poll(timeout_ms=500, max_records=50)
                for _tp, messages in raw_messages.items():
                    for message in messages:
                        await self._handle_message(message.value)
                if raw_messages:
                    self._consumer.commit()
        finally:
            self._consumer.close()
            logger.info("NotificationRouter shut down gracefully")

    def _request_shutdown(self) -> None:
        logger.info("Shutdown signal received")
        self._running = False

    # -- routing logic ---------------------------------------------------

    async def _handle_message(self, raw: dict[str, Any]) -> None:
        try:
            payload = NotificationPayload.model_validate(raw)
        except Exception:
            logger.exception("Invalid notification payload: %s", raw)
            return

        customer_id = payload.customer_id

        # Check opt-out
        if await self.preference_service.is_opted_out(customer_id):
            logger.info("Customer %s opted out — skipping", customer_id)
            return

        # Check quiet hours
        if await self.preference_service.is_quiet_hours(customer_id):
            logger.info("Quiet hours for customer %s — deferring", customer_id)
            return

        # Resolve effective channel (preference may override the payload hint)
        channel = await self.preference_service.resolve_channel(
            customer_id, payload.channel
        )

        # Enforce frequency cap
        if not await self.preference_service.check_frequency_cap(
            customer_id, channel
        ):
            logger.info(
                "Frequency cap reached for customer %s on %s", customer_id, channel
            )
            return

        await self._dispatch(channel, payload)
        await self.preference_service.record_notification(customer_id, channel)

    async def _dispatch(
        self, channel: str, payload: NotificationPayload
    ) -> None:
        adapters = {
            "push": self.push_adapter,
            "email": self.email_adapter,
            "in_app": self.inapp_adapter,
        }

        adapter = adapters.get(channel)
        if adapter is None:
            logger.error("Unknown channel %s for offer %s", channel, payload.offer_id)
            return

        try:
            await adapter.send(payload)
            logger.info(
                "Notification sent via %s for offer %s to customer %s",
                channel,
                payload.offer_id,
                payload.customer_id,
            )
        except Exception:
            logger.exception(
                "Failed to send %s notification for offer %s", channel, payload.offer_id
            )


# -- entrypoint ----------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    router = NotificationRouter()
    asyncio.run(router.run())


if __name__ == "__main__":
    main()
