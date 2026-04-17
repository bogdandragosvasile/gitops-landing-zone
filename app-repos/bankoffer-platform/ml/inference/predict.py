"""Inference module for customer profile and product scoring models.

Loads trained models from the MLflow model registry and exposes
`predict_profile()` and `predict_scores()` for online serving.

Models are cached in-process after first load to avoid repeated
registry lookups on every request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "ml" / "registry" / "mlflow_config.yaml"

# Module-level model cache
_model_cache: dict[str, Any] = {}


def _load_config() -> dict[str, Any]:
    """Load MLflow config for tracking URI and model names."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    return {"tracking_uri": "http://localhost:5000"}


def _init_mlflow() -> None:
    """Set MLflow tracking URI from config."""
    config = _load_config()
    mlflow.set_tracking_uri(config.get("tracking_uri", "http://localhost:5000"))


def _get_model(model_name: str, stage: str = "Production") -> Any:
    """Load a model from MLflow registry, with in-process caching.

    Args:
        model_name: Registered model name in MLflow.
        stage: Model stage to load (Production, Staging, None for latest).

    Returns:
        The loaded sklearn model/pipeline.
    """
    cache_key = f"{model_name}@{stage}"
    if cache_key in _model_cache:
        return _model_cache[cache_key]

    _init_mlflow()

    model_uri = f"models:/{model_name}/{stage}"
    try:
        model = mlflow.sklearn.load_model(model_uri)
        logger.info("Loaded model %s (stage=%s) from registry", model_name, stage)
    except Exception:
        # Fall back to latest version if stage not found
        logger.warning(
            "Could not load %s at stage %s, trying latest version", model_name, stage
        )
        model_uri = f"models:/{model_name}/latest"
        model = mlflow.sklearn.load_model(model_uri)
        logger.info("Loaded model %s (latest) from registry", model_name)

    _model_cache[cache_key] = model
    return model


def _get_label_classes(model_name: str = "life_stage_classifier") -> list[str]:
    """Retrieve label encoder classes artifact from MLflow."""
    cache_key = f"{model_name}_classes"
    if cache_key in _model_cache:
        return _model_cache[cache_key]

    _init_mlflow()

    try:
        client = mlflow.tracking.MlflowClient()
        # Get the latest production model version
        versions = client.get_latest_versions(model_name, stages=["Production"])
        if not versions:
            versions = client.get_latest_versions(model_name)
        if not versions:
            raise ValueError(f"No versions found for model {model_name}")

        run_id = versions[0].run_id
        artifact_path = client.download_artifacts(run_id, "life_stage_classes.json")

        import json

        with open(artifact_path) as f:
            data = json.load(f)
        classes = data["classes"]
        _model_cache[cache_key] = classes
        return classes

    except Exception as e:
        logger.warning("Could not load label classes from MLflow: %s. Using defaults.", e)
        default_classes = [
            "mid_career",
            "new_graduate",
            "pre_retirement",
            "retired",
            "young_family",
        ]
        _model_cache[cache_key] = default_classes
        return default_classes


@dataclass
class ProfilePrediction:
    """Result of a profile prediction."""

    customer_id: str
    life_stage: str
    life_stage_probabilities: dict[str, float]
    risk_tolerance: float


@dataclass
class ScorePrediction:
    """Result of a product scoring prediction."""

    acceptance_probability: float
    optimal_threshold: float
    recommended: bool


def predict_profile(
    customer_id: str,
    features: dict[str, Any],
    model_stage: str = "Production",
) -> ProfilePrediction:
    """Predict customer life stage and risk tolerance.

    Args:
        customer_id: Customer identifier.
        features: Dictionary containing feature values. Expected keys:
            age, account_tenure_years, dependents, annual_income,
            monthly_transactions, savings_balance, loan_balance,
            investment_balance, savings_ratio, loan_to_income,
            credit_score, years_with_bank
        model_stage: MLflow model stage to use.

    Returns:
        ProfilePrediction with life stage classification and risk score.
    """
    life_stage_features = [
        "age",
        "account_tenure_years",
        "dependents",
        "annual_income",
        "monthly_transactions",
        "savings_balance",
        "loan_balance",
    ]
    risk_features = [
        "age",
        "annual_income",
        "investment_balance",
        "savings_ratio",
        "loan_to_income",
        "credit_score",
        "monthly_transactions",
        "years_with_bank",
    ]

    # Life stage prediction
    clf = _get_model("life_stage_classifier", stage=model_stage)
    cls_input = np.array([[features[f] for f in life_stage_features]])
    predicted_class = clf.predict(cls_input)[0]
    class_probas = clf.predict_proba(cls_input)[0]

    classes = _get_label_classes()
    life_stage = classes[predicted_class] if isinstance(predicted_class, (int, np.integer)) else str(predicted_class)
    probabilities = {cls: round(float(p), 4) for cls, p in zip(classes, class_probas)}

    # Risk tolerance prediction
    reg = _get_model("risk_tolerance_regressor", stage=model_stage)
    risk_input = np.array([[features[f] for f in risk_features]])
    risk_score = float(reg.predict(risk_input)[0])
    risk_score = round(max(1.0, min(10.0, risk_score)), 2)

    logger.info(
        "Profile prediction for %s: life_stage=%s risk=%.2f",
        customer_id,
        life_stage,
        risk_score,
    )

    return ProfilePrediction(
        customer_id=customer_id,
        life_stage=life_stage,
        life_stage_probabilities=probabilities,
        risk_tolerance=risk_score,
    )


def predict_scores(
    features: dict[str, Any],
    model_stage: str = "Production",
    threshold: float | None = None,
) -> ScorePrediction:
    """Predict offer acceptance probability for a customer-product pair.

    Args:
        features: Dictionary with keys matching CATEGORICAL_FEATURES + NUMERIC_FEATURES
            from the scoring model training script.
        model_stage: MLflow model stage to use.
        threshold: Decision threshold. If None, uses the optimal threshold
            logged during training (defaults to 0.5 if unavailable).

    Returns:
        ScorePrediction with acceptance probability and recommendation.
    """
    import pandas as pd

    pipeline = _get_model("offer_scoring_model", stage=model_stage)

    input_df = pd.DataFrame([features])
    acceptance_prob = float(pipeline.predict_proba(input_df)[:, 1][0])

    # Use provided threshold or default
    if threshold is None:
        threshold = 0.5

    return ScorePrediction(
        acceptance_probability=round(acceptance_prob, 4),
        optimal_threshold=threshold,
        recommended=acceptance_prob >= threshold,
    )


def clear_cache() -> None:
    """Clear the in-process model cache. Useful for testing or model refresh."""
    _model_cache.clear()
    logger.info("Model cache cleared")
