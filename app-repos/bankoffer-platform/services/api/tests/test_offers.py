"""Tests for the offers endpoint."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from services.api.main import create_app
from services.api.middleware.auth import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET
from services.api.models import CustomerProfile, LifeStage, IncomeBracket, Offer


def _make_token(customer_id: str, expires_delta: timedelta | None = None) -> str:
    """Create a valid JWT for testing."""
    exp = datetime.utcnow() + (expires_delta or timedelta(hours=1))
    payload = {
        "customer_id": customer_id,
        "exp": exp,
        "aud": JWT_AUDIENCE,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


SAMPLE_PROFILE = CustomerProfile(
    customer_id="cust-001",
    life_stage=LifeStage.MID_CAREER,
    risk_score=6.5,
    segments=["high_value", "digital_first"],
    income_bracket=IncomeBracket.HIGH,
    spending_patterns=[],
)

SAMPLE_OFFERS = [
    Offer(
        offer_id="offer-001",
        product_name="Premium Cash Back Card",
        product_type="credit_card",
        relevance_score=0.95,
        confidence_score=0.88,
        personalization_reason="Your high spending on dining and travel makes this premium rewards card an excellent fit.",
        cta_url="https://bank.example.com/apply/premium-cashback",
    ),
    Offer(
        offer_id="offer-002",
        product_name="High-Yield Savings",
        product_type="savings",
        relevance_score=0.82,
        confidence_score=0.91,
        personalization_reason="Based on your stable income and risk profile, this high-yield savings account maximizes your returns.",
        cta_url="https://bank.example.com/apply/high-yield-savings",
    ),
]


@pytest.fixture
def app():
    """Create a test application with mocked dependencies."""
    application = create_app()

    # Mock DB and Redis on app state
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    application.state.redis = mock_redis
    application.state.db_session_factory = AsyncMock()
    application.state.db_engine = AsyncMock()

    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


class TestGetOffers:
    """Tests for GET /offers/{customer_id}."""

    def test_missing_auth_returns_403(self, client):
        """Request without auth token should be rejected."""
        response = client.get("/offers/cust-001")
        assert response.status_code == 403

    def test_invalid_token_returns_401(self, client):
        """Request with invalid JWT should return 401."""
        response = client.get(
            "/offers/cust-001",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401

    def test_wrong_customer_returns_403(self, client):
        """Token for customer A should not access customer B's offers."""
        token = _make_token("cust-other")
        response = client.get(
            "/offers/cust-001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    @patch("services.api.routers.offers._call_worker_scoring")
    @patch("services.api.routers.offers._fetch_customer_profile")
    def test_successful_offers_response(
        self, mock_fetch_profile, mock_call_worker, client
    ):
        """Valid request should return ranked offers."""
        mock_fetch_profile.return_value = SAMPLE_PROFILE
        mock_call_worker.return_value = SAMPLE_OFFERS

        token = _make_token("cust-001")
        response = client.get(
            "/offers/cust-001",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["customer_id"] == "cust-001"
        assert len(data["offers"]) == 2
        assert data["offers"][0]["offer_id"] == "offer-001"
        assert "personalization_reason" in data["offers"][0]

    @patch("services.api.routers.offers._call_worker_scoring")
    @patch("services.api.routers.offers._fetch_customer_profile")
    def test_top_n_limits_results(
        self, mock_fetch_profile, mock_call_worker, client
    ):
        """The top_n query param should limit the number of offers returned."""
        mock_fetch_profile.return_value = SAMPLE_PROFILE
        mock_call_worker.return_value = SAMPLE_OFFERS

        token = _make_token("cust-001")
        response = client.get(
            "/offers/cust-001?top_n=1",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["offers"]) == 1

    def test_expired_token_returns_401(self, client):
        """Expired JWT should be rejected."""
        token = _make_token("cust-001", expires_delta=timedelta(hours=-1))
        response = client.get(
            "/offers/cust-001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
