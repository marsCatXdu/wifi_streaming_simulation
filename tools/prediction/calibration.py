"""Validation-calibration-only Platt scaling and threshold selection."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression

from .metrics import threshold_metrics


@dataclass
class PlattCalibrator:
    model: LogisticRegression

    def predict(self, score: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(np.asarray(score).reshape(-1, 1))[:, 1]


def fit_platt(score: np.ndarray, y: np.ndarray, seed: int) -> PlattCalibrator:
    if len(np.unique(y)) < 2:
        raise ValueError("Platt calibration requires both classes")
    model = LogisticRegression(C=1e6, solver="lbfgs", random_state=seed, max_iter=500)
    model.fit(np.asarray(score).reshape(-1, 1), y)
    return PlattCalibrator(model)


def select_threshold(
    calibrated_probability: np.ndarray, y: np.ndarray, budget: float
) -> dict[str, object]:
    """Select the closest calibration-set action rate under frozen tie rules."""
    if not np.all(np.isfinite(calibrated_probability)):
        raise ValueError("nonfinite calibration scores")
    candidates: list[tuple[float, bool, float | None]] = [(budget, False, None)]
    for threshold in np.unique(calibrated_probability):
        rate = float(np.mean(calibrated_probability >= threshold))
        candidates.append((abs(rate - budget), rate > budget, float(threshold)))
    deviation, overshoot, threshold = min(
        candidates,
        key=lambda item: (
            item[0],
            item[1],
            -(item[2] if item[2] is not None else np.inf),
        ),
    )
    observed = threshold_metrics(y, calibrated_probability, threshold)
    return {
        "calibration_threshold_mode": "no_action" if threshold is None else "numeric",
        "calibration_score_threshold": threshold,
        "target_budget": budget,
        "absolute_action_rate_deviation": deviation,
        "observed_calibration_action_rate": observed["action_rate"],
        "calibration_miss_recall_at_threshold": observed["recall"],
        "calibration_precision_at_threshold": observed["precision"],
    }
