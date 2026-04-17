"""Profiles router - serves customer profile data."""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from services.api.middleware.auth import get_current_customer_id
from services.api.models import CustomerProfile

logger = logging.getLogger(__name__)

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

router = APIRouter()


@router.get(
    "/{customer_id}",
    response_model=CustomerProfile,
    summary="Get customer profile",
    description="Returns the customer profile including life stage, risk score, and segments.",
)
async def get_profile(
    customer_id: str,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Return the customer profile with life_stage, risk_score, and segments."""
    if not DEMO_MODE and authenticated_customer != customer_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to access this customer profile",
        )

    redis = request.app.state.redis

    # Try cache
    cached = await redis.get(f"profile:{customer_id}")
    if cached:
        return CustomerProfile.model_validate_json(cached)

    # Query DB
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        from sqlalchemy import text

        result = await session.execute(
            text("SELECT data FROM customer_profiles WHERE customer_id = :cid"),
            {"cid": customer_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        data = row[0]
        if isinstance(data, str):
            profile = CustomerProfile.model_validate_json(data)
        else:
            profile = CustomerProfile.model_validate(data)

        # Cache for 15 minutes
        await redis.set(
            f"profile:{customer_id}",
            profile.model_dump_json(),
            ex=900,
        )
        return profile
