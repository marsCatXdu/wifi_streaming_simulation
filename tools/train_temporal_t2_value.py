#!/usr/bin/env python3
"""Train an honest, primary-only temporal T2 value policy.

The input is the exact action-clean temporal artifact emitted by
``build_randomized_temporal_dataset.py``.  Models are fit only on fixed
training runs.  A bounded policy set is selected using calibration runs, and
the selected float32 score threshold is applied once to the existing
engineering test split.  Fresh closed-loop seeds, rather than this previously
opened split, remain the confirmation evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import pickle
import random
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import build_randomized_intervention_dataset as base_builder
import build_randomized_temporal_dataset as temporal_builder
import train_randomized_value as audited


TRAINING_SCHEMA_VERSION = 1
MODEL_BUNDLE_SCHEMA_VERSION = 1
TARGET_DEADLINE = "deadline_miss"
TARGET_LATE18 = "completed_late_18000us"
TARGET_COMPLETION = "completion"
TARGET_BAD12 = "bad_tail_12000us"
EVALUATION_TARGETS = (
    TARGET_DEADLINE,
    TARGET_LATE18,
    TARGET_COMPLETION,
    TARGET_BAD12,
)
VALUE_TARGETS = (TARGET_DEADLINE, TARGET_LATE18, TARGET_BAD12)
ROLES = ("train", "calibration", "test")
FRAME_GATES = ("all_frames", "p_frames_only")
REQUESTED_ACTION_FRACTIONS = (0.05, 0.075, 0.10, 0.125, 0.15, 0.165, 0.18, 0.20)
MAX_ACTION_FRACTION = 0.20
MAX_DR_AIRTIME_US_PER_ELIGIBLE_FRAME = 400.0
MIN_RELATIVE_IMPROVEMENT = 0.50
RISK_NORMALIZER_FLOOR = 1e-6
PREDICTED_COST_CAP_US = 1_000_000.0
RANDOM_SEED = 20260804
BOOTSTRAP_REPLICATIONS = 2000
BOOTSTRAP_CONFIDENCE = 0.95

FEATURE_ADAPTER_ID = "finite_numeric_float32_then_float64_one_hot_v1"
SCORE_ADAPTER_ID = "final_candidate_float32_threshold_ge_v1"
MODEL_SPEC_ID = "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1"
SELECTION_ID = "calibration_two_objective_50pct_maximin_v1"

OUTPUT_MODEL = "temporal_t2_value_models.pkl"
OUTPUT_CANDIDATES = "temporal_t2_value_policy_candidates.csv"
OUTPUT_METRICS = "temporal_t2_value_training_metrics.json"
OUTPUT_MANIFEST = "artifact_manifest.json"
OUTPUT_FILES = (OUTPUT_MODEL, OUTPUT_CANDIDATES, OUTPUT_METRICS, OUTPUT_MANIFEST)

EXPECTED_COMPARISON = {
    "file": temporal_builder.OUTPUT_CSV,
    "analysis_stage": "T2",
    "arms": ["CONTROL", "FULL_COPY_T2"],
    "estimand": "binary_assignment_ITT_on_action_clean_temporal_population",
    "population": (
        "v1 common-T2-eligible rows with exact lags 1/3/8 and "
        "action-clean secondary delayed-PHY endpoints"
    ),
}

COMPACT_PRIMARY_PHYSICS_NAMES = (
    "x_compact_primary_working_rate_margin_1ms_bytes_per_us",
    "x_compact_primary_working_rate_margin_5ms_bytes_per_us",
    "x_compact_primary_working_rate_margin_20ms_bytes_per_us",
    "x_compact_primary_ahead_clearance_us_at_5ms_rate",
    "x_compact_primary_ahead_clearance_over_slack_at_5ms_rate",
    "x_compact_primary_ack_rate_trend_1ms_minus_20ms",
    "x_compact_primary_busy_trend_1ms_minus_20ms",
)


def _lag_primary_columns(lag: int) -> tuple[str, ...]:
    prefix = f"x_primary_lag{lag}_"
    delta_prefix = f"x_primary_since_lag{lag}_"
    return tuple(
        name
        for name in temporal_builder._lag_columns(lag)
        if name.startswith(prefix) or name.startswith(delta_prefix)
    )


PRIMARY_TEMPORAL_COLUMNS = tuple(temporal_builder.CURRENT_RADIO_COLUMNS) + tuple(
    name
    for lag in temporal_builder.LAGS
    for name in _lag_primary_columns(lag)
)

FEATURE_FAMILY_ORDER = (
    "primary_base",
    "primary_compact_physics",
    "primary_compact_physics_temporal",
)
RANKER_ORDER = (
    "deadline_value_per_cost",
    "completed_late18_value_per_cost",
    "balanced_normalized_value_per_cost",
    "legacy_bad12_value_per_cost",
)


class TemporalTrainingError(ValueError):
    """Raised when training provenance or causal semantics are ambiguous."""


@dataclass(frozen=True)
class TemporalDataset:
    """Strict temporal data plus the three predeclared feature matrices."""

    path: Path
    metadata: dict[str, Any]
    manifest: dict[str, Any]
    data: audited.StageDataset
    family_matrices: dict[str, np.ndarray]
    family_feature_names: dict[str, tuple[str, ...]]

    def stage_for_family(self, family: str) -> audited.StageDataset:
        """Return an audited-helper-compatible view for one feature family."""

        return audited.StageDataset(
            stage="T2",
            feature_columns=self.data.feature_columns,
            encoded_feature_names=self.family_feature_names[family],
            rows=self.data.rows,
            matrix=self.family_matrices[family],
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise TemporalTrainingError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TemporalTrainingError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise TemporalTrainingError(f"{path}: expected a JSON object")
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _flag(row: dict[str, str], field: str, source: str) -> int:
    value = row.get(field)
    if value not in {"0", "1"}:
        raise TemporalTrainingError(f"{source}: invalid flag {field}")
    return int(value)


def _integer(row: dict[str, str], field: str, source: str) -> int:
    try:
        return int(row[field])
    except (KeyError, TypeError, ValueError) as error:
        raise TemporalTrainingError(f"{source}: invalid integer {field}") from error


def _number(row: dict[str, str], field: str, source: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as error:
        raise TemporalTrainingError(f"{source}: invalid number {field}") from error
    if not math.isfinite(value):
        raise TemporalTrainingError(f"{source}: non-finite number {field}")
    return value


def _optional_number(row: dict[str, str], field: str, source: str) -> float | None:
    if row.get(field) == "":
        return None
    return _number(row, field, source)


def _float32_numeric(value: str, feature: str, source: str) -> float:
    if value == "":
        return math.nan
    try:
        parsed = float(value)
    except ValueError as error:
        raise TemporalTrainingError(f"{source}: non-numeric feature {feature}") from error
    if not math.isfinite(parsed):
        raise TemporalTrainingError(f"{source}: non-finite feature {feature}")
    with np.errstate(over="ignore", invalid="ignore"):
        quantized = np.float32(parsed)
    if not np.isfinite(quantized):
        raise TemporalTrainingError(f"{source}: float32 overflow in {feature}")
    return float(quantized)


def _expected_feature_contract() -> dict[str, Any]:
    return {
        "feature_columns": list(temporal_builder.FEATURE_COLUMNS),
        "base_feature_columns": list(base_builder.FEATURE_COLUMNS),
        "new_feature_columns": list(
            temporal_builder.FEATURE_COLUMNS[len(base_builder.FEATURE_COLUMNS) :]
        ),
        "categorical_features": ["x_f0_frame_type"],
        "exact_frame_lags": list(temporal_builder.LAGS),
        "endpoint": (
            "T2 polling capture; capture + 1 ms = available <= sample, "
            "with sample-capture staleness in [1,2) ms"
        ),
        "history_coverage_us": [
            coverage for _, coverage in temporal_builder.WINDOWS_US
        ],
        "last_event_age_cap_us": temporal_builder.LAST_EVENT_AGE_CAP_US,
        "clearance_time_cap_us": temporal_builder.CLEARANCE_TIME_CAP_US,
        "minimum_working_rate_bytes_per_us": (
            temporal_builder.MIN_WORKING_RATE_BYTES_PER_US
        ),
        "secondary_action_cleanliness": {
            "direct_self_tx": (
                "no tagged secondary PPDU interval overlaps the 20 ms ending "
                "at any current/lag polling capture"
            ),
            "active_or_queued_tagged_reservation": (
                "no launched secondary reservation is active in the half-open "
                "launch-to-settlement interval at any polling capture"
            ),
            "cleanliness_flags_are_features": False,
        },
        "physics_formulas": {
            "working_rate_margin": (
                "acknowledged bytes/window us - pending bytes/max(slack us,1)"
            ),
            "clearance_margin": (
                "slack us - min(bytes/max(working rate,1e-6),1e6 us)"
            ),
            "joint_busy": "primary busy fraction * secondary busy fraction",
            "trend": "5 ms value - 20 ms value",
            "explicit_frame_type_interactions": True,
        },
        "missing_values": (
            "empty CSV is retained only for genuinely unavailable values; "
            "radio/last-event fields have explicit missing indicators"
        ),
        "conservative_exclusions": [
            "identifiers and absolute timestamps from X",
            "polling watermarks and raw lifetime totals from X",
            "assignment, execution, compliance, outcomes, and settlements from X",
            "receiver outcomes from all prior frames",
            "secondary tagged totals, MCS, retry, and ACK state",
            "current or lagged ACK signal",
            "cleanliness/filter bits",
        ],
    }


def _validate_split(metadata: dict[str, Any]) -> dict[tuple[int, int], str]:
    split = metadata.get("split")
    if not isinstance(split, dict) or set(split) != {
        "algorithm",
        "unit",
        "target_ratio",
        "counts",
        "assignments",
    }:
        raise TemporalTrainingError("temporal grouped split schema differs")
    if (
        split.get("algorithm") != audited.SPLIT_ALGORITHM
        or split.get("unit") != "(seed, run_number)"
        or split.get("target_ratio")
        != {"train": 64, "calibration": 16, "test": 16}
    ):
        raise TemporalTrainingError("temporal grouped split contract differs")
    assignments = split.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise TemporalTrainingError("temporal split assignments are absent")
    observed: dict[tuple[int, int], str] = {}
    for item in assignments:
        if not isinstance(item, dict) or set(item) != {
            "seed",
            "run_number",
            "split_role",
        }:
            raise TemporalTrainingError("temporal split assignment schema differs")
        seed = item.get("seed")
        run_number = item.get("run_number")
        role = item.get("split_role")
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not isinstance(run_number, int)
            or isinstance(run_number, bool)
            or role not in ROLES
            or (seed, run_number) in observed
        ):
            raise TemporalTrainingError("malformed temporal split assignment")
        observed[(seed, run_number)] = role
    if observed != audited.expected_run_splits(list(observed)):
        raise TemporalTrainingError("temporal run split is not the exact fixed split")
    counts = {role: sum(value == role for value in observed.values()) for role in ROLES}
    if split.get("counts") != counts:
        raise TemporalTrainingError("temporal split counts differ from assignments")
    return observed


def _validate_metadata(
    metadata: dict[str, Any], manifest: dict[str, Any], dataset_dir: Path
) -> tuple[dict[tuple[int, int], str], dict[str, str]]:
    if set(metadata) != {
        "dataset_schema_version",
        "feature_contract_id",
        "comparison",
        "validation",
        "input_v1",
        "feature_contract",
        "non_feature_columns",
        "split",
        "filter_counts",
        "build_identity",
        "environment_compatibility",
        "design_contract_sha256",
        "source_runs",
    }:
        raise TemporalTrainingError("temporal metadata top-level schema differs")
    if (
        metadata.get("dataset_schema_version")
        != temporal_builder.DATASET_SCHEMA_VERSION
        or metadata.get("feature_contract_id")
        != temporal_builder.FEATURE_CONTRACT_ID
        or metadata.get("feature_contract") != _expected_feature_contract()
        or metadata.get("non_feature_columns")
        != list(temporal_builder.NON_FEATURE_COLUMNS)
    ):
        raise TemporalTrainingError("temporal feature/schema contract differs")
    comparison = metadata.get("comparison")
    if (
        not isinstance(comparison, dict)
        or set(comparison) != set(EXPECTED_COMPARISON) | {"row_count"}
        or any(comparison.get(key) != value for key, value in EXPECTED_COMPARISON.items())
        or not isinstance(comparison.get("row_count"), int)
        or isinstance(comparison.get("row_count"), bool)
        or comparison["row_count"] <= 0
    ):
        raise TemporalTrainingError("temporal T2 comparison contract differs")
    if metadata.get("validation") != {
        "authoritative_validator": "validate_outputs.validate_run",
        "every_raw_run_validated_before_augmenter_source_reads": True,
        "v1_rows_regenerated_and_joined_exactly_by_run_id_frame_id": True,
        "v1_artifact_manifest_verified_before_row_reads": True,
        "raw_v1_source_hashes_verified": True,
    }:
        raise TemporalTrainingError("temporal validation provenance differs")

    input_v1 = metadata.get("input_v1")
    expected_v1_names = set(temporal_builder.V1_ARTIFACTS) | {
        "artifact_manifest.json"
    }
    if (
        not isinstance(input_v1, dict)
        or set(input_v1) != {"path", "feature_contract_id", "artifacts_sha256"}
        or input_v1.get("feature_contract_id") != base_builder.FEATURE_CONTRACT_ID
        or not isinstance(input_v1.get("path"), str)
        or not isinstance(input_v1.get("artifacts_sha256"), dict)
        or set(input_v1["artifacts_sha256"]) != expected_v1_names
        or not all(_is_sha256(value) for value in input_v1["artifacts_sha256"].values())
    ):
        raise TemporalTrainingError("temporal input-v1 provenance differs")

    environment = metadata.get("environment_compatibility")
    projection = (
        environment.get("invariant_projection")
        if isinstance(environment, dict)
        else None
    )
    if not isinstance(projection, dict):
        raise TemporalTrainingError("temporal environment projection is absent")
    if projection.get("randomizedIntervention") != audited.RANDOMIZED_INTERVENTION_CONTRACT:
        raise TemporalTrainingError("temporal randomized intervention contract differs")
    telemetry = projection.get("predictionTelemetry")
    if (
        not isinstance(telemetry, dict)
        or telemetry.get("history_windows_us") != [1000, 5000, 20000]
        or telemetry.get("polling_interval_us") != 1000
        or telemetry.get("polling_report_delay_us") != 1000
        or telemetry.get("sample_offsets_us") != [0, 2000, 4000]
        or telemetry.get("oracle_features_enabled") is not False
    ):
        raise TemporalTrainingError("temporal polling environment contract differs")
    stream = projection.get("stream")
    if (
        not isinstance(stream, dict)
        or stream.get("deadline_us") != 33333
        or stream.get("fps") != 30
    ):
        raise TemporalTrainingError("temporal stream timing contract differs")

    filters = metadata.get("filter_counts")
    expected_filter_keys = {
        "candidate_v1_t2_rows",
        "excluded_lag8_warmup",
        "excluded_secondary_direct_tx_dirty",
        "excluded_secondary_active_reservation",
        "excluded_any_secondary_action_dirty",
        "included_rows",
        "dirty_endpoint_counts",
    }
    if not isinstance(filters, dict) or set(filters) != expected_filter_keys:
        raise TemporalTrainingError("temporal action-clean filter schema differs")
    counts = [filters.get(name) for name in expected_filter_keys - {"dirty_endpoint_counts"}]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
        raise TemporalTrainingError("temporal action-clean filter count is invalid")
    if (
        filters["candidate_v1_t2_rows"]
        != filters["excluded_lag8_warmup"]
        + filters["excluded_any_secondary_action_dirty"]
        + filters["included_rows"]
        or filters["included_rows"] != comparison["row_count"]
    ):
        raise TemporalTrainingError("temporal action-clean filter arithmetic differs")
    dirty = filters.get("dirty_endpoint_counts")
    if not isinstance(dirty, dict) or set(dirty) != {"current", "lag1", "lag3", "lag8"}:
        raise TemporalTrainingError("temporal dirty-endpoint audit differs")
    for endpoint in dirty.values():
        if (
            not isinstance(endpoint, dict)
            or set(endpoint) != {"direct_tx", "active_reservation"}
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in endpoint.values()
            )
        ):
            raise TemporalTrainingError("temporal dirty-endpoint count is invalid")

    if not _is_sha256(metadata.get("design_contract_sha256")):
        raise TemporalTrainingError("temporal design-contract hash is invalid")
    build = metadata.get("build_identity")
    if not isinstance(build, dict) or set(build) != {
        "ns3_version",
        "ns3_upstream_commit",
        "project_git_commit",
        "compiler",
        "build_profile",
    } or not all(isinstance(value, str) and value for value in build.values()):
        raise TemporalTrainingError("temporal build identity differs")

    splits = _validate_split(metadata)
    source_runs = metadata.get("source_runs")
    expected_raw_files = set(base_builder.SOURCE_FILES) | set(
        temporal_builder.EXTRA_RAW_FILES
    )
    if not isinstance(source_runs, list) or len(source_runs) != len(splits):
        raise TemporalTrainingError("temporal source-run provenance differs")
    run_ids: dict[str, tuple[int, int]] = {}
    for item in source_runs:
        if not isinstance(item, dict) or set(item) != {
            "run_id",
            "seed",
            "run_number",
            "path",
            "files_sha256",
        }:
            raise TemporalTrainingError("temporal source-run schema differs")
        run_id = item.get("run_id")
        group = (item.get("seed"), item.get("run_number"))
        hashes = item.get("files_sha256")
        if (
            not isinstance(run_id, str)
            or not run_id
            or run_id in run_ids
            or group not in splits
            or not isinstance(item.get("path"), str)
            or not isinstance(hashes, dict)
            or set(hashes) != expected_raw_files
            or not all(_is_sha256(value) for value in hashes.values())
        ):
            raise TemporalTrainingError("temporal source-run identity or hashes differ")
        run_ids[run_id] = group  # type: ignore[assignment]
    if set(run_ids.values()) != set(splits):
        raise TemporalTrainingError("temporal source runs do not cover split groups")

    if set(manifest) != {
        "manifest_schema_version",
        "hash_algorithm",
        "artifacts_sha256",
    } or manifest.get("manifest_schema_version") != 1 or manifest.get("hash_algorithm") != "sha256":
        raise TemporalTrainingError("temporal artifact manifest schema differs")
    hashes = manifest.get("artifacts_sha256")
    expected_artifacts = {temporal_builder.OUTPUT_CSV, temporal_builder.OUTPUT_METADATA}
    if not isinstance(hashes, dict) or set(hashes) != expected_artifacts:
        raise TemporalTrainingError("temporal artifact manifest closure differs")
    for name, expected in hashes.items():
        if not _is_sha256(expected) or _sha256(dataset_dir / name) != expected:
            raise TemporalTrainingError(f"temporal artifact hash differs: {name}")
    return splits, run_ids


def _validate_outcomes(
    row: dict[str, str], source: str, deadline_us: float
) -> dict[str, float]:
    incomplete = _flag(row, "outcome_incomplete", source)
    primary_incomplete = _flag(row, "outcome_primary_incomplete", source)
    latency = _optional_number(row, "outcome_union_latency_us", source)
    primary_latency = _optional_number(row, "outcome_primary_latency_us", source)
    if (latency is None) != bool(incomplete) or (primary_latency is None) != bool(
        primary_incomplete
    ):
        raise TemporalTrainingError(f"{source}: incomplete/latency arithmetic differs")
    if latency is not None and latency < 0:
        raise TemporalTrainingError(f"{source}: negative union latency")
    if primary_latency is not None and primary_latency < 0:
        raise TemporalTrainingError(f"{source}: negative primary latency")
    deadline_bad = _flag(row, "outcome_deadline_miss", source)
    primary_deadline_bad = _flag(row, "outcome_primary_deadline_miss", source)
    if deadline_bad != int(latency is None or latency > deadline_us):
        raise TemporalTrainingError(f"{source}: union deadline label differs")
    if primary_deadline_bad != int(
        primary_latency is None or primary_latency > deadline_us
    ):
        raise TemporalTrainingError(f"{source}: primary deadline label differs")
    for threshold in (10000, 11000, 12000, 12500):
        complete = _flag(row, f"outcome_complete_by_{threshold}us", source)
        primary_complete = _flag(
            row, f"outcome_primary_complete_by_{threshold}us", source
        )
        if complete != int(latency is not None and latency <= threshold):
            raise TemporalTrainingError(f"{source}: union {threshold} us label differs")
        if primary_complete != int(
            primary_latency is not None and primary_latency <= threshold
        ):
            raise TemporalTrainingError(f"{source}: primary {threshold} us label differs")
        rescue = _flag(row, f"outcome_tail_rescue_{threshold}us", source)
        if rescue != int(primary_complete == 0 and complete == 1):
            raise TemporalTrainingError(f"{source}: {threshold} us rescue differs")
    if _flag(row, "outcome_deadline_rescue", source) != int(
        primary_deadline_bad == 1 and deadline_bad == 0
    ):
        raise TemporalTrainingError(f"{source}: deadline rescue differs")
    measured = _number(row, "outcome_secondary_airtime_us", source)
    if measured < 0:
        raise TemporalTrainingError(f"{source}: negative secondary airtime")
    return {
        TARGET_DEADLINE: float(deadline_bad),
        f"primary_bad_{TARGET_DEADLINE}": float(primary_deadline_bad),
        TARGET_LATE18: float(latency is not None and latency > 18000),
        f"primary_bad_{TARGET_LATE18}": float(
            primary_latency is not None and primary_latency > 18000
        ),
        TARGET_COMPLETION: float(latency is not None),
        TARGET_BAD12: float(latency is None or latency > 12000),
        f"primary_bad_{TARGET_BAD12}": float(
            primary_latency is None or primary_latency > 12000
        ),
        "measured_airtime_us": measured,
    }


def _compact_primary_physics(
    base_matrix: np.ndarray, base_names: tuple[str, ...]
) -> np.ndarray:
    columns = {name: base_matrix[:, index] for index, name in enumerate(base_names)}
    pending = columns["x_primary_frame_mac_service_bytes_pending_primary"]
    ahead = columns["x_primary_mac_service_bytes_ahead_of_frame"]
    slack = np.maximum(columns["x_f0_deadline_slack_us"], 1.0)
    rates = {
        window: columns[f"x_primary_acknowledged_mac_service_bytes_{window}ms"]
        / (window * 1000.0)
        for window in (1, 5, 20)
    }
    required = pending / slack
    clearance = np.full(len(base_matrix), np.nan, dtype=float)
    valid = np.isfinite(ahead) & np.isfinite(
        columns["x_primary_acknowledged_mac_service_bytes_5ms"]
    )
    zero = valid & (ahead <= 0)
    served = valid & (ahead > 0) & (
        columns["x_primary_acknowledged_mac_service_bytes_5ms"] > 0
    )
    stalled = valid & (ahead > 0) & ~served
    clearance[zero] = 0.0
    clearance[served] = np.minimum(
        ahead[served]
        * 5000.0
        / columns["x_primary_acknowledged_mac_service_bytes_5ms"][served],
        1_000_000.0,
    )
    clearance[stalled] = 1_000_000.0
    clearance_ratio = np.full(len(base_matrix), np.nan, dtype=float)
    ratio_valid = np.isfinite(clearance) & np.isfinite(slack) & (slack > 0)
    clearance_ratio[ratio_valid] = np.clip(
        clearance[ratio_valid] / slack[ratio_valid], -100.0, 100.0
    )
    values = np.column_stack(
        (
            rates[1] - required,
            rates[5] - required,
            rates[20] - required,
            clearance,
            clearance_ratio,
            rates[1] - rates[20],
            columns["x_primary_phy_busy_fraction_1ms"]
            - columns["x_primary_phy_busy_fraction_20ms"],
        )
    )
    with np.errstate(over="ignore", invalid="ignore"):
        quantized = values.astype(np.float32)
    if np.any(np.isinf(quantized)):
        raise TemporalTrainingError("compact primary physics overflows float32")
    return quantized.astype(float)


def load_temporal_dataset(dataset_dir: Path | str) -> TemporalDataset:
    """Load and fail-closed audit an action-clean temporal T2 artifact."""

    path = Path(dataset_dir).resolve()
    metadata = _read_json(path / temporal_builder.OUTPUT_METADATA)
    manifest = _read_json(path / temporal_builder.OUTPUT_MANIFEST)
    splits, run_ids = _validate_metadata(metadata, manifest, path)
    csv_path = path / temporal_builder.OUTPUT_CSV
    rows: list[audited.Observation] = []
    temporal_values: list[tuple[float, ...]] = []
    seen: set[tuple[int, int, int]] = set()
    prior_key: tuple[int, int, int] | None = None
    groups_seen: set[tuple[int, int]] = set()
    arm_support: set[str] = set()
    expected_probability = 0.08 / 0.88
    try:
        with csv_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None or tuple(reader.fieldnames) != tuple(
                temporal_builder.DATASET_COLUMNS
            ):
                raise TemporalTrainingError("temporal CSV exact schema differs")
            for line_number, source_row in enumerate(reader, start=2):
                if None in source_row or any(value is None for value in source_row.values()):
                    raise TemporalTrainingError(
                        f"temporal CSV line {line_number}: malformed row"
                    )
                context = f"temporal CSV line {line_number}"
                seed = _integer(source_row, "seed", context)
                run_number = _integer(source_row, "run_number", context)
                frame_id = _integer(source_row, "frame_id", context)
                group = (seed, run_number)
                key = (seed, run_number, frame_id)
                run_id = source_row.get("run_id", "")
                role = source_row.get("split_role")
                arm = source_row.get("assigned_arm")
                treatment = _flag(source_row, "treatment", context)
                attempted = _flag(source_row, "attempted", context)
                launched = _flag(source_row, "launched", context)
                noncompliance = _flag(source_row, "noncompliance", context)
                if (
                    source_row.get("dataset_schema_version") != "1"
                    or group not in splits
                    or role != splits[group]
                    or run_ids.get(run_id) != group
                    or source_row.get("analysis_stage") != "T2"
                    or arm not in {"CONTROL", "FULL_COPY_T2"}
                    or treatment != int(arm == "FULL_COPY_T2")
                    or _flag(source_row, "eligible_t2", context) != 1
                    or _flag(source_row, "decision_primary_actionable", context) != 1
                    or frame_id < max(temporal_builder.LAGS)
                ):
                    raise TemporalTrainingError(f"{context}: row identity/eligibility differs")
                if key in seen or (prior_key is not None and key <= prior_key):
                    raise TemporalTrainingError(f"{context}: duplicate or unsorted row identity")
                if not (launched <= attempted <= treatment) or noncompliance != int(
                    treatment == 1 and launched == 0
                ):
                    raise TemporalTrainingError(f"{context}: execution arithmetic differs")
                propensity = _number(source_row, "treatment_probability", context)
                assigned_probability = _number(
                    source_row, "assigned_arm_probability", context
                )
                if not math.isclose(
                    propensity, expected_probability, rel_tol=0.0, abs_tol=1e-15
                ) or not math.isclose(
                    assigned_probability,
                    0.08 if treatment else 0.8,
                    rel_tol=0.0,
                    abs_tol=1e-15,
                ):
                    raise TemporalTrainingError(f"{context}: known propensity differs")
                outcomes = _validate_outcomes(source_row, context, 33333.0)
                if launched == 0 and outcomes["measured_airtime_us"] != 0:
                    raise TemporalTrainingError(f"{context}: unlaunched action has airtime")
                feature_values: list[str] = []
                numeric_by_name: dict[str, float] = {}
                for name in temporal_builder.FEATURE_COLUMNS:
                    raw = source_row[name]
                    if name == "x_f0_frame_type":
                        if raw not in audited.FRAME_TYPES:
                            raise TemporalTrainingError(
                                f"{context}: unsupported frame type {raw!r}"
                            )
                    else:
                        numeric_by_name[name] = _float32_numeric(raw, name, context)
                    if name in base_builder.FEATURE_COLUMNS:
                        feature_values.append(raw)
                temporal_values.append(
                    tuple(numeric_by_name[name] for name in PRIMARY_TEMPORAL_COLUMNS)
                )
                rows.append(
                    audited.Observation(
                        run_id=run_id,
                        seed=seed,
                        run_number=run_number,
                        split_role=str(role),
                        frame_id=frame_id,
                        stage="T2",
                        treatment=treatment,
                        propensity=propensity,
                        launched=launched,
                        features=tuple(feature_values),
                        outcomes=outcomes,
                    )
                )
                seen.add(key)
                groups_seen.add(group)
                arm_support.add(str(arm))
                prior_key = key
    except OSError as error:
        raise TemporalTrainingError(f"cannot read {csv_path}: {error}") from error
    if (
        len(rows) != metadata["comparison"]["row_count"]
        or groups_seen != set(splits)
        or arm_support != {"CONTROL", "FULL_COPY_T2"}
    ):
        raise TemporalTrainingError("temporal CSV coverage differs from metadata")

    full_base, full_base_names = audited._encode_matrix(
        rows, tuple(base_builder.FEATURE_COLUMNS)
    )
    base_indices = [
        index
        for index, name in enumerate(full_base_names)
        if not name.startswith("x_secondary_")
    ]
    primary_base_names = tuple(full_base_names[index] for index in base_indices)
    primary_base = full_base[:, base_indices]
    compact = _compact_primary_physics(full_base, full_base_names)
    temporal = np.asarray(temporal_values, dtype=float)
    if temporal.shape != (len(rows), len(PRIMARY_TEMPORAL_COLUMNS)):
        raise TemporalTrainingError("primary temporal matrix shape differs")
    family_matrices = {
        "primary_base": primary_base,
        "primary_compact_physics": np.column_stack((primary_base, compact)),
        "primary_compact_physics_temporal": np.column_stack(
            (primary_base, compact, temporal)
        ),
    }
    family_names = {
        "primary_base": primary_base_names,
        "primary_compact_physics": (
            primary_base_names + COMPACT_PRIMARY_PHYSICS_NAMES
        ),
        "primary_compact_physics_temporal": (
            primary_base_names
            + COMPACT_PRIMARY_PHYSICS_NAMES
            + PRIMARY_TEMPORAL_COLUMNS
        ),
    }
    for family in FEATURE_FAMILY_ORDER:
        matrix = family_matrices[family]
        with np.errstate(over="ignore", invalid="ignore"):
            quantized = matrix.astype(np.float32)
        if np.any(np.isinf(quantized)) or matrix.shape[1] != len(family_names[family]):
            raise TemporalTrainingError(f"{family}: feature adapter contract differs")
        family_matrices[family] = quantized.astype(float)
        if any("secondary" in name for name in family_names[family]):
            raise TemporalTrainingError(f"{family}: secondary feature escaped exclusion")
    data = audited.StageDataset(
        stage="T2",
        feature_columns=tuple(base_builder.FEATURE_COLUMNS),
        encoded_feature_names=primary_base_names,
        rows=tuple(rows),
        matrix=family_matrices["primary_base"],
    )
    return TemporalDataset(path, metadata, manifest, data, family_matrices, family_names)


def _hgb_classifier(seed: int) -> Pipeline:
    return Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median", add_indicator=True, keep_empty_features=True
                ),
            ),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    loss="log_loss",
                    learning_rate=0.05,
                    max_iter=64,
                    max_leaf_nodes=7,
                    max_depth=3,
                    min_samples_leaf=20,
                    l2_regularization=1.0,
                    max_bins=63,
                    early_stopping=False,
                    random_state=seed,
                ),
            ),
        ]
    )


def _ridge() -> Pipeline:
    return Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median", add_indicator=True, keep_empty_features=True
                ),
            ),
            ("scale", StandardScaler()),
            ("regressor", Ridge(alpha=10.0)),
        ]
    )


def _fit(model: Pipeline, x: np.ndarray, y: np.ndarray, context: str) -> Pipeline:
    if len(x) == 0 or len(x) != len(y) or not np.all(np.isfinite(y)):
        raise TemporalTrainingError(f"{context}: invalid fit population")
    model.fit(x, y)
    return model


def _fit_classifier(
    x: np.ndarray, y: np.ndarray, seed: int, context: str
) -> Pipeline:
    if set(np.asarray(y, dtype=int).tolist()) != {0, 1}:
        raise TemporalTrainingError(f"{context}: binary fit lacks both outcomes")
    return _fit(_hgb_classifier(seed), x, y, context)


def _probability(model: Pipeline, x: np.ndarray) -> np.ndarray:
    classifier = model.named_steps.get("classifier")
    if classifier is not None:
        values = model.predict_proba(x)[:, 1]
    else:
        values = model.predict(x)
    return np.clip(np.asarray(values, dtype=float), 0.0, 1.0)


def _frame_gate(data: audited.StageDataset, indices: np.ndarray, gate: str) -> np.ndarray:
    if gate == "all_frames":
        return np.ones(len(indices), dtype=bool)
    categorical = data.feature_columns.index("x_f0_frame_type")
    return np.asarray(
        [data.rows[index].features[categorical] == "P_FRAME" for index in indices],
        dtype=bool,
    )


def _fit_value_heads(
    data: audited.StageDataset, train: np.ndarray, seed: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    x = data.matrix[train]
    treatment = np.asarray([data.rows[index].treatment for index in train])
    launched = np.asarray([data.rows[index].launched for index in train])
    treated_launched = (treatment == 1) & (launched == 1)
    if int(np.sum(treated_launched)) < 20:
        raise TemporalTrainingError("value heads have too few launched training actions")
    heads: dict[str, Any] = {}
    counts: dict[str, Any] = {
        "training_rows": len(train),
        "treated_rows": int(np.sum(treatment == 1)),
        "treated_launched_rows": int(np.sum(treated_launched)),
    }
    normalizers: dict[str, dict[str, float]] = {}
    control = treatment == 0
    for target_index, target in enumerate(VALUE_TARGETS):
        primary = np.asarray(
            [data.rows[index].outcomes[f"primary_bad_{target}"] for index in train]
        )
        treated_bad = np.asarray(
            [data.rows[index].outcomes[target] for index in train]
        )
        heads[f"{target}:primary_need"] = _fit_classifier(
            x,
            primary,
            seed + target_index * 10,
            f"{target} primary-need HGB",
        )
        heads[f"{target}:treated_bad"] = _fit_classifier(
            x[treated_launched],
            treated_bad[treated_launched],
            seed + target_index * 10 + 1,
            f"{target} treated-bad HGB",
        )
        observed = float(np.mean(primary[control]))
        normalizers[target] = {
            "training_control_primary_risk": observed,
            "normalizer": max(observed, RISK_NORMALIZER_FLOOR),
        }
    positive_cost = np.asarray(
        [data.rows[index].outcomes["measured_airtime_us"] for index in train]
    )[treated_launched]
    if np.any(positive_cost <= 0):
        raise TemporalTrainingError("launched training action has non-positive cost")
    log_cost = np.log1p(positive_cost)
    cost_model = _fit(
        _ridge(), x[treated_launched], log_cost, "launched log-cost ridge"
    )
    predicted_log = np.asarray(cost_model.predict(x[treated_launched]), dtype=float)
    smearing = audited._duan_smearing_factor(log_cost, predicted_log)
    heads["log_cost_given_launch"] = cost_model
    heads["log_cost_smearing_factor"] = smearing
    heads["training_risk_normalizers"] = normalizers
    return heads, counts


def _candidate_scores(
    heads: dict[str, Any], x: np.ndarray
) -> dict[str, np.ndarray]:
    deltas: dict[str, np.ndarray] = {}
    for target in VALUE_TARGETS:
        need = _probability(heads[f"{target}:primary_need"], x)
        treated_bad = _probability(heads[f"{target}:treated_bad"], x)
        deltas[target] = np.maximum(need - treated_bad, 0.0)
    predicted_cost = np.maximum(
        _inverse_log_cost(
            np.asarray(heads["log_cost_given_launch"].predict(x), dtype=float),
            heads["log_cost_smearing_factor"],
        ),
        1.0,
    )
    normalizers = heads["training_risk_normalizers"]
    raw = {
        "deadline_value_per_cost": deltas[TARGET_DEADLINE] / predicted_cost,
        "completed_late18_value_per_cost": deltas[TARGET_LATE18] / predicted_cost,
        "balanced_normalized_value_per_cost": (
            0.5
            * (
                deltas[TARGET_DEADLINE]
                / normalizers[TARGET_DEADLINE]["normalizer"]
                + deltas[TARGET_LATE18]
                / normalizers[TARGET_LATE18]["normalizer"]
            )
            / predicted_cost
        ),
        "legacy_bad12_value_per_cost": deltas[TARGET_BAD12] / predicted_cost,
    }
    return {name: audited._quantize_candidate_scores(value) for name, value in raw.items()}


def _inverse_log_cost(predicted_log: np.ndarray, smearing: float) -> np.ndarray:
    """Retransform a log-cost prediction at an explicit exportable cap."""

    values = np.asarray(predicted_log, dtype=float)
    if not np.all(np.isfinite(values)) or not math.isfinite(smearing) or smearing <= 0:
        raise TemporalTrainingError("predicted log-cost retransformation is invalid")
    retransformed_log = np.clip(
        values + math.log(smearing),
        0.0,
        math.log1p(PREDICTED_COST_CAP_US),
    )
    return np.expm1(retransformed_log)


def _threshold_for_global_fraction(
    scores: np.ndarray, gate: np.ndarray, requested_fraction: float
) -> float:
    if len(scores) != len(gate) or not np.any(gate):
        raise TemporalTrainingError("candidate threshold has an empty frame gate")
    gated_fraction = requested_fraction * len(scores) / int(np.sum(gate))
    if not 0 < gated_fraction <= 1:
        raise TemporalTrainingError("requested action fraction exceeds frame gate")
    threshold = np.quantile(
        audited._quantize_candidate_scores(scores[gate]),
        1.0 - gated_fraction,
        method="higher",
    )
    return float(np.float32(threshold))


def _apply_threshold(
    scores: np.ndarray, gate: np.ndarray, threshold: float
) -> np.ndarray:
    if not math.isfinite(threshold):
        raise TemporalTrainingError("candidate threshold is non-finite")
    return gate & audited._apply_score_threshold(scores, threshold)


def _fit_evaluation_nuisances(
    data: audited.StageDataset, train: np.ndarray
) -> dict[str, Any]:
    nuisances: dict[str, Any] = {"outcomes": {}}
    treatment = np.asarray([data.rows[index].treatment for index in train])
    for target in EVALUATION_TARGETS:
        outcome = np.asarray([data.rows[index].outcomes[target] for index in train])
        arm_models: list[Pipeline] = []
        for arm in (0, 1):
            mask = treatment == arm
            arm_models.append(
                _fit(
                    _ridge(),
                    data.matrix[train[mask]],
                    outcome[mask],
                    f"{target} arm-{arm} evaluation nuisance",
                )
            )
        nuisances["outcomes"][target] = tuple(arm_models)
    launched = np.asarray([data.rows[index].launched for index in train])
    treated = treatment == 1
    treated_launch = treated & (launched == 1)
    launch_model = _fit(
        _ridge(), data.matrix[train[treated]], launched[treated], "assignment launch nuisance"
    )
    cost = np.asarray(
        [data.rows[index].outcomes["measured_airtime_us"] for index in train]
    )
    if np.any(cost[treated_launch] <= 0):
        raise TemporalTrainingError("evaluation cost nuisance sees non-positive launch cost")
    log_cost = np.log1p(cost[treated_launch])
    cost_model = _fit(
        _ridge(),
        data.matrix[train[treated_launch]],
        log_cost,
        "evaluation launched log-cost nuisance",
    )
    smearing = audited._duan_smearing_factor(
        log_cost, np.asarray(cost_model.predict(data.matrix[train[treated_launch]]))
    )
    nuisances["assignment_launch_probability"] = launch_model
    nuisances["log_cost_given_launch"] = cost_model
    nuisances["log_cost_smearing_factor"] = smearing
    return nuisances


def _evaluation_components(
    data: audited.StageDataset,
    indices: np.ndarray,
    nuisances: dict[str, Any],
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], np.ndarray]:
    treatment = np.asarray([data.rows[index].treatment for index in indices])
    propensity = np.asarray([data.rows[index].propensity for index in indices])
    result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for target in EVALUATION_TARGETS:
        model0, model1 = nuisances["outcomes"][target]
        mu0 = np.clip(np.asarray(model0.predict(data.matrix[indices])), 0.0, 1.0)
        mu1 = np.clip(np.asarray(model1.predict(data.matrix[indices])), 0.0, 1.0)
        outcome = np.asarray([data.rows[index].outcomes[target] for index in indices])
        phi0 = mu0 + (1 - treatment) * (outcome - mu0) / (1 - propensity)
        phi1 = mu1 + treatment * (outcome - mu1) / propensity
        result[target] = (phi0, phi1)
    launch = np.clip(
        np.asarray(
            nuisances["assignment_launch_probability"].predict(data.matrix[indices])
        ),
        0.0,
        1.0,
    )
    conditional_cost = _inverse_log_cost(
        np.asarray(
            nuisances["log_cost_given_launch"].predict(data.matrix[indices])
        ),
        nuisances["log_cost_smearing_factor"],
    )
    predicted_assignment_cost = launch * conditional_cost
    observed_cost = np.asarray(
        [data.rows[index].outcomes["measured_airtime_us"] for index in indices]
    )
    cost_phi1 = audited._assignment_cost_dr(
        observed_cost, treatment, propensity, predicted_assignment_cost
    )
    return result, cost_phi1


def _policy_metrics(
    policy: np.ndarray,
    components: dict[str, tuple[np.ndarray, np.ndarray]],
    cost_phi1: np.ndarray,
) -> dict[str, float]:
    values: dict[str, tuple[float, float, float]] = {}
    for target, (phi0, phi1) in components.items():
        none = float(np.mean(phi0))
        benefit = float(np.mean(policy * (phi0 - phi1)))
        values[target] = (none, none - benefit, benefit)
    none_completion, policy_completion, _ = values[TARGET_COMPLETION]
    none_late, policy_late, _ = values[TARGET_LATE18]
    if none_completion <= 0 or policy_completion <= 0 or none_late <= 0:
        raise TemporalTrainingError("DR conditional-tail denominator is non-positive")
    none_ratio = none_late / none_completion
    policy_ratio = policy_late / policy_completion
    if none_ratio <= 0:
        raise TemporalTrainingError("DR treat-none conditional late ratio is non-positive")
    none_miss, policy_miss, miss_benefit = values[TARGET_DEADLINE]
    if none_miss <= 0:
        raise TemporalTrainingError("DR treat-none deadline-miss risk is non-positive")
    metrics = {
        "realized_action_fraction": float(np.mean(policy)),
        "dr_airtime_us_per_eligible_frame": float(np.mean(policy * cost_phi1)),
        "dr_treat_none_deadline_miss": none_miss,
        "dr_policy_deadline_miss": policy_miss,
        "dr_deadline_miss_benefit": miss_benefit,
        "deadline_miss_relative_improvement": (none_miss - policy_miss) / none_miss,
        "dr_treat_none_completed_late18_numerator": none_late,
        "dr_policy_completed_late18_numerator": policy_late,
        "dr_treat_none_completion_probability": none_completion,
        "dr_policy_completion_probability": policy_completion,
        "dr_treat_none_completed_late18_ratio": none_ratio,
        "dr_policy_completed_late18_ratio": policy_ratio,
        "completed_late18_relative_improvement": (none_ratio - policy_ratio) / none_ratio,
        "dr_treat_none_bad12": values[TARGET_BAD12][0],
        "dr_policy_bad12": values[TARGET_BAD12][1],
        "dr_bad12_benefit": values[TARGET_BAD12][2],
    }
    if any(not math.isfinite(value) for value in metrics.values()):
        raise TemporalTrainingError("policy metrics contain a non-finite estimate")
    return metrics


def _metric_feasibility_reasons(metrics: dict[str, float]) -> list[str]:
    """Return logical point-estimate violations shared by every evidence role."""

    metric_fields = (
        "realized_action_fraction",
        "dr_airtime_us_per_eligible_frame",
        "dr_treat_none_deadline_miss",
        "dr_policy_deadline_miss",
        "dr_deadline_miss_benefit",
        "deadline_miss_relative_improvement",
        "dr_treat_none_completed_late18_numerator",
        "dr_policy_completed_late18_numerator",
        "dr_treat_none_completion_probability",
        "dr_policy_completion_probability",
        "dr_treat_none_completed_late18_ratio",
        "dr_policy_completed_late18_ratio",
        "completed_late18_relative_improvement",
        "dr_treat_none_bad12",
        "dr_policy_bad12",
        "dr_bad12_benefit",
    )
    if any(not math.isfinite(metrics[name]) for name in metric_fields):
        return ["non_finite_policy_metric"]
    reasons: list[str] = []
    if not 0 <= metrics["realized_action_fraction"] <= 1:
        reasons.append("action_fraction_bounds")
    if metrics["dr_airtime_us_per_eligible_frame"] < 0:
        reasons.append("negative_assignment_airtime")
    probability_fields = (
        "dr_treat_none_deadline_miss",
        "dr_policy_deadline_miss",
        "dr_treat_none_completed_late18_numerator",
        "dr_policy_completed_late18_numerator",
        "dr_treat_none_completion_probability",
        "dr_policy_completion_probability",
        "dr_treat_none_bad12",
        "dr_policy_bad12",
    )
    if any(not 0 <= metrics[name] <= 1 for name in probability_fields):
        reasons.append("dr_probability_bounds")
    if (
        metrics["dr_treat_none_completed_late18_numerator"]
        > metrics["dr_treat_none_completion_probability"] + 1e-12
        or metrics["dr_policy_completed_late18_numerator"]
        > metrics["dr_policy_completion_probability"] + 1e-12
    ):
        reasons.append("completed_late18_arithmetic")
    for role in ("treat_none", "policy"):
        incomplete = 1.0 - metrics[f"dr_{role}_completion_probability"]
        deadline_miss = metrics[f"dr_{role}_deadline_miss"]
        completed_late18 = metrics[f"dr_{role}_completed_late18_numerator"]
        bad12 = metrics[f"dr_{role}_bad12"]
        if (
            incomplete > deadline_miss + 1e-12
            or deadline_miss > incomplete + completed_late18 + 1e-12
            or bad12 + 1e-12 < incomplete + completed_late18
        ):
            reasons.append("outcome_probability_nesting")
            break
    return reasons


def _require_feasible_policy_metrics(
    metrics: dict[str, float], context: str
) -> None:
    reasons = _metric_feasibility_reasons(metrics)
    if reasons:
        raise TemporalTrainingError(
            f"{context}: infeasible policy metrics: {'+'.join(reasons)}"
        )


def _cluster_uncertainty(
    data: audited.StageDataset,
    indices: np.ndarray,
    policy: np.ndarray,
    components: dict[str, tuple[np.ndarray, np.ndarray]],
    cost_phi1: np.ndarray,
    *,
    seed: int,
    context: str,
) -> dict[str, Any]:
    primitive: dict[str, np.ndarray] = {
        "action": policy.astype(float),
        "airtime": policy * cost_phi1,
    }
    for target, (phi0, phi1) in components.items():
        primitive[f"{target}:none"] = phi0
        primitive[f"{target}:policy"] = phi0 + policy * (phi1 - phi0)
    positions: dict[tuple[int, int], list[int]] = {}
    for position, index in enumerate(indices):
        positions.setdefault(data.rows[index].group, []).append(position)
    groups = sorted(positions)
    summaries = {
        name: [
            (float(np.sum(value[positions[group]])), len(positions[group]))
            for group in groups
        ]
        for name, value in primitive.items()
    }

    def derive(means: dict[str, float]) -> dict[str, float]:
        none_miss = means[f"{TARGET_DEADLINE}:none"]
        policy_miss = means[f"{TARGET_DEADLINE}:policy"]
        none_late = means[f"{TARGET_LATE18}:none"]
        policy_late = means[f"{TARGET_LATE18}:policy"]
        none_completion = means[f"{TARGET_COMPLETION}:none"]
        policy_completion = means[f"{TARGET_COMPLETION}:policy"]
        if min(none_miss, none_late, none_completion, policy_completion) <= 0:
            raise TemporalTrainingError(
                "cluster replicate has a non-positive DR ratio denominator"
            )
        none_ratio = none_late / none_completion
        policy_ratio = policy_late / policy_completion
        return {
            "dr_policy_deadline_miss": policy_miss,
            "deadline_miss_relative_improvement": (
                none_miss - policy_miss
            ) / none_miss,
            "dr_policy_completed_late18_numerator": policy_late,
            "dr_policy_completion_probability": policy_completion,
            "dr_policy_completed_late18_ratio": policy_ratio,
            "completed_late18_relative_improvement": (
                none_ratio - policy_ratio
            ) / none_ratio,
            "dr_airtime_us_per_eligible_frame": means["airtime"],
            "realized_action_fraction": means["action"],
        }

    point = derive({name: float(np.mean(value)) for name, value in primitive.items()})
    rng = random.Random(audited._cluster_seed(seed, context))
    replications = {name: [] for name in point}
    for _ in range(BOOTSTRAP_REPLICATIONS):
        sampled = [rng.randrange(len(groups)) for _ in groups]
        means = {
            name: sum(group_values[index][0] for index in sampled)
            / sum(group_values[index][1] for index in sampled)
            for name, group_values in summaries.items()
        }
        derived = derive(means)
        for name, value in derived.items():
            replications[name].append(value)
    alpha = (1.0 - BOOTSTRAP_CONFIDENCE) / 2.0
    return {
        "unit": "(seed, run_number)",
        "method": "deterministic_run_cluster_percentile_bootstrap_ratio_v1",
        "evidence_role": (
            "previously_opened_engineering_test"
            if context.endswith(":test")
            else "selection_reused_descriptive"
        ),
        "replications": BOOTSTRAP_REPLICATIONS,
        "confidence_level": BOOTSTRAP_CONFIDENCE,
        "run_count": len(groups),
        "estimands": {
            name: {
                "estimate": value,
                "ci_lower": audited._linear_percentile(
                    replications[name], alpha
                ),
                "ci_upper": audited._linear_percentile(
                    replications[name], 1.0 - alpha
                ),
            }
            for name, value in point.items()
        },
    }


def _repository_provenance() -> dict[str, Any]:
    source = Path(__file__).resolve()
    repository = source.parent.parent

    def git(*arguments: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return completed.stdout.strip()

    dependency_paths = {
        "build_randomized_intervention_dataset.py": Path(base_builder.__file__).resolve(),
        "build_randomized_temporal_dataset.py": Path(temporal_builder.__file__).resolve(),
        "train_randomized_value.py": Path(audited.__file__).resolve(),
    }
    worktree_status = git("status", "--porcelain=v1", "--untracked-files=all")
    return {
        "trainer_source_path": str(source.relative_to(repository)),
        "trainer_source_sha256": _sha256(source),
        "local_dependency_source_sha256": {
            name: _sha256(path) for name, path in dependency_paths.items()
        },
        "repository_git_commit": git("rev-parse", "HEAD"),
        "repository_worktree_clean": worktree_status == "",
        "repository_worktree_status_sha256": (
            hashlib.sha256(worktree_status.encode("utf-8")).hexdigest()
            if worktree_status is not None
            else None
        ),
    }


def _candidate_record(
    *,
    ordinal: int,
    family: str,
    ranker: str,
    gate_name: str,
    fraction: float,
    threshold: float,
    policy: np.ndarray,
    components: dict[str, tuple[np.ndarray, np.ndarray]],
    cost_phi1: np.ndarray,
) -> dict[str, Any]:
    if (
        not math.isfinite(fraction)
        or not 0 < fraction <= 1
        or not math.isfinite(threshold)
    ):
        raise TemporalTrainingError("candidate fraction or threshold is invalid")
    record: dict[str, Any] = {
        "candidate_ordinal": ordinal,
        "feature_family": family,
        "ranker": ranker,
        "frame_gate": gate_name,
        "requested_action_fraction": fraction,
        "score_threshold": threshold,
    }
    record.update(_policy_metrics(policy, components, cost_phi1))
    reasons = _metric_feasibility_reasons(record)
    if threshold <= 0:
        reasons.append("positive_threshold")
    if (
        "action_fraction_bounds" not in reasons
        and record["realized_action_fraction"] > MAX_ACTION_FRACTION + 1e-12
    ):
        reasons.append("action_fraction")
    if (
        "negative_assignment_airtime" not in reasons
        and record["dr_airtime_us_per_eligible_frame"]
        > MAX_DR_AIRTIME_US_PER_ELIGIBLE_FRAME + 1e-12
    ):
        reasons.append("assignment_airtime")
    if record["deadline_miss_relative_improvement"] < (
        MIN_RELATIVE_IMPROVEMENT - 1e-12
    ):
        reasons.append("deadline_improvement")
    if record["completed_late18_relative_improvement"] < (
        MIN_RELATIVE_IMPROVEMENT - 1e-12
    ):
        reasons.append("completed_late18_improvement")
    record["balanced_min_relative_improvement"] = min(
        record["deadline_miss_relative_improvement"],
        record["completed_late18_relative_improvement"],
    )
    record["mean_relative_improvement"] = 0.5 * (
        record["deadline_miss_relative_improvement"]
        + record["completed_late18_relative_improvement"]
    )
    record["admissible"] = int(not reasons)
    record["rejection_reason"] = "+".join(reasons)
    record["selected"] = 0
    return record


def _select(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    admissible = [record for record in records if record["admissible"]]
    if not admissible:
        raise TemporalTrainingError(
            "no calibration candidate meets both frozen 50% improvement gates"
        )
    selected = min(
        admissible,
        key=lambda record: (
            -record["balanced_min_relative_improvement"],
            -record["mean_relative_improvement"],
            record["dr_airtime_us_per_eligible_frame"],
            record["realized_action_fraction"],
            record["candidate_ordinal"],
        ),
    )
    selected["selected"] = 1
    return selected


CANDIDATE_CSV_FIELDS = (
    "candidate_ordinal",
    "feature_family",
    "ranker",
    "frame_gate",
    "requested_action_fraction",
    "score_threshold",
    "realized_action_fraction",
    "dr_airtime_us_per_eligible_frame",
    "dr_treat_none_deadline_miss",
    "dr_policy_deadline_miss",
    "dr_deadline_miss_benefit",
    "deadline_miss_relative_improvement",
    "dr_treat_none_completed_late18_numerator",
    "dr_policy_completed_late18_numerator",
    "dr_treat_none_completion_probability",
    "dr_policy_completion_probability",
    "dr_treat_none_completed_late18_ratio",
    "dr_policy_completed_late18_ratio",
    "completed_late18_relative_improvement",
    "dr_treat_none_bad12",
    "dr_policy_bad12",
    "dr_bad12_benefit",
    "balanced_min_relative_improvement",
    "mean_relative_improvement",
    "admissible",
    "rejection_reason",
    "selected",
)


def _write_candidates(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    try:
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination, fieldnames=CANDIDATE_CSV_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        field: (
                            format(row[field], ".17g")
                            if isinstance(row[field], float)
                            else row[field]
                        )
                        for field in CANDIDATE_CSV_FIELDS
                    }
                )
    except OSError as error:
        raise TemporalTrainingError(f"cannot write {path}: {error}") from error


def _write_json(path: Path, value: Any) -> None:
    try:
        path.write_text(
            json.dumps(
                audited._finite_json(value),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise TemporalTrainingError(f"cannot write {path}: {error}") from error


def train_temporal_t2_value(
    dataset: TemporalDataset,
    output_dir: Path | str,
    *,
    random_seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    """Fit, calibrate, and once evaluate the bounded temporal T2 policy set."""

    split_counts = dataset.metadata["split"]["counts"]
    if (
        split_counts["train"] < 4
        or split_counts["calibration"] < 1
        or split_counts["test"] < 1
    ):
        raise TemporalTrainingError(
            "honest temporal training needs four train runs and held-out roles"
        )
    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError as error:
        raise TemporalTrainingError(
            f"refusing to overwrite output directory: {output}"
        ) from error
    except OSError as error:
        raise TemporalTrainingError(
            f"cannot reserve output directory {output}: {error}"
        ) from error
    temporary: Path | None = None
    published: list[tuple[Path, int, int]] = []
    try:
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
        )
        base_data = dataset.data
        train = base_data.indices("train")
        calibration = base_data.indices("calibration")
        test = base_data.indices("test")
        if any(len(indices) == 0 for indices in (train, calibration, test)):
            raise TemporalTrainingError("one temporal run split has no rows")

        preferred_data = dataset.stage_for_family(
            "primary_compact_physics_temporal"
        )
        nuisances = _fit_evaluation_nuisances(preferred_data, train)
        calibration_components, calibration_cost = _evaluation_components(
            preferred_data, calibration, nuisances
        )
        family_heads: dict[str, Any] = {}
        family_fit_counts: dict[str, Any] = {}
        calibration_score_cache: dict[tuple[str, str], np.ndarray] = {}
        for family_index, family in enumerate(FEATURE_FAMILY_ORDER):
            family_data = dataset.stage_for_family(family)
            heads, counts = _fit_value_heads(
                family_data, train, random_seed + family_index * 100
            )
            family_heads[family] = heads
            family_fit_counts[family] = counts
            for ranker, values in _candidate_scores(
                heads, family_data.matrix[calibration]
            ).items():
                calibration_score_cache[(family, ranker)] = values

        records: list[dict[str, Any]] = []
        ordinal = 0
        for family in FEATURE_FAMILY_ORDER:
            for ranker in RANKER_ORDER:
                scores = calibration_score_cache[(family, ranker)]
                for gate_name in FRAME_GATES:
                    gate = _frame_gate(base_data, calibration, gate_name)
                    for fraction in REQUESTED_ACTION_FRACTIONS:
                        threshold = _threshold_for_global_fraction(
                            scores, gate, fraction
                        )
                        policy = _apply_threshold(scores, gate, threshold)
                        records.append(
                            _candidate_record(
                                ordinal=ordinal,
                                family=family,
                                ranker=ranker,
                                gate_name=gate_name,
                                fraction=fraction,
                                threshold=threshold,
                                policy=policy,
                                components=calibration_components,
                                cost_phi1=calibration_cost,
                            )
                        )
                        ordinal += 1
        selected = _select(records)
        selected_family = selected["feature_family"]
        selected_ranker = selected["ranker"]
        selected_gate = selected["frame_gate"]
        selected_threshold = selected["score_threshold"]
        selected_calibration_policy = _apply_threshold(
            calibration_score_cache[(selected_family, selected_ranker)],
            _frame_gate(base_data, calibration, selected_gate),
            selected_threshold,
        )
        calibration_uncertainty = _cluster_uncertainty(
            base_data,
            calibration,
            selected_calibration_policy,
            calibration_components,
            calibration_cost,
            seed=random_seed,
            context="temporal-t2:calibration",
        )

        # The test branch begins only after the calibration record is frozen.
        selected_data = dataset.stage_for_family(selected_family)
        test_components, test_cost = _evaluation_components(
            preferred_data, test, nuisances
        )
        test_scores = _candidate_scores(
            family_heads[selected_family], selected_data.matrix[test]
        )[selected_ranker]
        test_policy = _apply_threshold(
            test_scores,
            _frame_gate(base_data, test, selected_gate),
            selected_threshold,
        )
        test_result = _policy_metrics(test_policy, test_components, test_cost)
        _require_feasible_policy_metrics(test_result, "engineering test policy")
        test_uncertainty = _cluster_uncertainty(
            base_data,
            test,
            test_policy,
            test_components,
            test_cost,
            seed=random_seed,
            context="temporal-t2:test",
        )

        selected_summary = {
            key: value for key, value in selected.items() if key != "selected"
        }
        selected_summary["run_cluster_uncertainty"] = calibration_uncertainty
        test_result.update(
            {
                "row_count": len(test),
                "run_count": len({base_data.rows[index].group for index in test}),
                "frozen_feature_family": selected_family,
                "frozen_ranker": selected_ranker,
                "frozen_frame_gate": selected_gate,
                "frozen_float32_score_threshold": selected_threshold,
                "run_cluster_uncertainty": test_uncertainty,
            }
        )

        preprocessing = {
            "feature_adapter_id": FEATURE_ADAPTER_ID,
            "numeric_input": (
                "parse finite decimal, round to IEEE-754 float32, widen exactly "
                "to float64 before derived arithmetic/model fitting"
            ),
            "derived_input": (
                "derive from widened float32 base inputs, round result to float32, "
                "then widen exactly"
            ),
            "categorical": "exact I_FRAME/P_FRAME one-hot",
            "missing": "NaN then training-only median imputation with indicators",
            "hgb": {
                "class": "sklearn.ensemble.HistGradientBoostingClassifier",
                "loss": "log_loss",
                "learning_rate": 0.05,
                "max_iter": 64,
                "max_leaf_nodes": 7,
                "max_depth": 3,
                "min_samples_leaf": 20,
                "l2_regularization": 1.0,
                "max_bins": 63,
                "early_stopping": False,
            },
            "ridge_cost": {
                "target": "log1p(measured secondary airtime us) given launch",
                "steps": ["median imputation+indicators", "standard scaling", "Ridge(alpha=10)"],
                "retransform": (
                    "Duan smearing in log space, then expm1, clipped to the "
                    "serialized predicted-cost cap"
                ),
                "predicted_cost_cap_us": PREDICTED_COST_CAP_US,
            },
            "score_adapter_id": SCORE_ADAPTER_ID,
            "score_comparator": "float32 score >= frozen float32 calibration threshold",
        }
        bundle = {
            "model_bundle_schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
            "training_schema_version": TRAINING_SCHEMA_VERSION,
            "feature_contract_id": temporal_builder.FEATURE_CONTRACT_ID,
            "model_spec_id": MODEL_SPEC_ID,
            "selection_id": SELECTION_ID,
            "preprocessing": preprocessing,
            "feature_families": {
                family: {
                    "ordered_feature_names": dataset.family_feature_names[family],
                    "feature_count": len(dataset.family_feature_names[family]),
                    "contains_secondary_feature": False,
                    "requires_exact_lags": (
                        list(temporal_builder.LAGS)
                        if family == "primary_compact_physics_temporal"
                        else []
                    ),
                    "fit_counts": family_fit_counts[family],
                    "heads": family_heads[family],
                }
                for family in FEATURE_FAMILY_ORDER
            },
            "compact_primary_physics": {
                "ordered_feature_names": COMPACT_PRIMARY_PHYSICS_NAMES,
                "formulas": {
                    "working_rate_margin": (
                        "primary acknowledged bytes/window us - pending bytes/max(slack,1)"
                    ),
                    "ahead_clearance": (
                        "min(primary bytes ahead*5000/primary ack bytes 5ms,1e6); "
                        "0 if no backlog, 1e6 if stalled"
                    ),
                    "ack_rate_trend": "primary 1ms ack rate - primary 20ms ack rate",
                    "busy_trend": "primary 1ms busy fraction - primary 20ms busy fraction",
                },
            },
            "rankers": {
                "deadline_value_per_cost": "max(P(primary miss)-P(treated miss),0)/predicted cost",
                "completed_late18_value_per_cost": (
                    "max(P(primary complete-and-late18)-P(treated "
                    "complete-and-late18),0)/predicted cost"
                ),
                "balanced_normalized_value_per_cost": (
                    "mean of nonnegative deadline and late18 two-head values divided "
                    "by serialized training-only control-primary risk normalizers, then cost"
                ),
                "legacy_bad12_value_per_cost": (
                    "max(P(primary bad12)-P(treated bad12),0)/predicted cost; "
                    "descriptive comparator"
                ),
            },
            "evaluation_nuisances": nuisances,
            "selected_policy": {
                "feature_family": selected_family,
                "ordered_feature_names": dataset.family_feature_names[
                    selected_family
                ],
                "ranker": selected_ranker,
                "frame_gate": selected_gate,
                "score_threshold": selected_threshold,
                "requested_action_fraction": selected["requested_action_fraction"],
                "selection_role": "calibration",
                "runtime_gates": {
                    "frame_type": selected_gate,
                    "exact_lag1_lag3_lag8_history_required": (
                        selected_family == "primary_compact_physics_temporal"
                    ),
                    "threshold_must_be_positive": True,
                },
            },
        }
        metrics = {
            "training_schema_version": TRAINING_SCHEMA_VERSION,
            "evidence_status": (
                "previously_opened_run_group_test_engineering_evidence_not_confirmation"
            ),
            "decisive_confirmation": "fresh closed-loop seeds 1301+ on one final build",
            "provenance": _repository_provenance(),
            "dataset_dir": str(dataset.path),
            "dataset_artifacts_sha256": dataset.manifest["artifacts_sha256"],
            "input_v1_artifacts_sha256": dataset.metadata["input_v1"][
                "artifacts_sha256"
            ],
            "split": dataset.metadata["split"],
            "test_role_used_during_selection": False,
            "test_threshold_source": "single frozen calibration float32 threshold",
            "model_spec_id": MODEL_SPEC_ID,
            "selection": {
                "selection_id": SELECTION_ID,
                "role": "calibration",
                "candidate_count": len(records),
                "feature_family_order": list(FEATURE_FAMILY_ORDER),
                "ranker_order": list(RANKER_ORDER),
                "frame_gate_order": list(FRAME_GATES),
                "requested_action_fractions": list(REQUESTED_ACTION_FRACTIONS),
                "maximum_action_fraction": MAX_ACTION_FRACTION,
                "maximum_dr_airtime_us_per_eligible_frame": (
                    MAX_DR_AIRTIME_US_PER_ELIGIBLE_FRAME
                ),
                "minimum_relative_improvement_each_objective": (
                    MIN_RELATIVE_IMPROVEMENT
                ),
                "primary_rule": (
                    "maximize the smaller calibrated relative improvement across "
                    "deadline miss and conditional completed-late18"
                ),
                "tie_break": (
                    "larger mean relative improvement, lower DR airtime, lower action "
                    "fraction, stable predeclared ordinal"
                ),
            },
            "estimands": {
                "deadline_miss": "unconditional; incomplete frames are misses",
                "completed_late18_numerator": (
                    "P(frame completes and completion latency exceeds 18000 us)"
                ),
                "completion_denominator": "P(frame completes), not P(frame meets deadline)",
                "completed_late18_ratio": (
                    "completed-late18 numerator divided by completion probability"
                ),
                "legacy_bad12": (
                    "unconditional incomplete-or-later-than-12000 us; descriptive only"
                ),
                "population": (
                    "action-clean common-T2-eligible frames; mapping to all-generated-frame "
                    "P99 requires closed-loop reconstruction/confirmation"
                ),
            },
            "conditional_ratio_caveat": (
                "the DR ratio is nonlinear; numerator and denominator are reported "
                "separately and training fails on non-positive point/bootstrap denominators"
            ),
            "feature_families": {
                family: {
                    "ordered_feature_names": list(dataset.family_feature_names[family]),
                    "feature_count": len(dataset.family_feature_names[family]),
                    "fit_counts": family_fit_counts[family],
                    "training_risk_normalizers": family_heads[family][
                        "training_risk_normalizers"
                    ],
                }
                for family in FEATURE_FAMILY_ORDER
            },
            "selected_calibration_policy": selected_summary,
            "engineering_test_policy": test_result,
            "random_seed": random_seed,
            "software": {
                "python": sys.version.split()[0],
                "numpy": importlib.metadata.version("numpy"),
                "scikit_learn": importlib.metadata.version("scikit-learn"),
            },
        }

        with (temporary / OUTPUT_MODEL).open("wb") as destination:
            pickle.dump(bundle, destination, protocol=4)
        _write_candidates(temporary / OUTPUT_CANDIDATES, records)
        _write_json(temporary / OUTPUT_METRICS, metrics)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "artifacts_sha256": {
                name: _sha256(temporary / name)
                for name in (OUTPUT_MODEL, OUTPUT_CANDIDATES, OUTPUT_METRICS)
            },
            "selected_policy_contract": {
                "feature_family": selected_family,
                "ordered_feature_names": list(
                    dataset.family_feature_names[selected_family]
                ),
                "ranker": selected_ranker,
                "frame_gate": selected_gate,
                "score_threshold_float32": selected_threshold,
                "feature_adapter_id": FEATURE_ADAPTER_ID,
                "score_adapter_id": SCORE_ADAPTER_ID,
            },
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        # Publish the manifest last as the transaction commit marker. Hard
        # links fail instead of overwriting an externally-created path.
        for name in OUTPUT_FILES:
            source = temporary / name
            destination = output / name
            source_stat = source.stat(follow_symlinks=False)
            os.link(source, destination)
            published.append((destination, source_stat.st_dev, source_stat.st_ino))
            source.unlink()
        temporary.rmdir()
        return metrics
    except BaseException:
        for path, expected_device, expected_inode in reversed(published):
            try:
                actual = path.stat(follow_symlinks=False)
                if (
                    actual.st_dev == expected_device
                    and actual.st_ino == expected_inode
                ):
                    path.unlink()
            except FileNotFoundError:
                pass
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
        try:
            output.rmdir()
        except OSError:
            # Never remove an output directory containing paths not published
            # by this invocation.
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="train a two-objective primary-only temporal T2 value policy"
    )
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--random-seed", type=int, default=RANDOM_SEED)
    args = parser.parse_args(argv)
    dataset = load_temporal_dataset(args.dataset_dir)
    if args.audit_only:
        print(
            json.dumps(
                {
                    "dataset_dir": str(dataset.path),
                    "row_count": len(dataset.data.rows),
                    "split_counts": dataset.metadata["split"]["counts"],
                    "feature_family_counts": {
                        name: len(values)
                        for name, values in dataset.family_feature_names.items()
                    },
                    "status": "PASS",
                },
                sort_keys=True,
            )
        )
        return 0
    if args.output_dir is None:
        parser.error("--output-dir is required unless --audit-only is used")
    metrics = train_temporal_t2_value(
        dataset, args.output_dir, random_seed=args.random_seed
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "selected": {
                    key: metrics["selected_calibration_policy"][key]
                    for key in (
                        "feature_family",
                        "ranker",
                        "frame_gate",
                        "score_threshold",
                    )
                },
                "status": "PASS",
                "test_role_used_during_selection": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
