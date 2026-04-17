"""Offers router - serves ranked product offers with compliance checks."""

import json
import logging
import os
import time
import uuid
from datetime import datetime

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from services.api.middleware.auth import get_current_customer_id
from services.api.models import Offer, OfferResponse
from services.api.metrics import (
    CONSENT_BLOCKS, KILL_SWITCH_ACTIVE, OFFER_SCORING_DURATION,
    OFFERS_GENERATED, OFFERS_PER_REQUEST, PROFILE_BUILD_DURATION,
    SUITABILITY_CHECKS, OFFER_SCORE_BY_AGE, OFFER_SCORE_BY_INCOME,
)
from services.worker.profiler import build_profile
from services.worker.ranker import rank_offers

logger = logging.getLogger(__name__)

router = APIRouter()

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

# ===== Product catalog =====
PRODUCTS = [
    {"id": "prod_etf_starter", "name": "ETF Starter", "type": "investment"},
    {"id": "prod_etf_growth", "name": "ETF Growth", "type": "investment"},
    {"id": "prod_mutual_funds", "name": "Mutual Funds", "type": "investment"},
    {"id": "prod_managed_portfolio", "name": "Managed Portfolio", "type": "investment"},
    {"id": "prod_state_bonds", "name": "State Bonds", "type": "bond"},
    {"id": "prod_savings_deposit", "name": "Savings Deposit", "type": "savings"},
    {"id": "prod_private_pension", "name": "Private Pension", "type": "retirement"},
    {"id": "prod_personal_loan", "name": "Personal Loan", "type": "personal_loan"},
    {"id": "prod_mortgage", "name": "Mortgage", "type": "mortgage"},
    {"id": "prod_credit_card", "name": "Credit Card", "type": "credit_card"},
    {"id": "prod_life_insurance", "name": "Life Insurance", "type": "insurance"},
    {"id": "prod_travel_insurance", "name": "Travel Insurance", "type": "insurance"},
]

# ===== Requirement 2: Suitability - product risk levels (MiFID II / IDD) =====
PRODUCT_RISK_LEVEL = {
    "prod_etf_starter": "moderate",
    "prod_etf_growth": "high",
    "prod_mutual_funds": "moderate",
    "prod_managed_portfolio": "high",
    "prod_state_bonds": "low",
    "prod_savings_deposit": "low",
    "prod_private_pension": "low",
    "prod_personal_loan": "moderate",
    "prod_mortgage": "moderate",
    "prod_credit_card": "low",
    "prod_life_insurance": "low",
    "prod_travel_insurance": "low",
}

RISK_HIERARCHY = {"low": 1, "moderate": 2, "high": 3}

CREDIT_PRODUCTS = {"prod_personal_loan", "prod_mortgage", "prod_credit_card"}

# Life stage -> base relevance per product type
LIFE_STAGE_BASE = {
    "new_graduate": {
        "investment": 0.25, "bond": 0.08, "savings": 0.50, "retirement": 0.08,
        "personal_loan": 0.35, "mortgage": 0.05, "credit_card": 0.55, "insurance": 0.15,
    },
    "young_family": {
        "investment": 0.30, "bond": 0.15, "savings": 0.40, "retirement": 0.20,
        "personal_loan": 0.25, "mortgage": 0.50, "credit_card": 0.35, "insurance": 0.45,
    },
    "mid_career": {
        "investment": 0.45, "bond": 0.30, "savings": 0.30, "retirement": 0.40,
        "personal_loan": 0.15, "mortgage": 0.30, "credit_card": 0.25, "insurance": 0.30,
    },
    "pre_retirement": {
        "investment": 0.35, "bond": 0.55, "savings": 0.45, "retirement": 0.65,
        "personal_loan": 0.08, "mortgage": 0.10, "credit_card": 0.15, "insurance": 0.40,
    },
    "retired": {
        "investment": 0.20, "bond": 0.60, "savings": 0.55, "retirement": 0.25,
        "personal_loan": 0.05, "mortgage": 0.05, "credit_card": 0.10, "insurance": 0.35,
    },
}


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


