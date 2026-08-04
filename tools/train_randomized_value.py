#!/usr/bin/env python3
"""Train honest per-stage value models from randomized interventions.

The input is the artifact directory produced by
``build_randomized_intervention_dataset.py``.  Assignment, calibration, and
test roles are fixed by run before this program starts.  Within the training
role, four run-group folds provide out-of-fold nuisance predictions for
doubly robust (DR) benefit targets.  Candidate policies are selected only by
their calibration-set DR value and are reported once on the untouched test
set.

This first artifact deliberately trains T2 and untreated-wait-path T4 models
separately.  It does not claim that independently selected stage policies can
be composed without a fresh sequential replay.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import pickle
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from build_randomized_intervention_dataset import (
    DATASET_COLUMNS as BUILDER_DATASET_COLUMNS,
    DATASET_SCHEMA_VERSION as BUILDER_DATASET_SCHEMA_VERSION,
    FEATURE_COLUMNS as BUILDER_FEATURE_COLUMNS,
    FEATURE_CONTRACT_ID as BUILDER_FEATURE_CONTRACT_ID,
    NON_FEATURE_COLUMNS as BUILDER_NON_FEATURE_COLUMNS,
)


TRAINING_SCHEMA_VERSION = 1
DATASET_SCHEMA_VERSION = BUILDER_DATASET_SCHEMA_VERSION
FEATURE_CONTRACT_ID = BUILDER_FEATURE_CONTRACT_ID
SPLIT_ALGORITHM = "sha256_seed_run_exact_64_16_16_v1"
FOLD_ALGORITHM = "sha256_seed_run_round_robin_4fold_v1"
MODEL_BUNDLE_SCHEMA_VERSION = 1
MODEL_SELECTION = "maximum_calibration_dr_policy_value_v1"
FEATURE_ADAPTER_ID = "finite_numeric_float32_then_float64_one_hot_v1"
SCORE_ADAPTER_ID = "final_candidate_float32_threshold_ge_v1"

STAGES = ("T2", "T4")
ROLES = ("train", "calibration", "test")
TARGETS = (
    "deadline_miss",
    "bad_tail_12000us",
    "bad_tail_12500us",
)
FRAME_TYPES = ("I_FRAME", "P_FRAME")
POLICY_FRACTIONS = (0.0, 0.02, 0.05, 0.10, 0.15, 0.20)
CLUSTER_BOOTSTRAP_REPLICATIONS = 2000
CLUSTER_BOOTSTRAP_CONFIDENCE = 0.95
DEFAULT_MAX_DR_ASSIGNMENT_AIRTIME_US_PER_FRAME = 400.0

FEATURE_COLUMNS = tuple(BUILDER_FEATURE_COLUMNS)
NON_FEATURE_COLUMNS = tuple(BUILDER_NON_FEATURE_COLUMNS)
DATASET_COLUMNS = tuple(BUILDER_DATASET_COLUMNS)

COMPARISON_CONTRACTS: dict[str, dict[str, Any]] = {
    "T2": {
        "file": "randomized_t2.csv",
        "arms": ["CONTROL", "FULL_COPY_T2"],
        "population": "common_T2_eligible",
        "estimand": "binary_assignment_ITT",
    },
    "T4": {
        "file": "randomized_t4_wait.csv",
        "arms": ["CONTROL", "FULL_COPY_T4"],
        "population": "common_T2_eligible_and_primary_actionable_at_T4",
        "estimand": "untreated_wait_path_binary_assignment_ITT",
        "excludes": "all FULL_COPY_T2 post-treatment T4 rows",
    },
}

RANDOMIZED_INTERVENTION_CONTRACT: dict[str, Any] = {
    "arm_probabilities": {
        "CONTROL": 0.8,
        "FULL_COPY_T2": 0.08,
        "FULL_COPY_T4": 0.12,
    },
    "assignment_algorithm": "splitmix64_v1",
    "assignment_salt": 5927104639973545521,
    "assignment_stop_guard_us": 534000,
    "assignment_window_start_ns": 1000000000,
    "assignment_window_stop_ns": 60466000000,
    "common_eligibility_rule": (
        "T2_at_or_after_start_and_prospective_T4_before_stop_and_primary_"
        "actionable_and_canonical_secondary_descriptor_available"
    ),
    "cost_estimator_id": "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1",
    "csv_schema_version": 1,
    "intervention": "canonical_full_secondary_copy",
    "primary_copy_id": 0,
    "primary_path": 1,
    "randomization_consumes_ns3_rng": False,
    "secondary_copy_id": 1,
    "secondary_path": 0,
    "stage_offsets_us": [2000, 4000],
    "stages": ["T2", "T4"],
    "token_gate_enabled": False,
}

OUTPUT_FILES = (
    "value_models.pkl",
    "value_policy_candidates.csv",
    "value_training_metrics.json",
    "artifact_manifest.json",
)


class TrainingError(ValueError):
    """Raised when the causal dataset or training contract is ambiguous."""


@dataclass(frozen=True)
class Observation:
    """One analysis row with an immutable run-group identity."""

    run_id: str
    seed: int
    run_number: int
    split_role: str
    frame_id: int
    stage: str
    treatment: int
    propensity: float
    launched: int
    features: tuple[str, ...]
    outcomes: dict[str, float]

    @property
    def group(self) -> tuple[int, int]:
        """Return the randomized split unit."""

        return (self.seed, self.run_number)

    @property
    def key(self) -> tuple[int, int, int]:
        """Return a stable row identity."""

        return (self.seed, self.run_number, self.frame_id)


@dataclass(frozen=True)
class StageDataset:
    """One stage-specific binary randomized comparison."""

    stage: str
    feature_columns: tuple[str, ...]
    encoded_feature_names: tuple[str, ...]
    rows: tuple[Observation, ...]
    matrix: np.ndarray

    def indices(self, role: str) -> np.ndarray:
        """Return row indices belonging to one fixed split role."""

        return np.asarray(
            [index for index, row in enumerate(self.rows) if row.split_role == role],
            dtype=int,
        )


@dataclass(frozen=True)
class RandomizedDataset:
    """Strictly loaded builder artifacts."""

    path: Path
    metadata: dict[str, Any]
    manifest: dict[str, Any]
    feature_columns: tuple[str, ...]
    stages: dict[str, StageDataset]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise TrainingError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TrainingError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise TrainingError(f"{path}: expected a JSON object")
    return value


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            header = reader.fieldnames
            if header is None or len(header) != len(set(header)):
                raise TrainingError(f"{path}: missing or duplicate CSV columns")
            rows = list(reader)
    except OSError as error:
        raise TrainingError(f"cannot read {path}: {error}") from error
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise TrainingError(f"{path}: malformed CSV row")
    return list(header), rows


def _flag(row: dict[str, str], field: str, source: str) -> int:
    value = row.get(field)
    if value not in {"0", "1"}:
        raise TrainingError(f"{source}: invalid flag {field}")
    return int(value)


def _integer(row: dict[str, str], field: str, source: str) -> int:
    try:
        return int(row[field])
    except (KeyError, TypeError, ValueError) as error:
        raise TrainingError(f"{source}: invalid integer {field}") from error


def _number(row: dict[str, str], field: str, source: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as error:
        raise TrainingError(f"{source}: invalid number {field}") from error
    if not math.isfinite(value):
        raise TrainingError(f"{source}: non-finite number {field}")
    return value


def _split_counts(run_count: int) -> dict[str, int]:
    if run_count <= 0:
        raise TrainingError("dataset has no randomized run groups")
    quotas = [run_count * weight / 96 for weight in (64, 16, 16)]
    counts = [math.floor(quota) for quota in quotas]
    order = sorted(
        range(3), key=lambda index: (-(quotas[index] - counts[index]), index)
    )
    for index in order[: run_count - sum(counts)]:
        counts[index] += 1
    return dict(zip(ROLES, counts))


def expected_run_splits(
    groups: Sequence[tuple[int, int]],
) -> dict[tuple[int, int], str]:
    """Reproduce the builder's exact 64/16/16 run-group allocation."""

    if len(groups) != len(set(groups)):
        raise TrainingError("split metadata repeats a (seed, run_number) group")
    ordered = sorted(
        groups,
        key=lambda group: (
            hashlib.sha256(f"{group[0]}:{group[1]}".encode("ascii")).digest(),
            group,
        ),
    )
    counts = _split_counts(len(ordered))
    result: dict[tuple[int, int], str] = {}
    start = 0
    for role in ROLES:
        stop = start + counts[role]
        result.update((group, role) for group in ordered[start:stop])
        start = stop
    return result


