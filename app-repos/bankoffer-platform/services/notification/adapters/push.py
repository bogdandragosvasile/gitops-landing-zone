"""Push notification adapter — FCM (Android) and APNs (iOS)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)

# APNs is handled via the FCM HTTP v1 API for iOS when an APNS token is
# registered.  For direct APNs integration (e.g. VoIP or silent pushes)
# a dedicated APNs client can be layered in.


@dataclass
class PushAdapter:
    """Sends push notifications through Firebase Cloud Messaging.

    FCM natively bridges to APNs for iOS devices, so a single send path
    covers both platforms.  The adapter expects a Firebase service-account
    JSON whose path is set via ``GOOGLE_APPLICATION_CREDENTIALS``.

    If ``fcm_dry_run`` is *True*, messages are validated by FCM but not
    actually delivered — useful in staging environments.
    """

    fcm_dry_run: bool = field(
        default_factory=lambda: os.getenv("FCM_DRY_RUN", "false").lower() == "true"
    )
    _app: firebase_admin.App | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not firebase_admin._apps:
            cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if cred_path:
                cred = credentials.Certificate(cred_path)
                self._app = firebase_admin.initialize_app(cred)
            else:
                logger.warning(
                    "GOOGLE_APPLICATION_CREDENTIALS not set — "
                    "FCM will not be available"
                )
        else:
            self._app = firebase_admin.get_app()

    # -- public API ------------------------------------------------------

    async def send(self, payload: Any) -> str | None:
        """Send a push notification for the given ``NotificationPayload``.

        Returns the FCM message ID on success, or *None* when the adapter
        is not initialised.
        """
        if self._app is None:
            logger.error("FCM app not initialised — cannot send push")
            return None

        device_token = await self._resolve_device_token(payload.customer_id)
        if device_token is None:
            logger.warning(
                "No device token for customer %s — skipping push",
                payload.customer_id,
            )
            return None

        message = self._build_message(device_token, payload)
        response = messaging.send(message, dry_run=self.fcm_dry_run)
        logger.info("FCM response for offer %s: %s", payload.offer_id, response)
        return response

    # -- internals -------------------------------------------------------

    def _build_message(
        self, device_token: str, payload: Any
    ) -> messaging.Message:
        """Construct an FCM ``Message`` with platform-specific overrides."""
        notification = messaging.Notification(
            title=f"New offer: {payload.product_name}",
            body=payload.personalization_reason,
        )

        # Android-specific config
        android = messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                click_action="OPEN_OFFER_DETAIL",
                channel_id="bank_offers",
            ),
        )

        # APNs-specific config (delivered via FCM bridge)
        apns = messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(
                        title=f"New offer: {payload.product_name}",
                        body=payload.personalization_reason,
                    ),
                    badge=1,
                    sound="default",
                    category="OFFER",
                ),
            ),
        )

        data: dict[str, str] = {
            "offer_id": payload.offer_id,
            "cta_url": str(payload.cta_url),
            "product_name": payload.product_name,
        }

        return messaging.Message(
            token=device_token,
            notification=notification,
            android=android,
            apns=apns,
            data=data,
        )

    async def _resolve_device_token(self, customer_id: str) -> str | None:
        """Look up the FCM device token for *customer_id*.

        In production this queries a device-registry service or database.
        This stub returns ``None`` so callers degrade gracefully until the
        registry is wired up.
        """
        # TODO: integrate with device-token registry
        _ = customer_id
        return None