# ===== Requirement 2: Suitability check (MiFID II hard filter) =====

def _check_suitability(product_id: str, customer_risk_profile: str,
                       financial_health: str) -> tuple[bool, str | None]:
    """Hard suitability filter. Returns (suitable, reason_if_excluded)."""
    product_risk = PRODUCT_RISK_LEVEL.get(product_id, "moderate")
    customer_level = RISK_HIERARCHY.get(customer_risk_profile.lower(), 2)
    product_level = RISK_HIERARCHY.get(product_risk, 2)

    if product_level > customer_level:
        return False, (
            f"Product risk level ({product_risk}) exceeds client risk profile "
            f"({customer_risk_profile}) — MiFID II suitability filter"
        )

    if financial_health == "fragile" and product_id in CREDIT_PRODUCTS:
        return False, (
            "Client financial health is fragile — credit products excluded "
            "per responsible lending policy"
        )

    return True, None


def _check_suitability_dynamic(product_id: str, customer_risk_profile: str,
                               financial_health: str, product_risk: str,
                               credit_products: set) -> tuple[bool, str | None]:
    """Suitability filter using dynamic product catalog data."""
    customer_level = RISK_HIERARCHY.get(customer_risk_profile.lower(), 2)
    product_level = RISK_HIERARCHY.get(product_risk, 2)

    if product_level > customer_level:
        return False, (
            f"Product risk level ({product_risk}) exceeds client risk profile "
            f"({customer_risk_profile}) — MiFID II suitability filter"
        )

    if financial_health == "fragile" and product_id in credit_products:
        return False, (
            "Client financial health is fragile — credit products excluded "
            "per responsible lending policy"
        )

    return True, None


# ===== Requirement 1: Explainability (GDPR art.22 + AI Act) =====

def _build_explanation(product: dict, reasons: list[str], f: dict,
                       life_stage: str) -> str:
    """Build a full natural-language explanation with actual customer values."""
    product_name = product["name"]
    parts = []

    idle_cash = _safe_float(f.get("idle_cash"))
    risk_profile = str(f.get("risk_profile") or "moderate").lower()
    income = _safe_float(f.get("annual_income"), 50000)
    dependents = _safe_int(f.get("dependents"))
    dti = _safe_float(f.get("debt_to_income"))

    parts.append(f"We recommend {product_name} for you")

    detail_parts = []
    if idle_cash > 5000 and product["type"] in ("investment", "bond", "savings"):
        detail_parts.append(f"you have {idle_cash:,.0f} in idle cash available for deployment")
    if risk_profile:
        detail_parts.append(f"your risk profile is {risk_profile}")
    if dependents >= 2 and product["type"] == "insurance":
        detail_parts.append(f"you have {dependents} dependents who need financial protection")
    if dti > 3.0 and product["id"] == "prod_personal_loan":
        detail_parts.append(f"your debt-to-income ratio ({dti:.1f}x) suggests consolidation would help")
    if life_stage:
        detail_parts.append(f"your life stage is {life_stage.replace('_', ' ')}")

    if detail_parts:
        parts.append("because " + ", ".join(detail_parts[:3]))

    explanation = " ".join(parts) + "."

    if reasons:
        explanation += " Key signals: " + "; ".join(reasons[:3]) + "."

    return explanation


