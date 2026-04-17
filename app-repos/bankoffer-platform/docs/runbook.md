# BankOffer AI -- Operational Runbook

## 1. Service Overview

| Service              | Port  | Health Endpoint        | Owner           |
|----------------------|-------|------------------------|-----------------|
| Offer API            | 8000  | `/health`              | api_agent       |
| Notification Router  | 8001  | `/health`              | notification    |
| Kafka Consumer       | --    | Liveness probe (TCP)   | data_pipeline   |
| Airflow Webserver    | 8080  | `/health`              | data_pipeline   |
| Feast Feature Server | 6566  | `/health`              | data_pipeline   |
| MLflow Tracking      | 5000  | `/health`              | aiml_agent      |

## 2. Common Alerts and Response Procedures

### 2.1 OfferAPI_HighLatency

**Alert:** P99 latency for `/v1/offers` exceeds 200ms for 5 minutes.

**Severity:** Warning (P3)

**Diagnosis:**

1. Check Grafana dashboard "Offer API Latency" for the latency distribution.
2. Check Redis connectivity -- high latency often means a cache miss spike.
3. Check Kafka consumer lag -- if the feature store is stale, the API falls back to synchronous scoring which is slower.

**Resolution:**

```bash
# Check Redis connectivity from the API pod
kubectl exec -n production deploy/offer-api -- redis-cli -h redis-master ping

# Check current cache hit rate
kubectl exec -n production deploy/offer-api -- curl -s localhost:8000/metrics | grep cache_hit

# If Redis is down, restart the Redis pod
kubectl rollout restart statefulset/redis-master -n production

# If the issue is consumer lag, see section 2.3
```

**Escalation:** If latency remains above 200ms after 15 minutes, page the on-call engineer via PagerDuty.

---

### 2.2 OfferAPI_HighErrorRate

**Alert:** 5xx error rate exceeds 1% for 3 minutes.

**Severity:** Critical (P1)

**Diagnosis:**

1. Check Grafana dashboard "Offer API Errors" for error breakdown by status code.
2. Check Loki logs for stack traces: `{app="offer-api"} |= "ERROR"`.
3. Check if a recent deployment happened -- correlate with ArgoCD sync history.

**Resolution:**

```bash
# Check recent ArgoCD sync
argocd app history bankoffer-api

# If a bad deploy, rollback to previous revision
argocd app rollback bankoffer-api <previous-revision>

# Check logs for the root cause
kubectl logs -n production deploy/offer-api --tail=200 | grep ERROR

# If database connection errors, check PostgreSQL
kubectl exec -n production deploy/offer-api -- pg_isready -h postgres-primary
```

**Escalation:** Immediate page to on-call. If rollback does not resolve, escalate to the engineering lead.

---

### 2.3 Kafka_ConsumerLag_High

**Alert:** Consumer group `offer-feature-loader` lag exceeds 10,000 messages for 10 minutes.

**Severity:** Warning (P3)

**Diagnosis:**

1. Check Grafana dashboard "Kafka Consumer Lag".
2. Check if the consumer pods are running and healthy.
3. Check if the upstream producer rate has spiked (e.g., batch import from core banking).

**Resolution:**

```bash
# Check consumer group status
kubectl exec -n production deploy/kafka-consumer -- \
  kafka-consumer-groups.sh --bootstrap-server kafka:9092 \
  --group offer-feature-loader --describe

# Scale up consumers if throughput is insufficient
kubectl scale deploy/kafka-consumer -n production --replicas=6

# Check for stuck partitions
kubectl logs -n production deploy/kafka-consumer --tail=100 | grep -i "partition"
```

**Escalation:** If lag continues growing after scaling, check for poison messages and page on-call.

---

### 2.4 ML_ModelServing_Degraded

**Alert:** Model prediction error rate exceeds 5% or model version is older than 7 days.

**Severity:** Warning (P3)

**Diagnosis:**

1. Check MLflow UI for the currently deployed model version.
2. Check Grafana dashboard "ML Model Performance" for prediction accuracy metrics.
3. Check if the training pipeline (Airflow) has failed recently.

**Resolution:**

