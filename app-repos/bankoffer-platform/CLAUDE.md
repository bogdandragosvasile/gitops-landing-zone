# CLAUDE.md -- BankOffer AI Project Instructions

## What is this project?

BankOffer AI is an AI-powered real-time personalization platform for banking customers. It ingests transaction streams, builds customer profiles, scores eligible financial products, and delivers hyper-personalized offers through push notifications, email, and in-app banners. The system is deployed on Kubernetes via a GitOps pipeline (ArgoCD) and orchestrated by a multi-agent CI/CD architecture where each domain is owned by an autonomous Claude Code agent.

## Tech Stack

| Layer              | Technology                                              |
|--------------------|---------------------------------------------------------|
| Language           | Python 3.12+, TypeScript (frontend)                    |
| API Framework      | FastAPI with Uvicorn                                    |
| ML / AI            | scikit-learn, XGBoost, sentence-transformers, MLflow    |
| Data Pipeline      | Apache Kafka, Apache Airflow, dbt                       |
| Feature Store      | Feast (backed by Redis + PostgreSQL)                    |
| Database           | PostgreSQL 16, Redis 7                                  |
| Infrastructure     | Terraform, Helm, Kubernetes (EKS)                       |
| GitOps             | ArgoCD, Kustomize, GitHub Actions                       |
| Observability      | Prometheus, Grafana, Loki, Alertmanager                 |
| Security           | OPA/Gatekeeper, Trivy, Gitleaks, Cosign                 |
| Testing            | pytest, Cypress, k6, Pact (contract tests)              |
| Frontend           | Next.js / React                                         |

## Repository Structure

```
/
  audit.yaml            # Append-only agent state + audit log (GitOps truth)
  CLAUDE.md             # This file -- project instructions for Claude agents
  README.md             # Human-facing project README
  infra/                # Terraform modules, Helm charts, ArgoCD apps
  services/
    api/                # FastAPI offer service
    worker/             # Kafka consumers, background jobs
    notification/       # Push / email / in-app notification service
  data/                 # Airflow DAGs, dbt models, Kafka schemas
  ml/                   # Customer profiler, product scorer, offer ranker
  frontend/             # Next.js dashboard
  gitops/               # ArgoCD Application CRDs, Kustomize overlays
  observability/        # Prometheus rules, Grafana dashboards, Loki config
  security/             # OPA policies, SBOM, secret rotation
  tests/                # Unit, integration, e2e, load, contract tests
  docs/                 # Architecture docs, runbooks, ADRs
  .github/              # Workflows, PR template, CODEOWNERS
```

## Agent Communication Protocol

Agents coordinate exclusively through Git artifacts. There is no shared memory or message bus between agents.

### Primary channels

1. **audit.yaml** -- The single source of truth for agent state. Before starting work, every agent reads `audit.yaml` to check its status and task list. After completing a task, it appends an entry to the `history` array and updates its own status block.

2. **Pull Requests** -- Each agent opens a PR when its task batch is complete. The PR body contains a structured YAML block (`agent-result`) describing what was done, what changed, and any follow-up tasks.

3. **GitHub Issues** -- Agents create issues to flag blockers, request human review, or propose architectural changes.

4. **repository_dispatch events** -- The orchestrator agent fires `repository_dispatch` events to wake child agents after updating `audit.yaml`. The event type follows the pattern `dispatch_{agent_name}`.

### Rules every agent must follow

- Read `audit.yaml` before doing anything.
- Never delete history entries -- only append.
- Keep PRs scoped to one agent's task batch.
- Include the `agent-result` YAML block in every PR body.
- If a task fails, set your status to `blocked`, append the error to history, and open a GitHub Issue.
- Do not modify files outside your designated directories unless coordinating through a PR review.

### Task lifecycle

```
pending -> active -> completed
                  -> blocked (on failure)
```

### Depth hierarchy

- **Depth 0 -- Orchestrator**: Owns `audit.yaml`, dispatches depth-1 agents.
- **Depth 1 -- Domain agents**: infra, data_pipeline, aiml, api, notification.
- **Depth 2 -- Sub-agents**: gitops, security, observability, test_qa. Each has a parent at depth 1.

## Code Conventions

- Python: follow PEP 8, use type hints, run `ruff` for linting.
- All services must include a `Dockerfile` and a Helm chart under `infra/helm/`.
- Every new endpoint needs an OpenAPI schema update and at least one pytest test.
- Terraform modules use the `tf-` prefix for resource names.
- Kubernetes manifests go through Kustomize overlays (base / staging / production).
- Secrets are never committed -- use External Secrets Operator with AWS Secrets Manager.

## How to Run Locally

```bash
# Prerequisites: Python 3.12+, Docker, kubectl, helm
pip install -r requirements.txt
docker compose up -d   # Postgres, Redis, Kafka
uvicorn services.api.main:app --reload --port 8000
```

## How to Deploy

Merging to `main` triggers the ArgoCD sync. ArgoCD watches `gitops/` overlays and applies changes to the target Kubernetes cluster. Image tags are updated automatically by the ArgoCD Image Updater.