def _score_product(product: dict, life_stage: str, risk_score: float,
                   income: float, f: dict, sensitive_consent: bool = True,
                   family_consent: bool = True) -> dict:
    """Rule-based scoring for a single product using customer signals.

    NOTE: city is deliberately excluded from scoring (AI Act compliance).
    marital_status & dependents excluded unless family_context_consent granted (GDPR Art. 9).
    """
    pid = product["id"]
    ptype = product["type"]
    base = LIFE_STAGE_BASE.get(life_stage, LIFE_STAGE_BASE["mid_career"]).get(ptype, 0.2)

    boost = 0.0
    penalty = 0.0
    reasons = []

    age = _safe_int(f.get("age"), 30)
    # dependents: only use if family_context_consent granted (GDPR Art. 9)
    dependents = _safe_int(f.get("dependents"), 0) if family_consent else 0
    idle_cash = _safe_float(f.get("idle_cash"))
    savings_rate = _safe_float(f.get("savings_rate"))
    dti = _safe_float(f.get("debt_to_income"))
    investment_gap = _safe_int(f.get("investment_gap_flag"))
    category = str(f.get("dominant_spend_category") or "").lower()
    trend = str(f.get("balance_trend") or "").lower()
    risk_profile = str(f.get("risk_profile") or "").lower()
    homeowner = str(f.get("homeowner_status") or "").lower()
    existing = str(f.get("existing_products") or "").lower()
    # city: EXCLUDED from scoring (AI Act compliance)
    # marital_status: EXCLUDED from scoring (requires separate consent)

    # Existing-product penalties
    if "mortgage" in existing and pid == "prod_mortgage":
        penalty += 0.5
        reasons.append("already has mortgage")
    if "credit_card" in existing and pid == "prod_credit_card":
        penalty += 0.4
        reasons.append("already has credit card")
    if "loan" in existing and pid == "prod_personal_loan":
        penalty += 0.4
        reasons.append("already has a loan")

    # Risk profile
    if risk_profile == "high":
        if ptype == "investment":
            boost += 0.20
            reasons.append("high risk appetite favours equity products")
        elif ptype in ("bond", "savings"):
            penalty += 0.10
    elif risk_profile == "low":
        if ptype in ("bond", "savings"):
            boost += 0.20
            reasons.append("low risk profile suits capital-preservation products")
        elif ptype == "investment":
            penalty += 0.15
    elif risk_profile == "moderate":
        if pid in ("prod_mutual_funds", "prod_state_bonds", "prod_etf_starter"):
            boost += 0.10
            reasons.append("balanced risk appetite suits diversified options")

    # Idle cash
    if idle_cash > 10000:
        if ptype in ("investment", "bond", "savings"):
            boost += 0.25
            reasons.append(f"high idle cash ({idle_cash:,.0f}) ready for deployment")
    elif idle_cash > 5000:
        if ptype in ("investment", "savings"):
            boost += 0.12
            reasons.append("idle cash available for investment")

    # Investment gap
    if investment_gap == 1:
        if pid == "prod_etf_starter":
            boost += 0.25
            reasons.append("investment gap detected — ETF Starter is ideal entry point")
        elif pid == "prod_mutual_funds":
            boost += 0.20
            reasons.append("investment gap makes mutual funds a strong fit")
        elif ptype == "investment":
            boost += 0.10

    # Savings rate
    if savings_rate >= 0.5:
        if ptype == "retirement":
            boost += 0.20
            reasons.append("strong saver profile ideal for pension planning")
        elif ptype == "bond":
            boost += 0.10
            reasons.append("consistent savings enable long-term bond allocation")
    elif savings_rate <= 0.0:
        if pid == "prod_savings_deposit":
            boost += 0.15
            reasons.append("savings deposit can help build a savings habit")

    # Debt-to-income
    if dti > 3.0:
        if pid == "prod_personal_loan":
            boost += 0.25
            reasons.append(f"high debt-to-income ({dti:.1f}x) — consolidation recommended")
        if ptype in ("investment", "bond"):
            penalty += 0.15
    elif dti < 0.5:
        if ptype in ("investment", "bond"):
            boost += 0.10
            reasons.append("low leverage enables investment capacity")

    # Dominant spend category
    if category == "travel":
        if pid == "prod_travel_insurance":
            boost += 0.30
            reasons.append("travel-heavy spending pattern makes insurance essential")
        elif pid == "prod_credit_card" and "credit_card" not in existing:
            boost += 0.15
            reasons.append("travel rewards card aligns with spending habits")
    elif category == "shopping":
        if pid == "prod_credit_card" and "credit_card" not in existing:
            boost += 0.12
            reasons.append("cashback card matches shopping-heavy lifestyle")
    elif category == "rent":
        if pid == "prod_mortgage" and "mortgage" not in existing and homeowner == "rent":
            boost += 0.20
            reasons.append("renter with rent as top expense — mortgage could reduce costs")

    # Homeowner status
    if homeowner == "rent" and pid == "prod_mortgage" and "mortgage" not in existing:
        boost += 0.15
        reasons.append("renter status suggests home ownership opportunity")
    elif homeowner == "owner" and pid == "prod_mortgage" and "mortgage" not in existing:
        boost += 0.05

    # Dependents (only when family_context_consent is active — GDPR Art. 9)
    if family_consent:
        if dependents >= 2:
            if pid == "prod_life_insurance":
                boost += 0.30
                reasons.append(f"{dependents} dependents — life insurance is critical")
            elif pid == "prod_private_pension":
                boost += 0.10
                reasons.append("family responsibility supports pension planning")
        elif dependents == 1:
            if pid == "prod_life_insurance":
                boost += 0.15
                reasons.append("family dependent increases life insurance need")
        elif dependents == 0:
            if pid == "prod_life_insurance":
                penalty += 0.15

    # Balance trend
    if trend == "growing":
        if ptype in ("investment", "bond"):
            boost += 0.08
            reasons.append("growing balance supports new investments")
    elif trend == "declining":
        if ptype == "investment":
            penalty += 0.10
        if pid == "prod_savings_deposit":
            boost += 0.10
            reasons.append("declining balance — savings buffer recommended")

    # Income-based
    if income >= 120000:
        if pid == "prod_managed_portfolio":
            boost += 0.25
            reasons.append("premium income tier qualifies for managed portfolio")
        elif pid == "prod_etf_starter":
            penalty += 0.10
    elif income >= 80000:
        if pid == "prod_etf_growth":
            boost += 0.15
            reasons.append("solid income supports growth-oriented ETF strategy")
        elif pid == "prod_managed_portfolio":
            boost += 0.08
    elif income < 45000:
        if pid == "prod_personal_loan" and dti < 2.0:
            boost += 0.12
            reasons.append("personal loan can supplement lower income")
        if pid == "prod_managed_portfolio":
            penalty += 0.20

    # Age-specific
    if age <= 25:
        if pid == "prod_etf_starter":
            boost += 0.15
            reasons.append("young age makes time-in-market advantage huge")
        elif pid == "prod_credit_card" and "credit_card" not in existing:
            boost += 0.10
            reasons.append("first credit card builds credit history early")
    elif age >= 50:
        if pid == "prod_private_pension":
            boost += 0.20
            reasons.append("approaching retirement — pension planning is urgent")
        elif pid == "prod_state_bonds":
            boost += 0.12
            reasons.append("state bonds provide stable pre-retirement income")

    # ===== Generic scoring for AI-generated products (prod_ai_*) =====
    # These products don't have hardcoded pid rules above, so apply
    # type-level signals to ensure they score competitively.
    if pid.startswith("prod_ai_"):
        # Investment products: boost for high idle cash and growth trend
        if ptype == "investment":
            if idle_cash > 10000:
                boost += 0.15
                reasons.append(f"significant idle cash ({idle_cash:,.0f}) available for this opportunity")
            if risk_profile == "high":
                boost += 0.10
                reasons.append("risk appetite matches this investment product")
            if trend == "growing":
                boost += 0.08
                reasons.append("positive balance trend supports new investment")
        # Savings products: boost for low savings rate
        elif ptype == "savings":
            if savings_rate < 0.2:
                boost += 0.15
                reasons.append("current savings rate suggests this product could help")
            if risk_profile == "low":
                boost += 0.10
                reasons.append("capital-preservation profile aligns well")
        # Lending products: boost for low debt, penalize high debt
        elif ptype == "personal_loan":
            if dti < 1.5 and income >= 40000:
                boost += 0.12
                reasons.append("low leverage and adequate income support borrowing capacity")
            elif dti > 3.0:
                penalty += 0.15
        # Mortgage products: boost for renters
        elif ptype == "mortgage":
            if homeowner == "rent" and "mortgage" not in existing:
                boost += 0.20
                reasons.append("renter profile makes this mortgage product highly relevant")
            if income >= 60000 and dti < 2.0:
                boost += 0.10
                reasons.append("income and debt levels qualify for mortgage")
        # Credit card products
        elif ptype == "credit_card":
            if "credit_card" not in existing:
                boost += 0.10
                reasons.append("no existing credit card — this fills a gap")
            if category == "shopping":
                boost += 0.10
                reasons.append("spending pattern benefits from card rewards")
        # Insurance products: boost for families
        elif ptype == "insurance":
            if family_consent and dependents >= 1:
                boost += 0.20
                reasons.append(f"family with {dependents} dependent(s) benefits from insurance coverage")
            if age >= 40:
                boost += 0.08
                reasons.append("age bracket increases insurance value")

    # Final scores
    relevance = max(0.0, min(1.0, base + boost - penalty))
    confidence = max(0.2, min(1.0, 0.50 + boost * 0.6 + (0.1 if len(reasons) >= 2 else 0)))
    reason_text = "; ".join(reasons[:3]) if reasons else f"Recommended for {life_stage.replace('_', ' ')} profile"

    explanation = _build_explanation(product, reasons, f, life_stage)

    return {
        "product_id": pid,
        "product_name": product["name"],
        "product_type": ptype,
        "relevance_score": round(relevance, 4),
        "confidence_score": round(confidence, 4),
        "personalization_reason": reason_text,
        "explanation": explanation,
    }


