"""Mandatory causal heuristic baselines."""

from __future__ import annotations

import numpy as np

NAMES = (
    "random_expected",
    "retry_pressure",
    "delivery_drought",
    "byte_service_queue_slack",
    "packet_count_additive_baseline",
)


def fit_byte_service_fallback(
    acknowledged_bytes: np.ndarray, coverage_us: np.ndarray
) -> float:
    valid = (coverage_us > 0) & (acknowledged_bytes > 0)
    if not valid.any():
        raise ValueError("training data has no positive byte-service observation")
    return float(np.median(acknowledged_bytes[valid] / coverage_us[valid]))


def score_heuristics(
    columns: dict[str, np.ndarray], byte_service_fallback: float, epsilon: float = 1e-9
) -> dict[str, np.ndarray]:
    """Score all non-random mandatory heuristics."""
    n = len(columns["deadline_slack_us"])
    attempts = columns["mpdu_attempts_5ms"]
    retries = columns["mpdu_retries_5ms"]
    retry = np.nan_to_num(retries, nan=0.0) / np.maximum(np.nan_to_num(attempts), 1.0)

    drought = np.nan_to_num(
        columns["last_positive_ack_age_us"], nan=np.finfo(np.float32).max / 4
    ) / np.maximum(columns["deadline_slack_us"], epsilon)

    acknowledged = columns["acknowledged_mac_service_bytes_20ms"]
    coverage = columns["history_coverage_20ms_us"]
    rate = np.full(n, byte_service_fallback, dtype=np.float64)
    valid = (coverage > 0) & (acknowledged > 0)
    rate[valid] = acknowledged[valid] / coverage[valid]
    remaining = np.nan_to_num(columns["mac_service_bytes_ahead_of_frame"]) + np.nan_to_num(
        columns["frame_mac_service_bytes_pending_primary"]
    )
    byte_score = remaining / np.maximum(rate, epsilon)
    byte_score /= np.maximum(columns["deadline_slack_us"], epsilon)
    byte_score += (np.nan_to_num(columns["frame_packets_terminally_dropped"]) > 0).astype(
        float
    )

    packet_work = np.nan_to_num(columns["packets_ahead_of_frame"]) + np.nan_to_num(
        columns["frame_packets_pending_primary"]
    )
    packet_service = np.nan_to_num(
        columns["mpdu_queue_to_ack_mean_20ms_us"],
        nan=float(np.nanmedian(columns["mpdu_queue_to_ack_mean_20ms_us"]))
        if np.isfinite(columns["mpdu_queue_to_ack_mean_20ms_us"]).any()
        else 0.0,
    )
    packet_score = packet_work * packet_service / np.maximum(
        columns["deadline_slack_us"], epsilon
    )
    return {
        "retry_pressure": retry,
        "delivery_drought": drought,
        "byte_service_queue_slack": byte_score,
        "packet_count_additive_baseline": packet_score,
    }