def grouped_four_folds(
    groups: Iterable[tuple[int, int]],
) -> dict[tuple[int, int], int]:
    """Assign training run groups to deterministic four-fold cross-fitting."""

    unique = set(groups)
    if len(unique) < 4:
        raise TrainingError("four-fold nuisance fitting needs at least four train runs")
    ordered = sorted(
        unique,
        key=lambda group: (
            hashlib.sha256(f"fold:{group[0]}:{group[1]}".encode("ascii")).digest(),
            group,
        ),
    )
    return {group: index % 4 for index, group in enumerate(ordered)}


def _validate_metadata(
    metadata: dict[str, Any], manifest: dict[str, Any], dataset_dir: Path
) -> tuple[
    tuple[str, ...],
    dict[tuple[int, int], str],
    dict[str, float],
]:
    if metadata.get("dataset_schema_version") != DATASET_SCHEMA_VERSION:
        raise TrainingError("unsupported randomized dataset schema")
    if metadata.get("feature_contract_id") != FEATURE_CONTRACT_ID:
        raise TrainingError("randomized feature contract changed")
    feature_contract = metadata.get("feature_contract")
    if not isinstance(feature_contract, dict):
        raise TrainingError("dataset metadata has no feature contract")
    features = feature_contract.get("feature_columns")
    if features != list(FEATURE_COLUMNS):
        raise TrainingError("dataset feature allowlist differs from the builder contract")
    if feature_contract.get("categorical_features") != ["x_f0_frame_type"]:
        raise TrainingError("categorical feature contract changed")
    if metadata.get("non_feature_columns") != list(NON_FEATURE_COLUMNS):
        raise TrainingError("dataset non-feature columns differ from the builder contract")

    comparisons = metadata.get("comparisons")
    if not isinstance(comparisons, dict) or set(comparisons) != set(STAGES):
        raise TrainingError("dataset stage comparison set changed")
    for stage, contract in COMPARISON_CONTRACTS.items():
        comparison = comparisons.get(stage)
        if (
            not isinstance(comparison, dict)
            or set(comparison) != set(contract) | {"row_count"}
            or any(comparison.get(key) != value for key, value in contract.items())
            or not isinstance(comparison.get("row_count"), int)
            or isinstance(comparison.get("row_count"), bool)
            or comparison["row_count"] <= 0
        ):
            raise TrainingError(f"{stage}: exact randomized comparison contract changed")

    environment = metadata.get("environment_compatibility")
    projection = (
        environment.get("invariant_projection")
        if isinstance(environment, dict)
        else None
    )
    randomized = (
        projection.get("randomizedIntervention")
        if isinstance(projection, dict)
        else None
    )
    if randomized != RANDOMIZED_INTERVENTION_CONTRACT:
        raise TrainingError("randomized intervention design contract changed")
    arm_probabilities = RANDOMIZED_INTERVENTION_CONTRACT["arm_probabilities"]

    split = metadata.get("split")
    if not isinstance(split, dict):
        raise TrainingError("dataset split metadata is absent")
    if (
        split.get("algorithm") != SPLIT_ALGORITHM
        or split.get("unit") != "(seed, run_number)"
        or split.get("target_ratio") != {"train": 64, "calibration": 16, "test": 16}
    ):
        raise TrainingError("dataset run-group split contract changed")
    assignments = split.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise TrainingError("dataset split assignments are absent")
    observed: dict[tuple[int, int], str] = {}
    for item in assignments:
        if not isinstance(item, dict):
            raise TrainingError("malformed split assignment")
        seed = item.get("seed")
        run_number = item.get("run_number")
        role = item.get("split_role")
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not isinstance(run_number, int)
            or isinstance(run_number, bool)
            or role not in ROLES
        ):
            raise TrainingError("malformed split assignment")
        group = (seed, run_number)
        if group in observed:
            raise TrainingError("split metadata repeats a randomized run group")
        observed[group] = role
    expected = expected_run_splits(list(observed))
    if observed != expected:
        raise TrainingError("metadata does not contain the exact deterministic split")
    counts = {role: sum(value == role for value in observed.values()) for role in ROLES}
    if split.get("counts") != counts or counts != _split_counts(len(observed)):
        raise TrainingError("split counts disagree with assignments")

    if (
        manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
        or not isinstance(manifest.get("artifacts_sha256"), dict)
    ):
        raise TrainingError("dataset artifact manifest is invalid")
    expected_files = {
        "randomized_t2.csv",
        "randomized_t4_wait.csv",
        "dataset_metadata.json",
    }
    if set(manifest["artifacts_sha256"]) != expected_files:
        raise TrainingError("dataset artifact manifest file set changed")
    for name, expected_digest in manifest["artifacts_sha256"].items():
        if (
            not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or _sha256(dataset_dir / name) != expected_digest
        ):
            raise TrainingError(f"dataset artifact hash differs: {name}")
    return (
        tuple(features),
        observed,
        {name: float(value) for name, value in arm_probabilities.items()},
    )


def _outcome_fields(row: dict[str, str], source: str) -> dict[str, float]:
    deadline_bad = _flag(row, "outcome_deadline_miss", source)
    primary_deadline_bad = _flag(row, "outcome_primary_deadline_miss", source)
    deadline_rescue = _flag(row, "outcome_deadline_rescue", source)
    if deadline_rescue != int(primary_deadline_bad == 1 and deadline_bad == 0):
        raise TrainingError(f"{source}: deadline rescue arithmetic differs")
    result: dict[str, float] = {
        "deadline_miss": float(deadline_bad),
        "primary_bad_deadline_miss": float(primary_deadline_bad),
        "rescue_deadline_miss": float(deadline_rescue),
    }
    for threshold in (12000, 12500):
        union_good = _flag(row, f"outcome_complete_by_{threshold}us", source)
        primary_good = _flag(
            row, f"outcome_primary_complete_by_{threshold}us", source
        )
        rescue = _flag(row, f"outcome_tail_rescue_{threshold}us", source)
        expected_rescue = int(primary_good == 0 and union_good == 1)
        if rescue != expected_rescue:
            raise TrainingError(f"{source}: {threshold} us rescue arithmetic differs")
        target = f"bad_tail_{threshold}us"
        result[target] = float(1 - union_good)
        result[f"primary_bad_{target}"] = float(1 - primary_good)
        result[f"rescue_{target}"] = float(rescue)
    measured = _number(row, "outcome_secondary_airtime_us", source)
    if measured < 0:
        raise TrainingError(f"{source}: negative measured secondary airtime")
    result["measured_airtime_us"] = measured
    return result


