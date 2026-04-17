"""Customer profiler: classifies life stage and scores risk tolerance from features."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

LIFE_STAGE_RULES: dict[str, dict[str, Any]] = {
    "new_graduate": {"age_max": 25, "account_tenure_max": 2},
    "young_family": {"age_min": 25, "age_max": 40, "dependents_min": 1},
    "mid_career": {"age_min": 35, "age_max": 55, "income_min": 60000},
    "pre_retirement": {"age_min": 55, "age_max": 67},
    "retired": {"age_min": 67},
}


@dataclass
class ProfileResult:
    customer_id: str
    life_stage: str
    risk_score: float
    segments: list[str]


def classify_life_stage(
    age: int,
    account_tenure_years: float,
    dependents: int,
    annual_income: float,
) -> str:
    """Classify customer life stage based on demographic features.

    Returns one of: new_graduate, young_family, mid_career, pre_retirement, retired.
    Falls back to 'mid_career' if no rule matches.
    """
    if age < 0:
        raise ValueError("age must be non-negative")

    if age >= 67:
        return "retired"
    if 55 <= age < 67:
        return "pre_retirement"
    if 25 <= age <= 40 and dependents >= 1:
        return "young_family"
    if age < 25 and account_tenure_years <= 2:
        return "new_graduate"
    if 25 <= age < 25 and account_tenure_years <= 2:
        return "new_graduate"
    if annual_income >= 60000 and 35 <= age <= 55:
        return "mid_career"

    # Default fallback
    return "mid_career"


def score_risk_tolerance(
    age: int,
    annual_income: float,
    investment_balance: float,
    savings_ratio: float,
    loan_to_income: float,
) -> float:
    """Score risk tolerance on a 1.0-10.0 scale.

    Higher score = higher risk tolerance.  Combines weighted factors:
      - Age factor: younger = more risk tolerant
      - Income factor: higher income = slightly more risk tolerant
      - Investment ratio: higher investment balance relative to income = more tolerant
      - Savings cushion: higher savings ratio = more tolerant
      - Leverage: higher loan-to-income = less risk tolerant
    """
    if annual_income <= 0:
        raise ValueError("annual_income must be positive")
    if not (0.0 <= savings_ratio <= 1.0):
        raise ValueError("savings_ratio must be between 0 and 1")

    # Age factor: linear decay from 10 (age 18) to 3 (age 80)
    age_factor = max(1.0, min(10.0, 10.0 - (age - 18) * (7.0 / 62.0)))

    # Income factor: log-scaled, normalized to 1-10
    import math

    income_factor = min(10.0, 1.0 + math.log10(max(1, annual_income / 10000)) * 3.0)

    # Investment ratio factor
    inv_ratio = investment_balance / annual_income
    inv_factor = min(10.0, 1.0 + inv_ratio * 3.0)

    # Savings factor
    savings_factor = 1.0 + savings_ratio * 9.0

    # Leverage penalty
    leverage_penalty = min(5.0, loan_to_income * 5.0)

    weights = {
        "age": 0.25,
        "income": 0.15,
        "investment": 0.25,
        "savings": 0.15,
        "leverage": 0.20,
    }

    raw_score = (
        weights["age"] * age_factor
        + weights["income"] * income_factor
        + weights["investment"] * inv_factor
        + weights["savings"] * savings_factor
        - weights["leverage"] * leverage_penalty
    )

    return round(max(1.0, min(10.0, raw_score)), 2)


def build_profile(
    customer_id: str,
    features: dict[str, Any],
) -> ProfileResult:
    """Build a complete customer profile from feature dictionary.

    Expected feature keys:
        age, account_tenure_years, dependents, annual_income,
        investment_balance, savings_ratio, loan_to_income
    """
    age = int(features["age"])
    account_tenure = float(features.get("account_tenure_years", 0))
    dependents = int(features.get("dependents", 0))
    annual_income = float(features["annual_income"])
    investment_balance = float(features.get("investment_balance", 0))
    savings_ratio = float(features.get("savings_ratio", 0.1))
    loan_to_income = float(features.get("loan_to_income", 0))

    life_stage = classify_life_stage(age, account_tenure, dependents, annual_income)
    risk_score = score_risk_tolerance(
        age, annual_income, investment_balance, savings_ratio, loan_to_income
    )

    # Derive segments
    segments: list[str] = [life_stage]
    if annual_income >= 150000:
        segments.append("high_net_worth")
    if savings_ratio >= 0.3:
        segments.append("strong_saver")
    if loan_to_income >= 0.5:
        segments.append("leveraged")

    logger.info(
        "Built profile for %s: life_stage=%s risk=%.2f segments=%s",
        customer_id,
        life_stage,
        risk_score,
        segments,
    )

    return ProfileResult(
        customer_id=customer_id,
        life_stage=life_stage,
        risk_score=risk_score,
        segments=segments,
    )
