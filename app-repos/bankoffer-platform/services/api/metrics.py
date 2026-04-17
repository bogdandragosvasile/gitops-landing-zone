"""Custom Prometheus metrics for BankOffer AI business observability."""

from prometheus_client import Counter, Gauge, Histogram

# -- Offer scoring --
OFFERS_GENERATED = Counter(
    "bankoffer_offers_generated_total",
    "Total offers generated",
    ["product_type"],
)

OFFER_SCORING_DURATION = Histogram(
    "bankoffer_offer_scoring_seconds",
    "Time spent scoring offers for a customer",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

OFFERS_PER_REQUEST = Histogram(
    "bankoffer_offers_per_request",
    "Number of offers returned per request",
    buckets=(0, 1, 2, 3, 5, 8, 10, 15, 20),
)

# -- Suitability --
SUITABILITY_CHECKS = Counter(
    "bankoffer_suitability_checks_total",
    "Suitability check outcomes",
    ["result"],  # passed, failed
)

# -- Consent --
CONSENT_BLOCKS = Counter(
    "bankoffer_consent_blocks_total",
    "Requests blocked due to missing consent",
    ["consent_type"],  # profiling, automated_decision
)

# -- Kill switch --
KILL_SWITCH_ACTIVE = Gauge(
    "bankoffer_kill_switch_active",
    "Whether the model kill-switch is engaged (1=active, 0=inactive)",
)

# -- Business gauges (updated periodically or on request) --
CUSTOMERS_TOTAL = Gauge(
    "bankoffer_customers_total",
    "Total customers in the database",
)

PRODUCTS_ACTIVE = Gauge(
    "bankoffer_products_active_total",
    "Number of active products",
)

# -- Profile --
PROFILE_BUILD_DURATION = Histogram(
    "bankoffer_profile_build_seconds",
    "Time to build a customer profile",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
)

# -- Auth --
DEMO_LOGINS = Counter(
    "bankoffer_demo_logins_total",
    "Demo login attempts",
    ["role"],
)

# -- Intelligence / AI guardrails --
AI_API_CALLS = Counter(
    "bankoffer_ai_api_calls_total",
    "External AI provider API calls",
    ["provider", "result"],  # result: success, failure, skipped
)

AI_API_LATENCY = Histogram(
    "bankoffer_ai_api_latency_seconds",
    "Latency of external AI API calls",
    ["provider"],
    buckets=(1.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0),
)

AI_SUGGESTIONS_FILTERED = Counter(
    "bankoffer_ai_suggestions_filtered_total",
    "AI suggestions filtered out by guardrails",
    ["reason"],  # low_confidence, content_filter, duplicate, expired
)

AI_ANALYSIS_RATE_LIMITED = Counter(
    "bankoffer_ai_analysis_rate_limited_total",
    "Analysis requests blocked by rate limiter",
)

# -- Fairness monitoring --
OFFER_SCORE_BY_AGE = Histogram(
    "bankoffer_offer_score_by_age_bracket",
    "Offer relevance scores grouped by age bracket",
    ["age_bracket", "product_type"],
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

OFFER_SCORE_BY_INCOME = Histogram(
    "bankoffer_offer_score_by_income_tier",
    "Offer relevance scores grouped by income tier",
    ["income_tier", "product_type"],
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)
