"""Integration tests for the BankOffer API."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from services.api.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-valid-jwt-token"}


@pytest.fixture
def mock_ranked_offers():
    return [
        {
            "offer_id": "offer_001",
            "product_id": "prod_ins_001",
            "product_name": "Family Life Insurance",
            "product_type": "insurance",
            "personalization_reason": "As a parent with dependents, life insurance ensures your family's financial security.",
            "confidence": 0.91,
            "cta_url": "https://bank.example.com/offers/offer_001",
        },
        {
            "offer_id": "offer_002",
            "product_id": "prod_etf_001",
            "product_name": "Global Equity ETF",
            "product_type": "ETF",
            "personalization_reason": "Your investment behavior and risk tolerance align well with diversified equity exposure.",
            "confidence": 0.82,
            "cta_url": "https://bank.example.com/offers/offer_002",
        },
    ]


class TestHealthCheck:
    def test_health_endpoint_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_endpoint_returns_ok(self, client):
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "ok"


class TestOffersEndpoint:
    @patch("services.api.routers.offers.get_ranked_offers")
    @patch("services.api.middleware.auth.verify_token")
    def test_get_offers_returns_200(self, mock_verify, mock_get_offers, client, auth_headers, mock_ranked_offers):
        mock_verify.return_value = {"sub": "cust_001", "role": "customer"}
        mock_get_offers.return_value = mock_ranked_offers

        response = client.get("/offers/cust_001", headers=auth_headers)
        assert response.status_code == 200

    @patch("services.api.routers.offers.get_ranked_offers")
    @patch("services.api.middleware.auth.verify_token")
    def test_get_offers_returns_list(self, mock_verify, mock_get_offers, client, auth_headers, mock_ranked_offers):
        mock_verify.return_value = {"sub": "cust_001", "role": "customer"}
        mock_get_offers.return_value = mock_ranked_offers

        response = client.get("/offers/cust_001", headers=auth_headers)
        data = response.json()
        assert isinstance(data["offers"], list)

    @patch("services.api.routers.offers.get_ranked_offers")
    @patch("services.api.middleware.auth.verify_token")
    def test_offer_has_personalization_reason(self, mock_verify, mock_get_offers, client, auth_headers, mock_ranked_offers):
        mock_verify.return_value = {"sub": "cust_001", "role": "customer"}
        mock_get_offers.return_value = mock_ranked_offers

        response = client.get("/offers/cust_001", headers=auth_headers)
        data = response.json()
        for offer in data["offers"]:
            assert "personalization_reason" in offer
            assert "cta_url" in offer
            assert "confidence" in offer

    def test_get_offers_without_auth_returns_401(self, client):
        response = client.get("/offers/cust_001")
        assert response.status_code == 401

    @patch("services.api.middleware.auth.verify_token")
    def test_get_offers_wrong_customer_returns_403(self, mock_verify, client, auth_headers):
        mock_verify.return_value = {"sub": "cust_002", "role": "customer"}
        response = client.get("/offers/cust_001", headers=auth_headers)
        assert response.status_code == 403


class TestWebhooksEndpoint:
    @patch("services.api.routers.webhooks.verify_webhook_signature")
    @patch("services.api.routers.webhooks.publish_to_kafka")
    def test_webhook_transaction_accepted(self, mock_publish, mock_verify_sig, client):
        mock_verify_sig.return_value = True
        mock_publish.return_value = None

        payload = {
            "event_type": "transaction.created",
            "customer_id": "cust_001",
            "transaction": {
                "id": "txn_abc123",
                "amount": 250.0,
                "category": "groceries",
                "merchant": "SuperStore",
                "timestamp": "2026-03-31T10:00:00Z",
                "currency": "USD",
            },
        }
        response = client.post(
            "/webhooks/transactions",
            json=payload,
            headers={"X-Webhook-Signature": "valid-sig"},
        )
        assert response.status_code == 202

    def test_webhook_invalid_signature_returns_401(self, client):
        with patch("services.api.routers.webhooks.verify_webhook_signature", return_value=False):
            response = client.post(
                "/webhooks/transactions",
                json={"event_type": "transaction.created"},
                headers={"X-Webhook-Signature": "bad-sig"},
            )
            assert response.status_code == 401