@router.get(
    "/eligibility/all",
    summary="Real-time eligibility counts per product across all customers",
    description="Returns how many customers are eligible vs ineligible for each product.",
)
async def get_eligibility_counts(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
):
    """Run suitability checks for all customers against all products. Returns per-product counts."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""SELECT c.customer_id, c.risk_profile, c.financial_health,
                        c.profiling_consent
                 FROM customers c ORDER BY c.customer_id""")
            )
            customers = result.mappings().fetchall()

        # Load products from DB with fallback
        products_list = PRODUCTS
        p_risk_map = PRODUCT_RISK_LEVEL
        p_credit_set = CREDIT_PRODUCTS
        try:
            async with session_factory() as session:
                db_result = await session.execute(
                    text("SELECT product_id, product_name, type, risk_level, is_credit_product FROM products WHERE active = TRUE")
                )
                db_rows = db_result.mappings().fetchall()
                if db_rows:
                    products_list = [
                        {"id": r["product_id"], "name": r["product_name"] or r["product_id"], "type": r["type"] or "investment"}
                        for r in db_rows if r.get("product_id")
                    ]
                    p_risk_map = {r["product_id"]: (r["risk_level"] or "moderate") for r in db_rows if r.get("product_id")}
                    p_credit_set = {r["product_id"] for r in db_rows if r.get("product_id") and r.get("is_credit_product")}
        except Exception:
            pass

        counts = {}
        for product in products_list:
            pid = product["id"]
            eligible = 0
            ineligible = 0
            no_consent = 0
            for cust in customers:
                if not cust["profiling_consent"]:
                    no_consent += 1
                    continue
                risk = str(cust["risk_profile"] or "moderate").lower()
                health = str(cust["financial_health"] or "stable")
                suitable, _ = _check_suitability_dynamic(pid, risk, health, p_risk_map.get(pid, "moderate"), p_credit_set)
                if suitable:
                    eligible += 1
                else:
                    ineligible += 1
            counts[pid] = {
                "product_id": pid,
                "product_name": product["name"],
                "product_type": product["type"],
                "eligible": eligible,
                "ineligible": ineligible,
                "no_consent": no_consent,
                "total": len(customers),
            }

        return {"products": counts, "total_customers": len(customers)}
    except Exception as e:
        logger.error("Failed to compute eligibility: %s", e)
        raise HTTPException(status_code=500, detail="Failed to compute eligibility counts")


