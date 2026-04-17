"""Email notification adapter — SendGrid integration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from string import Template
from typing import Any

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Category,
    ClickTracking,
    Content,
    Email,
    Mail,
    OpenTracking,
    Subject,
    To,
    TrackingSettings,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTML template — kept inline for simplicity; in production this would live
# in a template store (S3, database, etc.) and be versioned independently.
# ---------------------------------------------------------------------------
_HTML_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${product_name}</title>
  <style>
    body { margin:0; padding:0; font-family: Arial, Helvetica, sans-serif;
           background-color: #f4f4f7; }
    .container { max-width: 600px; margin: 0 auto; background: #ffffff;
                 border-radius: 8px; overflow: hidden; }
    .header { background: #003366; color: #ffffff; padding: 24px 32px; }
    .header h1 { margin: 0; font-size: 22px; }
    .body-content { padding: 32px; color: #333333; line-height: 1.6; }
    .body-content h2 { color: #003366; margin-top: 0; }
    .cta-btn { display: inline-block; margin-top: 24px; padding: 14px 28px;
               background: #0066cc; color: #ffffff; text-decoration: none;
               border-radius: 6px; font-weight: bold; }
    .footer { padding: 16px 32px; font-size: 12px; color: #999999;
              text-align: center; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>A new offer just for you</h1>
    </div>
    <div class="body-content">
      <h2>${product_name}</h2>
      <p>${personalization_reason}</p>
      <a href="${cta_url}" class="cta-btn">View Offer</a>
    </div>
    <div class="footer">
      <p>You received this email because you are a valued customer.
         <a href="${unsubscribe_url}">Unsubscribe</a></p>
      <p>Offer ID: ${offer_id}</p>
    </div>
  </div>
</body>
</html>
""")


@dataclass
class EmailAdapter:
    """Sends personalised offer emails via the SendGrid v3 API.

    Configuration is driven by environment variables:

    * ``SENDGRID_API_KEY`` — API key with *Mail Send* permission.
    * ``SENDGRID_FROM_EMAIL`` — verified sender address.
    * ``SENDGRID_FROM_NAME``  — display name (default ``"Bank Offers"``).
    * ``UNSUBSCRIBE_BASE_URL`` — base URL for one-click unsubscribe links.
    """

    from_email: str = field(
        default_factory=lambda: os.getenv("SENDGRID_FROM_EMAIL", "offers@bank.com")
    )
    from_name: str = field(
        default_factory=lambda: os.getenv("SENDGRID_FROM_NAME", "Bank Offers")
    )
    unsubscribe_base_url: str = field(
        default_factory=lambda: os.getenv(
            "UNSUBSCRIBE_BASE_URL", "https://bank.com/unsubscribe"
        )
    )
    _client: SendGridAPIClient | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        api_key = os.getenv("SENDGRID_API_KEY")
        if api_key:
            self._client = SendGridAPIClient(api_key=api_key)
        else:
            logger.warning(
                "SENDGRID_API_KEY not set — email sending will be unavailable"
            )

    # -- public API -------------------------------------------------------

    async def send(self, payload: Any) -> bool:
        """Send a personalised offer email.

        Returns *True* on accepted (2xx) response, *False* otherwise.
        """
        if self._client is None:
            logger.error("SendGrid client not initialised — cannot send email")
            return False

        recipient_email = await self._resolve_email(payload.customer_id)
        if recipient_email is None:
            logger.warning(
                "No email address for customer %s — skipping", payload.customer_id
            )
            return False

        mail = self._build_mail(recipient_email, payload)

        try:
            response = self._client.send(mail)
            accepted = 200 <= response.status_code < 300
            if accepted:
                logger.info(
                    "Email accepted for offer %s to %s (status %d)",
                    payload.offer_id,
                    recipient_email,
                    response.status_code,
                )
            else:
                logger.warning(
                    "SendGrid returned %d for offer %s",
                    response.status_code,
                    payload.offer_id,
                )
            return accepted
        except Exception:
            logger.exception(
                "SendGrid request failed for offer %s", payload.offer_id
            )
            return False

    # -- internals --------------------------------------------------------

    def _build_mail(self, recipient_email: str, payload: Any) -> Mail:
        unsubscribe_url = (
            f"{self.unsubscribe_base_url}?customer_id={payload.customer_id}"
        )

        html_body = _HTML_TEMPLATE.safe_substitute(
            product_name=payload.product_name,
            personalization_reason=payload.personalization_reason,
            cta_url=str(payload.cta_url),
            offer_id=payload.offer_id,
            unsubscribe_url=unsubscribe_url,
        )

        mail = Mail()
        mail.from_email = Email(self.from_email, self.from_name)
        mail.subject = Subject(f"Exclusive offer: {payload.product_name}")
        mail.to = To(recipient_email)
        mail.content = Content("text/html", html_body)
        mail.category = Category("bank_offer")
        mail.tracking_settings = TrackingSettings(
            click_tracking=ClickTracking(enable=True),
            open_tracking=OpenTracking(enable=True),
        )

        return mail

    async def _resolve_email(self, customer_id: str) -> str | None:
        """Resolve the customer's email address.

        In production this queries the customer profile service.
        """
        # TODO: integrate with customer profile service
        _ = customer_id
        return None