def _encode_matrix(
    rows: Sequence[Observation], feature_columns: tuple[str, ...]
) -> tuple[np.ndarray, tuple[str, ...]]:
    categorical_index = feature_columns.index("x_f0_frame_type")
    encoded_names = tuple(
        name for index, name in enumerate(feature_columns) if index != categorical_index
    ) + tuple(f"x_f0_frame_type={name}" for name in FRAME_TYPES)
    matrix = np.empty((len(rows), len(encoded_names)), dtype=float)
    for row_index, row in enumerate(rows):
        output_index = 0
        for feature_index, value in enumerate(row.features):
            if feature_index == categorical_index:
                if value not in FRAME_TYPES:
                    raise TrainingError(
                        f"{row.stage} row {row.key}: unknown frame type {value!r}"
                    )
                continue
            if value == "":
                matrix[row_index, output_index] = np.nan
            else:
                try:
                    number = float(value)
                except ValueError as error:
                    raise TrainingError(
                        f"{row.stage} row {row.key}: non-numeric feature "
                        f"{feature_columns[feature_index]}"
                    ) from error
                if not math.isfinite(number):
                    raise TrainingError(
                        f"{row.stage} row {row.key}: non-finite feature "
                        f"{feature_columns[feature_index]}"
                    )
                with np.errstate(over="ignore"):
                    quantized = np.float32(number)
                if not np.isfinite(quantized):
                    raise TrainingError(
                        f"{row.stage} row {row.key}: float32-overflow feature "
                        f"{feature_columns[feature_index]}"
                    )
                # The runtime adapter stores every raw numeric input in float
                # before model evaluation.  Round here, then widen for sklearn,
                # so training/export cannot exploit unavailable precision.
                matrix[row_index, output_index] = float(quantized)
            output_index += 1
        frame_type = row.features[categorical_index]
        for category in FRAME_TYPES:
            matrix[row_index, output_index] = float(frame_type == category)
            output_index += 1
    return matrix, encoded_names


def _load_stage(
    dataset_dir: Path,
    stage: str,
    comparison: dict[str, Any],
    feature_columns: tuple[str, ...],
    splits: dict[tuple[int, int], str],
    expected_arm_probabilities: dict[str, float],
) -> StageDataset:
    contract = COMPARISON_CONTRACTS[stage]
    expected_file = contract["file"]
    expected_arms = contract["arms"]
    if comparison != {**contract, "row_count": comparison.get("row_count")}:
        raise TrainingError(f"{stage}: exact randomized comparison contract changed")
    header, source_rows = _read_csv(dataset_dir / expected_file)
    metadata = _read_json(dataset_dir / "dataset_metadata.json")
    if tuple(feature_columns) != FEATURE_COLUMNS or tuple(header) != DATASET_COLUMNS:
        raise TrainingError(f"{expected_file}: exact dataset schema differs")
    if len(source_rows) != comparison["row_count"] or not source_rows:
        raise TrainingError(f"{expected_file}: row count differs from metadata")

    observations: list[Observation] = []
    seen: set[tuple[int, int, int]] = set()
    run_groups: dict[str, tuple[int, int]] = {}
    conditional_propensity: float | None = None
    arm_assignment_propensities: dict[str, float] = {}
    for source_row in source_rows:
        seed = _integer(source_row, "seed", expected_file)
        run_number = _integer(source_row, "run_number", expected_file)
        frame_id = _integer(source_row, "frame_id", expected_file)
        group = (seed, run_number)
        key = (seed, run_number, frame_id)
        run_id = source_row.get("run_id", "")
        role = source_row.get("split_role")
        arm = source_row.get("assigned_arm")
        treatment = _flag(source_row, "treatment", expected_file)
        launched = _flag(source_row, "launched", expected_file)
        if (
            not run_id
            or group not in splits
            or role != splits[group]
            or source_row.get("analysis_stage") != stage
            or arm not in expected_arms
            or treatment != int(arm == f"FULL_COPY_{stage}")
        ):
            raise TrainingError(f"{expected_file}: row identity or arm contract differs")
        if key in seen:
            raise TrainingError(f"{expected_file}: duplicate row identity {key}")
        seen.add(key)
        if run_id in run_groups and run_groups[run_id] != group:
            raise TrainingError(f"{expected_file}: run_id maps to multiple groups")
        run_groups[run_id] = group

        propensity = _number(source_row, "treatment_probability", expected_file)
        assignment_propensity = _number(
            source_row, "assigned_arm_probability", expected_file
        )
        if not 0 < propensity < 1 or not 0 < assignment_propensity < 1:
            raise TrainingError(f"{expected_file}: invalid known propensity")
        if conditional_propensity is None:
            conditional_propensity = propensity
        elif not math.isclose(
            propensity, conditional_propensity, rel_tol=0.0, abs_tol=1e-15
        ):
            raise TrainingError(f"{expected_file}: treatment propensity varies")
        prior = arm_assignment_propensities.setdefault(arm, assignment_propensity)
        if not math.isclose(
            prior, assignment_propensity, rel_tol=0.0, abs_tol=1e-15
        ):
            raise TrainingError(f"{expected_file}: arm assignment propensity varies")

        outcomes = _outcome_fields(source_row, f"{expected_file}: row {key}")
        if treatment == 0 and (launched != 0 or outcomes["measured_airtime_us"] != 0):
            raise TrainingError(f"{expected_file}: control row launches or consumes airtime")
        if launched == 0 and outcomes["measured_airtime_us"] != 0:
            raise TrainingError(f"{expected_file}: unlaunched row consumes airtime")
        observations.append(
            Observation(
                run_id=run_id,
                seed=seed,
                run_number=run_number,
                split_role=str(role),
                frame_id=frame_id,
                stage=stage,
                treatment=treatment,
                propensity=propensity,
                launched=launched,
                features=tuple(source_row[name] for name in feature_columns),
                outcomes=outcomes,
            )
        )

    if set(arm_assignment_propensities) != set(expected_arms):
        raise TrainingError(f"{expected_file}: randomized arm support is incomplete")
    assert conditional_propensity is not None
    expected_stage_probabilities = {
        arm: expected_arm_probabilities[arm] for arm in expected_arms
    }
    if any(
        not math.isclose(
            arm_assignment_propensities[arm],
            probability,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
        for arm, probability in expected_stage_probabilities.items()
    ):
        raise TrainingError(f"{expected_file}: original arm propensity differs")
    implied = expected_stage_probabilities[f"FULL_COPY_{stage}"] / sum(
        expected_stage_probabilities.values()
    )
    if not math.isclose(
        implied, conditional_propensity, rel_tol=0.0, abs_tol=1e-15
    ):
        raise TrainingError(f"{expected_file}: conditional propensity arithmetic differs")

    observations.sort(key=lambda row: row.key)
    matrix, names = _encode_matrix(observations, feature_columns)
    return StageDataset(stage, feature_columns, names, tuple(observations), matrix)


def load_randomized_dataset(dataset_dir: Path | str) -> RandomizedDataset:
    """Load and strictly audit deterministic randomized dataset artifacts."""

    path = Path(dataset_dir).resolve()
    metadata = _read_json(path / "dataset_metadata.json")
    manifest = _read_json(path / "artifact_manifest.json")
    features, splits, arm_probabilities = _validate_metadata(metadata, manifest, path)
    comparisons = metadata.get("comparisons")
    if not isinstance(comparisons, dict) or set(comparisons) != set(STAGES):
        raise TrainingError("dataset stage comparison set changed")
    stages = {
        stage: _load_stage(
            path,
            stage,
            comparisons[stage],
            features,
            splits,
            arm_probabilities,
        )
        for stage in STAGES
    }
    groups_in_rows = {row.group for data in stages.values() for row in data.rows}
    if groups_in_rows != set(splits):
        raise TrainingError("dataset rows do not cover the split run groups exactly")
    return RandomizedDataset(path, metadata, manifest, features, stages)


def _ridge(alpha: float = 10.0) -> Pipeline:
    return Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median", add_indicator=True, keep_empty_features=True
                ),
            ),
            ("scale", StandardScaler()),
            ("regressor", Ridge(alpha=alpha)),
        ]
    )


