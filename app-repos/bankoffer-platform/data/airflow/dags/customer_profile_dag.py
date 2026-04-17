"""Airflow DAG: daily customer profile pipeline.

1. Extract transactions from PostgreSQL.
2. Normalise and validate with TransactionNormalizer.
3. Compute and load features into the Feast feature store.
4. Check for data drift and trigger model retraining if detected.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.utils.trigger_rule import TriggerRule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DAG-level defaults
# ---------------------------------------------------------------------------

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "email": ["data-alerts@bank.com"],
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

# ---------------------------------------------------------------------------
# Task callables
# ---------------------------------------------------------------------------


def extract_transactions(**context: Any) -> str:
    """Pull the previous day's transactions from PostgreSQL."""
    execution_date = context["ds"]
    hook = PostgresHook(postgres_conn_id="bank_postgres")

    sql = """
        SELECT transaction_id, customer_id, amount, currency,
               merchant_name, merchant_category_code, transaction_type,
               timestamp, channel, status
        FROM transactions
        WHERE DATE(timestamp) = %s
          AND status = 'completed'
        ORDER BY timestamp
    """
    records = hook.get_records(sql, parameters=[execution_date])
    columns = [
        "transaction_id", "customer_id", "amount", "currency",
        "merchant_name", "merchant_category_code", "transaction_type",
        "timestamp", "channel", "status",
    ]
    rows = [dict(zip(columns, r)) for r in records]
    logger.info("Extracted %d transactions for %s", len(rows), execution_date)

    # Serialise through XCom (for moderate volumes; use S3/GCS for large)
    return json.dumps(rows, default=str)


def normalize_transactions(**context: Any) -> str:
    """Run the TransactionNormalizer over extracted records."""
    from data.kafka.consumers.normalizer import TransactionNormalizer

    raw_json = context["ti"].xcom_pull(task_ids="extract_transactions")
    raw_records = json.loads(raw_json)

    normalizer = TransactionNormalizer()
    normalized = normalizer.normalize_batch(raw_records)
    logger.info("Normalised %d / %d records", len(normalized), len(raw_records))

    return json.dumps(
        [n.model_dump(mode="json") for n in normalized], default=str
    )


def compute_and_load_features(**context: Any) -> dict[str, Any]:
    """Aggregate normalised transactions into feature vectors and
    materialise them into the Feast online store."""
    import pandas as pd

    raw = context["ti"].xcom_pull(task_ids="normalize_transactions")
    records = json.loads(raw)
    if not records:
        logger.warning("No normalised records — skipping feature computation")
        return {"customers_processed": 0}

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["amount_usd"] = pd.to_numeric(df["amount_usd"])

    # --- per-customer aggregation ---
    features = (
        df.groupby("customer_id")
        .agg(
            avg_txn_amount_30d=("amount_usd", "mean"),
            total_spend_30d=("amount_usd", "sum"),
            txn_count_30d=("transaction_id", "count"),
            max_single_txn_30d=("amount_usd", "max"),
            std_txn_amount_30d=("amount_usd", "std"),
        )
        .reset_index()
    )
    features["std_txn_amount_30d"] = features["std_txn_amount_30d"].fillna(0)
    features["feature_timestamp"] = datetime.utcnow()

    # Category distribution
    category_pivot = (
        df.groupby(["customer_id", "merchant_category"])["amount_usd"]
        .sum()
        .unstack(fill_value=0)
    )
    category_totals = category_pivot.sum(axis=1)
    category_pct = category_pivot.div(category_totals, axis=0).add_prefix("category_").add_suffix("_pct")
    category_pct = category_pct.reset_index()

    features = features.merge(category_pct, on="customer_id", how="left")

    # Materialise to Feast
    try:
        from feast import FeatureStore

        store = FeatureStore(repo_path="data/feature_store")
        store.push("transaction_push", features)
        logger.info(
            "Pushed features for %d customers to Feast", len(features)
        )
    except Exception:
        logger.exception("Failed to push features to Feast — falling back to parquet")
        features.to_parquet(
            "data/feature_store/data/customer_transaction_features.parquet",
            index=False,
        )

    return {"customers_processed": len(features)}


def check_data_drift(**context: Any) -> str:
    """Detect feature drift and decide whether to retrain.

    Returns the task_id to follow: ``trigger_retraining`` if drift is
    detected, ``skip_retraining`` otherwise.
    """
    import numpy as np
    import pandas as pd

    try:
        current = pd.read_parquet(
            "data/feature_store/data/customer_transaction_features.parquet"
        )
    except FileNotFoundError:
        logger.info("No historical features found — skipping drift check")
        return "skip_retraining"

    # Simple drift heuristic: compare mean and std of key features against
    # thresholds.  In production, use Evidently / Great Expectations.
    drift_detected = False
    for col in ["avg_txn_amount_30d", "total_spend_30d", "txn_count_30d"]:
        if col not in current.columns:
            continue
        mean = current[col].mean()
        std = current[col].std()
        # Flag if the coefficient of variation exceeds a threshold
        if std > 0 and abs(mean) > 0:
            cv = std / abs(mean)
            if cv > 1.5:
                logger.warning("Drift detected on %s (CV=%.2f)", col, cv)
                drift_detected = True

    return "trigger_retraining" if drift_detected else "skip_retraining"


def trigger_retraining(**context: Any) -> None:
    """Kick off model retraining (calls the ML training pipeline)."""
    from airflow.api.common.trigger_dag import trigger_dag

    trigger_dag(
        dag_id="model_retraining_dag",
        run_id=f"drift_retrain_{context['ds']}",
        conf={"trigger_reason": "data_drift"},
        execution_date=context["execution_date"],
        replace_microseconds=False,
    )
    logger.info("Triggered model retraining due to data drift")


def skip_retraining(**context: Any) -> None:
    logger.info("No drift detected — skipping retraining for %s", context["ds"])


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

with DAG(
    dag_id="customer_profile_pipeline",
    default_args=default_args,
    description="Daily pipeline: extract txns -> normalise -> features -> drift check",
    schedule_interval="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["customer", "features", "ml"],
    max_active_runs=1,
) as dag:

    t_extract = PythonOperator(
        task_id="extract_transactions",
        python_callable=extract_transactions,
    )

    t_normalize = PythonOperator(
        task_id="normalize_transactions",
        python_callable=normalize_transactions,
    )

    t_features = PythonOperator(
        task_id="compute_and_load_features",
        python_callable=compute_and_load_features,
    )

    t_drift = BranchPythonOperator(
        task_id="check_data_drift",
        python_callable=check_data_drift,
    )

    t_retrain = PythonOperator(
        task_id="trigger_retraining",
        python_callable=trigger_retraining,
    )

    t_skip = PythonOperator(
        task_id="skip_retraining",
        python_callable=skip_retraining,
    )

    # DAG wiring
    t_extract >> t_normalize >> t_features >> t_drift
    t_drift >> [t_retrain, t_skip]
