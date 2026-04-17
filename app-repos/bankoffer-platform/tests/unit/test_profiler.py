"""Unit tests for CustomerProfiler."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from services.worker.profiler import CustomerProfiler, CustomerProfile


@pytest.fixture
def profiler():
    return CustomerProfiler()


@pytest.fixture
def sample_transactions():
    base_date = datetime(2025, 3, 1)
    return [
        {
            "id": f"txn_{i}",
            "customer_id": "cust_001",
            "amount": 150.0 + i * 10,
            "category": "groceries",
            "merchant": "Supermarket",
            "timestamp": (base_date - timedelta(days=i * 5)).isoformat(),
            "currency": "USD",
        }
        for i in range(20)
    ] + [
        {
            "id": f"txn_inv_{i}",
            "customer_id": "cust_001",
            "amount": 500.0 + i * 50,
            "category": "investments",
            "merchant": "BrokerApp",
            "timestamp": (base_date - timedelta(days=i * 15)).isoformat(),
            "currency": "USD",
        }
        for i in range(5)
    ]


@pytest.fixture
def sample_demographics():
    return {
        "customer_id": "cust_001",
        "age": 32,
        "income": 75000,
        "employment_status": "employed",
        "dependents": 1,
        "products": ["checking", "savings"],
    }


class TestLifeStageClassification:
    def test_new_graduate_classification(self, profiler):
        demographics = {"age": 23, "income": 35000, "employment_status": "employed", "dependents": 0, "products": []}
        transactions = [{"amount": 50, "category": "entertainment"} for _ in range(10)]
        profile = profiler.classify_life_stage(demographics, transactions)
        assert profile == "new_graduate"

    def test_young_family_classification(self, profiler):
        demographics = {"age": 33, "income": 80000, "employment_status": "employed", "dependents": 2, "products": ["mortgage"]}
        transactions = [{"amount": 200, "category": "groceries"} for _ in range(20)]
        profile = profiler.classify_life_stage(demographics, transactions)
        assert profile == "young_family"

    def test_pre_retirement_classification(self, profiler):
        demographics = {"age": 56, "income": 120000, "employment_status": "employed", "dependents": 0, "products": ["pension"]}
        transactions = [{"amount": 1000, "category": "investments"} for _ in range(15)]
        profile = profiler.classify_life_stage(demographics, transactions)
        assert profile == "pre_retirement"

    def test_retired_classification(self, profiler):
        demographics = {"age": 67, "income": 40000, "employment_status": "retired", "dependents": 0, "products": ["pension"]}
        transactions = [{"amount": 80, "category": "healthcare"} for _ in range(10)]
        profile = profiler.classify_life_stage(demographics, transactions)
        assert profile == "retired"

    def test_mid_career_classification(self, profiler):
        demographics = {"age": 42, "income": 95000, "employment_status": "employed", "dependents": 1, "products": ["mortgage", "savings"]}
        transactions = [{"amount": 300, "category": "mixed"} for _ in range(20)]
        profile = profiler.classify_life_stage(demographics, transactions)
        assert profile == "mid_career"


class TestRiskToleranceScoring:
    def test_low_risk_profile(self, profiler):
        demographics = {"age": 62, "income": 50000, "employment_status": "employed", "dependents": 2}
        transactions = [{"amount": 50, "category": "necessities"} for _ in range(30)]
        score = profiler.compute_risk_tolerance(demographics, transactions)
        assert 1 <= score <= 4

    def test_high_risk_profile(self, profiler):
        demographics = {"age": 28, "income": 150000, "employment_status": "employed", "dependents": 0}
        transactions = [{"amount": 2000, "category": "investments"} for _ in range(20)]
        score = profiler.compute_risk_tolerance(demographics, transactions)
        assert 7 <= score <= 10

    def test_risk_score_in_valid_range(self, profiler, sample_transactions, sample_demographics):
        score = profiler.compute_risk_tolerance(sample_demographics, sample_transactions)
        assert 1 <= score <= 10

    def test_risk_score_is_integer_or_float(self, profiler, sample_transactions, sample_demographics):
        score = profiler.compute_risk_tolerance(sample_demographics, sample_transactions)
        assert isinstance(score, (int, float))


class TestFullProfileBuilding:
    def test_profile_has_required_fields(self, profiler, sample_transactions, sample_demographics):
        profile = profiler.build_profile("cust_001", sample_transactions, sample_demographics)
        assert isinstance(profile, CustomerProfile)
        assert profile.customer_id == "cust_001"
        assert profile.life_stage in ["new_graduate", "young_family", "mid_career", "pre_retirement", "retired"]
        assert 1 <= profile.risk_score <= 10
        assert isinstance(profile.segments, list)
        assert profile.income_bracket in ["low", "medium", "high", "very_high"]
        assert isinstance(profile.spending_patterns, dict)

    def test_profile_segments_not_empty(self, profiler, sample_transactions, sample_demographics):
        profile = profiler.build_profile("cust_001", sample_transactions, sample_demographics)
        assert len(profile.segments) > 0

    def test_empty_transactions_handled(self, profiler, sample_demographics):
        profile = profiler.build_profile("cust_001", [], sample_demographics)
        assert isinstance(profile, CustomerProfile)
        assert profile.life_stage is not None

    def test_spending_patterns_categories(self, profiler, sample_transactions, sample_demographics):
        profile = profiler.build_profile("cust_001", sample_transactions, sample_demographics)
        assert "top_category" in profile.spending_patterns
        assert "avg_monthly_spend" in profile.spending_patterns
