"""Unit tests for ProductScorer."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from services.worker.scorer import ProductScorer, ScoredProduct
from services.worker.profiler import CustomerProfile


@pytest.fixture
def sample_profile():
    return CustomerProfile(
        customer_id="cust_001",
        life_stage="young_family",
        risk_score=5.5,
        segments=["homeowner", "parent", "mid_income"],
        income_bracket="medium",
        spending_patterns={
            "top_category": "groceries",
            "avg_monthly_spend": 3200.0,
            "investment_ratio": 0.12,
        },
    )


@pytest.fixture
def sample_catalog():
    return [
        {
            "product_id": "prod_etf_001",
            "name": "Global Equity ETF",
            "type": "ETF",
            "min_risk_tolerance": 5,
            "description": "Diversified global equity fund for long-term growth",
            "min_investment": 500,
            "expected_return": "8-12% annually",
        },
        {
            "product_id": "prod_ins_001",
            "name": "Family Life Insurance",
            "type": "insurance",
            "min_risk_tolerance": 1,
            "description": "Term life insurance for families with dependents",
            "min_investment": 50,
            "expected_return": "Protection product",
        },
        {
            "product_id": "prod_mf_001",
            "name": "Conservative Bond Fund",
            "type": "mutual_fund",
            "min_risk_tolerance": 2,
            "description": "Low-risk fixed income fund",
            "min_investment": 1000,
            "expected_return": "3-5% annually",
        },
        {
            "product_id": "prod_cc_001",
            "name": "Rewards Credit Card",
            "type": "credit",
            "min_risk_tolerance": 1,
            "description": "Cash back credit card with travel rewards",
            "min_investment": 0,
            "expected_return": "2% cash back",
        },
    ]


@pytest.fixture
def mock_anthropic_response():
    response = MagicMock()
    response.content = [
        MagicMock(
            text='{"scores": [{"product_id": "prod_etf_001", "relevance_score": 0.82, "reasoning": "Profile matches ETF risk tolerance"}, {"product_id": "prod_ins_001", "relevance_score": 0.91, "reasoning": "Family with dependents needs life coverage"}, {"product_id": "prod_mf_001", "relevance_score": 0.45, "reasoning": "Risk tolerance above minimum but conservative"}, {"product_id": "prod_cc_001", "relevance_score": 0.73, "reasoning": "Regular spending patterns suggest rewards card utility"}]}'
        )
    ]
    return response


class TestProductScorer:
    def test_scorer_uses_correct_model(self):
        scorer = ProductScorer(api_key="test-key")
        assert scorer.model == "claude-sonnet-4-20250514"

    @patch("services.worker.scorer.anthropic.Anthropic")
    def test_score_returns_all_products(self, mock_client_cls, mock_anthropic_response, sample_profile, sample_catalog):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_anthropic_response

        scorer = ProductScorer(api_key="test-key")
        results = scorer.score_products(sample_profile, sample_catalog)

        assert len(results) == len(sample_catalog)

    @patch("services.worker.scorer.anthropic.Anthropic")
    def test_scores_are_normalized(self, mock_client_cls, mock_anthropic_response, sample_profile, sample_catalog):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_anthropic_response

        scorer = ProductScorer(api_key="test-key")
        results = scorer.score_products(sample_profile, sample_catalog)

        for result in results:
            assert 0.0 <= result.relevance_score <= 1.0

    @patch("services.worker.scorer.anthropic.Anthropic")
    def test_scored_products_have_reasoning(self, mock_client_cls, mock_anthropic_response, sample_profile, sample_catalog):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_anthropic_response

        scorer = ProductScorer(api_key="test-key")
        results = scorer.score_products(sample_profile, sample_catalog)

        for result in results:
            assert isinstance(result, ScoredProduct)
            assert result.reasoning is not None
            assert len(result.reasoning) > 0

    @patch("services.worker.scorer.anthropic.Anthropic")
    def test_max_tokens_set_correctly(self, mock_client_cls, mock_anthropic_response, sample_profile, sample_catalog):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_anthropic_response

        scorer = ProductScorer(api_key="test-key")
        scorer.score_products(sample_profile, sample_catalog)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1000

    @patch("services.worker.scorer.anthropic.Anthropic")
    def test_api_failure_raises_exception(self, mock_client_cls, sample_profile, sample_catalog):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        scorer = ProductScorer(api_key="test-key")
        with pytest.raises(Exception):
            scorer.score_products(sample_profile, sample_catalog)

    @patch("services.worker.scorer.anthropic.Anthropic")
    def test_products_filtered_by_min_risk(self, mock_client_cls, mock_anthropic_response, sample_catalog):
        low_risk_profile = CustomerProfile(
            customer_id="cust_002",
            life_stage="pre_retirement",
            risk_score=2.0,
            segments=["conservative"],
            income_bracket="medium",
            spending_patterns={"top_category": "healthcare", "avg_monthly_spend": 2000.0},
        )

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_anthropic_response

        scorer = ProductScorer(api_key="test-key")
        eligible_products = scorer.filter_eligible_products(low_risk_profile, sample_catalog)

        for p in eligible_products:
            assert p["min_risk_tolerance"] <= low_risk_profile.risk_score
