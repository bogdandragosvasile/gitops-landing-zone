"""Pydantic models for the Bank Offering AI API."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class LifeStage(str, Enum):
    NEW_GRADUATE = "new_graduate"
    YOUNG_FAMILY = "young_family"
    MID_CAREER = "mid_career"
    PRE_RETIREMENT = "pre_retirement"
    RETIRED = "retired"


class IncomeBracket(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class Channel(str, Enum):
    PUSH = "push"
    EMAIL = "email"
    SMS = "sms"
    IN_APP = "in_app"


class SpendingPattern(BaseModel):
    category: str = Field(..., description="Spending category (e.g., groceries, travel)")
    monthly_average: float = Field(..., ge=0, description="Average monthly spend in this category")
    trend: str = Field(..., description="Trend direction: increasing, stable, decreasing")


class CustomerProfile(BaseModel):
    customer_id: str = Field(..., description="Unique customer identifier")
    life_stage: LifeStage = Field(..., description="Classified life stage of the customer")
    risk_score: float = Field(..., ge=1.0, le=10.0, description="Risk tolerance score 1-10")
    segments: list[str] = Field(default_factory=list, description="Customer segments")
    income_bracket: IncomeBracket = Field(..., description="Income bracket classification")
    spending_patterns: list[SpendingPattern] = Field(
        default_factory=list, description="Categorized spending patterns"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Transaction(BaseModel):
    transaction_id: str = Field(..., description="Unique transaction identifier")
    customer_id: str = Field(..., description="Customer who made the transaction")
    amount: float = Field(..., description="Transaction amount")
    currency: str = Field(default="USD", description="ISO 4217 currency code")
    category: str = Field(..., description="Transaction category")
    merchant: str = Field(..., description="Merchant name")
    timestamp: datetime = Field(..., description="When the transaction occurred")
    description: Optional[str] = Field(None, description="Transaction description")


# --- Compliance-enhanced offer models ---

class SuitabilityResult(BaseModel):
    suitable: bool = Field(..., description="Whether the product passed suitability check")
    reason: Optional[str] = Field(None, description="Reason for exclusion if not suitable")


class Offer(BaseModel):
    offer_id: str = Field(..., description="Unique offer identifier")
    product_name: str = Field(..., description="Bank product name")
    product_type: str = Field(..., description="Product type (e.g., credit_card, loan, savings)")
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="AI relevance score")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Model confidence")
    personalization_reason: str = Field(
        ..., description="One-sentence explanation of why this offer fits"
    )
    explanation: str = Field(
        default="", description="GDPR art.22 compliant natural language explanation"
    )
    suitability_status: str = Field(
        default="passed", description="Suitability check result: passed/excluded"
    )
    human_review_required: bool = Field(
        default=False, description="True if automated_decision_consent is missing (GDPR Art. 22)"
    )
    requires_suitability_confirmation: bool = Field(
        default=False, description="True for investment products needing MiFID II confirmation"
    )
    terms_summary: Optional[str] = Field(None, description="Brief terms summary")
    cta_url: str = Field(..., description="Call-to-action URL")


class OfferResponse(BaseModel):
    customer_id: str = Field(..., description="Customer identifier (pseudonymized)")
    recommendation_id: str = Field(default="", description="Audit trail reference ID")
    offers: list[Offer] = Field(..., description="Ranked list of offers")
    excluded_products: list[dict] = Field(
        default_factory=list,
        description="Products excluded by suitability check with reasons"
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    model_version: str = Field(default="1.0.0", description="Scoring model version")
    consent_valid: bool = Field(default=True, description="Whether profiling consent was active")


# --- Feedback models (Human-in-the-loop) ---

class FeedbackRequest(BaseModel):
    recommendation_id: str = Field(..., description="Audit trail recommendation ID")
    offer_id: str = Field(..., description="Offer being acted upon")
    product_id: Optional[str] = Field(None, description="Product ID if known")
    customer_id: Optional[str] = Field(None, description="Customer ID")
    actor_type: str = Field(..., description="'customer' or 'employee'")
    actor_id: Optional[str] = Field(None, description="Actor identifier")
    action: str = Field(..., description="'accepted', 'rejected', 'flagged', or 'overridden'")
    reason: Optional[str] = Field(None, description="Reason for the action")


class FeedbackResponse(BaseModel):
    id: int
    recommendation_id: str
    offer_id: str
    action: str
    reason: Optional[str]
    created_at: datetime


# --- Consent models (5-tier GDPR/ePrivacy/MiFID II consent map) ---

class ConsentUpdate(BaseModel):
    """Update any combination of the 5 consent types."""
    profiling_consent: Optional[bool] = None           # Consent #1: GDPR Art. 6(1)(a)
    automated_decision_consent: Optional[bool] = None  # Consent #2: GDPR Art. 22
    marketing_push: Optional[bool] = None              # Consent #3a: ePrivacy
    marketing_email: Optional[bool] = None             # Consent #3b: ePrivacy
    marketing_sms: Optional[bool] = None               # Consent #3c: ePrivacy
    family_context_consent: Optional[bool] = None      # Consent #4: GDPR Art. 9
    sensitive_data_consent: Optional[bool] = None      # Legacy (maps to family_context)


class ConsentStatus(BaseModel):
    """Full consent state for a customer."""
    customer_id: str
    external_id: str
    # Consent #1: General financial profiling
    profiling_consent: bool
    profiling_consent_ts: Optional[datetime]
    # Consent #2: Automated decisions (GDPR Art. 22)
    automated_decision_consent: bool = False
    automated_decision_consent_ts: Optional[datetime] = None
    # Consent #3: Marketing per channel
    marketing_push: bool = False
    marketing_push_ts: Optional[datetime] = None
    marketing_email: bool = False
    marketing_email_ts: Optional[datetime] = None
    marketing_sms: bool = False
    marketing_sms_ts: Optional[datetime] = None
    # Consent #4: Family context (Art. 9 special category)
    family_context_consent: bool = False
    family_context_consent_ts: Optional[datetime] = None
    # Legacy
    sensitive_data_consent: bool
    sensitive_data_consent_ts: Optional[datetime]


# --- MiFID II Suitability confirmation (Consent #5) ---

class SuitabilityConfirmRequest(BaseModel):
    product_id: str = Field(..., description="Product being confirmed")
    recommendation_id: Optional[str] = Field(None, description="Audit trail reference")
    confirmed: bool = Field(..., description="Client confirms understanding of risk")


class SuitabilityConfirmResponse(BaseModel):
    id: int
    customer_id: str
    product_id: str
    risk_profile_at_confirmation: str
    confirmed: bool
    confirmed_at: datetime


# --- AI Act compliance models ---

class KillSwitchStatus(BaseModel):
    active: bool
    activated_by: Optional[str] = None
    reason: Optional[str] = None
    activated_at: Optional[datetime] = None


class KillSwitchToggle(BaseModel):
    active: bool
    reason: str
    activated_by: str


class RiskRegisterEntry(BaseModel):
    id: Optional[int] = None
    risk_id: str
    category: str
    description: str
    severity: str
    mitigation: str
    status: str = "open"
    owner: Optional[str] = None
    model_version: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OverrideRequest(BaseModel):
    recommendation_id: str
    customer_id: str
    employee_id: str
    product_id: Optional[str] = None
    override_type: str = Field(..., description="'reject', 'suppress', or 'escalate'")
    reason: str


class OverrideResponse(BaseModel):
    id: int
    recommendation_id: str
    override_type: str
    reason: str
    created_at: datetime


# --- Audit models ---

class AuditEntry(BaseModel):
    id: int
    recommendation_id: str
    external_customer_id: str
    customer_id: str
    input_features: dict
    profile_result: dict
    suitability_checks: dict
    scored_products: list
    final_offers: list
    excluded_products: list
    model_version: str
    consent_snapshot: dict
    created_at: datetime


class AuditListEntry(BaseModel):
    id: int
    recommendation_id: str
    external_customer_id: str
    customer_id: str
    num_offers: int
    num_excluded: int
    model_version: str
    created_at: datetime


# --- Existing models ---

# --- Staff authentication models ---

class StaffLoginRequest(BaseModel):
    email: str = Field(..., description="Staff email address")
    password: str = Field(..., description="Password")


class StaffLoginResponse(BaseModel):
    token: str = Field(..., description="JWT session token")
    email: str
    display_name: str
    role: str = Field(..., description="'admin' or 'employee'")


# --- Customer authentication models (GDPR-compliant) ---

class CustomerRegisterRequest(BaseModel):
    email: str = Field(..., description="Email address (will be hashed, never stored in plaintext)")
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")
    display_name: Optional[str] = Field(None, description="Display name (optional)")
    gdpr_consent: bool = Field(..., description="Must accept GDPR data processing terms")


class CustomerRegisterResponse(BaseModel):
    token: str = Field(..., description="JWT session token")
    customer_id: str = Field(..., description="Assigned customer ID for API calls")
    external_id: str = Field(..., description="Pseudonymized UUID for display (GDPR Art. 25)")
    display_name: str
    anonymize_after: datetime = Field(..., description="Auto-anonymization date (GDPR Art. 5(1)(e))")
    onboarding_complete: bool = Field(default=False, description="Whether customer completed onboarding wizard")
    message: str


class CustomerLoginRequest(BaseModel):
    email: str = Field(..., description="Email address")
    password: str = Field(..., description="Password")


class CustomerLoginResponse(BaseModel):
    token: str = Field(..., description="JWT session token")
    customer_id: str = Field(..., description="Customer ID for API calls")
    external_id: str = Field(..., description="Pseudonymized UUID for display")
    display_name: str
    anonymize_after: Optional[datetime] = Field(None, description="Auto-anonymization date")
    onboarding_complete: bool = Field(default=False, description="Whether customer completed onboarding wizard")


class WebhookPayload(BaseModel):
    event_type: str = Field(..., description="Type of webhook event")
    timestamp: datetime = Field(..., description="Event timestamp")
    transactions: list[Transaction] = Field(..., description="Batch of transactions")
    signature: str = Field(..., description="HMAC signature for verification")


class NotificationPayload(BaseModel):
    offer_id: str = Field(..., description="Offer identifier")
    product_name: str = Field(..., description="Product name for the notification")
    personalization_reason: str = Field(
        ..., description="Why this offer is relevant to the customer"
    )
    cta_url: str = Field(..., description="Call-to-action deep link URL")
    channel: Channel = Field(..., description="Delivery channel for the notification")
    customer_id: str = Field(..., description="Target customer identifier")


# --- Workflow models (Employee ↔ Customer) ---

class NotificationOut(BaseModel):
    id: int
    customer_id: str
    offer_id: str
    product_name: str
    action: str
    recommendation_id: Optional[str] = None
    read: bool = False
    created_at: datetime


class FormCreate(BaseModel):
    customer_id: str = Field(..., description="Target customer")
    employee_id: str = Field(..., description="Employee sending the form")
    notification_id: Optional[int] = Field(None, description="Linked notification")
    product_name: str = Field(..., description="Product the form is for")
    offer_id: Optional[str] = Field(None, description="Original offer ID")
    form_type: str = Field(default="product_application", description="Form type")
    fields: list[dict] = Field(..., description="Form field definitions [{name, label, type, required}]")


class FormOut(BaseModel):
    id: int
    customer_id: str
    employee_id: str
    notification_id: Optional[int] = None
    product_name: str
    offer_id: Optional[str] = None
    form_type: str
    fields: list[dict]
    status: str
    submitted_data: Optional[dict] = None
    submitted_at: Optional[datetime] = None
    created_at: datetime


class FormSubmission(BaseModel):
    data: dict = Field(..., description="Customer-filled form data {field_name: value}")


# --- Connector models (third-party service integrations) ---

class ConnectorOut(BaseModel):
    id: int
    name: str
    category: str
    provider: str
    description: Optional[str] = None
    icon: str = "plug"
    config_schema: list[dict] = Field(default_factory=list)
    config_values: dict = Field(default_factory=dict)
    status: str = "available"
    suggested_by: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ConnectorCreate(BaseModel):
    name: str = Field(..., description="Connector display name")
    category: str = Field(..., description="ai, cloud, advertising, analytics, crm, messaging, payments, security")
    provider: str = Field(..., description="Service provider name")
    description: Optional[str] = None
    icon: str = Field(default="plug", description="Icon identifier")
    config_schema: list[dict] = Field(default_factory=list, description="Config field definitions [{name, label, type, required, placeholder}]")
    suggested_by: Optional[str] = Field(None, description="Who suggested this connector (AI or user)")


class ConnectorUpdate(BaseModel):
    config_values: dict = Field(..., description="Configuration values to save")


class ConnectorApproval(BaseModel):
    approved_by: str = Field(..., description="Admin who approved")
    action: str = Field(..., description="'approve' or 'reject'")
