"""Unit tests for OfferRanker."""
import pytest
from datetime import datetime, timedelta

from services.worker.ranker import OfferRanker, RankedOffer
from services.worker.scorer import ScoredProduct


@pytest.fixture
def ranker():
    return OfferRanker(top_n=3)


@pytest.fixture
def sample_scored_products():
    return [
        ScoredProduct(product_id="prod_ins_001", product_name="Family Life Insurance", product_type="insurance", relevance_score=0.91, reasoning="Family with dependents"),
        ScoredProduct(product_id="prod_etf_001", product_name="Global Equity ETF", product_type="ETF", relevance_score=0.82, reasoning="Matches risk tolerance"),
        ScoredProduct(product_id="prod_cc_001", product_name="Rewards Credit Card", product_type="credit", relevance_score=0.73, reasoning="Regular spending patterns"),
        ScoredProduct(product_id="prod_mf_001", product_name="Conservative Bond Fund", product_type="mutual_fund", relevance_score=0.45, reasoning="Low risk option"),
        ScoredProduct(product_id="prod_loan_001", product_name="Personal Loan", product_type="loan", relevance_score=0.38, reasoning="Credit utilization low"),
    ]


@pytest.fixture
def customer_context():
    return {
        "customer_id": "cust_001",
        "last_offers": {},
        "accepted_products": ["savings"],
        "dismissed_products": [],
        "channel_preference": "push",
    }


class TestRanking:
    def test_returns_top_n_offers(self, ranker, sample_scored_products, customer_context):
        results = ranker.rank(sample_scored_products, customer_context)
        assert len(results) == 3

    def test_results_sorted_by_score_descending(self, ranker, sample_scored_products, customer_context):
        results = ranker.rank(sample_scored_products, customer_context)
        scores = [r.confidence for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_ranked_offers_have_required_fields(self, ranker, sample_scored_products, customer_context):
        results = ranker.rank(sample_scored_products, customer_context)
        for offer in results:
            assert isinstance(offer, RankedOffer)
            assert offer.offer_id is not None
            assert offer.product_name is not None
            assert offer.personalization_reason is not None
            assert 0.0 <= offer.confidence <= 1.0
            assert offer.cta_url is not None

    def test_personalization_reason_is_sentence(self, ranker, sample_scored_products, customer_context):
        results = ranker.rank(sample_scored_products, customer_context)
        for offer in results:
            assert len(offer.personalization_reason) > 10
            assert len(offer.personalization_reason) < 300


class TestBusinessRules:
    def test_cooldown_period_respected(self, ranker, sample_scored_products, customer_context):
        recent_offer_time = (datetime.utcnow() - timedelta(hours=12)).isoformat()
        customer_context["last_offers"] = {
            "prod_ins_001": recent_offer_time,
        }
        results = ranker.rank(sample_scored_products, customer_context)
        offer_ids = [r.product_id for r in results]
        assert "prod_ins_001" not in offer_ids

    def test_dismissed_products_excluded(self, ranker, sample_scored_products, customer_context):
        customer_context["dismissed_products"] = ["prod_etf_001"]
        results = ranker.rank(sample_scored_products, customer_context)
        offer_ids = [r.product_id for r in results]
        assert "prod_etf_001" not in offer_ids

    def test_already_owned_products_excluded(self, ranker, sample_scored_products, customer_context):
        customer_context["accepted_products"] = ["ins_001_product"]
        scored = [
            ScoredProduct(product_id="prod_cc_001", product_name="Rewards Credit Card", product_type="credit", relevance_score=0.73, reasoning=""),
        ]
        results = ranker.rank(scored, customer_context)
        assert len(results) <= 1

    def test_empty_products_returns_empty_list(self, ranker, customer_context):
        results = ranker.rank([], customer_context)
        assert results == []

    def test_fewer_products_than_top_n(self, ranker, customer_context):
        scored = [
            ScoredProduct(product_id="prod_etf_001", product_name="Global ETF", product_type="ETF", relevance_score=0.8, reasoning="Good fit"),
        ]
        results = ranker.rank(scored, customer_context)
        assert len(results) == 1
