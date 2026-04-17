# BankOffer AI -- System Architecture

## 1. Overview

BankOffer AI is a real-time personalization engine that matches bank customers with the most relevant financial products. The system processes transaction streams, builds behavioral profiles, scores candidate offers, and delivers results through multiple notification channels -- all within a sub-100ms serving latency budget.

The platform runs on Kubernetes (AWS EKS) and is deployed via a GitOps pipeline backed by ArgoCD. Infrastructure is provisioned with Terraform, services are packaged as Helm charts, and configuration is managed through Kustomize overlays.

## 2. High-Level Architecture

```
 Customers
     |
     v
 [Mobile App / Web Portal]
     |
     v
 [API Gateway -- Kong]
     |
     +-----> [Offer API -- FastAPI]
     |              |
     |         +----+----+----+
     |         |         |    |
     |    [Feature    [ML   [Auth
     |     Store]   Pipeline] Middleware]
     |     (Feast)     |       |
     |         |       |    [JWT + OAuth2]
     |         v       v
     |    [Redis]  [MLflow Registry]
     |
     +-----> [Notification Router]
                  |       |       |
               [Push]  [Email] [In-App]
                  |       |       |
               [FCM]  [SES]   [WebSocket]

 [Kafka Cluster]
     |
     +-----> [Transaction Consumer]
     |              |
     |              v
     |       [Transaction Normalizer]
     |              |
     |              v
     |       [Feature Store Loader]
     |
     +-----> [Airflow -- Batch Pipelines]
                    |
                    v
              [dbt Models -- Analytics Warehouse]
```

## 3. Customer Profiling Pipeline

### 3.1 Data Ingestion

Transaction events are published to Kafka by the core banking system. Each event contains the transaction amount, merchant category code (MCC), timestamp, and customer identifier. The Kafka consumer (`services/worker/`) deserializes these events using Avro schemas defined in `data/kafka/`.

### 3.2 Transaction Normalization

The normalizer enriches raw transactions with:

- **MCC category mapping** -- Groups merchant codes into spending categories (groceries, travel, dining, etc.).
- **Currency normalization** -- Converts all amounts to the customer's home currency using daily FX rates.
- **Velocity features** -- Calculates rolling transaction counts and amounts over 7-day, 30-day, and 90-day windows.

### 3.3 Feature Store Loading

Normalized features are written to the Feast feature store, which uses Redis as the online store (for real-time serving) and PostgreSQL as the offline store (for model training). Key feature groups:

| Feature Group          | Examples                                           | Update Frequency |
|------------------------|----------------------------------------------------|------------------|
| spending_profile       | avg_monthly_spend, top_3_categories, txn_count_30d | Real-time        |
| income_signals         | estimated_income_band, salary_deposit_regularity   | Daily (batch)    |
| product_holdings       | has_credit_card, has_mortgage, savings_balance_band | Daily (batch)    |
| engagement_scores      | app_login_frequency, offer_click_rate, nps_score   | Daily (batch)    |
| life_stage_indicators  | age_band, account_tenure_months, recent_life_event | Weekly (batch)   |

### 3.4 Batch Pipelines

Apache Airflow DAGs (`data/airflow/`) run nightly batch jobs for features that do not require real-time updates. dbt models (`data/dbt/`) transform raw data in the analytics warehouse into curated feature tables that are synced to Feast.

## 4. Offer Scoring

### 4.1 Customer Profiler

The profiler (`ml/profiler/`) reads a customer's feature vector from Feast and constructs a dense profile embedding. This embedding captures spending behavior, product affinity, risk tolerance, and engagement level. The profiler uses a combination of hand-crafted feature crosses and a sentence-transformer model for encoding free-text signals (e.g., customer support notes).

### 4.2 Product Scorer

The scorer (`ml/scorer/`) evaluates each eligible banking product against the customer profile. An XGBoost model predicts the probability that the customer will convert (apply for / activate) a given product within 30 days. The model is trained on historical offer-response data and is registered in MLflow (`ml/registry/`).

Feature inputs to the scorer:

- Customer profile embedding (from profiler)
- Product attributes (interest rate, fee structure, reward program)
- Contextual features (time of day, day of week, recent activity)
- Historical interaction features (past offers shown, clicked, converted)

### 4.3 Offer Ranker

The ranker (`ml/ranker/`) takes the scored candidates and produces the final ordered list. It applies:

1. **Business rules** -- Regulatory filters (e.g., do not offer credit products to customers flagged for affordability concerns), product eligibility checks, and cooldown periods for recently declined offers.
2. **A/B test assignments** -- Customers are bucketed into experiment variants. The ranker selects the scoring model or ranking strategy associated with the assigned variant.
3. **Diversity constraints** -- Ensures the top-N list contains offers from at least two product categories to avoid recommendation fatigue.
4. **Fairness checks** -- Validates that protected demographic groups receive equitable offer distributions.

