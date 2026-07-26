"""Feature contracts and causal F1 observation degradation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

PROHIBITED = {
    "dataset_schema_version",
    "telemetry_schema_version",
    "run_id",
    "run_group_id",
    "run_seed",
    "run_number",
    "frame_id",
    "path_id",
    "copy_id",
    "sample_stage",
    "sample_offset_us",
    "sample_time_ns",
    "generation_time_ns",
    "deadline_time_ns",
    "latest_feature_event_time_ns",
    "latest_feature_event_sequence",
    "scenario_name",
    "background_profile",
    "correlation_mode",
    "selected_policy",
    "miss_regime",
    "split_role",
    "frame_complete",
    "frame_completion_time_ns",
    "frame_latency_us",
    "deadline_miss",
    "sender_mac_complete",
    "actionable",
    "feature_support_mask",
}

CATEGORICAL_VOCABULARIES = {
    "frame_type": ("I_FRAME", "P_FRAME", "B_FRAME"),
    "frequency_band": ("2.4GHz", "5GHz", "6GHz"),
    "current_phy_state": ("IDLE", "CCA_BUSY", "TX", "RX", "SWITCHING", "SLEEP", "OFF"),
    "channel_access_status": ("NOT_REQUESTED", "REQUESTED", "GRANTED"),
    "expected_access_reason_within_slack": (
        "ACCESS_EXPECTED",
        "NOT_REQUESTED",
        "NOTHING_TO_TX",
        "RX_END",
        "BUSY_END",
        "TX_END",
        "NAV_END",
        "ACK_TIMER_END",
        "CTS_TIMER_END",
        "SWITCHING_END",
        "NO_PHY_END",
        "SLEEP_END",
        "OFF_END",
        "BACKOFF_END",
    ),
}

FAMILIES = {
    "queue": ("queue", "ahead_of_frame"),
    "retry": ("retry", "attempt_failure", "terminal_drop"),
    "phy_occupancy": ("phy_", "history_coverage"),
    "frame_progress": ("frame_packets_", "frame_mac_service_"),
    "oracle": ("current_cw", "backoff", "nav_", "channel_access", "medium_busy"),
}


@dataclass(frozen=True)
class FeatureSets:
    """Frozen explicit allowlists derived from the normative manifest dictionary."""

    by_tier: dict[str, tuple[str, ...]]
    sets: dict[str, tuple[str, ...]]
    categorical: tuple[str, ...]


def build_feature_sets(
    feature_dictionary: dict[str, dict[str, Any]], f2_exportable: list[str]
) -> FeatureSets:
    """Build nested allowlists and reject metadata/outcome leakage."""
    by_tier: dict[str, tuple[str, ...]] = {}
    for tier in ("F0", "F1-ideal", "F2", "F3"):
        by_tier[tier] = tuple(
            sorted(
                name
                for name, entry in feature_dictionary.items()
                if entry.get("tier") == tier and entry.get("model_eligible") is True
            )
        )
        if not by_tier[tier]:
            raise ValueError(f"manifest has no model-eligible {tier} features")
    all_allowed = set().union(*map(set, by_tier.values()))
    leaked = all_allowed & PROHIBITED
    if leaked:
        raise ValueError(f"prohibited predictors in manifest allowlists: {sorted(leaked)}")
    # The frozen export declaration may name a driver-exported absolute source
    # timestamp. Models consume its causal, same-row derived age instead.
    resolved_exportable = [
        "queue_oldest_age_us"
        if name == "mac_queue_oldest_enqueue_time_ns"
        and "queue_oldest_age_us" in by_tier["F2"]
        else name
        for name in f2_exportable
    ]
    exportable = tuple(resolved_exportable)
    if len(exportable) != len(set(exportable)):
        raise ValueError("f2_exportable_allowlist contains duplicates")
    unknown = set(exportable) - set(by_tier["F2"])
    if unknown:
        raise ValueError(f"F2-exportable fields are not eligible F2 fields: {sorted(unknown)}")

    f0, f1, f2, f3 = (by_tier[key] for key in ("F0", "F1-ideal", "F2", "F3"))
    sets = {
        "F0": f0,
        "F0+F1-ideal": f0 + f1,
        "F0+F1-ideal+F2": f0 + f1 + f2,
        "F0+F1-ideal+F2-exportable": f0 + f1 + exportable,
        "F0+F1-ideal+F2+F3": f0 + f1 + f2 + f3,
    }
    categorical = tuple(sorted(all_allowed & CATEGORICAL_VOCABULARIES.keys()))
    return FeatureSets(by_tier, sets, categorical)


def ablation_sets(features: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Return targeted family ablations without changing the frozen primary set."""
    result: dict[str, tuple[str, ...]] = {}
    for family, tokens in FAMILIES.items():
        kept = tuple(name for name in features if not any(token in name for token in tokens))
        if kept != features:
            result[f"without_{family}"] = kept
    return result


def encode_value(name: str, value: str) -> float:
    """Encode one CSV field without data-dependent category fitting."""
    if value == "":
        return np.nan
    vocabulary = CATEGORICAL_VOCABULARIES.get(name)
    if vocabulary is not None:
        try:
            return float(vocabulary.index(value))
        except ValueError:
            return -1.0
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return float(lowered == "true")
    return float(value)


def _disabled(name: str, families: list[str]) -> bool:
    return any(
        family in FAMILIES and any(token in name for token in FAMILIES[family])
        for family in families
    )


def degrade_f1(
    matrix: np.ndarray,
    feature_names: tuple[str, ...],
    sample_times_us: np.ndarray,
    run_codes: np.ndarray,
    profile: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Causally hold delayed reports; return values, source times and staleness.

    The input must contain one stage and be ordered arbitrarily. A value at time
    ``t`` uses the latest same-run snapshot no later than the synthetic report
    cutoff. No future row can be selected.
    """
    interval = int(profile["report_interval_us"])
    delay = int(profile["observation_delay_us"])
    if interval <= 0 or delay < 0:
        raise ValueError("invalid F1 degradation timing")
    result = np.full_like(matrix, np.nan, dtype=np.float32)
    sources = np.full(len(matrix), np.nan, dtype=np.float64)
    disabled = profile.get("disabled_feature_families", [])
    for run in np.unique(run_codes):
        positions = np.flatnonzero(run_codes == run)
        order = positions[np.argsort(sample_times_us[positions], kind="stable")]
        times = sample_times_us[order]
        report_times = np.floor_divide(times - delay, interval) * interval
        source_pos = np.searchsorted(times, report_times, side="right") - 1
        valid = source_pos >= 0
        result[order[valid]] = matrix[order[source_pos[valid]]]
        sources[order[valid]] = times[source_pos[valid]]
    for column, name in enumerate(feature_names):
        if _disabled(name, disabled):
            result[:, column] = np.nan
            continue
        finite = np.isfinite(result[:, column])
        quantum = None
        if "signal" in name:
            quantum = float(profile["signal_quantization_db"])
        elif any(token in name for token in ("mcs", "nss", "width", "guard_interval")):
            quantum = float(profile["rate_quantization"])
        elif any(token in name for token in ("count", "attempt", "ack", "retr", "drop", "bytes")):
            quantum = float(profile["counter_quantization"])
        if quantum and quantum > 0:
            result[finite, column] = (
                np.rint(result[finite, column] / quantum) * quantum
            )
    staleness = sample_times_us.astype(np.float64) - sources
    if np.any(staleness[np.isfinite(staleness)] < delay):
        raise AssertionError("degraded F1 selected a future or insufficiently delayed snapshot")
    return result, sources, staleness