```bash
# Check current model version in the registry
kubectl exec -n production deploy/offer-api -- \
  python -c "import mlflow; print(mlflow.get_latest_versions('product-scorer'))"

# If model is stale, trigger retraining
kubectl exec -n production deploy/airflow-scheduler -- \
  airflow dags trigger model_training_dag

# If model predictions are wrong, rollback to previous version
kubectl exec -n production deploy/offer-api -- \
  python -c "
import mlflow
client = mlflow.tracking.MlflowClient()
client.transition_model_version_stage('product-scorer', version=<prev>, stage='Production')
"
```

**Escalation:** If model quality does not recover after retraining, escalate to the ML engineering team.

---

### 2.5 ArgoCD_SyncFailed

**Alert:** ArgoCD application sync status is "Failed" or "Degraded" for 10 minutes.

**Severity:** Critical (P2)

**Diagnosis:**

1. Check ArgoCD UI or CLI for sync errors.
2. Common causes: invalid YAML, image pull failures, resource quota exceeded.

**Resolution:**

```bash
# Check application sync status
argocd app get bankoffer-api

# View sync errors
argocd app sync bankoffer-api --dry-run

# If image pull failure, verify image exists in the registry
aws ecr describe-images --repository-name bankoffer/offer-api --image-ids imageTag=main-<sha>

# If resource quota exceeded, check namespace quotas
kubectl describe resourcequota -n production

# Force sync after fixing the issue
argocd app sync bankoffer-api --force
```

---

### 2.6 Notification_DeliveryRate_Low

**Alert:** Notification delivery success rate drops below 95% for 15 minutes.

**Severity:** Warning (P3)

**Diagnosis:**

1. Check Grafana dashboard "Notification Delivery" for per-channel breakdown.
2. Check if a specific channel (push/email/in-app) is failing.

**Resolution:**

```bash
# Check FCM delivery status
kubectl logs -n production deploy/notification-router --tail=200 | grep "fcm"

# Check SES sending quota
aws ses get-send-quota

# Check WebSocket connections
kubectl exec -n production deploy/notification-router -- \
  curl -s localhost:8001/metrics | grep websocket_connections

# If SES quota exceeded, check bounce rate
aws ses get-send-statistics
```

---

## 3. Routine Operations

### 3.1 Database Migrations

```bash
# Run Alembic migrations (staging first, then production)
kubectl exec -n staging deploy/offer-api -- alembic upgrade head
# Verify
kubectl exec -n staging deploy/offer-api -- alembic current

# After staging verification, apply to production
kubectl exec -n production deploy/offer-api -- alembic upgrade head
```

### 3.2 Kafka Topic Management

```bash
# List topics
kubectl exec -n production deploy/kafka-consumer -- \
  kafka-topics.sh --bootstrap-server kafka:9092 --list

# Create a new topic
kubectl exec -n production deploy/kafka-consumer -- \
  kafka-topics.sh --bootstrap-server kafka:9092 \
  --create --topic <topic-name> --partitions 12 --replication-factor 3

# Check topic configuration
kubectl exec -n production deploy/kafka-consumer -- \
  kafka-topics.sh --bootstrap-server kafka:9092 \
  --describe --topic transactions
```

### 3.3 Feature Store Materialization

```bash
# Materialize features to the online store
kubectl exec -n production deploy/feast-server -- \
  feast materialize-incremental $(date -u +%Y-%m-%dT%H:%M:%S)
```

### 3.4 Secret Rotation

Secrets are managed by the External Secrets Operator and stored in AWS Secrets Manager. To rotate:

```bash
# Update the secret in AWS Secrets Manager
aws secretsmanager update-secret --secret-id bankoffer/db-password --secret-string '<new-password>'

# The External Secrets Operator will sync within its poll interval (default: 1 hour)
# To force immediate sync:
kubectl annotate externalsecret db-credentials -n production force-sync=$(date +%s) --overwrite

# Verify the Kubernetes secret was updated
kubectl get secret db-credentials -n production -o jsonpath='{.metadata.annotations}'
```

### 3.5 Scaling Services

```bash
# Scale the Offer API (horizontal pod autoscaler is configured, but manual override is possible)
kubectl scale deploy/offer-api -n production --replicas=10

# Scale Kafka consumers
kubectl scale deploy/kafka-consumer -n production --replicas=8

# Check HPA status
kubectl get hpa -n production
```