@router.get(
    "/{customer_id}",
    response_model=OfferResponse,
    summary="Get ranked offers for a customer",
)
async def get_offers(
    customer_id: str,
    request: Request,
    top_n: int = Query(default=5, ge=1, le=20, description="Number of offers to return"),
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Return top-N ranked offers with suitability checks, audit trail, and explainability."""
    if not DEMO_MODE and authenticated_customer != customer_id:
        # Allow staff/admin: Keycloak SSO users have a UUID sub, not a DB integer ID.
        # If authenticated_customer looks like a UUID it is a staff user accessing on behalf of a customer.
        import re as _re
        _is_staff = bool(_re.match(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            authenticated_customer, _re.I,
        ))
        if not _is_staff and authenticated_customer not in ("__api_token__", "__demo__"):
            raise HTTPException(status_code=403, detail="Not authorized")

    session_factory = request.app.state.db_session_factory
    recommendation_id = str(uuid.uuid4())
    scoring_start = time.monotonic()

    # ===== Fetch customer features =====
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM customer_features WHERE customer_id = :cid"),
                {"cid": customer_id},
            )
            row = result.mappings().fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Customer not found")
            features = dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch features for %s: %s", customer_id, e)
        raise HTTPException(status_code=500, detail="Failed to retrieve customer data")

    # ===== AI Act Art. 14: Check kill-switch =====
    try:
        async with session_factory() as session:
            ks_result = await session.execute(
                text("SELECT active FROM model_kill_switch ORDER BY id DESC LIMIT 1")
            )
            ks_row = ks_result.mappings().fetchone()
            if ks_row and ks_row["active"]:
                KILL_SWITCH_ACTIVE.set(1)
                return OfferResponse(
                    customer_id=customer_id,
                    recommendation_id=recommendation_id,
                    offers=[],
                    excluded_products=[{"product_id": "all", "product_name": "All Products",
                                        "product_type": "system", "reason": "Model suspended via kill-switch (AI Act Art. 14)"}],
                    generated_at=datetime.utcnow(),
                    model_version="rules-1.0.0",
                    consent_valid=True,
                )
    except Exception as e:
        logger.warning("Failed to check kill-switch: %s", e)

    # ===== Requirement 5: Check granular consent =====
    consent_snapshot = {
        "profiling_consent": True, "automated_decision_consent": True,
        "family_context_consent": True, "sensitive_data_consent": True,
        "marketing_push": False, "marketing_email": False, "marketing_sms": False,
    }
    external_id = ""
    financial_health = "stable"
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""SELECT external_id, financial_health,
                        profiling_consent, profiling_consent_ts,
                        automated_decision_consent, automated_decision_consent_ts,
                        family_context_consent, family_context_consent_ts,
                        sensitive_data_consent, sensitive_data_consent_ts,
                        marketing_push, marketing_email, marketing_sms
                 FROM customers WHERE customer_id = :cid"""),
                {"cid": customer_id},
            )
            cust_row = result.mappings().fetchone()
            if cust_row:
                external_id = str(cust_row["external_id"])
                financial_health = cust_row["financial_health"] or "stable"
                consent_snapshot = {
                    "profiling_consent": bool(cust_row["profiling_consent"]),
                    "profiling_consent_ts": str(cust_row["profiling_consent_ts"] or ""),
                    "automated_decision_consent": bool(cust_row["automated_decision_consent"]),
                    "automated_decision_consent_ts": str(cust_row["automated_decision_consent_ts"] or ""),
                    "family_context_consent": bool(cust_row["family_context_consent"]),
                    "family_context_consent_ts": str(cust_row["family_context_consent_ts"] or ""),
                    "sensitive_data_consent": bool(cust_row["sensitive_data_consent"]),
                    "sensitive_data_consent_ts": str(cust_row["sensitive_data_consent_ts"] or ""),
                    "marketing_push": bool(cust_row["marketing_push"]),
                    "marketing_email": bool(cust_row["marketing_email"]),
                    "marketing_sms": bool(cust_row["marketing_sms"]),
                }
    except Exception as e:
        logger.warning("Failed to fetch consent for %s: %s", customer_id, e)

    # Consent #1: If no profiling consent, return empty — pipeline cannot process
    if not consent_snapshot["profiling_consent"]:
        CONSENT_BLOCKS.labels(consent_type="profiling").inc()
        return OfferResponse(
            customer_id=external_id or customer_id,
            recommendation_id=recommendation_id,
            offers=[],
            excluded_products=[],
            generated_at=datetime.utcnow(),
            model_version="rules-1.0.0",
            consent_valid=False,
        )

    # Consent #2: Without automated_decision_consent, offers need human review
    human_review_required = not consent_snapshot["automated_decision_consent"]
    # Consent #4: Family context gates marital_status & dependents
    family_consent = consent_snapshot["family_context_consent"]
    # Legacy compat: also check sensitive_data_consent
    sensitive_consent = consent_snapshot["sensitive_data_consent"] or family_consent

    # ===== Build profile =====
    profiler_input = {
        "age": features.get("age", 30),
        "annual_income": max(1.0, _safe_float(features.get("annual_income"), 50000)),
        "dependents": features.get("dependents", 0) if family_consent else 0,
        "account_tenure_years": features.get("account_tenure_years", 0),
        "investment_balance": features.get("investment_balance", 0),
        "savings_ratio": min(1.0, max(0.0, _safe_float(features.get("savings_rate"), 0.1))),
        "loan_to_income": min(1.0, max(0.0, _safe_float(features.get("debt_to_income"), 0))),
    }

    try:
        profile_start = time.monotonic()
        profile = build_profile(customer_id, profiler_input)
        PROFILE_BUILD_DURATION.observe(time.monotonic() - profile_start)
    except Exception as e:
        logger.error("Profiler failed for %s: %s", customer_id, e)
        raise HTTPException(status_code=500, detail="Profile generation failed")

    profile_result = {
        "life_stage": profile.life_stage,
        "risk_score": profile.risk_score,
        "segments": profile.segments,
    }

    # ===== Load product catalog from DB (fallback to hardcoded) =====
    active_products = PRODUCTS
    product_risk_map = PRODUCT_RISK_LEVEL
    credit_set = CREDIT_PRODUCTS
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT product_id, product_name, type, risk_level, is_credit_product FROM products WHERE active = TRUE")
            )
            db_rows = result.mappings().fetchall()
            if db_rows:
                active_products = [
                    {"id": r["product_id"], "name": r["product_name"] or r["product_id"], "type": r["type"] or "investment"}
                    for r in db_rows if r.get("product_id")
                ]
                product_risk_map = {r["product_id"]: (r["risk_level"] or "moderate") for r in db_rows if r.get("product_id")}
                credit_set = {r["product_id"] for r in db_rows if r.get("product_id") and r.get("is_credit_product")}
    except Exception as e:
        logger.warning("Failed to load products from DB, using defaults: %s", e)

    # ===== Requirement 2: Suitability filter (hard) =====
    risk_profile = str(features.get("risk_profile") or "moderate").lower()
    suitable_products = []
    excluded_products = []
    suitability_checks = {}

    for product in active_products:
        product_risk = product_risk_map.get(product["id"], "moderate")
        suitable, reason = _check_suitability_dynamic(
            product["id"], risk_profile, financial_health, product_risk, credit_set
        )
        suitability_checks[product["id"]] = {
            "suitable": suitable,
            "reason": reason,
            "product_risk": product_risk,
            "customer_risk": risk_profile,
        }
        if suitable:
            SUITABILITY_CHECKS.labels(result="passed").inc()
            suitable_products.append(product)
        else:
            SUITABILITY_CHECKS.labels(result="failed").inc()
            excluded_products.append({
                "product_id": product["id"],
                "product_name": product["name"],
                "product_type": product["type"],
                "reason": reason,
            })

    # ===== Score only suitable products =====
    income = _safe_float(features.get("annual_income"), 50000)
    scored = [
        _score_product(p, profile.life_stage, profile.risk_score, income, features,
                       sensitive_consent, family_consent)
        for p in suitable_products
    ]
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)

    # ===== Guardrail #8: Fairness monitoring (demographic parity) =====
    age = _safe_int(features.get("age"), 30)
    age_bracket = (
        "18-25" if age <= 25 else
        "26-35" if age <= 35 else
        "36-50" if age <= 50 else
        "51-65" if age <= 65 else
        "65+"
    )
    income_tier = (
        "low" if income < 30000 else
        "medium" if income < 70000 else
        "high" if income < 120000 else
        "premium"
    )
    for s in scored:
        OFFER_SCORE_BY_AGE.labels(age_bracket=age_bracket, product_type=s["product_type"]).observe(s["relevance_score"])
        OFFER_SCORE_BY_INCOME.labels(income_tier=income_tier, product_type=s["product_type"]).observe(s["relevance_score"])

    ranked = rank_offers(scored)

    # ===== Build response =====
    INVESTMENT_TYPES = {"investment", "bond"}
    offers = []
    for r in ranked[:top_n]:
        requires_suitability = r.product_type in INVESTMENT_TYPES
        offers.append(Offer(
            offer_id=r.offer_id,
            product_name=r.product_name,
            product_type=r.product_type,
            relevance_score=r.relevance_score,
            confidence_score=r.confidence_score,
            personalization_reason=r.personalization_reason,
            explanation=getattr(r, "explanation", "") or next(
                (s.get("explanation", "") for s in scored
                 if s["product_id"] == r.product_id), ""
            ),
            suitability_status="passed",
            human_review_required=human_review_required,
            requires_suitability_confirmation=requires_suitability,
            cta_url=f"/products/{r.product_id}/apply?customer={customer_id}",
        ))

    # ===== Metrics =====
    OFFER_SCORING_DURATION.observe(time.monotonic() - scoring_start)
    OFFERS_PER_REQUEST.observe(len(offers))
    for o in offers:
        OFFERS_GENERATED.labels(product_type=o.product_type).inc()

    # ===== Requirement 4: Audit trail (immutable log) =====
    try:
        async with session_factory() as session:
            # Sanitize features for audit: exclude city (pseudonymization)
            audit_features = {k: v for k, v in features.items()
                             if k not in ("city",) and v is not None}
            # Convert non-serializable types
            for k, v in audit_features.items():
                if hasattr(v, "isoformat"):
                    audit_features[k] = v.isoformat()
                elif not isinstance(v, (str, int, float, bool, type(None))):
                    audit_features[k] = str(v)

            await session.execute(
                text("""INSERT INTO audit_recommendations
                    (recommendation_id, external_customer_id, customer_id,
                     input_features, profile_result, suitability_checks,
                     scored_products, final_offers, excluded_products,
                     model_version, consent_snapshot)
                    VALUES (CAST(:rid AS uuid), CAST(:ext_cid AS uuid), :cid,
                            CAST(:features AS jsonb), CAST(:profile AS jsonb),
                            CAST(:suitability AS jsonb), CAST(:scored AS jsonb),
                            CAST(:offers AS jsonb), CAST(:excluded AS jsonb),
                            :model, CAST(:consent AS jsonb))"""),
                {
                    "rid": recommendation_id,
                    "ext_cid": external_id or str(uuid.uuid4()),
                    "cid": customer_id,
                    "features": json.dumps(audit_features, default=str),
                    "profile": json.dumps(profile_result),
                    "suitability": json.dumps(suitability_checks),
                    "scored": json.dumps(scored, default=str),
                    "offers": json.dumps([o.model_dump() for o in offers], default=str),
                    "excluded": json.dumps(excluded_products),
                    "model": "rules-1.0.0",
                    "consent": json.dumps(consent_snapshot),
                },
            )
            await session.commit()
    except Exception as e:
        logger.critical("AUDIT TRAIL WRITE FAILED for %s: %s — request will fail", customer_id, e)
        raise HTTPException(
            status_code=500,
            detail="Audit trail write failed — recommendation cannot be served without audit record (AI Act Art. 12)",
        )

    # ===== Requirement 6: Pseudonymization - return external_id =====
    return OfferResponse(
        customer_id=external_id or customer_id,
        recommendation_id=recommendation_id,
        offers=offers,
        excluded_products=excluded_products,
        generated_at=datetime.utcnow(),
        model_version="rules-1.0.0",
        consent_valid=True,
    )
