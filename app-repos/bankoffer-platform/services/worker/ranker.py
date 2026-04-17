"""Offer ranker: applies business rules, cooldowns, and diversity constraints on scored products."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Business rule defaults
MAX_OFFERS_PER_BATCH = 5
COOLDOWN_DAYS = 30
MAX_SAME_TYPE = 2
MIN_RELEVANCE_THRESHOLD = 0.15
MIN_CONFIDENCE_THRESHOLD = 0.10


@dataclass
class RankedOffer:
    offer_id: str
    product_id: str
    product_name: str
    product_type: str
    relevance_score: float
    confidence_score: float
    personalization_reason: str
    rank: int


@dataclass
class CooldownRecord:
    product_id: str
    dismissed_at: datetime


@dataclass
class RankingConfig:
    max_offers: int = MAX_OFFERS_PER_BATCH
    cooldown_days: int = COOLDOWN_DAYS
    max_same_type: int = MAX_SAME_TYPE
    min_relevance: float = MIN_RELEVANCE_THRESHOLD
    min_confidence: float = MIN_CONFIDENCE_THRESHOLD
    diversity_weight: float = 0.1


def _is_on_cooldown(
    product_id: str,
    cooldowns: list[CooldownRecord],
    cooldown_days: int,
    now: datetime | None = None,
) -> bool:
    """Check if a product is still within its cooldown period after dismissal."""
    if now is None:
        now = datetime.now(timezone.utc)
    for record in cooldowns:
        if record.product_id == product_id:
            if now - record.dismissed_at < timedelta(days=cooldown_days):
                return True
    return False


def _apply_type_diversity(
    offers: list[dict[str, Any]],
    max_same_type: int,
) -> list[dict[str, Any]]:
    """Enforce that no single product type dominates the result set."""
    type_counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for offer in offers:
        ptype = offer["product_type"]
        current = type_counts.get(ptype, 0)
        if current < max_same_type:
            result.append(offer)
            type_counts[ptype] = current + 1
    return result


def rank_offers(
    scored_products: list[dict[str, Any]],
    cooldowns: list[CooldownRecord] | None = None,
    config: RankingConfig | None = None,
    now: datetime | None = None,
) -> list[RankedOffer]:
    """Rank and filter scored products into a final offer list.

    Pipeline:
      1. Filter by minimum relevance and confidence thresholds.
      2. Remove products on cooldown (recently dismissed).
      3. Apply product-type diversity constraint.
      4. Truncate to max_offers.
      5. Assign rank numbers.

    Args:
        scored_products: List of dicts with keys: product_id, product_name,
            product_type, relevance_score, confidence_score, personalization_reason.
        cooldowns: Recent dismissal records for the customer.
        config: Ranking configuration overrides.
        now: Current timestamp (defaults to UTC now).

    Returns:
        Ordered list of RankedOffer.
    """
    if config is None:
        config = RankingConfig()
    if cooldowns is None:
        cooldowns = []
    if now is None:
        now = datetime.now(timezone.utc)

    # 1. Threshold filter
    candidates = [
        p
        for p in scored_products
        if p.get("relevance_score", 0) >= config.min_relevance
        and p.get("confidence_score", 0) >= config.min_confidence
    ]
    logger.debug("After threshold filter: %d / %d", len(candidates), len(scored_products))

    # 2. Cooldown filter
    candidates = [
        p
        for p in candidates
        if not _is_on_cooldown(p["product_id"], cooldowns, config.cooldown_days, now)
    ]
    logger.debug("After cooldown filter: %d candidates", len(candidates))

    # 3. Sort by relevance (already sorted, but be safe)
    candidates.sort(key=lambda p: p.get("relevance_score", 0), reverse=True)

    # 4. Diversity constraint
    candidates = _apply_type_diversity(candidates, config.max_same_type)
    logger.debug("After diversity filter: %d candidates", len(candidates))

    # 5. Truncate
    final = candidates[: config.max_offers]

    # 6. Build ranked offers
    import uuid

    ranked: list[RankedOffer] = []
    for idx, p in enumerate(final, start=1):
        ranked.append(
            RankedOffer(
                offer_id=str(uuid.uuid4()),
                product_id=p["product_id"],
                product_name=p["product_name"],
                product_type=p["product_type"],
                relevance_score=p["relevance_score"],
                confidence_score=p["confidence_score"],
                personalization_reason=p.get("personalization_reason", ""),
                rank=idx,
            )
        )

    logger.info("Ranked %d offers from %d scored products", len(ranked), len(scored_products))
    return ranked
