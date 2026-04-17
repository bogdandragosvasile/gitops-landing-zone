# BankOffer AI

Real-time, AI-powered personalization platform that delivers hyper-targeted financial product offers to bank customers based on their transaction behavior, risk profile, and life-stage signals.

## Architecture Overview

```
                         +------------------+
                         |   Mobile / Web   |
                         +--------+---------+
                                  |
                           HTTPS / WSS
                                  |
                    +-------------v--------------+
                    |     API Gateway (Kong)      |
                    +-------------+--------------+
                                  |
                    +-------------v--------------+
                    |   Offer API  (FastAPI)      |
                    |   /v1/offers  /v1/profile   |
                    +---+--------+----------+----+
                        |        |          |
              +---------+   +----v----+  +--v-----------+
              |             | Feature |  | Notification |
              |             | Store   |  | Router       |
              |             | (Feast) |  +--+-----------+
              |             +----+----+     |    |    |
              |                  |        Push Email InApp
              |                  |
     +--------v--------+  +-----v---------+
     | ML Pipeline      |  | Data Pipeline  |
     | - Profiler       |  | - Kafka        |
     | - Scorer         |  | - Airflow      |
     | - Ranker         |  | - dbt          |
     +--------+---------+  +-------+--------+
              |                     |
              +----------+----------+
                         |
                +--------v---------+
                |   PostgreSQL 16  |
                |   Redis 7        |
                +------------------+
```

### Flow

1. **Ingest** -- Transaction events arrive via Kafka topics. Consumers normalize the data and load derived features into the Feast feature store.
2. **Profile** -- The customer profiler aggregates features (spending patterns, income signals, product holdings) into a real-time profile vector.
3. **Score** -- The product scorer evaluates every eligible banking product against the profile. An XGBoost model predicts conversion probability.
4. **Rank** -- The offer ranker applies business rules, A/B test assignments, and diversity constraints to produce a final ranked list.
5. **Serve** -- The FastAPI offer endpoint returns the top-N offers for a given customer, with sub-100ms P99 latency.
6. **Notify** -- The notification router delivers the winning offer through the customer's preferred channel (push, email, or in-app banner).

## Tech Stack

| Layer              | Technology                                              |
|--------------------|---------------------------------------------------------|
| Language           | Python 3.12+, TypeScript (frontend)                    |
| API Framework      | FastAPI, Uvicorn                                        |
| ML / AI            | scikit-learn, XGBoost, sentence-transformers, MLflow    |
| Data Pipeline      | Apache Kafka, Apache Airflow, dbt                       |
| Feature Store      | Feast (Redis + PostgreSQL backend)                      |
| Database           | PostgreSQL 16, Redis 7                                  |
| Infrastructure     | Terraform, Helm, Kubernetes (EKS)                       |
| GitOps             | ArgoCD, Kustomize, GitHub Actions                       |
| Observability      | Prometheus, Grafana, Loki, Alertmanager                 |
| Security           | OPA/Gatekeeper, Trivy, Gitleaks, Cosign                 |
| Testing            | pytest, Cypress, k6, Pact                               |

## Project Structure

```
BankOfferingAI/
  audit.yaml                 # Append-only agent state and audit log
  CLAUDE.md                  # Agent instructions for Claude Code
  README.md                  # This file
  infra/
    terraform/               # EKS cluster, RDS, ElastiCache, VPC modules
    helm/                    # Helm charts for each service
    argocd/                  # ArgoCD Application manifests
  services/
    api/                     # FastAPI offer service
    worker/                  # Kafka consumers, async background jobs
    notification/            # Multi-channel notification service
  data/
    airflow/                 # Airflow DAG definitions
    dbt/                     # dbt models for analytics warehouse
    kafka/                   # Avro schemas, topic configs
  ml/
    profiler/                # Customer profile builder
    scorer/                  # Product conversion-probability model
    ranker/                  # Offer ranking with business rules
    registry/                # MLflow model registry configuration
  frontend/                  # Next.js customer dashboard
  gitops/
    base/                    # Kustomize base manifests
    overlays/                # staging / production overlays
  observability/
    prometheus/              # Recording and alerting rules
    grafana/                 # Dashboard JSON definitions
    loki/                    # Log aggregation config
  security/
    opa/                     # OPA/Gatekeeper policies
    sbom/                    # Software bill of materials
  tests/
    unit/                    # pytest unit tests
    integration/             # pytest integration tests
    e2e/                     # Cypress end-to-end tests
    load/                    # k6 load test scripts
    contract/                # Pact consumer/provider tests
  docs/
    architecture.md          # System architecture document
    agent-communication.md   # Agent protocol specification
    runbook.md               # Operational runbook
  .github/
    workflows/               # CI/CD GitHub Actions
    CODEOWNERS               # Code ownership rules
    pull_request_template.md # PR template with agent-result block
```

## Getting Started

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- kubectl and Helm 3
- Terraform 1.5+
- Node.js 20+ (for frontend)

### Local Development

```bash
# Clone the repository
git clone https://github.com/ptx37/BankOfferingAI.git
cd BankOfferingAI

# Create a Python virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt

# Start infrastructure services
docker compose up -d   # PostgreSQL, Redis, Kafka, Zookeeper

# Run database migrations
alembic upgrade head

# Start the API server
uvicorn services.api.main:app --reload --port 8000

# Run tests
pytest tests/unit/ -v
```

### Deploying to Kubernetes

The platform follows a GitOps workflow. Merging to `main` triggers ArgoCD to sync all manifests under `gitops/overlays/production/`.

```bash
# Provision infrastructure (first time only)
cd infra/terraform
terraform init && terraform apply

# Install ArgoCD app-of-apps
kubectl apply -f infra/argocd/app-of-apps.yaml

# ArgoCD handles everything from here -- monitor via:
argocd app list
argocd app get bankoffer-api
```

## Multi-Agent CI/CD

This repository is maintained by a hierarchy of Claude Code agents. The orchestrator agent coordinates domain-specific agents (infra, data, AI/ML, API, notifications) and sub-agents (GitOps, security, observability, QA). All agent state lives in `audit.yaml` at the repo root. See `docs/agent-communication.md` for the full protocol specification.

## Contributing

1. Fork the repository.
2. Create a feature branch from `main`.
3. Make your changes and add tests.
4. Open a pull request using the provided PR template.
5. Ensure all CI checks pass before requesting review.

## License

Proprietary. All rights reserved.
