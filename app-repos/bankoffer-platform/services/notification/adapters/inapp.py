"""In-app notification adapter — WebSocket real-time delivery with REST polling fallback.

Notifications are persisted in Redis so they survive reconnections and can
be fetched via a simple REST endpoint for clients that don't maintain a
WebSocket connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)

# Redis key conventions
_INBOX_KEY = "notifications:inbox:{customer_id}"  # sorted set (score = ts)
_UNREAD_KEY = "notifications:unread:{customer_id}"  # simple counter
_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


@dataclass
class InAppAdapter:
    """Delivers in-app notifications via WebSocket push and REST polling.

    Architecture
    ------------
    * **Redis sorted set** stores each customer's notification inbox keyed
      by timestamp, enabling chronological retrieval and automatic expiry.
    * **WebSocket server** (optional) pushes new notifications to connected
      clients in real time.
    * **REST helpers** expose ``get_notifications`` / ``mark_read`` for the
      polling fallback consumed by the API layer.
    """

    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    ws_host: str = field(default_factory=lambda: os.getenv("WS_HOST", "0.0.0.0"))
    ws_port: int = field(
        default_factory=lambda: int(os.getenv("WS_PORT", "8765"))
    )
    inbox_ttl: int = _TTL_SECONDS

    _redis: aioredis.Redis | None = field(default=None, init=False, repr=False)
    # customer_id -> set of active WebSocket connections
    _connections: dict[str, set[WebSocketServerProtocol]] = field(
        default_factory=dict, init=False, repr=False
    )

    # -- lifecycle -------------------------------------------------------

    async def _ensure_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self.redis_url, decode_responses=True
            )
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # -- adapter send (called by router) ---------------------------------

    async def send(self, payload: Any) -> None:
        """Persist the notification and attempt real-time WebSocket delivery."""
        redis = await self._ensure_redis()
        notification = self._serialize(payload)
        customer_id = payload.customer_id
        inbox_key = _INBOX_KEY.format(customer_id=customer_id)
        unread_key = _UNREAD_KEY.format(customer_id=customer_id)

        now = time.time()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.zadd(inbox_key, {json.dumps(notification): now})
            pipe.incr(unread_key)
            pipe.expire(inbox_key, self.inbox_ttl)
            pipe.expire(unread_key, self.inbox_ttl)
            await pipe.execute()

        logger.info(
            "Stored in-app notification %s for customer %s",
            notification["id"],
            customer_id,
        )

        # Real-time push if the customer has an open WebSocket
        await self._push_to_websocket(customer_id, notification)

    # -- REST polling helpers (used by API layer) ------------------------

    async def get_notifications(
        self,
        customer_id: str,
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the latest notifications for *customer_id* (newest first)."""
        redis = await self._ensure_redis()
        inbox_key = _INBOX_KEY.format(customer_id=customer_id)
        raw_items = await redis.zrevrange(
            inbox_key, offset, offset + limit - 1
        )
        return [json.loads(item) for item in raw_items]

    async def get_unread_count(self, customer_id: str) -> int:
        redis = await self._ensure_redis()
        unread_key = _UNREAD_KEY.format(customer_id=customer_id)
        count = await redis.get(unread_key)
        return int(count) if count else 0

    async def mark_read(self, customer_id: str) -> None:
        """Reset the unread counter for *customer_id*."""
        redis = await self._ensure_redis()
        unread_key = _UNREAD_KEY.format(customer_id=customer_id)
        await redis.set(unread_key, 0, ex=self.inbox_ttl)

    # -- WebSocket server ------------------------------------------------

    async def start_websocket_server(self) -> None:
        """Launch the WebSocket server (blocking). Typically run as a
        background task alongside the Kafka consumer."""
        logger.info("Starting WebSocket server on %s:%d", self.ws_host, self.ws_port)
        async with websockets.serve(
            self._ws_handler, self.ws_host, self.ws_port
        ):
            await asyncio.Future()  # run forever

    async def _ws_handler(self, ws: WebSocketServerProtocol) -> None:
        """Handle a single WebSocket connection.

        The client must send a JSON ``{"customer_id": "..."}`` message
        immediately after connecting to register for real-time updates.
        """
        customer_id: str | None = None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            data = json.loads(raw)
            customer_id = data.get("customer_id")
            if not customer_id:
                await ws.close(1008, "customer_id required")
                return

            self._connections.setdefault(customer_id, set()).add(ws)
            logger.info("WebSocket registered for customer %s", customer_id)

            # Keep the connection alive — the client may send pings or
            # further commands (e.g. mark-read).
            async for message in ws:
                try:
                    cmd = json.loads(message)
                    if cmd.get("action") == "mark_read":
                        await self.mark_read(customer_id)
                        await ws.send(json.dumps({"status": "ok", "action": "mark_read"}))
                except json.JSONDecodeError:
                    pass
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
        finally:
            if customer_id and customer_id in self._connections:
                self._connections[customer_id].discard(ws)
                if not self._connections[customer_id]:
                    del self._connections[customer_id]

    async def _push_to_websocket(
        self, customer_id: str, notification: dict[str, Any]
    ) -> None:
        conns = self._connections.get(customer_id, set())
        if not conns:
            return
        message = json.dumps({"type": "new_notification", "data": notification})
        dead: list[WebSocketServerProtocol] = []
        for ws in conns:
            try:
                await ws.send(message)
            except websockets.ConnectionClosed:
                dead.append(ws)
        for ws in dead:
            conns.discard(ws)

    # -- serialisation ---------------------------------------------------

    @staticmethod
    def _serialize(payload: Any) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "offer_id": payload.offer_id,
            "product_name": payload.product_name,
            "personalization_reason": payload.personalization_reason,
            "cta_url": str(payload.cta_url),
            "customer_id": payload.customer_id,
            "read": False,
            "timestamp": time.time(),
        }
