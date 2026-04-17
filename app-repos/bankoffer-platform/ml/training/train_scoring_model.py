"""Training script for the product scoring model.

Fine-tunes scoring weights based on historical offer acceptance data.
The model learns per-product-type relevance weights conditioned on customer
segments, improving on the default LLM-based scoring.

Logs all experiments and artifacts to MLflow.

Usage:
    python -m ml.training.train_scoring_model --experiment scoring_model
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import yaml
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    log_loss,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.compose import ColumnTransformer

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "ml" / "registry" / "mlflow_config.yaml"

CATEGORICAL_FEATURES = ["life_stage", "product_type"]
NUMERIC_FEATURES = [
    "risk_score",
    "annual_income",
    "savings_ratio",
    "loan_to_income",
    "llm_relevance_score",
    "llm_confidence_score",
    "days_since_last_offer",
    "previous_acceptances",
    "previous_dismissals",
]
TARGET = "accepted"


def load_mlflow_config() -> dict[str, Any]:
    """Load MLflow configuration from mlflow_config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    return {
        "tracking_uri": "http://localhost:5000",
        "experiments": {"scoring_model": "bank-offering-scoring-model"},
    }


def load_acceptance_data() -> pd.DataFrame:
    """Load historical offer acceptance data.

    In production this reads from the data warehouse.  Falls back to
    synthetic data for local development.
    """
    try:
        import sqlalchemy

        engine = sqlalchemy.create_engine(
            "postgresql://postgres:postgres@localhost:5432/bankofferingai"
        )
        query = """
            SELECT
                o.customer_id,
                cp.life_stage,
                cp.risk_score,
                cp.annual_income,
                cp.savings_ratio,
                cp.loan_to_income,
                o.product_type,
                o.llm_relevance_score,
                o.llm_confidence_score,
                o.days_since_last_offer,
                o.previous_acceptances,
                o.previous_dismissals,
                o.accepted
            FROM offer_history o
            JOIN customer_profiles cp ON o.customer_id = cp.customer_id
            WHERE o.created_at >= NOW() - INTERVAL '90 days'
        """
        df = pd.read_sql(query, engine)
        logger.info("Loaded %d acceptance records from database", len(df))
        return df
    except Exception as e:
        logger.warning("Database unavailable (%s), using synthetic data", e)
        return _generate_synthetic_acceptance_data()


def _generate_synthetic_acceptance_data(
    n_samples: int = 10000, seed: int = 42
) -> pd.DataFrame:
    """Generate synthetic offer acceptance data for training."""
    rng = np.random.RandomState(seed)

    life_stages = rng.choice(
        ["new_graduate", "young_family", "mid_career", "pre_retirement", "retired"],
        size=n_samples,
        p=[0.12, 0.25, 0.35, 0.18, 0.10],
    )
    product_types = rng.choice(
        ["credit_card", "savings", "mortgage", "personal_loan", "investment", "insurance"],
        size=n_samples,
    )
    risk_scores = rng.uniform(1, 10, n_samples).round(2)
    incomes = rng.lognormal(10.8, 0.6, n_samples).clip(15000, 500000).round(2)
    savings_ratios = rng.beta(2, 5, n_samples).round(4)
    loan_to_incomes = rng.exponential(0.3, n_samples).clip(0, 3).round(4)
    llm_relevance = rng.beta(3, 2, n_samples).round(4)
    llm_confidence = rng.beta(4, 2, n_samples).round(4)
    days_since = rng.exponential(30, n_samples).clip(0, 365).astype(int)
    prev_accepts = rng.poisson(1.5, n_samples).clip(0, 20)
    prev_dismissals = rng.poisson(3, n_samples).clip(0, 30)

    # Acceptance probability: logistic combination of features
    logit = (
        0.8 * llm_relevance
        + 0.3 * llm_confidence
        + 0.1 * (risk_scores / 10)
        + 0.2 * (prev_accepts / (prev_accepts + prev_dismissals + 1))
        - 0.3 * (prev_dismissals / (prev_accepts + prev_dismissals + 1))
        + rng.normal(0, 0.2, n_samples)
        - 0.5
    )
    prob = 1 / (1 + np.exp(-logit * 3))
    accepted = (rng.random(n_samples) < prob).astype(int)

    return pd.DataFrame(
        {
            "life_stage": life_stages,
            "product_type": product_types,
            "risk_score": risk_scores,
            "annual_income": incomes,
            "savings_ratio": savings_ratios,
            "loan_to_income": loan_to_incomes,
            "llm_relevance_score": llm_relevance,
            "llm_confidence_score": llm_confidence,
            "days_since_last_offer": days_since,
            "previous_acceptances": prev_accepts,
            "previous_dismissals": prev_dismissals,
            "accepted": accepted,
        }
    )


