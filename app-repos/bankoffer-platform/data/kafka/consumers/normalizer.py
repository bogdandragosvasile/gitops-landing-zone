"""Transaction normalizer — standardises merchant categories, converts
currencies, and buckets amounts for downstream feature engineering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Merchant Category Code (MCC) mapping
# ---------------------------------------------------------------------------

# Maps raw MCC codes to a standardised top-level category.  In production
# this would be a maintained reference table; here we cover the most common
# groups.
_MCC_CATEGORY_MAP: dict[str, str] = {
    # Groceries & supermarkets
    "5411": "groceries",
    "5422": "groceries",
    "5441": "groceries",
    "5451": "groceries",
    "5462": "groceries",
    # Restaurants & food
    "5812": "dining",
    "5813": "dining",
    "5814": "dining",
    # Travel
    "3000": "travel",
    "3001": "travel",
    "4511": "travel",
    "4722": "travel",
    "7011": "travel",
    # Fuel
    "5541": "fuel",
    "5542": "fuel",
    # Healthcare
    "5912": "healthcare",
    "8011": "healthcare",
    "8021": "healthcare",
    "8099": "healthcare",
    # Utilities
    "4900": "utilities",
    # Entertainment
    "7832": "entertainment",
    "7841": "entertainment",
    "7922": "entertainment",
    "7929": "entertainment",
    # Retail / shopping
    "5311": "retail",
    "5331": "retail",
    "5651": "retail",
    "5691": "retail",
    "5699": "retail",
    # Education
    "8211": "education",
    "8220": "education",
    "8241": "education",
    "8299": "education",
    # Financial services
    "6010": "financial_services",
    "6011": "financial_services",
    "6012": "financial_services",
    "6051": "financial_services",
}

_DEFAULT_CATEGORY = "other"

# ---------------------------------------------------------------------------
# Static exchange rates (would be fetched from a rates service in production)
# ---------------------------------------------------------------------------

_EXCHANGE_RATES_TO_USD: dict[str, Decimal] = {
    "USD": Decimal("1.0"),
    "EUR": Decimal("1.09"),
    "GBP": Decimal("1.27"),
    "RON": Decimal("0.22"),
    "CHF": Decimal("1.12"),
    "JPY": Decimal("0.0067"),
    "CAD": Decimal("0.74"),
    "AUD": Decimal("0.65"),
}

# ---------------------------------------------------------------------------
# Amount buckets
# ---------------------------------------------------------------------------


class AmountBucket(str, Enum):
    MICRO = "micro"          # < 10 USD
    SMALL = "small"          # 10 – 50
    MEDIUM = "medium"        # 50 – 200
    LARGE = "large"          # 200 – 1000
    HIGH_VALUE = "high_value"  # > 1000


def _bucket_amount(usd_amount: Decimal) -> AmountBucket:
    if usd_amount < 10:
        return AmountBucket.MICRO
    if usd_amount < 50:
        return AmountBucket.SMALL
    if usd_amount < 200:
        return AmountBucket.MEDIUM
    if usd_amount < 1000:
        return AmountBucket.LARGE
    return AmountBucket.HIGH_VALUE


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class NormalizedTransaction(BaseModel):
    """Output schema after normalisation."""

    transaction_id: str
    customer_id: str
    original_amount: Decimal
    original_currency: str
    amount_usd: Decimal = Field(
        ..., description="Amount converted to USD (2 dp)"
    )
    amount_bucket: AmountBucket
    merchant_name: str
    merchant_category_code: str
    merchant_category: str = Field(
        ..., description="Standardised top-level category"
    )
    transaction_type: str
    timestamp: datetime
    channel: str
    status: str


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


@dataclass
class TransactionNormalizer:
    """Stateless transformer that standardises raw transaction records.

    * **Merchant categories** — MCC codes are mapped to a fixed taxonomy.
    * **Currency conversion** — amounts are converted to USD for comparable
      feature engineering.
    * **Amount bucketing** — the USD amount is assigned a discrete bucket
      label for categorical features.
    """

    target_currency: str = "USD"
    exchange_rates: dict[str, Decimal] = field(
        default_factory=lambda: dict(_EXCHANGE_RATES_TO_USD)
    )

    def normalize(self, raw: Any) -> NormalizedTransaction:
        """Normalize a single :class:`RawTransaction` (or dict) and return
        a validated :class:`NormalizedTransaction`."""
        if isinstance(raw, dict):
            data = raw
        else:
            # Pydantic model — use model_dump for safe conversion
            data = raw.model_dump()

        amount = Decimal(str(data["amount"]))
        currency = data["currency"].upper()
        amount_usd = self._convert_to_usd(amount, currency)
        bucket = _bucket_amount(amount_usd)
        category = self._map_category(data["merchant_category_code"])

        return NormalizedTransaction(
            transaction_id=data["transaction_id"],
            customer_id=data["customer_id"],
            original_amount=amount,
            original_currency=currency,
            amount_usd=amount_usd,
            amount_bucket=bucket,
            merchant_name=data["merchant_name"],
            merchant_category_code=data["merchant_category_code"],
            merchant_category=category,
            transaction_type=data["transaction_type"],
            timestamp=data["timestamp"],
            channel=data["channel"],
            status=data["status"],
        )

    def normalize_batch(self, records: list[Any]) -> list[NormalizedTransaction]:
        """Normalize a batch and return only successfully validated records."""
        results: list[NormalizedTransaction] = []
        for rec in records:
            try:
                results.append(self.normalize(rec))
            except Exception:
                logger.warning(
                    "Skipping invalid record during batch normalisation: %s", rec
                )
        return results

    # -- internals -------------------------------------------------------

    def _convert_to_usd(self, amount: Decimal, currency: str) -> Decimal:
        rate = self.exchange_rates.get(currency)
        if rate is None:
            logger.warning(
                "No exchange rate for %s — using 1:1 fallback", currency
            )
            rate = Decimal("1.0")
        return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _map_category(mcc: str) -> str:
        return _MCC_CATEGORY_MAP.get(mcc, _DEFAULT_CATEGORY)
