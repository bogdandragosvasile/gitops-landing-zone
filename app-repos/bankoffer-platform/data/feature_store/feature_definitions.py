"""Feast feature store definitions for the BankOfferingAI platform.

Defines three feature views that power the ML recommendation models:

1. **customer_transaction_features** — aggregated spending patterns.
2. **customer_demographic_features** — static/slow-changing profile data.
3. **customer_behavior_features** — digital engagement signals.
"""

from __future__ import annotations

from datetime import timedelta

from feast import (
    Entity,
    Feature,
    FeatureService,
    FeatureView,
    Field,
    FileSource,
    PushSource,
    ValueType,
)
from feast.types import Float32, Float64, Int64, String

# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

customer = Entity(
    name="customer",
    join_keys=["customer_id"],
    description="A bank customer uniquely identified by customer_id.",
)

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

# In production these would point at a data-warehouse (BigQuery / Redshift)
# or a streaming source.  File paths are placeholders for local dev.

transaction_features_source = FileSource(
    path="data/feature_store/data/customer_transaction_features.parquet",
    timestamp_field="feature_timestamp",
    created_timestamp_column="created_at",
    description="Daily-aggregated transaction features produced by the Airflow DAG.",
)

demographic_features_source = FileSource(
    path="data/feature_store/data/customer_demographic_features.parquet",
    timestamp_field="feature_timestamp",
    created_timestamp_column="created_at",
    description="Customer demographic data refreshed weekly from the core banking system.",
)

behavior_features_source = FileSource(
    path="data/feature_store/data/customer_behavior_features.parquet",
    timestamp_field="feature_timestamp",
    created_timestamp_column="created_at",
    description="Digital-channel engagement metrics computed daily.",
)

transaction_push_source = PushSource(
    name="transaction_push",
    batch_source=transaction_features_source,
)

# ---------------------------------------------------------------------------
# Feature views
# ---------------------------------------------------------------------------

customer_transaction_features = FeatureView(
    name="customer_transaction_features",
    entities=[customer],
    ttl=timedelta(days=7),
    schema=[
        Field(name="avg_txn_amount_7d", dtype=Float64),
        Field(name="avg_txn_amount_30d", dtype=Float64),
        Field(name="avg_txn_amount_90d", dtype=Float64),
        Field(name="txn_count_7d", dtype=Int64),
        Field(name="txn_count_30d", dtype=Int64),
        Field(name="txn_count_90d", dtype=Int64),
        Field(name="txn_frequency_daily", dtype=Float32),
        Field(name="txn_frequency_weekly", dtype=Float32),
        Field(name="total_spend_30d", dtype=Float64),
        Field(name="total_spend_90d", dtype=Float64),
        Field(name="category_groceries_pct", dtype=Float32),
        Field(name="category_dining_pct", dtype=Float32),
        Field(name="category_travel_pct", dtype=Float32),
        Field(name="category_fuel_pct", dtype=Float32),
        Field(name="category_retail_pct", dtype=Float32),
        Field(name="category_entertainment_pct", dtype=Float32),
        Field(name="category_healthcare_pct", dtype=Float32),
        Field(name="category_utilities_pct", dtype=Float32),
        Field(name="category_education_pct", dtype=Float32),
        Field(name="category_other_pct", dtype=Float32),
        Field(name="max_single_txn_30d", dtype=Float64),
        Field(name="std_txn_amount_30d", dtype=Float64),
    ],
    source=transaction_features_source,
    online=True,
    description="Aggregated transaction statistics and category distribution.",
)

customer_demographic_features = FeatureView(
    name="customer_demographic_features",
    entities=[customer],
    ttl=timedelta(days=30),
    schema=[
        Field(name="age_bucket", dtype=String),
        Field(name="income_bucket", dtype=String),
        Field(name="life_stage", dtype=String),
        Field(name="tenure_months", dtype=Int64),
        Field(name="region", dtype=String),
        Field(name="num_products_held", dtype=Int64),
        Field(name="credit_score_bucket", dtype=String),
        Field(name="has_mortgage", dtype=Int64),
        Field(name="has_credit_card", dtype=Int64),
        Field(name="has_savings_account", dtype=Int64),
        Field(name="has_investment_account", dtype=Int64),
    ],
    source=demographic_features_source,
    online=True,
    description="Slowly-changing demographic and product-holding features.",
)

customer_behavior_features = FeatureView(
    name="customer_behavior_features",
    entities=[customer],
    ttl=timedelta(days=7),
    schema=[
        Field(name="login_count_7d", dtype=Int64),
        Field(name="login_count_30d", dtype=Int64),
        Field(name="login_frequency_daily", dtype=Float32),
        Field(name="mobile_sessions_7d", dtype=Int64),
        Field(name="web_sessions_7d", dtype=Int64),
        Field(name="product_page_views_7d", dtype=Int64),
        Field(name="product_page_views_30d", dtype=Int64),
        Field(name="offer_clicks_30d", dtype=Int64),
        Field(name="offer_dismissals_30d", dtype=Int64),
        Field(name="offer_ctr_30d", dtype=Float32),
        Field(name="avg_session_duration_sec", dtype=Float32),
        Field(name="days_since_last_login", dtype=Int64),
    ],
    source=behavior_features_source,
    online=True,
    description="Digital engagement and offer-interaction features.",
)

# ---------------------------------------------------------------------------
# Feature service (bundles all views for the recommendation model)
# ---------------------------------------------------------------------------

bank_offering_feature_service = FeatureService(
    name="bank_offering_feature_service",
    features=[
        customer_transaction_features,
        customer_demographic_features,
        customer_behavior_features,
    ],
    description=(
        "Combined feature service used by the offer-recommendation model "
        "at inference time."
    ),
)
