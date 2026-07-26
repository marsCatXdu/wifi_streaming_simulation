"""Tie-aware prediction metrics and run-group bootstrap utilities."""

from __future__ import annotations

import math
from typing import Callable

import numpy as np
from scipy.stats import hypergeom
from sklearn.metrics import brier_score_loss, roc_auc_score


def _finite(y: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(score)
    if not np.all(mask):
        raise ValueError("scores must be finite")
    return np.asarray(y, dtype=np.int8), np.asarray(score, dtype=np.float64)


def topk_metrics(
    y: np.ndarray, score: np.ndarray, budget: float, confidence: float = 0.95
) -> dict[str, float | int | None]:
    """Compute analytical expected metrics under uniform cutoff-tie selection."""
    y, score = _finite(y, score)
    n = len(y)
    if n == 0:
        return {"status": "insufficient_data", "eligible_frames": 0}
    if not 0 < budget <= 1:
        raise ValueError("budget must be in (0, 1]")
    k = int(math.ceil(budget * n))
    cutoff = float(np.partition(score, n - k)[n - k])
    strict = score > cutoff
    tied = score == cutoff
    strict_count = int(strict.sum())
    tie_count = int(tied.sum())
    slots = k - strict_count
    y_a = int(y[strict].sum())
    y_t = int(y[tied].sum())
    expected_tp = y_a + slots * y_t / tie_count
    misses = int(y.sum())
    alpha = 1 - confidence
    lower_x = int(hypergeom.ppf(alpha / 2, tie_count, y_t, slots))
    upper_x = int(hypergeom.ppf(1 - alpha / 2, tie_count, y_t, slots))
    return {
        "status": "ok",
        "eligible_frames": n,
        "eligible_misses": misses,
        "budget": budget,
        "k": k,
        "topk_cutoff_score": cutoff,
        "topk_strict_count": strict_count,
        "topk_tie_count": tie_count,
        "topk_tie_slots": slots,
        "expected_topk_true_positives": expected_tp,
        "recall": expected_tp / misses if misses else None,
        "precision": expected_tp / k,
        "tie_recall_lower": (y_a + lower_x) / misses if misses else None,
        "tie_recall_upper": (y_a + upper_x) / misses if misses else None,
        "tie_precision_lower": (y_a + lower_x) / k,
        "tie_precision_upper": (y_a + upper_x) / k,
    }


def average_precision_tied(y: np.ndarray, score: np.ndarray) -> float | None:
    """Step-wise AP after grouping exact score ties."""
    y, score = _finite(y, score)
    positives = int(y.sum())
    if len(y) == 0 or positives == 0:
        return None
    order = np.argsort(-score, kind="stable")
    sorted_score, sorted_y = score[order], y[order]
    ends = np.r_[np.flatnonzero(sorted_score[1:] != sorted_score[:-1]) + 1, len(y)]
    cumulative_tp = np.cumsum(sorted_y)[ends - 1]
    precision = cumulative_tp / ends
    increments = np.diff(np.r_[0, cumulative_tp / positives])
    return float(np.sum(increments * precision))


def equal_frequency_calibration(
    y: np.ndarray, probability: np.ndarray, requested_bins: int
) -> tuple[float | None, list[dict[str, float | int]], int]:
    """Compute ECE with indivisible exact-probability tie groups."""
    y, probability = _finite(y, probability)
    if np.any((probability < 0) | (probability > 1)):
        raise ValueError("probabilities must be in [0, 1]")
    if len(y) == 0:
        return None, [], 0
    order = np.argsort(probability, kind="stable")
    p, labels = probability[order], y[order]
    starts = np.r_[0, np.flatnonzero(p[1:] != p[:-1]) + 1]
    ends = np.r_[starts[1:], len(p)]
    effective = min(requested_bins, len(y), len(starts))
    boundaries: list[int] = []
    previous_group = 0
    for boundary in range(1, effective):
        candidates = np.arange(previous_group, len(starts) - (effective - boundary))
        cumulative = ends[candidates]
        target = boundary * len(y) / effective
        distance = np.abs(cumulative - target)
        chosen = candidates[np.flatnonzero(distance == distance.min())[0]]
        boundaries.append(int(ends[chosen]))
        previous_group = int(chosen + 1)
    row_bounds = [0] + boundaries + [len(y)]
    bins = []
    error = 0.0
    for lo, hi in zip(row_bounds[:-1], row_bounds[1:]):
        mean_p, mean_y = float(p[lo:hi].mean()), float(labels[lo:hi].mean())
        error += (hi - lo) / len(y) * abs(mean_p - mean_y)
        bins.append(
            {
                "count": hi - lo,
                "probability_min": float(p[lo]),
                "probability_max": float(p[hi - 1]),
                "mean_probability": mean_p,
                "observed_miss_rate": mean_y,
            }
        )
    return error, bins, effective


def probability_metrics(
    y: np.ndarray, probability: np.ndarray, bins: int
) -> dict[str, float | int | None]:
    """Return AP, ROC, Brier and tie-preserving equal-frequency ECE."""
    y, probability = _finite(y, probability)
    ece, _, effective = equal_frequency_calibration(y, probability, bins)
    one_class = len(np.unique(y)) < 2
    return {
        "average_precision": average_precision_tied(y, probability),
        "roc_auc": None if one_class else float(roc_auc_score(y, probability)),
        "brier_score": None if len(y) == 0 else float(brier_score_loss(y, probability)),
        "calibration_error": ece,
        "requested_calibration_bin_count": bins,
        "effective_calibration_bin_count": effective,
        "prevalence": None if len(y) == 0 else float(y.mean()),
    }


def threshold_metrics(
    y: np.ndarray, score: np.ndarray, threshold: float | None
) -> dict[str, float | int | None]:
    """Apply one fixed threshold; ``None`` is the explicit no-action rule."""
    y, score = _finite(y, score)
    action = np.zeros(len(y), dtype=bool) if threshold is None else score >= threshold
    tp = int(np.sum(action & (y == 1)))
    fp = int(np.sum(action & (y == 0)))
    positives = int(y.sum())
    negatives = len(y) - positives
    return {
        "action_count": int(action.sum()),
        "action_rate": float(action.mean()) if len(y) else None,
        "recall": tp / positives if positives else None,
        "precision": tp / int(action.sum()) if action.any() else None,
        "false_positive_rate": fp / negatives if negatives else None,
        "false_negative_rate": (positives - tp) / positives if positives else None,
    }


def grouped_bootstrap(
    groups: np.ndarray,
    replicates: int,
    seed: int,
    statistic: Callable[[np.ndarray], float | None],
) -> np.ndarray:
    """Vectorized group-count bootstrap with deterministic replicate sampling.

    ``statistic`` receives row multiplicities. This avoids copying frame rows.
    """
    unique, inverse = np.unique(groups, return_inverse=True)
    rng = np.random.default_rng(seed)
    output = np.full(replicates, np.nan)
    for start in range(0, replicates, 128):
        count = min(128, replicates - start)
        sampled = rng.integers(0, len(unique), size=(count, len(unique)))
        group_counts = np.apply_along_axis(
            lambda row: np.bincount(row, minlength=len(unique)), 1, sampled
        )
        for offset, weights in enumerate(group_counts):
            output[start + offset] = statistic(weights[inverse])
    return output


def percentile_interval(values: np.ndarray, confidence: float) -> tuple[float | None, float | None]:
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        return None, None
    alpha = 1 - confidence
    low, high = np.quantile(finite, [alpha / 2, 1 - alpha / 2], method="linear")
    return float(low), float(high)


def weighted_topk_recall(
    y: np.ndarray, score: np.ndarray, budget: float, weights: np.ndarray
) -> float | None:
    """Tie-expected Top-K recall for integral bootstrap row multiplicities."""
    total = int(weights.sum())
    misses = float(np.dot(weights, y))
    if total == 0 or misses == 0:
        return None
    k = int(math.ceil(budget * total))
    order = np.argsort(-score, kind="stable")
    s, yy, ww = score[order], y[order], weights[order]
    cumulative = np.cumsum(ww)
    position = int(np.searchsorted(cumulative, k, side="left"))
    cutoff = s[position]
    strict = s > cutoff
    tied = s == cutoff
    strict_count = int(ww[strict].sum())
    slots = k - strict_count
    tie_count = int(ww[tied].sum())
    tp = float(np.dot(ww[strict], yy[strict]))
    tie_tp = float(np.dot(ww[tied], yy[tied]))
    return (tp + slots * tie_tp / tie_count) / misses


def grouped_topk_bootstrap(
    y: np.ndarray,
    score: np.ndarray,
    groups: np.ndarray,
    budget: float,
    replicates: int,
    seed: int,
) -> np.ndarray:
    """Efficient grouped bootstrap for tie-expected Top-K recall."""
    y, score = _finite(y, score)
    unique, inverse = np.unique(groups, return_inverse=True)
    order = np.argsort(-score, kind="stable")
    sorted_y = y[order]
    sorted_score = score[order]
    sorted_group = inverse[order]
    rng = np.random.default_rng(seed)
    output = np.full(replicates, np.nan)
    for start in range(0, replicates, 64):
        count = min(64, replicates - start)
        sampled = rng.integers(0, len(unique), size=(count, len(unique)))
        group_counts = np.zeros((count, len(unique)), dtype=np.int16)
        for row in range(count):
            group_counts[row] = np.bincount(sampled[row], minlength=len(unique))
        weights = group_counts[:, sorted_group]
        totals = weights.sum(axis=1)
        misses = weights @ sorted_y
        cumulative = np.cumsum(weights, axis=1)
        ks = np.ceil(budget * totals).astype(int)
        positions = np.argmax(cumulative >= ks[:, None], axis=1)
        for row, position in enumerate(positions):
            if totals[row] == 0 or misses[row] == 0:
                continue
            cutoff = sorted_score[position]
            left = int(np.searchsorted(-sorted_score, -cutoff, side="left"))
            right = int(np.searchsorted(-sorted_score, -cutoff, side="right"))
            strict_weights = weights[row, :left]
            tie_weights = weights[row, left:right]
            strict_tp = float(strict_weights @ sorted_y[:left])
            strict_count = int(strict_weights.sum())
            tie_count = int(tie_weights.sum())
            tie_tp = float(tie_weights @ sorted_y[left:right])
            slots = ks[row] - strict_count
            output[start + row] = (
                strict_tp + slots * tie_tp / tie_count
            ) / misses[row]
    return output
