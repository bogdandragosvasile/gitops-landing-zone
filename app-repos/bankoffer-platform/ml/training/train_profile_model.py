"""Training script for customer profile classification model.

Trains two models:
  1. life_stage classifier (RandomForest) -- predicts customer life stage from features.
  2. risk_tolerance regressor (GradientBoosting) -- predicts risk tolerance score.

Features are loaded from the Feast feature store.  Models and metrics are logged
to MLflow for experiment tracking and model registry.

Usage:
    python -m ml.training.train_profile_model --experiment profile_model --epochs 1
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import GradientBoostingRegressor, RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "ml" / "registry" / "mlflow_config.yaml"

LIFE_STAGE_FEATURES = [
    "age",
    "account_tenure_years",
    "dependents",
    "annual_income",
    "monthly_transactions",
    "savings_balance",
    "loan_balance",
]

RISK_FEATURES = [
    "age",
    "annual_income",
    "investment_balance",
    "savings_ratio",
    "loan_to_income",
    "credit_score",
    "monthly_transactions",
    "years_with_bank",
]


def load_mlflow_config() -> dict[str, Any]:
    """Load MLflow configuration from mlflow_config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    logger.warning("MLflow config not found at %s, using defaults", CONFIG_PATH)
    return {
        "tracking_uri": "http://localhost:5000",
        "experiments": {
            "profile_model": "bank-offering-profile-model",
        },
    }


def load_features_from_feast(entity_ids: list[str] | None = None) -> pd.DataFrame:
    """Load training features from the Feast feature store.

    Falls back to a synthetic dataset if Feast is unavailable (e.g. local dev).
    """
    try:
        from feast import FeatureStore

        store = FeatureStore(repo_path=str(PROJECT_ROOT / "data" / "feast"))
        entity_df = pd.DataFrame({"customer_id": entity_ids or []})
        entity_df["event_timestamp"] = pd.Timestamp.now()

        features = store.get_historical_features(
            entity_df=entity_df,
            features=[
                "customer_features:age",
                "customer_features:account_tenure_years",
                "customer_features:dependents",
                "customer_features:annual_income",
                "customer_features:monthly_transactions",
                "customer_features:savings_balance",
                "customer_features:loan_balance",
                "customer_features:investment_balance",
                "customer_features:savings_ratio",
                "customer_features:loan_to_income",
                "customer_features:credit_score",
                "customer_features:years_with_bank",
                "customer_features:life_stage",
                "customer_features:risk_tolerance",
            ],
        ).to_df()
        logger.info("Loaded %d rows from Feast", len(features))
        return features

    except Exception as e:
        logger.warning("Feast unavailable (%s), generating synthetic data", e)
        return _generate_synthetic_data(n_samples=5000)


