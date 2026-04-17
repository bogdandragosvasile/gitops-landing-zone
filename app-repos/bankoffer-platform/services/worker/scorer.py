"""Product scorer: uses Anthropic Claude to generate relevance scores for offers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Protocol for LLM API clients (allows easy mocking)."""

    def create_message(self, prompt: str, system: str, max_tokens: int) -> str: ...


@dataclass
class ScoredProduct:
    product_id: str
    product_name: str
    product_type: str
    relevance_score: float
    confidence_score: float
    personalization_reason: str


@dataclass
class ScoringContext:
    customer_id: str
    life_stage: str
    risk_score: float
    segments: list[str]
    annual_income: float
    spending_categories: list[dict[str, Any]] = field(default_factory=list)


# Default product catalog used when none is injected
DEFAULT_PRODUCTS = [
    {"id": "prod_cc_cashback", "name": "CashBack Plus Card", "type": "credit_card"},
    {"id": "prod_cc_travel", "name": "Travel Rewards Card", "type": "credit_card"},
    {"id": "prod_savings_high", "name": "High-Yield Savings", "type": "savings"},
    {"id": "prod_mortgage_fixed", "name": "Fixed-Rate Mortgage", "type": "mortgage"},
    {"id": "prod_loan_personal", "name": "Personal Loan", "type": "personal_loan"},
    {"id": "prod_invest_managed", "name": "Managed Portfolio", "type": "investment"},
    {"id": "prod_cd_12m", "name": "12-Month CD", "type": "certificate_of_deposit"},
    {"id": "prod_insurance_life", "name": "Term Life Insurance", "type": "insurance"},
]

SCORING_SYSTEM_PROMPT = """You are a bank product recommendation engine. Given a customer profile and a product,
output a JSON object with exactly these fields:
  - relevance_score: float 0.0-1.0 (how relevant this product is to the customer)
  - confidence_score: float 0.0-1.0 (how confident you are in this assessment)
  - personalization_reason: string (one sentence explaining the recommendation)

Base your scores on the customer's life stage, risk tolerance, income, segments, and spending patterns.
Only output valid JSON. No markdown fences."""


def _build_scoring_prompt(context: ScoringContext, product: dict[str, Any]) -> str:
    """Build the user prompt for a single product scoring call."""
    return (
        f"Customer: id={context.customer_id}, life_stage={context.life_stage}, "
        f"risk_score={context.risk_score}, segments={context.segments}, "
        f"income={context.annual_income}, "
        f"top_spending={context.spending_categories[:5]}\n\n"
        f"Product: id={product['id']}, name={product['name']}, type={product['type']}\n\n"
        "Score this product for this customer."
    )


def _parse_llm_response(raw: str, product: dict[str, Any]) -> ScoredProduct:
    """Parse the LLM JSON response into a ScoredProduct."""
    import json

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        # Try to extract JSON from potential markdown fences
        import re

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise ValueError(f"Could not parse LLM response as JSON: {raw[:200]}")

    relevance = float(data["relevance_score"])
    confidence = float(data["confidence_score"])
    reason = str(data["personalization_reason"])

    # Clamp scores
    relevance = max(0.0, min(1.0, relevance))
    confidence = max(0.0, min(1.0, confidence))

    return ScoredProduct(
        product_id=product["id"],
        product_name=product["name"],
        product_type=product["type"],
        relevance_score=round(relevance, 4),
        confidence_score=round(confidence, 4),
        personalization_reason=reason,
    )


def score_products(
    context: ScoringContext,
    llm_client: LLMClient,
    products: list[dict[str, Any]] | None = None,
    max_tokens: int = 256,
) -> list[ScoredProduct]:
    """Score all products for a given customer context using the LLM.

    Args:
        context: Customer profile context for scoring.
        llm_client: LLM client implementing the create_message protocol.
        products: Product catalog; defaults to DEFAULT_PRODUCTS.
        max_tokens: Max tokens per LLM call.

    Returns:
        List of ScoredProduct sorted by relevance_score descending.
    """
    if products is None:
        products = DEFAULT_PRODUCTS

    scored: list[ScoredProduct] = []
    for product in products:
        prompt = _build_scoring_prompt(context, product)
        try:
            raw_response = llm_client.create_message(
                prompt=prompt,
                system=SCORING_SYSTEM_PROMPT,
                max_tokens=max_tokens,
            )
            result = _parse_llm_response(raw_response, product)
            scored.append(result)
        except Exception:
            logger.exception("Failed to score product %s for customer %s", product["id"], context.customer_id)
            # Assign a low default score so the product isn't silently dropped
            scored.append(
                ScoredProduct(
                    product_id=product["id"],
                    product_name=product["name"],
                    product_type=product["type"],
                    relevance_score=0.0,
                    confidence_score=0.0,
                    personalization_reason="Scoring unavailable",
                )
            )

    scored.sort(key=lambda s: s.relevance_score, reverse=True)
    logger.info("Scored %d products for customer %s", len(scored), context.customer_id)
    return scored