def _compact_hgb(seed: int) -> Pipeline:
    return Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median", add_indicator=True, keep_empty_features=True
                ),
            ),
            (
                "regressor",
                HistGradientBoostingRegressor(
                    loss="squared_error",
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


def _fit(model: Pipeline, x: np.ndarray, y: np.ndarray, context: str) -> Pipeline:
    if len(x) == 0 or len(y) != len(x) or not np.all(np.isfinite(y)):
        raise TrainingError(f"{context}: invalid fit population")
    model.fit(x, y)
    return model


def _predict_probability(model: Pipeline, x: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(model.predict(x), dtype=float), 0.0, 1.0)


def _target(row: Observation, name: str) -> float:
    return row.outcomes[name]


def _primary_bad(row: Observation, name: str) -> float:
    return row.outcomes[f"primary_bad_{name}"]


def _rescue(row: Observation, name: str) -> float:
    return row.outcomes[f"rescue_{name}"]


def _fit_arm_nuisances(
    data: StageDataset, indices: np.ndarray, target_name: str
) -> tuple[Pipeline, Pipeline]:
    treatment = np.asarray([data.rows[index].treatment for index in indices])
    outcome = np.asarray([_target(data.rows[index], target_name) for index in indices])
    models: list[Pipeline] = []
    for arm in (0, 1):
        mask = treatment == arm
        if int(mask.sum()) == 0:
            raise TrainingError(
                f"{data.stage} {target_name}: nuisance fold has no arm {arm} rows"
            )
        models.append(
            _fit(
                _ridge(),
                data.matrix[indices[mask]],
                outcome[mask],
                f"{data.stage} {target_name} arm-{arm} nuisance",
            )
        )
    return models[0], models[1]


def _dr_benefit_pseudo_outcome(
    outcome: np.ndarray,
    treatment: np.ndarray,
    propensity: np.ndarray,
    mu0: np.ndarray,
    mu1: np.ndarray,
) -> np.ndarray:
    """Return the AIPW pseudo-outcome for benefit ``Y(0) - Y(1)``."""

    arrays = (outcome, treatment, propensity, mu0, mu1)
    if len({len(value) for value in arrays}) != 1:
        raise TrainingError("DR pseudo-outcome arrays have inconsistent lengths")
    if np.any((propensity <= 0) | (propensity >= 1)):
        raise TrainingError("DR pseudo-outcome has an invalid known propensity")
    return (
        mu0
        - mu1
        + (1 - treatment) * (outcome - mu0) / (1 - propensity)
        - treatment * (outcome - mu1) / propensity
    )


def _cross_fit_partitions(
    data: StageDataset, train_indices: np.ndarray
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    """Return held/fit row partitions with every run wholly in one fold."""

    fold_by_group = grouped_four_folds(data.rows[index].group for index in train_indices)
    partitions: list[tuple[np.ndarray, np.ndarray]] = []
    for fold in range(4):
        held = np.asarray(
            [
                index
                for index in train_indices
                if fold_by_group[data.rows[index].group] == fold
            ],
            dtype=int,
        )
        fitted = np.asarray(
            [
                index
                for index in train_indices
                if fold_by_group[data.rows[index].group] != fold
            ],
            dtype=int,
        )
        held_groups = {data.rows[index].group for index in held}
        fitted_groups = {data.rows[index].group for index in fitted}
        if not len(held) or held_groups & fitted_groups:
            raise TrainingError("cross-fitting did not keep randomized runs intact")
        partitions.append((held, fitted))
    return tuple(partitions)


def cross_fitted_dr_benefit(
    data: StageDataset, target_name: str
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return training-row DR benefit pseudo-outcomes using four run folds."""

    train_indices = data.indices("train")
    pseudo = np.empty(len(train_indices), dtype=float)
    mu0_all = np.empty(len(train_indices), dtype=float)
    mu1_all = np.empty(len(train_indices), dtype=float)
    positions = {int(index): position for position, index in enumerate(train_indices)}
    fold_rows: list[dict[str, Any]] = []
    for fold, (held, fitted) in enumerate(_cross_fit_partitions(data, train_indices)):
        model0, model1 = _fit_arm_nuisances(data, fitted, target_name)
        mu0 = _predict_probability(model0, data.matrix[held])
        mu1 = _predict_probability(model1, data.matrix[held])
        held_outcome = np.asarray(
            [_target(data.rows[index], target_name) for index in held]
        )
        held_treatment = np.asarray([data.rows[index].treatment for index in held])
        held_propensity = np.asarray([data.rows[index].propensity for index in held])
        held_pseudo = _dr_benefit_pseudo_outcome(
            held_outcome, held_treatment, held_propensity, mu0, mu1
        )
        for local, row_index in enumerate(held):
            position = positions[int(row_index)]
            # Positive values mean that assigning treatment reduces bad outcome.
            pseudo[position] = held_pseudo[local]
            mu0_all[position] = mu0[local]
            mu1_all[position] = mu1[local]
        fold_rows.append(
            {
                "fold": fold,
                "fit_run_count": len({data.rows[index].group for index in fitted}),
                "held_run_count": len({data.rows[index].group for index in held}),
                "held_row_count": len(held),
            }
        )
    outcome = np.asarray([_target(data.rows[index], target_name) for index in train_indices])
    treatment = np.asarray([data.rows[index].treatment for index in train_indices])
    observed_prediction = np.where(treatment == 1, mu1_all, mu0_all)
    return pseudo, {
        "algorithm": FOLD_ALGORITHM,
        "folds": fold_rows,
        "observed_outcome_mse": float(np.mean((outcome - observed_prediction) ** 2)),
        "pseudo_outcome_mean": float(np.mean(pseudo)),
        "pseudo_outcome_stddev": float(np.std(pseudo)),
    }


def _effect_audit(
    data: StageDataset, indices: np.ndarray, target_name: str
) -> dict[str, Any]:
    treatment = np.asarray([data.rows[index].treatment for index in indices])
    outcome = np.asarray([_target(data.rows[index], target_name) for index in indices])
    propensity = np.asarray([data.rows[index].propensity for index in indices])
    if len(indices) == 0 or set(treatment.tolist()) != {0, 1}:
        raise TrainingError(
            f"{data.stage} {target_name}: split lacks both randomized arms"
        )
    simple = float(np.mean(outcome[treatment == 0]) - np.mean(outcome[treatment == 1]))
    ht = float(
        np.mean(
            (1 - treatment) * outcome / (1 - propensity)
            - treatment * outcome / propensity
        )
    )
    return {
        "row_count": len(indices),
        "run_count": len({data.rows[index].group for index in indices}),
        "control_count": int(np.sum(treatment == 0)),
        "treatment_count": int(np.sum(treatment == 1)),
        "known_treatment_probability": float(propensity[0]),
        "simple_difference_benefit": simple,
        "horvitz_thompson_benefit": ht,
    }


def _duan_smearing_factor(
    observed_log1p_cost: np.ndarray, predicted_log1p_cost: np.ndarray
) -> float:
    """Return Duan's retransformation factor for a log-cost regression."""

    if len(observed_log1p_cost) == 0 or len(observed_log1p_cost) != len(
        predicted_log1p_cost
    ):
        raise TrainingError("log-cost residual arrays have inconsistent lengths")
    residual = observed_log1p_cost - predicted_log1p_cost
    if not np.all(np.isfinite(residual)):
        raise TrainingError("log-cost residual is non-finite")
    return float(np.mean(np.exp(residual)))


def _expected_assignment_cost(
    launch_probability: np.ndarray,
    predicted_log1p_cost_given_launch: np.ndarray,
    smearing_factor: float,
) -> np.ndarray:
    """Return expected cost per assignment from the fitted hurdle model."""

    if len(launch_probability) != len(predicted_log1p_cost_given_launch):
        raise TrainingError("assignment-cost arrays have inconsistent lengths")
    if not math.isfinite(smearing_factor) or smearing_factor <= 0:
        raise TrainingError("assignment-cost smearing factor is invalid")
    launch = np.clip(np.asarray(launch_probability, dtype=float), 0.0, 1.0)
    conditional = np.maximum(
        np.exp(np.asarray(predicted_log1p_cost_given_launch, dtype=float))
        * smearing_factor
        - 1.0,
        0.0,
    )
    return launch * conditional


def _assignment_cost_dr(
    observed_cost: np.ndarray,
    treatment: np.ndarray,
    propensity: np.ndarray,
    predicted_assignment_cost: np.ndarray,
) -> np.ndarray:
    """Return the known-propensity AIPW cost under treatment assignment."""

    arrays = (observed_cost, treatment, propensity, predicted_assignment_cost)
    if len({len(value) for value in arrays}) != 1:
        raise TrainingError("assignment-cost DR arrays have inconsistent lengths")
    if np.any((propensity <= 0) | (propensity >= 1)):
        raise TrainingError("assignment-cost DR has an invalid propensity")
    return predicted_assignment_cost + treatment * (
        observed_cost - predicted_assignment_cost
    ) / propensity


def _fit_components(
    data: StageDataset, target_name: str, pseudo: np.ndarray, seed: int
) -> dict[str, Any]:
    train = data.indices("train")
    x = data.matrix[train]
    treatment = np.asarray([data.rows[index].treatment for index in train])
    launched = np.asarray([data.rows[index].launched for index in train])
    primary_bad = np.asarray([_primary_bad(data.rows[index], target_name) for index in train])
    rescue = np.asarray([_rescue(data.rows[index], target_name) for index in train])
    cost = np.asarray(
        [data.rows[index].outcomes["measured_airtime_us"] for index in train]
    )

    control = treatment == 0
    treated_launch = (treatment == 1) & (launched == 1)
    conditional = treated_launch & (primary_bad == 1)
    if not np.any(control) or not np.any(conditional) or not np.any(treated_launch):
        raise TrainingError(
            f"{data.stage} {target_name}: mechanistic fit population is empty"
        )
    if np.any(cost[treated_launch] <= 0):
        raise TrainingError(
            f"{data.stage} {target_name}: launched treatment has non-positive cost"
        )

    log_cost = np.log1p(cost[treated_launch])
    log_cost_model = _fit(
        _ridge(),
        x[treated_launch],
        log_cost,
        "launched conditional log measured-cost head",
    )
    smearing_factor = _duan_smearing_factor(
        log_cost,
        np.asarray(log_cost_model.predict(x[treated_launch]), dtype=float),
    )
    components: dict[str, Any] = {
        "ridge_dr": _fit(_ridge(), x, pseudo, "ridge DR effect"),
        "hgb_dr": _fit(_compact_hgb(seed), x, pseudo, "HGB DR effect"),
        "primary_need": _fit(
            _ridge(), x[control], primary_bad[control], "control primary-need head"
        ),
        "conditional_rescue": _fit(
            _ridge(),
            x[conditional],
            rescue[conditional],
            "treated primary-bad conditional-rescue head",
        ),
        "direct_realized_rescue": _fit(
            _compact_hgb(seed + 1),
            x[treated_launch],
            rescue[treated_launch],
            "all-treated exact realized-rescue head",
        ),
        "assignment_launch_probability": _fit(
            _ridge(),
            x[treatment == 1],
            launched[treatment == 1],
            "treated assignment-to-launch head",
        ),
        "log_measured_cost_given_launch": log_cost_model,
        "log_cost_smearing_factor": smearing_factor,
    }
    components["fit_counts"] = {
        "control_primary_need": int(np.sum(control)),
        "treated_assigned": int(np.sum(treatment == 1)),
        "treated_launched": int(np.sum(treated_launch)),
        "treated_launched_primary_bad": int(np.sum(conditional)),
        "factual_rescue_count_treated_launched": int(np.sum(rescue[treated_launch])),
    }
    return components


CANDIDATE_SPECS: dict[str, dict[str, str]] = {
    "ridge_dr": {"kind": "dr_effect", "head": "ridge_dr"},
    "compact_hgb_dr": {"kind": "dr_effect", "head": "hgb_dr"},
    "mechanistic_need_x_conditional_rescue": {
        "kind": "mechanistic",
        "score": (
            "assignment_launch_probability_times_control_primary_need_times_"
            "treated_launched_primary_bad_conditional_rescue"
        ),
    },
    "direct_realized_rescue": {
        "kind": "direct_rescue",
        "score": (
            "assignment_launch_probability_times_exact_factual_rescue_head_"
            "fit_on_all_launched_treatments"
        ),
    },
    "compact_hgb_dr_per_predicted_airtime": {
        "kind": "effect_per_cost",
        "cost": "assignment_launch_probability_times_smeared_inverse_log1p_cost",
    },
    "mechanistic_rescue_per_predicted_airtime": {
        "kind": "mechanistic_per_cost",
        "cost": "assignment_launch_probability_times_smeared_inverse_log1p_cost",
    },
}


def _candidate_scores(
    components: dict[str, Any], x: np.ndarray
) -> dict[str, np.ndarray]:
    ridge = np.asarray(components["ridge_dr"].predict(x), dtype=float)
    hgb = np.asarray(components["hgb_dr"].predict(x), dtype=float)
    need = _predict_probability(components["primary_need"], x)
    conditional = _predict_probability(components["conditional_rescue"], x)
    direct = _predict_probability(components["direct_realized_rescue"], x)
    launch_probability = _predict_probability(
        components["assignment_launch_probability"], x
    )
    expected_assignment_cost = _expected_assignment_cost(
        launch_probability,
        np.asarray(
            components["log_measured_cost_given_launch"].predict(x), dtype=float
        ),
        components["log_cost_smearing_factor"],
    )
    cost_denominator = np.maximum(expected_assignment_cost, 1.0)
    mechanistic_assignment_benefit = launch_probability * need * conditional
    raw_scores = {
        "ridge_dr": ridge,
        "compact_hgb_dr": hgb,
        "mechanistic_need_x_conditional_rescue": mechanistic_assignment_benefit,
        "direct_realized_rescue": launch_probability * direct,
        # The DR numerator is already an assignment ITT.  The mechanistic
        # numerator is explicitly converted from launch-conditional rescue to
        # assignment benefit above.  Both divide by assignment-level cost.
        "compact_hgb_dr_per_predicted_airtime": hgb / cost_denominator,
        "mechanistic_rescue_per_predicted_airtime": (
            mechanistic_assignment_benefit / cost_denominator
        ),
    }
    return {
        name: _quantize_candidate_scores(values)
        for name, values in raw_scores.items()
    }


def _full_nuisance_predictions(
    data: StageDataset,
    fit_indices: np.ndarray,
    predict_indices: np.ndarray,
    target_name: str,
) -> tuple[np.ndarray, np.ndarray]:
    model0, model1 = _fit_arm_nuisances(data, fit_indices, target_name)
    return (
        _predict_probability(model0, data.matrix[predict_indices]),
        _predict_probability(model1, data.matrix[predict_indices]),
    )


def _dr_components(
    data: StageDataset,
    indices: np.ndarray,
    target_name: str,
    mu0: np.ndarray,
    mu1: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    treatment = np.asarray([data.rows[index].treatment for index in indices])
    outcome = np.asarray([_target(data.rows[index], target_name) for index in indices])
    propensity = np.asarray([data.rows[index].propensity for index in indices])
    phi0 = mu0 + (1 - treatment) * (outcome - mu0) / (1 - propensity)
    phi1 = mu1 + treatment * (outcome - mu1) / propensity
    return phi0, phi1


def _cost_dr_component(
    data: StageDataset,
    indices: np.ndarray,
    components: dict[str, Any],
) -> np.ndarray:
    treatment = np.asarray([data.rows[index].treatment for index in indices])
    propensity = np.asarray([data.rows[index].propensity for index in indices])
    observed = np.asarray(
        [data.rows[index].outcomes["measured_airtime_us"] for index in indices]
    )
    launch_probability = _predict_probability(
        components["assignment_launch_probability"], data.matrix[indices]
    )
    predicted = _expected_assignment_cost(
        launch_probability,
        np.asarray(
            components["log_measured_cost_given_launch"].predict(
                data.matrix[indices]
            ),
            dtype=float,
        ),
        components["log_cost_smearing_factor"],
    )
    return _assignment_cost_dr(observed, treatment, propensity, predicted)


def _quantize_candidate_scores(scores: np.ndarray) -> np.ndarray:
    """Round final candidate scores to the runtime/export float32 boundary."""

    values = np.asarray(scores, dtype=float)
    if not np.all(np.isfinite(values)):
        raise TrainingError("candidate score is non-finite before float32 adaptation")
    with np.errstate(over="ignore", invalid="ignore"):
        quantized = values.astype(np.float32)
    if not np.all(np.isfinite(quantized)):
        raise TrainingError("candidate score overflows the float32 adapter")
    return quantized.astype(float)


def _threshold_for_fraction(scores: np.ndarray, fraction: float) -> float:
    if fraction == 0:
        return math.inf
    quantized = _quantize_candidate_scores(scores)
    threshold = np.quantile(quantized, 1.0 - fraction, method="higher")
    return float(np.float32(threshold))


def _apply_score_threshold(scores: np.ndarray, threshold: float) -> np.ndarray:
    """Apply a frozen calibration threshold without re-estimating a quantile."""

    if math.isinf(threshold):
        return np.zeros(len(scores), dtype=bool)
    quantized_scores = _quantize_candidate_scores(scores)
    quantized_threshold = _quantize_candidate_scores(np.asarray([threshold]))[0]
    return np.asarray(quantized_scores >= quantized_threshold, dtype=bool)


def _policy_record(
    *,
    stage: str,
    target_name: str,
    candidate: str,
    requested_fraction: float,
    scores: np.ndarray,
    phi0: np.ndarray,
    phi1: np.ndarray,
    cost_phi1: np.ndarray,
) -> dict[str, Any]:
    threshold = _threshold_for_fraction(scores, requested_fraction)
    policy = _apply_score_threshold(scores, threshold)
    benefit = float(np.mean(policy * (phi0 - phi1)))
    cost = float(np.mean(policy * cost_phi1))
    return {
        "stage": stage,
        "target": target_name,
        "candidate": candidate,
        "requested_treatment_fraction": requested_fraction,
        "score_threshold": threshold,
        "realized_treatment_fraction": float(np.mean(policy)),
        "dr_treat_none_bad_outcome": float(np.mean(phi0)),
        "dr_policy_bad_outcome": float(np.mean(phi0) - benefit),
        "dr_policy_benefit": benefit,
        "dr_policy_measured_airtime_us_per_frame": cost,
    }


def _linear_percentile(values: Sequence[float], probability: float) -> float:
    """Return a deterministic linearly interpolated sample percentile."""

    if not values or not 0 <= probability <= 1:
        raise TrainingError("invalid bootstrap percentile input")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _cluster_seed(base_seed: int, context: str) -> int:
    digest = hashlib.sha256(f"{base_seed}:{context}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big")


def _cluster_bootstrap_policy_uncertainty(
    data: StageDataset,
    indices: np.ndarray,
    policy: np.ndarray,
    phi0: np.ndarray,
    phi1: np.ndarray,
    cost_phi1: np.ndarray,
    *,
    seed: int,
    context: str,
) -> dict[str, Any]:
    """Bootstrap policy estimands by resampling whole randomized run groups."""

    if not (
        len(indices)
        == len(policy)
        == len(phi0)
        == len(phi1)
        == len(cost_phi1)
    ):
        raise TrainingError("cluster bootstrap arrays have inconsistent lengths")
    benefit = policy * (phi0 - phi1)
    bad_outcome = phi0 - benefit
    airtime = policy * cost_phi1
    contributions = {
        "dr_policy_benefit": benefit,
        "dr_policy_bad_outcome": bad_outcome,
        "dr_policy_measured_airtime_us_per_frame": airtime,
    }
    positions_by_group: dict[tuple[int, int], list[int]] = {}
    for position, index in enumerate(indices):
        positions_by_group.setdefault(data.rows[index].group, []).append(position)
    groups = sorted(positions_by_group)
    if not groups:
        raise TrainingError("cluster bootstrap has no run groups")
    group_summaries = {
        name: [
            (float(np.sum(values[positions_by_group[group]])), len(positions_by_group[group]))
            for group in groups
        ]
        for name, values in contributions.items()
    }
    rng = random.Random(_cluster_seed(seed, context))
    replicates = {name: [] for name in contributions}
    for _ in range(CLUSTER_BOOTSTRAP_REPLICATIONS):
        sampled = [rng.randrange(len(groups)) for _ in groups]
        for name, summaries in group_summaries.items():
            total = sum(summaries[index][0] for index in sampled)
            count = sum(summaries[index][1] for index in sampled)
            replicates[name].append(total / count)
    alpha = (1.0 - CLUSTER_BOOTSTRAP_CONFIDENCE) / 2.0
    return {
        "unit": "(seed, run_number)",
        "method": "deterministic_run_cluster_percentile_bootstrap_v1",
        "evidence_role": (
            "locked_heldout_after_calibration_selection"
            if context.endswith(":test")
            else "selection_reused_descriptive"
        ),
        "random_generator": "python_mt19937_with_sha256_context_seed",
        "replications": CLUSTER_BOOTSTRAP_REPLICATIONS,
        "confidence_level": CLUSTER_BOOTSTRAP_CONFIDENCE,
        "run_count": len(groups),
        "estimands": {
            name: {
                "estimate": float(np.mean(values)),
                "ci_lower": _linear_percentile(replicates[name], alpha),
                "ci_upper": _linear_percentile(replicates[name], 1.0 - alpha),
            }
            for name, values in contributions.items()
        },
    }


def _finite_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if isinstance(value, np.generic):
        return _finite_json(value.item())
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    return value


def _write_json(path: Path, value: Any) -> None:
    try:
        path.write_text(
            json.dumps(
                _finite_json(value),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise TrainingError(f"cannot write {path}: {error}") from error


def _write_candidate_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = (
        "stage",
        "target",
        "candidate",
        "requested_treatment_fraction",
        "score_threshold",
        "realized_treatment_fraction",
        "dr_treat_none_bad_outcome",
        "dr_policy_bad_outcome",
        "dr_policy_benefit",
        "dr_policy_measured_airtime_us_per_frame",
        "admissible",
        "rejection_reason",
        "selected",
    )
    try:
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        field: (
                            format(row[field], ".17g")
                            if isinstance(row[field], float)
                            else row[field]
                        )
                        for field in fields
                    }
                )
    except OSError as error:
        raise TrainingError(f"cannot write {path}: {error}") from error


def _repository_provenance() -> dict[str, Any]:
    """Return exact trainer identity plus best-effort repository provenance."""

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

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    source_status = git(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        str(source.relative_to(repository)),
    )
    return {
        "trainer_source_path": str(source.relative_to(repository)),
        "trainer_source_sha256": _sha256(source),
        "repository_git_commit": commit,
        "repository_status_available": status is not None,
        "repository_dirty": None if status is None else bool(status),
        "trainer_source_dirty_or_untracked": (
            None if source_status is None else bool(source_status)
        ),
    }


def _select_policy_record(
    records: Sequence[dict[str, Any]],
    *,
    max_treatment_fraction: float,
    max_dr_assignment_airtime_us_per_frame: float | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Mark calibration candidates and choose the best admissible DR value."""

    audited: list[dict[str, Any]] = []
    admissible: list[dict[str, Any]] = []
    for source in records:
        record = dict(source)
        reasons: list[str] = []
        if record["realized_treatment_fraction"] > max_treatment_fraction + 1e-12:
            reasons.append("treatment_fraction")
        if (
            max_dr_assignment_airtime_us_per_frame is not None
            and record["dr_policy_measured_airtime_us_per_frame"]
            > max_dr_assignment_airtime_us_per_frame + 1e-12
        ):
            reasons.append("assignment_airtime")
        record["admissible"] = int(not reasons)
        record["rejection_reason"] = "+".join(reasons)
        record["selected"] = 0
        audited.append(record)
        if not reasons:
            admissible.append(record)
    if not admissible:
        raise TrainingError("no policy candidate satisfies the prespecified constraints")
    selected = max(
        admissible,
        key=lambda item: (
            item["dr_policy_benefit"],
            -item["dr_policy_measured_airtime_us_per_frame"],
            -item["realized_treatment_fraction"],
            item["candidate"],
            -item["requested_treatment_fraction"],
        ),
    )
    selected["selected"] = 1
    return selected, audited


def train_value_models(
    dataset: RandomizedDataset,
    output_dir: Path | str,
    *,
    random_seed: int = 20260804,
    max_treatment_fraction: float = 0.20,
    max_dr_assignment_airtime_us_per_frame: float | None = (
        DEFAULT_MAX_DR_ASSIGNMENT_AIRTIME_US_PER_FRAME
    ),
) -> dict[str, Any]:
    """Train, select, and test separate T2 and T4 value policies."""

    if not 0 < max_treatment_fraction <= 1:
        raise TrainingError("max treatment fraction must be in (0, 1]")
    if (
        max_dr_assignment_airtime_us_per_frame is not None
        and (
            not math.isfinite(max_dr_assignment_airtime_us_per_frame)
            or max_dr_assignment_airtime_us_per_frame <= 0
        )
    ):
        raise TrainingError("DR assignment-airtime ceiling must be positive or None")
    split_counts = dataset.metadata["split"]["counts"]
    if (
        split_counts["train"] < 4
        or split_counts["calibration"] < 1
        or split_counts["test"] < 1
    ):
        raise TrainingError(
            "honest training needs at least four train runs and nonempty "
            "calibration/test run splits"
        )
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    existing = [name for name in OUTPUT_FILES if (output / name).exists()]
    if existing:
        raise TrainingError(
            "refusing to overwrite existing training artifacts: " + ", ".join(existing)
        )

    fractions = tuple(
        fraction
        for fraction in POLICY_FRACTIONS
        if fraction == 0 or fraction <= max_treatment_fraction + 1e-15
    )
    if fractions == (0.0,):
        fractions = (0.0, max_treatment_fraction)
    elif fractions[-1] < max_treatment_fraction:
        fractions += (max_treatment_fraction,)

    bundle: dict[str, Any] = {
        "model_bundle_schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "training_schema_version": TRAINING_SCHEMA_VERSION,
        "feature_contract_id": FEATURE_CONTRACT_ID,
        "feature_adapter_id": FEATURE_ADAPTER_ID,
        "feature_adapter_contract": {
            "finite_numeric": "parse decimal, round to IEEE-754 float32, widen to float64",
            "missing_numeric": "NaN before fit-only imputation",
            "categorical": "exact I_FRAME/P_FRAME one-hot",
        },
        "score_adapter_id": SCORE_ADAPTER_ID,
        "score_comparator": "float32 score >= float32 threshold",
        "selection_contract": {
            "maximum_treatment_fraction": max_treatment_fraction,
            "maximum_calibration_dr_assignment_airtime_us_per_frame": (
                max_dr_assignment_airtime_us_per_frame
            ),
            "campaign_primary_stage": "T2",
            "campaign_primary_target": "bad_tail_12000us",
        },
        "feature_columns": dataset.feature_columns,
        "encoded_feature_names": next(iter(dataset.stages.values())).encoded_feature_names,
        "candidate_specs": CANDIDATE_SPECS,
        "stages": {},
    }
    metrics: dict[str, Any] = {
        "training_schema_version": TRAINING_SCHEMA_VERSION,
        "evidence_status": "run_group_heldout_randomized_engineering_evidence",
        "provenance": _repository_provenance(),
        "dataset_dir": str(dataset.path),
        "dataset_artifacts_sha256": dataset.manifest["artifacts_sha256"],
        "feature_adapter": {
            "adapter_id": FEATURE_ADAPTER_ID,
            "finite_numeric": "float32 quantization before float64 sklearn matrix",
            "one_hot_values": "exact 0/1",
        },
        "score_adapter": {
            "adapter_id": SCORE_ADAPTER_ID,
            "model_arithmetic": "candidate may compute in float64",
            "decision_boundary": (
                "round final candidate score and calibration threshold to "
                "IEEE-754 float32, widen exactly, then compare score >= threshold"
            ),
            "test_threshold_source": "frozen calibration float32 threshold",
        },
        "split": dataset.metadata["split"],
        "nuisance_cross_fitting": {
            "fold_count": 4,
            "unit": "(seed, run_number)",
            "algorithm": FOLD_ALGORITHM,
        },
        "model_selection": {
            "criterion": MODEL_SELECTION,
            "role": "calibration",
            "maximum_requested_treatment_fraction": max_treatment_fraction,
            "maximum_calibration_dr_assignment_airtime_us_per_frame": (
                max_dr_assignment_airtime_us_per_frame
            ),
            "airtime_constraint_estimator": (
                "calibration known-propensity DR mean assignment cost"
            ),
            "candidate_treatment_fractions": list(fractions),
            "test_role_used_during_selection": False,
        },
        "cluster_uncertainty": {
            "unit": "(seed, run_number)",
            "method": "deterministic_run_cluster_percentile_bootstrap_v1",
            "replications": CLUSTER_BOOTSTRAP_REPLICATIONS,
            "confidence_level": CLUSTER_BOOTSTRAP_CONFIDENCE,
            "selection_use": False,
        },
        "assignment_cost_model": {
            "estimand": "expected_measured_secondary_airtime_per_assignment",
            "hurdle_probability": "P(launch | randomized treatment assignment, X)",
            "positive_cost_head": "E[log1p(measured_airtime_us) | launch, X]",
            "score_cost": (
                "launch probability times Duan-smearing-corrected inverse-log "
                "conditional cost; "
                "assigned nonlaunches therefore have factual zero cost"
            ),
            "dr_evaluation": (
                "known-propensity correction of observed assignment cost, including zeros"
            ),
        },
        "assignment_benefit_models": {
            "dr_heads": "assignment ITT benefit directly",
            "mechanistic": (
                "P(launch|assignment,X) * P(primary bad|control,X) * "
                "P(rescue|launched treatment,primary bad,X)"
            ),
            "direct_rescue": (
                "P(launch|assignment,X) * P(factual rescue|launched treatment,X)"
            ),
            "per_cost_denominator": (
                "P(launch|assignment,X) * E(measured airtime|launch,X), "
                "floored at 1 us only to stabilize division"
            ),
        },
        "outcomes": {
            "deadline_miss": "unconditional; incomplete frames are misses",
            "bad_tail_12000us": (
                "unconditional not-complete-by-12000us; incomplete frames are bad"
            ),
            "bad_tail_12500us": (
                "unconditional not-complete-by-12500us; incomplete frames are bad"
            ),
        },
        "campaign_primary_objective": {
            "stage": "T2",
            "target": "bad_tail_12000us",
            "definition": "unconditional completion later than 12000us or incomplete",
            "final_evidence": "fresh closed-loop confirmation seeds",
        },
        "sequential_policy_replay": (
            "deferred; independently selected T2/T4 heads are not a combined policy"
        ),
        "random_seed": random_seed,
        "software": {
            "python": sys.version.split()[0],
            "numpy": importlib.metadata.version("numpy"),
            "scikit_learn": importlib.metadata.version("scikit-learn"),
        },
        "stages": {},
    }
    candidate_rows: list[dict[str, Any]] = []

    for stage_index, stage in enumerate(STAGES):
        data = dataset.stages[stage]
        train = data.indices("train")
        calibration = data.indices("calibration")
        test = data.indices("test")
        if any(len(indices) == 0 for indices in (train, calibration, test)):
            raise TrainingError(f"{stage}: one run-group split has no analysis rows")
        stage_bundle: dict[str, Any] = {"targets": {}}
        stage_metrics: dict[str, Any] = {"targets": {}}
        for target_index, target_name in enumerate(TARGETS):
            audits = {
                role: _effect_audit(data, data.indices(role), target_name)
                for role in ROLES
            }
            pseudo, crossfit = cross_fitted_dr_benefit(data, target_name)
            components = _fit_components(
                data,
                target_name,
                pseudo,
                random_seed + stage_index * 100 + target_index * 10,
            )
            fit_counts = components.pop("fit_counts")
            calibration_mu0, calibration_mu1 = _full_nuisance_predictions(
                data, train, calibration, target_name
            )
            calibration_phi0, calibration_phi1 = _dr_components(
                data,
                calibration,
                target_name,
                calibration_mu0,
                calibration_mu1,
            )
            calibration_cost = _cost_dr_component(data, calibration, components)
            scores = _candidate_scores(components, data.matrix[calibration])
            calibration_records: list[dict[str, Any]] = []
            for candidate in sorted(scores):
                for fraction in fractions:
                    record = _policy_record(
                        stage=stage,
                        target_name=target_name,
                        candidate=candidate,
                        requested_fraction=fraction,
                        scores=scores[candidate],
                        phi0=calibration_phi0,
                        phi1=calibration_phi1,
                        cost_phi1=calibration_cost,
                    )
                    calibration_records.append(record)
            selected, audited_records = _select_policy_record(
                calibration_records,
                max_treatment_fraction=max_treatment_fraction,
                max_dr_assignment_airtime_us_per_frame=(
                    max_dr_assignment_airtime_us_per_frame
                ),
            )
            selected_threshold = selected["score_threshold"]
            calibration_policy = _apply_score_threshold(
                scores[selected["candidate"]], selected_threshold
            )
            calibration_uncertainty = _cluster_bootstrap_policy_uncertainty(
                data,
                calibration,
                calibration_policy,
                calibration_phi0,
                calibration_phi1,
                calibration_cost,
                seed=random_seed,
                context=f"{stage}:{target_name}:calibration",
            )
            for record in audited_records:
                candidate_rows.append(record)

            test_mu0, test_mu1 = _full_nuisance_predictions(
                data, train, test, target_name
            )
            test_phi0, test_phi1 = _dr_components(
                data, test, target_name, test_mu0, test_mu1
            )
            test_cost = _cost_dr_component(data, test, components)
            test_scores = _candidate_scores(components, data.matrix[test])[
                selected["candidate"]
            ]
            threshold = selected["score_threshold"]
            test_policy = _apply_score_threshold(test_scores, threshold)
            test_benefit = float(np.mean(test_policy * (test_phi0 - test_phi1)))
            test_uncertainty = _cluster_bootstrap_policy_uncertainty(
                data,
                test,
                test_policy,
                test_phi0,
                test_phi1,
                test_cost,
                seed=random_seed,
                context=f"{stage}:{target_name}:test",
            )
            test_result = {
                "row_count": len(test),
                "run_count": len({data.rows[index].group for index in test}),
                "realized_treatment_fraction": float(np.mean(test_policy)),
                "dr_treat_none_bad_outcome": float(np.mean(test_phi0)),
                "dr_policy_bad_outcome": float(np.mean(test_phi0) - test_benefit),
                "dr_policy_benefit": test_benefit,
                "dr_policy_measured_airtime_us_per_frame": float(
                    np.mean(test_policy * test_cost)
                ),
                "run_cluster_uncertainty": test_uncertainty,
            }
            stage_bundle["targets"][target_name] = {
                "components": components,
                "selected_candidate": selected["candidate"],
                "score_threshold": selected["score_threshold"],
                "requested_treatment_fraction": selected[
                    "requested_treatment_fraction"
                ],
                "selection_role": "calibration",
            }
            stage_metrics["targets"][target_name] = {
                "effect_audits": audits,
                "cross_fitted_nuisance": crossfit,
                "mechanistic_fit_counts": fit_counts,
                "selected_calibration_policy": {
                    **{
                        key: value
                        for key, value in selected.items()
                        if key != "selected"
                    },
                    "run_cluster_uncertainty": calibration_uncertainty,
                },
                "heldout_test_policy": test_result,
            }
        bundle["stages"][stage] = stage_bundle
        metrics["stages"][stage] = stage_metrics

    candidate_rows.sort(
        key=lambda row: (
            row["stage"],
            row["target"],
            row["candidate"],
            row["requested_treatment_fraction"],
        )
    )
    try:
        with (output / "value_models.pkl").open("wb") as destination:
            pickle.dump(bundle, destination, protocol=4)
    except OSError as error:
        raise TrainingError(f"cannot write value model bundle: {error}") from error
    _write_candidate_csv(output / "value_policy_candidates.csv", candidate_rows)
    _write_json(output / "value_training_metrics.json", metrics)
    manifest = {
        "manifest_schema_version": 1,
        "hash_algorithm": "sha256",
        "artifacts_sha256": {
            name: _sha256(output / name)
            for name in (
                "value_models.pkl",
                "value_policy_candidates.csv",
                "value_training_metrics.json",
            )
        },
    }
    _write_json(output / "artifact_manifest.json", manifest)
    return metrics


def _positive_float_or_none(value: str) -> float | None:
    if value.lower() == "none":
        return None
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a positive number or 'none'") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive number or 'none'")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="train held-out per-stage value models from randomized data"
    )
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--random-seed", type=int, default=20260804)
    parser.add_argument("--max-treatment-fraction", type=float, default=0.20)
    parser.add_argument(
        "--max-dr-assignment-airtime-us-per-frame",
        type=_positive_float_or_none,
        default=DEFAULT_MAX_DR_ASSIGNMENT_AIRTIME_US_PER_FRAME,
        help=(
            "calibration DR assignment-cost ceiling (default: 400 us); "
            "pass 'none' only to explicitly disable it"
        ),
    )
    args = parser.parse_args(argv)
    dataset = load_randomized_dataset(args.dataset_dir)
    if args.audit_only:
        print(
            json.dumps(
                {
                    "dataset_dir": str(dataset.path),
                    "run_count": len(dataset.metadata["split"]["assignments"]),
                    "split_counts": dataset.metadata["split"]["counts"],
                    "stage_rows": {
                        stage: len(data.rows) for stage, data in dataset.stages.items()
                    },
                    "status": "PASS",
                },
                sort_keys=True,
            )
        )
        return 0
    if args.output_dir is None:
        parser.error("--output-dir is required unless --audit-only is used")
    metrics = train_value_models(
        dataset,
        args.output_dir,
        random_seed=args.random_seed,
        max_treatment_fraction=args.max_treatment_fraction,
        max_dr_assignment_airtime_us_per_frame=(
            args.max_dr_assignment_airtime_us_per_frame
        ),
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "status": "PASS",
                "test_role_used_during_selection": metrics["model_selection"][
                    "test_role_used_during_selection"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