## 4. Disaster Recovery

### 4.1 Database Recovery

PostgreSQL runs on RDS with automated daily snapshots and point-in-time recovery.

```bash
# Restore from snapshot (creates a new RDS instance)
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier bankoffer-db-restored \
  --db-snapshot-identifier <snapshot-id>

# Point-in-time recovery
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier bankoffer-db \
  --target-db-instance-identifier bankoffer-db-pitr \
  --restore-time "2026-03-31T10:00:00Z"
```

### 4.2 Kafka Recovery

Kafka (MSK) replicates across three availability zones. If a broker fails, MSK automatically replaces it.

For topic data recovery, consumer groups can be reset to replay events:

```bash
kubectl exec -n production deploy/kafka-consumer -- \
  kafka-consumer-groups.sh --bootstrap-server kafka:9092 \
  --group offer-feature-loader --reset-offsets --to-datetime "2026-03-30T00:00:00.000" \
  --topic transactions --execute
```

### 4.3 Full Cluster Recovery

If the EKS cluster is lost, rebuild from Terraform state and let ArgoCD re-sync all applications:

```bash
cd infra/terraform
terraform apply

# Once the cluster is up, install ArgoCD
helm install argocd argo/argo-cd -n argocd --create-namespace

# Apply the app-of-apps
kubectl apply -f infra/argocd/app-of-apps.yaml

# ArgoCD will sync all applications from the gitops/ directory
argocd app sync --all
```

## 5. Maintenance Windows

| Task                          | Schedule            | Duration | Impact                   |
|-------------------------------|---------------------|----------|--------------------------|
| RDS minor version upgrades    | Monthly, Sunday 3AM | 15 min   | Brief API latency spike  |
| EKS node group rotation       | Quarterly           | 30 min   | Rolling, no downtime     |
| Kafka broker patching (MSK)   | Monthly, Sunday 4AM | 20 min   | Temporary consumer lag   |
| Certificate renewal           | 60 days before exp  | 0 min    | Automated via cert-manager|
| Model retraining              | Weekly, Tuesday 2AM | 45 min   | No user impact           |

## 6. Contacts

| Role                | Team           | Escalation Channel     |
|---------------------|----------------|------------------------|
| On-call engineer    | Platform       | PagerDuty              |
| ML engineering lead | AI/ML          | Slack #ml-engineering  |
| Data engineering    | Data Platform  | Slack #data-platform   |
| Security            | InfoSec        | Slack #security-alerts |
| Infrastructure      | Platform       | Slack #infra           |

---

## Infrastructure Notes

### Docker networking — AI connector internet access

**Context:** The Docker daemon on this host is configured with `"iptables": false` and `"ip-forward": false`
in `/etc/docker/daemon.json`. This is intentional — it prevents Docker from conflicting with OpenStack
Neutron's iptables management. The side effect is that Docker never installs its own MASQUERADE rule,
so containers on the `bankofferingai_default` bridge (`172.18.0.0/16`, interface `br-9d316977e851`)
cannot reach the internet. This breaks AI connector calls (Perplexity, Claude, OpenAI, etc.).

**Fix:** A systemd service adds the single missing NAT rule at boot:

```
/etc/systemd/system/bankofferingai-nat.service
```

It runs `iptables -t nat -A POSTROUTING -s 172.18.0.0/16 ! -o br-9d316977e851 -j MASQUERADE`
idempotently (skips if the rule already exists). It is enabled and will start automatically after
`docker.service` on every boot.

**To verify after a reboot:**
```bash
systemctl status bankofferingai-nat.service
# Should show: Active: active (exited)

# Confirm the rule is present:
sudo iptables -t nat -S POSTROUTING | grep 172.18

# Confirm connectivity from the API container:
docker exec bankofferingai-api-1 python3 -c \
  "import socket; s=socket.create_connection(('104.18.27.48',443),5); print('OK'); s.close()"
```

**To disable** (e.g. if you intentionally want to air-gap the containers):
```bash
sudo systemctl disable --now bankofferingai-nat.service
```