## 5. Notification Delivery

### 5.1 Notification Router

The router (`services/notification/`) determines the delivery channel based on the customer's communication preferences and the offer's urgency tier.

| Urgency | Channels              | Example                               |
|---------|-----------------------|---------------------------------------|
| High    | Push + In-App         | Credit limit increase (time-limited)  |
| Medium  | Push or Email         | New savings product launch            |
| Low     | In-App only           | General financial wellness tip        |

### 5.2 Channel Adapters

- **Push adapter** -- Sends notifications through Firebase Cloud Messaging (FCM) for Android and APNs for iOS.
- **Email adapter** -- Renders HTML templates and sends via Amazon SES. Supports personalization tokens.
- **In-app adapter** -- Pushes real-time messages through WebSocket connections to the frontend.

### 5.3 Delivery Tracking

Every notification is logged with a unique delivery ID. The system tracks:

- Delivery status (sent, delivered, opened, clicked, dismissed)
- Channel-specific metadata (FCM message ID, SES message ID)
- Latency from offer decision to delivery

## 6. API Layer

### 6.1 Offer Endpoint

`GET /v1/offers/{customer_id}` returns the top-N ranked offers. The endpoint reads the pre-computed offer list from Redis (refreshed by the ML pipeline) and falls back to a synchronous scoring path if the cache is cold.

Response payload:

```json
{
  "customer_id": "cust_abc123",
  "offers": [
    {
      "offer_id": "offer_001",
      "product_type": "credit_card",
      "headline": "Earn 3x points on travel",
      "score": 0.87,
      "experiment_variant": "v2_personalized",
      "expires_at": "2026-04-15T00:00:00Z"
    }
  ],
  "request_id": "req_xyz789",
  "latency_ms": 42
}
```

### 6.2 Auth Middleware

All API requests are authenticated via JWT tokens issued by the bank's identity provider. The middleware validates the token signature, checks expiration, and extracts customer and role claims. OAuth2 scopes control access to admin endpoints.

### 6.3 Webhook Receiver

`POST /v1/webhooks/events` receives real-time events from the core banking system (e.g., new account opened, card activated). These events trigger immediate re-scoring for the affected customer.

## 7. GitOps Deployment

### 7.1 ArgoCD App-of-Apps

The root ArgoCD Application (`infra/argocd/app-of-apps.yaml`) points to the `gitops/` directory. Each service has its own ArgoCD Application CRD that references the corresponding Kustomize overlay.

### 7.2 Environment Promotion

```
Feature branch --> PR --> main (staging auto-sync) --> production (manual sync / progressive rollout)
```

- **Staging** syncs automatically when a PR merges to `main`.
- **Production** uses Argo Rollouts with a canary strategy: 10% traffic for 5 minutes, then 50%, then 100%. Automatic rollback triggers if error rate exceeds 1% or P99 latency exceeds 200ms.

### 7.3 Image Updates

ArgoCD Image Updater watches container registries for new image tags matching the `main-<sha>` pattern. When a new image is pushed by CI, the updater writes the new tag to the Kustomize overlay and commits it, triggering a sync.

## 8. Data Stores

| Store        | Purpose                                    | Deployed As         |
|--------------|--------------------------------------------|---------------------|
| PostgreSQL   | Transactional data, offline feature store  | RDS (Terraform)     |
| Redis        | Online feature store, offer cache, sessions| ElastiCache         |
| Kafka        | Event streaming                            | MSK (Terraform)     |
| S3           | Model artifacts, training data, logs       | S3 bucket           |

## 9. Security

- **Network policies** restrict pod-to-pod communication to declared dependencies.
- **OPA/Gatekeeper** policies enforce resource limits, image provenance, and label requirements.
- **Trivy** scans container images in CI for known CVEs.
- **Gitleaks** scans commits for accidentally committed secrets.
- **External Secrets Operator** syncs secrets from AWS Secrets Manager into Kubernetes.
- **Cosign** verifies image signatures before deployment.

## 10. Observability

- **Prometheus** scrapes metrics from all services. Custom recording rules aggregate offer latency, conversion rates, and pipeline throughput.
- **Grafana** dashboards provide operational visibility. Key dashboards: API latency, ML model performance, Kafka consumer lag, infrastructure health.
- **Loki** aggregates logs from all pods. Structured JSON logging with correlation IDs enables distributed tracing.
- **Alertmanager** routes alerts to Slack and PagerDuty based on severity and service ownership.