def build_pipeline() -> Pipeline:
    """Build the sklearn pipeline with preprocessing and model."""
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), NUMERIC_FEATURES),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_FEATURES,
            ),
        ]
    )

    pipeline = Pipeline(
        [
            ("preprocessor", preprocessor),
            (
                "classifier",
                CalibratedClassifierCV(
                    LogisticRegression(
                        C=1.0,
                        max_iter=1000,
                        solver="lbfgs",
                        class_weight="balanced",
                    ),
                    cv=5,
                    method="isotonic",
                ),
            ),
        ]
    )
    return pipeline


def evaluate_model(
    pipeline: Pipeline,
    X_test: pd.DataFrame,
    y_test: np.ndarray,
) -> dict[str, float]:
    """Evaluate the scoring model on held-out test data."""
    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_proba)
    ap = average_precision_score(y_test, y_proba)
    f1 = f1_score(y_test, y_pred)
    logloss = log_loss(y_test, y_proba)

    # Find optimal threshold by F1
    precisions, recalls, thresholds = precision_recall_curve(y_test, y_proba)
    f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-8)
    optimal_idx = np.argmax(f1_scores)
    optimal_threshold = float(thresholds[optimal_idx]) if optimal_idx < len(thresholds) else 0.5

    metrics = {
        "auc_roc": round(auc, 4),
        "average_precision": round(ap, 4),
        "f1": round(f1, 4),
        "log_loss": round(logloss, 4),
        "optimal_threshold": round(optimal_threshold, 4),
        "acceptance_rate": round(float(y_test.mean()), 4),
    }

    logger.info(
        "Scoring model: AUC=%.4f AP=%.4f F1=%.4f LogLoss=%.4f",
        auc,
        ap,
        f1,
        logloss,
    )
    return metrics


def run_training(experiment_name: str | None = None) -> None:
    """Execute the full scoring model training pipeline."""
    config = load_mlflow_config()
    mlflow.set_tracking_uri(config.get("tracking_uri", "http://localhost:5000"))

    exp_name = experiment_name or config.get("experiments", {}).get(
        "scoring_model", "bank-offering-scoring-model"
    )
    mlflow.set_experiment(exp_name)

    logger.info("Loading acceptance data...")
    df = load_acceptance_data()
    logger.info("Loaded %d samples (acceptance rate: %.2f%%)", len(df), df[TARGET].mean() * 100)

    X = df[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    y = df[TARGET].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    with mlflow.start_run(run_name="scoring_model_training") as run:
        mlflow.log_param("dataset_size", len(df))
        mlflow.log_param("train_size", len(X_train))
        mlflow.log_param("test_size", len(X_test))
        mlflow.log_param("features_categorical", CATEGORICAL_FEATURES)
        mlflow.log_param("features_numeric", NUMERIC_FEATURES)
        mlflow.log_param("acceptance_rate", round(float(y.mean()), 4))

        logger.info("Building and training pipeline...")
        pipeline = build_pipeline()
        pipeline.fit(X_train, y_train)

        logger.info("Evaluating model...")
        metrics = evaluate_model(pipeline, X_test, y_test)

        mlflow.log_metrics(metrics)
        mlflow.sklearn.log_model(
            pipeline,
            artifact_path="scoring_model",
            registered_model_name="offer_scoring_model",
        )

        # Log feature importance from the underlying logistic regression
        try:
            base_estimator = pipeline.named_steps["classifier"].calibrated_classifiers_[0].estimator
            feature_names = pipeline.named_steps["preprocessor"].get_feature_names_out()
            importances = dict(zip(feature_names.tolist(), base_estimator.coef_[0].tolist()))
            mlflow.log_dict(importances, artifact_file="feature_coefficients.json")
        except Exception as e:
            logger.warning("Could not extract feature coefficients: %s", e)

        logger.info("Training complete. MLflow run ID: %s", run.info.run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train product scoring model")
    parser.add_argument(
        "--experiment",
        type=str,
        default=None,
        help="MLflow experiment name (overrides config)",
    )
    args = parser.parse_args()

    run_training(experiment_name=args.experiment)


if __name__ == "__main__":
    main()