def _generate_synthetic_data(n_samples: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Generate a synthetic training dataset for development/testing."""
    rng = np.random.RandomState(seed)

    ages = rng.randint(18, 80, n_samples)
    tenures = rng.uniform(0, 40, n_samples).clip(0, (ages - 18).astype(float))
    dependents = rng.poisson(1, n_samples).clip(0, 5)
    incomes = rng.lognormal(mean=10.8, sigma=0.6, size=n_samples).clip(15000, 500000)
    monthly_txns = rng.poisson(30, n_samples).clip(1, 200)
    savings = rng.lognormal(mean=9.0, sigma=1.2, size=n_samples).clip(0, 2_000_000)
    loans = rng.lognormal(mean=9.5, sigma=1.5, size=n_samples).clip(0, 1_000_000)
    investments = rng.lognormal(mean=8.5, sigma=1.8, size=n_samples).clip(0, 5_000_000)
    credit_scores = rng.normal(700, 80, n_samples).clip(300, 850).astype(int)
    years_with_bank = tenures + rng.uniform(0, 2, n_samples)

    savings_ratio = (savings / (incomes + 1e-6)).clip(0, 1)
    loan_to_income = (loans / (incomes + 1e-6)).clip(0, 5)

    # Derive life stage labels using rule-based logic
    life_stages = []
    for i in range(n_samples):
        if ages[i] >= 67:
            life_stages.append("retired")
        elif ages[i] >= 55:
            life_stages.append("pre_retirement")
        elif ages[i] <= 25 and tenures[i] <= 2:
            life_stages.append("new_graduate")
        elif 25 <= ages[i] <= 40 and dependents[i] >= 1:
            life_stages.append("young_family")
        else:
            life_stages.append("mid_career")
    life_stages = np.array(life_stages)

    # Risk tolerance: derived continuous target
    age_factor = 10.0 - (ages - 18) * (7.0 / 62.0)
    risk_tolerance = (
        0.3 * age_factor
        + 0.2 * np.log10(np.maximum(incomes / 10000, 1)) * 3
        + 0.25 * (investments / incomes).clip(0, 3)
        + 0.15 * savings_ratio * 9
        - 0.1 * loan_to_income * 5
        + rng.normal(0, 0.5, n_samples)
    ).clip(1, 10)

    return pd.DataFrame(
        {
            "age": ages,
            "account_tenure_years": tenures.round(2),
            "dependents": dependents,
            "annual_income": incomes.round(2),
            "monthly_transactions": monthly_txns,
            "savings_balance": savings.round(2),
            "loan_balance": loans.round(2),
            "investment_balance": investments.round(2),
            "savings_ratio": savings_ratio.round(4),
            "loan_to_income": loan_to_income.round(4),
            "credit_score": credit_scores,
            "years_with_bank": years_with_bank.round(2),
            "life_stage": life_stages,
            "risk_tolerance": risk_tolerance.round(2),
        }
    )


def train_life_stage_classifier(
    df: pd.DataFrame,
    test_size: float = 0.2,
    random_state: int = 42,
) -> tuple[RandomForestClassifier, LabelEncoder, dict[str, Any]]:
    """Train a RandomForest classifier to predict life_stage.

    Returns:
        Tuple of (trained model, label encoder, evaluation metrics dict).
    """
    le = LabelEncoder()
    y = le.fit_transform(df["life_stage"])
    X = df[LIFE_STAGE_FEATURES].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_split=10,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=random_state,
        n_jobs=-1,
    )
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    f1_macro = f1_score(y_test, y_pred, average="macro")
    f1_weighted = f1_score(y_test, y_pred, average="weighted")

    # Cross-validation
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=random_state)
    cv_scores = cross_val_score(clf, X, y, cv=cv, scoring="f1_macro")

    report = classification_report(
        y_test, y_pred, target_names=le.classes_, output_dict=True
    )

    metrics = {
        "accuracy": round(accuracy, 4),
        "f1_macro": round(f1_macro, 4),
        "f1_weighted": round(f1_weighted, 4),
        "cv_f1_macro_mean": round(cv_scores.mean(), 4),
        "cv_f1_macro_std": round(cv_scores.std(), 4),
        "classification_report": report,
    }

    logger.info(
        "Life stage classifier: accuracy=%.4f f1_macro=%.4f cv_f1=%.4f +/- %.4f",
        accuracy,
        f1_macro,
        cv_scores.mean(),
        cv_scores.std(),
    )

    return clf, le, metrics


def train_risk_tolerance_regressor(
    df: pd.DataFrame,
    test_size: float = 0.2,
    random_state: int = 42,
) -> tuple[GradientBoostingRegressor, dict[str, Any]]:
    """Train a GradientBoosting regressor to predict risk_tolerance (1-10).

    Returns:
        Tuple of (trained model, evaluation metrics dict).
    """
    y = df["risk_tolerance"].values
    X = df[RISK_FEATURES].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state
    )

    reg = GradientBoostingRegressor(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        min_samples_split=10,
        min_samples_leaf=5,
        random_state=random_state,
    )
    reg.fit(X_train, y_train)

    y_pred = reg.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    r2 = r2_score(y_test, y_pred)

    metrics = {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4),
    }

    logger.info("Risk tolerance regressor: MAE=%.4f RMSE=%.4f R2=%.4f", mae, rmse, r2)

    return reg, metrics


def run_training(experiment_name: str | None = None) -> None:
    """Execute the full training pipeline and log results to MLflow."""
    config = load_mlflow_config()
    mlflow.set_tracking_uri(config.get("tracking_uri", "http://localhost:5000"))

    exp_name = experiment_name or config.get("experiments", {}).get(
        "profile_model", "bank-offering-profile-model"
    )
    mlflow.set_experiment(exp_name)

    logger.info("Loading features...")
    df = load_features_from_feast()
    logger.info("Loaded %d training samples", len(df))

    with mlflow.start_run(run_name="profile_model_training") as run:
        mlflow.log_param("dataset_size", len(df))
        mlflow.log_param("life_stage_features", LIFE_STAGE_FEATURES)
        mlflow.log_param("risk_features", RISK_FEATURES)

        # --- Life stage classifier ---
        logger.info("Training life stage classifier...")
        clf, le, cls_metrics = train_life_stage_classifier(df)

        mlflow.log_metrics(
            {
                "cls_accuracy": cls_metrics["accuracy"],
                "cls_f1_macro": cls_metrics["f1_macro"],
                "cls_f1_weighted": cls_metrics["f1_weighted"],
                "cls_cv_f1_mean": cls_metrics["cv_f1_macro_mean"],
                "cls_cv_f1_std": cls_metrics["cv_f1_macro_std"],
            }
        )
        mlflow.sklearn.log_model(
            clf,
            artifact_path="life_stage_classifier",
            registered_model_name="life_stage_classifier",
        )

        # Log label encoder classes for inference
        mlflow.log_dict(
            {"classes": le.classes_.tolist()},
            artifact_file="life_stage_classes.json",
        )

        # --- Risk tolerance regressor ---
        logger.info("Training risk tolerance regressor...")
        reg, reg_metrics = train_risk_tolerance_regressor(df)

        mlflow.log_metrics(
            {
                "reg_mae": reg_metrics["mae"],
                "reg_rmse": reg_metrics["rmse"],
                "reg_r2": reg_metrics["r2"],
            }
        )
        mlflow.sklearn.log_model(
            reg,
            artifact_path="risk_tolerance_regressor",
            registered_model_name="risk_tolerance_regressor",
        )

        # Log feature importances
        cls_importances = dict(zip(LIFE_STAGE_FEATURES, clf.feature_importances_.tolist()))
        reg_importances = dict(zip(RISK_FEATURES, reg.feature_importances_.tolist()))
        mlflow.log_dict(
            {
                "life_stage_importances": cls_importances,
                "risk_tolerance_importances": reg_importances,
            },
            artifact_file="feature_importances.json",
        )

        logger.info("Training complete. MLflow run ID: %s", run.info.run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train customer profile models")
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
