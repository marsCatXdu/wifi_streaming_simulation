"""Training-only preprocessing and deterministic model candidates."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


@dataclass(frozen=True)
class Candidate:
    name: str
    parameters: dict[str, object]


CANDIDATES = (
    Candidate("logistic_regression", {"C": 1.0}),
    Candidate(
        "histogram_gradient_boosting",
        {"learning_rate": 0.08, "max_iter": 120, "max_leaf_nodes": 15, "l2_regularization": 1.0},
    ),
)


def balanced_weights(y: np.ndarray) -> np.ndarray:
    counts = np.bincount(y.astype(int), minlength=2)
    if np.any(counts == 0):
        raise ValueError("model fitting requires both classes")
    return len(y) / (2 * counts[y.astype(int)])


def make_pipeline(
    candidate: Candidate, categorical_indices: tuple[int, ...], seed: int
) -> Pipeline:
    """Create a fit-data-only sklearn pipeline."""
    categorical = list(categorical_indices)
    numeric = [index for index in range(max(categorical, default=-1) + 1)]
    # The caller replaces this sentinel list with all columns via set_params.
    if candidate.name == "logistic_regression":
        estimator = LogisticRegression(
            C=float(candidate.parameters["C"]),
            max_iter=1000,
            solver="liblinear",
            random_state=seed,
        )
    elif candidate.name == "histogram_gradient_boosting":
        estimator = HistGradientBoostingClassifier(
            **candidate.parameters,
            random_state=seed,
        )
    else:
        raise ValueError(f"unknown model candidate: {candidate.name}")
    return Pipeline([("preprocess", "passthrough"), ("model", estimator)])


def fit_pipeline(
    candidate: Candidate,
    x: np.ndarray,
    y: np.ndarray,
    categorical_indices: tuple[int, ...],
    seed: int,
) -> Pipeline:
    numeric = [i for i in range(x.shape[1]) if i not in categorical_indices]
    transformers = []
    if numeric:
        numeric_steps: list[tuple[str, object]] = [
            ("impute", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True))
        ]
        if candidate.name == "logistic_regression":
            numeric_steps.append(("scale", StandardScaler()))
        transformers.append(("numeric", Pipeline(numeric_steps), numeric))
    if categorical_indices:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        (
                            "impute",
                            SimpleImputer(
                                strategy="most_frequent",
                                add_indicator=True,
                                keep_empty_features=True,
                            ),
                        ),
                        (
                            "encode",
                            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                        ),
                    ]
                ),
                list(categorical_indices),
            )
        )
    pipeline = make_pipeline(candidate, categorical_indices, seed)
    pipeline.set_params(
        preprocess=ColumnTransformer(transformers, remainder="drop", sparse_threshold=0)
    )
    pipeline.fit(x, y, model__sample_weight=balanced_weights(y))
    return pipeline


def ranking_score(pipeline: Pipeline, x: np.ndarray) -> np.ndarray:
    if hasattr(pipeline, "decision_function"):
        return np.asarray(pipeline.decision_function(x), dtype=np.float64)
    return np.asarray(pipeline.predict_proba(x)[:, 1], dtype=np.float64)
