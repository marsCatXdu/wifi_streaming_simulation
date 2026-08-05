#!/usr/bin/env python3
"""Cross-fit counterfactual temporal-T2 completion distributions.

This is an engineering analysis of the already-opened randomized intervention
campaign.  Every row is predicted by models fit without its complete
``(seed, run_number)`` group.  The tool preserves separate no-action and
full-copy completion CDFs instead of prematurely reducing them to one score.
"""

from __future__ import annotations

import argparse
import csv
import gc
import gzip
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
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
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline

import build_randomized_temporal_dataset as temporal_builder
import build_randomized_intervention_dataset as base_builder
import train_temporal_t2_value as trainer


ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_ID = "temporal-t2-distributional-frontier-v1"
DESIGN_CONTRACT = Path(
    "experiments/model-selection/temporal-t2-distributional-frontier-v1.json"
)
DESIGN_CONTRACT_SHA256 = (
    "7351064d437022b99c3e7481f44a4dba9c6b4e6435b3c8b957ca18e7c8d7d6f3"
)
THRESHOLDS_US = (12_000, 18_000, 24_000, 30_000, 33_333)
CLASS_COUNT = len(THRESHOLDS_US) + 1
FOLD_COUNT = 8
GROUPS_PER_FOLD = 12
RANDOM_SEED = 20260805
DIRICHLET_ALPHA = 0.5

PRIMARY_FAMILY = "primary_compact_physics_temporal"
SECONDARY_FAMILY = (
    "primary_compact_physics_temporal_plus_passive_secondary"
)
FEATURE_FAMILY_ORDER = (PRIMARY_FAMILY, SECONDARY_FAMILY)

MODEL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "hgb64_depth3_7leaf_multiclass_v1",
        "learning_rate": 0.05,
        "max_iter": 64,
        "max_leaf_nodes": 7,
        "max_depth": 3,
        "min_samples_leaf": 20,
        "l2_regularization": 1.0,
        "max_bins": 63,
        "early_stopping": False,
    },
    {
        "id": "hgb128_depth4_15leaf_multiclass_v1",
        "learning_rate": 0.05,
        "max_iter": 128,
        "max_leaf_nodes": 15,
        "max_depth": 4,
        "min_samples_leaf": 20,
        "l2_regularization": 1.0,
        "max_bins": 127,
        "early_stopping": False,
    },
)

OUTPUT_PREDICTIONS = "temporal_t2_distribution_predictions.csv.gz"
OUTPUT_METRICS = "temporal_t2_distribution_metrics.json"
OUTPUT_MANIFEST = "artifact_manifest.json"
OUTPUT_FILES = (OUTPUT_PREDICTIONS, OUTPUT_METRICS, OUTPUT_MANIFEST)


class DistributionError(RuntimeError):
    """Raised when the cross-fitted analysis contract cannot be established."""


@dataclass(frozen=True)
class DistributionDataset:
    """Validated temporal observations and predeclared feature matrices."""

    path: Path
    metadata: dict[str, Any]
    manifest: dict[str, Any]
    family_matrices: dict[str, np.ndarray]
    family_feature_names: dict[str, tuple[str, ...]]
    run_ids: tuple[str, ...]
    seeds: np.ndarray
    run_numbers: np.ndarray
    frame_ids: np.ndarray
    split_roles: tuple[str, ...]
    treatment: np.ndarray
    propensity: np.ndarray
    outcome_bins: np.ndarray
    primary_deadline_miss: np.ndarray
    canonical_reservation_us: np.ndarray
    frame_types: tuple[str, ...]
    folds: np.ndarray
    fold_groups: tuple[tuple[tuple[int, int], ...], ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise DistributionError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DistributionError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise DistributionError(f"{path}: expected a JSON object")
    return value


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _display_path(path: Path) -> str:
    """Prefer a repository-relative path for portable provenance."""

    resolved = path.resolve()
    try:
        return str(resolved.relative_to(_repository_root()))
    except ValueError:
        return str(resolved)


def _validate_design_contract() -> dict[str, Any]:
    path = _repository_root() / DESIGN_CONTRACT
    if _sha256(path) != DESIGN_CONTRACT_SHA256:
        raise DistributionError("distributional design contract hash differs")
    contract = _read_json(path)
    if (
        contract.get("schema_version") != 1
        or contract.get("analysis_id") != ANALYSIS_ID
        or contract.get("status") != "frozen_before_cross_fitted_fit"
        or contract.get("completion_distribution", {}).get("thresholds_us")
        != list(THRESHOLDS_US)
        or contract.get("cross_fitting", {}).get("fold_count") != FOLD_COUNT
        or contract.get("cross_fitting", {}).get("groups_per_fold")
        != GROUPS_PER_FOLD
        or contract.get("cross_fitting", {}).get("random_seed") != RANDOM_SEED
    ):
        raise DistributionError("distributional design contract semantics differ")
    if contract.get("model_specs") != list(MODEL_SPECS):
        raise DistributionError("distributional model specification differs")
    families = contract.get("feature_families")
    if (
        not isinstance(families, list)
        or [item.get("id") for item in families] != list(FEATURE_FAMILY_ORDER)
        or [item.get("encoded_feature_count") for item in families] != [246, 308]
    ):
        raise DistributionError("distributional feature-family contract differs")
    return contract


def latency_bin(latency_us: float | None) -> int:
    """Return the predeclared completion bin, treating incomplete as infinity."""

    if latency_us is None:
        return len(THRESHOLDS_US)
    if not math.isfinite(latency_us) or latency_us < 0:
        raise DistributionError("latency must be finite and nonnegative or absent")
    return int(np.searchsorted(THRESHOLDS_US, latency_us, side="left"))


def assign_group_folds(
    groups: Sequence[tuple[int, int]],
    fold_count: int = FOLD_COUNT,
    random_seed: int = RANDOM_SEED,
) -> tuple[dict[tuple[int, int], int], tuple[tuple[tuple[int, int], ...], ...]]:
    """Assign complete run groups to deterministic, exactly balanced folds."""

    unique = set(groups)
    if len(unique) != len(groups) or len(unique) % fold_count:
        raise DistributionError("cross-fit groups are duplicate or cannot balance")

    def key(group: tuple[int, int]) -> bytes:
        payload = f"{group[0]}:{group[1]}:{random_seed}".encode("ascii")
        return hashlib.sha256(payload).digest()

    ordered = sorted(unique, key=lambda group: (key(group), group))
    assignment = {group: index % fold_count for index, group in enumerate(ordered)}
    folds = tuple(
        tuple(sorted(group for group in ordered if assignment[group] == fold))
        for fold in range(fold_count)
    )
    sizes = {len(value) for value in folds}
    if sizes != {len(ordered) // fold_count}:
        raise DistributionError("cross-fit fold sizes differ")
    return assignment, folds


def _optional_latency(row: dict[str, str], field: str, context: str) -> float | None:
    raw = row.get(field, "")
    if raw == "":
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise DistributionError(f"{context}: invalid {field}") from error
    if not math.isfinite(value) or value < 0:
        raise DistributionError(f"{context}: invalid {field}")
    return value


def _positive_number(row: dict[str, str], field: str, context: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, ValueError) as error:
        raise DistributionError(f"{context}: invalid {field}") from error
    if not math.isfinite(value) or value <= 0:
        raise DistributionError(f"{context}: non-positive {field}")
    return value


def load_distribution_dataset(dataset_dir: Path | str) -> DistributionDataset:
    """Stream the frozen temporal artifact into bounded dense arrays."""

    path = Path(dataset_dir).resolve()
    metadata = trainer._read_json(path / temporal_builder.OUTPUT_METADATA)
    manifest = trainer._read_json(path / temporal_builder.OUTPUT_MANIFEST)
    splits, source_run_ids = trainer._validate_metadata(metadata, manifest, path)
    row_count = metadata["comparison"]["row_count"]
    if row_count != 73_400:
        raise DistributionError("distributional row count differs")

    primary_base_columns = tuple(
        name
        for name in base_builder.FEATURE_COLUMNS
        if not name.startswith("x_secondary_")
    )
    numeric_primary_base = tuple(
        name for name in primary_base_columns if name != "x_f0_frame_type"
    )
    primary_base_names = numeric_primary_base + tuple(
        f"x_f0_frame_type={name}" for name in trainer.audited.FRAME_TYPES
    )
    secondary_names = tuple(
        name
        for name in temporal_builder.FEATURE_COLUMNS
        if name.startswith("x_secondary_")
    )
    primary_names = (
        primary_base_names
        + trainer.COMPACT_PRIMARY_PHYSICS_NAMES
        + trainer.PRIMARY_TEMPORAL_COLUMNS
    )
    combined_names = primary_names + secondary_names
    consumed_raw_features = set(numeric_primary_base) | set(
        trainer.PRIMARY_TEMPORAL_COLUMNS
    ) | set(secondary_names) | {"x_f0_frame_type"}
    validation_only_features = tuple(
        name
        for name in temporal_builder.FEATURE_COLUMNS
        if name not in consumed_raw_features
    )
    if len(primary_base_names) != 68 or [len(primary_names), len(combined_names)] != [
        246,
        308,
    ]:
        raise DistributionError("distributional feature-name closure differs")

    # Store the already-quantized adapter values compactly.  HistGradientBoosting
    # widens its validated input to float64 internally, matching the frozen
    # float32-then-float64 training contract without retaining two full copies.
    combined = np.empty((row_count, len(combined_names)), dtype=np.float32)
    run_ids: list[str] = []
    seeds = np.empty(row_count, dtype=np.int32)
    run_numbers = np.empty(row_count, dtype=np.int32)
    frame_ids = np.empty(row_count, dtype=np.int32)
    split_roles: list[str] = []
    treatment = np.empty(row_count, dtype=np.int8)
    propensity = np.empty(row_count, dtype=float)
    bins = np.empty(row_count, dtype=np.int8)
    primary_misses = np.empty(row_count, dtype=np.int8)
    reservations = np.empty(row_count, dtype=float)
    frame_types: list[str] = []
    seen: set[tuple[int, int, int]] = set()
    groups_seen: set[tuple[int, int]] = set()
    arms_seen: set[str] = set()
    prior_key: tuple[int, int, int] | None = None
    expected_probability = 0.08 / 0.88
    csv_path = path / temporal_builder.OUTPUT_CSV
    try:
        with csv_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or tuple(reader.fieldnames) != tuple(
                temporal_builder.DATASET_COLUMNS
            ):
                raise DistributionError("temporal CSV exact schema differs")
            for index, row in enumerate(reader):
                if index >= row_count:
                    raise DistributionError("temporal CSV has extra rows")
                context = f"temporal CSV line {index + 2}"
                if None in row or any(value is None for value in row.values()):
                    raise DistributionError(f"{context}: malformed row")
                seed = trainer._integer(row, "seed", context)
                run_number = trainer._integer(row, "run_number", context)
                frame_id = trainer._integer(row, "frame_id", context)
                group = (seed, run_number)
                key = (seed, run_number, frame_id)
                run_id = row.get("run_id", "")
                role = row.get("split_role", "")
                arm = row.get("assigned_arm", "")
                assigned_treatment = trainer._flag(row, "treatment", context)
                attempted = trainer._flag(row, "attempted", context)
                launched = trainer._flag(row, "launched", context)
                noncompliance = trainer._flag(row, "noncompliance", context)
                if (
                    row.get("dataset_schema_version") != "1"
                    or group not in splits
                    or role != splits[group]
                    or source_run_ids.get(run_id) != group
                    or row.get("analysis_stage") != "T2"
                    or arm not in {"CONTROL", "FULL_COPY_T2"}
                    or assigned_treatment != int(arm == "FULL_COPY_T2")
                    or trainer._flag(row, "eligible_t2", context) != 1
                    or trainer._flag(row, "decision_primary_actionable", context)
                    != 1
                    or frame_id < max(temporal_builder.LAGS)
                    or not (launched <= attempted <= assigned_treatment)
                    or noncompliance != int(assigned_treatment == 1 and launched == 0)
                    or key in seen
                    or (prior_key is not None and key <= prior_key)
                ):
                    raise DistributionError(f"{context}: identity or execution differs")
                row_propensity = trainer._number(
                    row, "treatment_probability", context
                )
                assigned_probability = trainer._number(
                    row, "assigned_arm_probability", context
                )
                if not math.isclose(
                    row_propensity,
                    expected_probability,
                    rel_tol=0.0,
                    abs_tol=1e-15,
                ) or not math.isclose(
                    assigned_probability,
                    0.08 if assigned_treatment else 0.8,
                    rel_tol=0.0,
                    abs_tol=1e-15,
                ):
                    raise DistributionError(f"{context}: propensity differs")

                outcomes = trainer._validate_outcomes(row, context, 33_333.0)
                if launched == 0 and outcomes["measured_airtime_us"] != 0:
                    raise DistributionError(f"{context}: unlaunched airtime differs")
                frame_type = row["x_f0_frame_type"]
                if frame_type not in trainer.audited.FRAME_TYPES:
                    raise DistributionError(f"{context}: frame type differs")
                latency = _optional_latency(
                    row, "outcome_union_latency_us", context
                )
                primary_latency = _optional_latency(
                    row, "outcome_primary_latency_us", context
                )
                primary_miss = int(primary_latency is None or primary_latency > 33_333)
                if primary_miss != int(
                    outcomes[f"primary_bad_{trainer.TARGET_DEADLINE}"]
                ):
                    raise DistributionError(f"{context}: primary miss differs")

                for output_index, name in enumerate(numeric_primary_base):
                    combined[index, output_index] = trainer._float32_numeric(
                        row[name], name, context
                    )
                one_hot_offset = len(numeric_primary_base)
                for category_index, category in enumerate(
                    trainer.audited.FRAME_TYPES
                ):
                    combined[index, one_hot_offset + category_index] = float(
                        frame_type == category
                    )
                temporal_offset = len(primary_base_names) + len(
                    trainer.COMPACT_PRIMARY_PHYSICS_NAMES
                )
                for temporal_index, name in enumerate(
                    trainer.PRIMARY_TEMPORAL_COLUMNS
                ):
                    combined[index, temporal_offset + temporal_index] = (
                        trainer._float32_numeric(row[name], name, context)
                    )
                secondary_offset = len(primary_names)
                for secondary_index, name in enumerate(secondary_names):
                    combined[index, secondary_offset + secondary_index] = (
                        trainer._float32_numeric(row[name], name, context)
                    )
                for name in validation_only_features:
                    trainer._float32_numeric(row[name], name, context)

                run_ids.append(run_id)
                seeds[index] = seed
                run_numbers[index] = run_number
                frame_ids[index] = frame_id
                split_roles.append(role)
                treatment[index] = assigned_treatment
                propensity[index] = row_propensity
                bins[index] = latency_bin(latency)
                primary_misses[index] = primary_miss
                reservations[index] = _positive_number(
                    row, "action_estimated_airtime_us", context
                )
                frame_types.append(frame_type)
                seen.add(key)
                groups_seen.add(group)
                arms_seen.add(arm)
                prior_key = key
    except OSError as error:
        raise DistributionError(f"cannot read {csv_path}: {error}") from error
    if (
        len(run_ids) != row_count
        or groups_seen != set(splits)
        or arms_seen != {"CONTROL", "FULL_COPY_T2"}
    ):
        raise DistributionError("temporal CSV coverage differs")

    compact_start = len(primary_base_names)
    compact_stop = compact_start + len(trainer.COMPACT_PRIMARY_PHYSICS_NAMES)
    primary_base_wide = combined[:, : len(primary_base_names)].astype(float)
    combined[:, compact_start:compact_stop] = trainer._compact_primary_physics(
        primary_base_wide, primary_base_names
    )
    del primary_base_wide
    if np.any(np.isinf(combined)):
        raise DistributionError("distributional feature matrix overflows float32")
    primary = combined[:, : len(primary_names)]
    families = {
        PRIMARY_FAMILY: primary,
        SECONDARY_FAMILY: combined,
    }
    names = {
        PRIMARY_FAMILY: primary_names,
        SECONDARY_FAMILY: combined_names,
    }
    if [families[name].shape[1] for name in FEATURE_FAMILY_ORDER] != [246, 308]:
        raise DistributionError("distributional encoded feature counts differ")
    if len(set(names[SECONDARY_FAMILY])) != len(names[SECONDARY_FAMILY]):
        raise DistributionError("distributional feature names are duplicate")

    groups = sorted(groups_seen)
    if len(groups) != FOLD_COUNT * GROUPS_PER_FOLD:
        raise DistributionError("distributional run-group count differs")
    assignment, fold_groups = assign_group_folds(groups)
    folds = np.asarray(
        [assignment[(int(seeds[index]), int(run_numbers[index]))] for index in range(row_count)],
        dtype=np.int8,
    )
    return DistributionDataset(
        path=path,
        metadata=metadata,
        manifest=manifest,
        family_matrices=families,
        family_feature_names=names,
        run_ids=tuple(run_ids),
        seeds=seeds,
        run_numbers=run_numbers,
        frame_ids=frame_ids,
        split_roles=tuple(split_roles),
        treatment=treatment,
        propensity=propensity,
        outcome_bins=bins,
        primary_deadline_miss=primary_misses,
        canonical_reservation_us=reservations,
        frame_types=tuple(frame_types),
        folds=folds,
        fold_groups=fold_groups,
    )


def _model(spec: dict[str, Any], seed: int) -> Pipeline:
    return Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median",
                    add_indicator=True,
                    keep_empty_features=True,
                ),
            ),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    loss="log_loss",
                    learning_rate=spec["learning_rate"],
                    max_iter=spec["max_iter"],
                    max_leaf_nodes=spec["max_leaf_nodes"],
                    max_depth=spec["max_depth"],
                    min_samples_leaf=spec["min_samples_leaf"],
                    l2_regularization=spec["l2_regularization"],
                    max_bins=spec["max_bins"],
                    early_stopping=spec["early_stopping"],
                    random_state=seed,
                ),
            ),
        ]
    )


def aligned_smoothed_probabilities(
    model: Pipeline,
    matrix: np.ndarray,
    training_count: int,
    class_count: int = CLASS_COUNT,
    alpha: float = DIRICHLET_ALPHA,
) -> np.ndarray:
    """Return class-aligned probabilities with the frozen rare-class guard."""

    classifier = model.named_steps.get("classifier")
    if classifier is None or training_count <= 0 or alpha <= 0:
        raise DistributionError("invalid probability-alignment inputs")
    raw = np.asarray(model.predict_proba(matrix), dtype=float)
    classes = np.asarray(classifier.classes_, dtype=int)
    if (
        raw.ndim != 2
        or len(raw) != len(matrix)
        or raw.shape[1] != len(classes)
        or len(set(classes.tolist())) != len(classes)
        or np.any(classes < 0)
        or np.any(classes >= class_count)
        or not np.all(np.isfinite(raw))
    ):
        raise DistributionError("classifier probability schema differs")
    aligned = np.zeros((len(matrix), class_count), dtype=float)
    aligned[:, classes] = raw
    smoothed = (training_count * aligned + alpha) / (
        training_count + alpha * class_count
    )
    if not np.allclose(np.sum(smoothed, axis=1), 1.0, atol=1e-12, rtol=0.0):
        raise DistributionError("smoothed class probabilities do not sum to one")
    return smoothed


def _variant_id(family: str, spec: dict[str, Any]) -> str:
    family_id = "primary" if family == PRIMARY_FAMILY else "primary_secondary"
    model_id = "hgb64" if spec["max_iter"] == 64 else "hgb128"
    return f"{family_id}_{model_id}"


def crossfit_variant(
    data: DistributionDataset,
    family: str,
    spec: dict[str, Any],
    variant_ordinal: int,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Cross-fit both randomized arms for one feature/model variant."""

    matrix = data.family_matrices[family]
    treatment = data.treatment
    cdf = np.full((len(matrix), 2, len(THRESHOLDS_US)), np.nan, dtype=float)
    fits: list[dict[str, Any]] = []
    for fold in range(FOLD_COUNT):
        test = data.folds == fold
        train = ~test
        if int(np.sum(test)) == 0:
            raise DistributionError(f"fold {fold}: empty prediction population")
        for arm in (0, 1):
            selected = train & (treatment == arm)
            labels = data.outcome_bins[selected]
            if len(labels) < 20 or len(set(labels.tolist())) < 2:
                raise DistributionError(f"fold {fold} arm {arm}: insufficient support")
            seed = RANDOM_SEED + variant_ordinal * 1000 + fold * 10 + arm
            model = _model(spec, seed)
            model.fit(matrix[selected], labels)
            probabilities = aligned_smoothed_probabilities(
                model, matrix[test], int(np.sum(selected))
            )
            cdf[test, arm, :] = np.cumsum(probabilities, axis=1)[
                :, : len(THRESHOLDS_US)
            ]
            fits.append(
                {
                    "fold": fold,
                    "arm": arm,
                    "training_rows": int(np.sum(selected)),
                    "prediction_rows": int(np.sum(test)),
                    "observed_training_classes": sorted(set(labels.tolist())),
                    "random_seed": seed,
                }
            )
            del model, probabilities
            gc.collect()
            print(
                f"{_variant_id(family, spec)} fold {fold + 1}/{FOLD_COUNT} "
                f"arm {arm} complete",
                flush=True,
            )
    if not np.all(np.isfinite(cdf)):
        raise DistributionError("cross-fitted CDF contains missing predictions")
    if np.any(np.diff(cdf, axis=2) < -1e-12) or np.any((cdf < 0) | (cdf > 1)):
        raise DistributionError("cross-fitted CDF is not a valid distribution")
    return cdf, fits


def _class_probabilities(cdf: np.ndarray) -> np.ndarray:
    if cdf.ndim != 2 or cdf.shape[1] != len(THRESHOLDS_US):
        raise DistributionError("CDF matrix shape differs")
    probabilities = np.column_stack(
        (cdf[:, 0], np.diff(cdf, axis=1), 1.0 - cdf[:, -1])
    )
    if np.any(probabilities < -1e-12):
        raise DistributionError("CDF implies a negative class probability")
    probabilities = np.maximum(probabilities, 0.0)
    probabilities /= np.sum(probabilities, axis=1, keepdims=True)
    return probabilities


def _safe_auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    if len(set(np.asarray(labels, dtype=int).tolist())) != 2:
        return None
    return float(roc_auc_score(labels, scores))


def _arm_metrics(
    labels: np.ndarray, cdf: np.ndarray, arm: int
) -> dict[str, Any]:
    probabilities = _class_probabilities(cdf)
    chosen = np.maximum(probabilities[np.arange(len(labels)), labels], 1e-15)
    one_hot = np.eye(CLASS_COUNT, dtype=float)[labels]
    result: dict[str, Any] = {
        "arm": arm,
        "row_count": len(labels),
        "outcome_bin_counts": [int(np.sum(labels == value)) for value in range(CLASS_COUNT)],
        "multiclass_log_loss": float(-np.mean(np.log(chosen))),
        "multiclass_brier_score": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        "deadline_miss_auc": _safe_auc(labels == CLASS_COUNT - 1, 1.0 - cdf[:, -1]),
        "thresholds": {},
    }
    for threshold_index, threshold in enumerate(THRESHOLDS_US):
        observed = labels <= threshold_index
        predicted = cdf[:, threshold_index]
        result["thresholds"][str(threshold)] = {
            "observed_completion_probability": float(np.mean(observed)),
            "mean_predicted_completion_probability": float(np.mean(predicted)),
            "cdf_brier_score": float(np.mean((predicted - observed) ** 2)),
        }
    return result


def doubly_robust_cdf_components(
    outcome_bins: np.ndarray,
    treatment: np.ndarray,
    propensity: np.ndarray,
    cdf: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return cross-fitted DR potential CDF components for both arms."""

    if (
        cdf.shape != (len(outcome_bins), 2, len(THRESHOLDS_US))
        or treatment.shape != outcome_bins.shape
        or propensity.shape != outcome_bins.shape
        or np.any((propensity <= 0) | (propensity >= 1))
    ):
        raise DistributionError("DR component inputs differ")
    outcomes = np.column_stack(
        [outcome_bins <= index for index in range(len(THRESHOLDS_US))]
    ).astype(float)
    phi0 = cdf[:, 0, :] + (
        ((1 - treatment) / (1 - propensity))[:, None]
        * (outcomes - cdf[:, 0, :])
    )
    phi1 = cdf[:, 1, :] + (
        (treatment / propensity)[:, None] * (outcomes - cdf[:, 1, :])
    )
    if not np.all(np.isfinite(phi0)) or not np.all(np.isfinite(phi1)):
        raise DistributionError("DR components are non-finite")
    return phi0, phi1


def _variant_metrics(
    data: DistributionDataset,
    cdf: np.ndarray,
    fits: list[dict[str, Any]],
) -> dict[str, Any]:
    treatment = data.treatment
    propensity = data.propensity
    arms = {
        str(arm): _arm_metrics(
            data.outcome_bins[treatment == arm],
            cdf[treatment == arm, arm, :],
            arm,
        )
        for arm in (0, 1)
    }
    phi0, phi1 = doubly_robust_cdf_components(
        data.outcome_bins, treatment, propensity, cdf
    )
    effects: dict[str, Any] = {}
    for index, threshold in enumerate(THRESHOLDS_US):
        predicted = cdf[:, 1, index] - cdf[:, 0, index]
        effects[str(threshold)] = {
            "mean_predicted_cdf_gain": float(np.mean(predicted)),
            "negative_predicted_gain_fraction": float(np.mean(predicted < 0)),
            "dr_average_cdf_gain": float(np.mean(phi1[:, index] - phi0[:, index])),
        }
    deadline_benefit = np.maximum(cdf[:, 1, -1] - cdf[:, 0, -1], 0.0)
    no_action_miss = 1.0 - cdf[:, 0, -1]
    return {
        "observed_arm_prediction": arms,
        "causal_cdf_gain": effects,
        "primary_deadline_information": {
            "no_action_miss_probability_auc": _safe_auc(
                data.primary_deadline_miss, no_action_miss
            ),
            "nonnegative_deadline_rescue_auc": _safe_auc(
                data.primary_deadline_miss, deadline_benefit
            ),
            "mean_predicted_no_action_miss_probability": float(
                np.mean(no_action_miss)
            ),
            "mean_nonnegative_predicted_deadline_rescue": float(
                np.mean(deadline_benefit)
            ),
        },
        "fold_fits": fits,
    }


def _prediction_header(variant_ids: Sequence[str]) -> list[str]:
    fields = [
        "analysis_schema_version",
        "seed",
        "run_number",
        "run_id",
        "frame_id",
        "crossfit_fold",
        "split_role",
        "treatment",
        "treatment_probability",
        "frame_type",
        "outcome_bin",
        "outcome_primary_deadline_miss",
        "canonical_reservation_us",
    ]
    for variant in variant_ids:
        for arm in (0, 1):
            for threshold in THRESHOLDS_US:
                fields.append(f"{variant}__arm{arm}_cdf_{threshold}us")
    return fields


def _write_predictions(
    path: Path,
    data: DistributionDataset,
    predictions: dict[str, np.ndarray],
) -> None:
    variant_ids = tuple(predictions)
    with path.open("wb") as binary:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=binary, mtime=0
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(
                    text, fieldnames=_prediction_header(variant_ids), lineterminator="\n"
                )
                writer.writeheader()
                for index in range(len(data.outcome_bins)):
                    row: dict[str, Any] = {
                        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
                        "seed": int(data.seeds[index]),
                        "run_number": int(data.run_numbers[index]),
                        "run_id": data.run_ids[index],
                        "frame_id": int(data.frame_ids[index]),
                        "crossfit_fold": int(data.folds[index]),
                        "split_role": data.split_roles[index],
                        "treatment": int(data.treatment[index]),
                        "treatment_probability": format(
                            data.propensity[index], ".17g"
                        ),
                        "frame_type": data.frame_types[index],
                        "outcome_bin": int(data.outcome_bins[index]),
                        "outcome_primary_deadline_miss": int(
                            data.primary_deadline_miss[index]
                        ),
                        "canonical_reservation_us": format(
                            data.canonical_reservation_us[index], ".17g"
                        ),
                    }
                    for variant, cdf in predictions.items():
                        for arm in (0, 1):
                            for threshold_index, threshold in enumerate(
                                THRESHOLDS_US
                            ):
                                row[
                                    f"{variant}__arm{arm}_cdf_{threshold}us"
                                ] = format(cdf[index, arm, threshold_index], ".17g")
                    writer.writerow(row)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=_repository_root(),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def crossfit_temporal_t2_distributions(
    dataset_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Run the frozen cross-fit and write a checksum-closed artifact set."""

    contract = _validate_design_contract()
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise DistributionError(f"refusing to overwrite output directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        data = load_distribution_dataset(dataset_dir)
        predictions: dict[str, np.ndarray] = {}
        metrics: dict[str, Any] = {}
        ordinal = 0
        for family in FEATURE_FAMILY_ORDER:
            for spec in MODEL_SPECS:
                variant = _variant_id(family, spec)
                cdf, fits = crossfit_variant(data, family, spec, ordinal)
                predictions[variant] = cdf
                metrics[variant] = {
                    "feature_family": family,
                    "feature_count": len(data.family_feature_names[family]),
                    "model_spec_id": spec["id"],
                    **_variant_metrics(data, cdf, fits),
                }
                ordinal += 1

        prediction_path = temporary / OUTPUT_PREDICTIONS
        metrics_path = temporary / OUTPUT_METRICS
        _write_predictions(prediction_path, data, predictions)
        treatment = data.treatment
        result = {
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "analysis_id": ANALYSIS_ID,
            "evidence_role": "retrospective_engineering_screen",
            "design_contract": {
                "path": str(DESIGN_CONTRACT),
                "sha256": DESIGN_CONTRACT_SHA256,
            },
            "dataset": {
                "path": _display_path(data.path),
                "artifact_manifest_sha256": _sha256(
                    data.path / temporal_builder.OUTPUT_MANIFEST
                ),
                "dataset_metadata_sha256": _sha256(
                    data.path / temporal_builder.OUTPUT_METADATA
                ),
                "randomized_t2_temporal_sha256": _sha256(
                    data.path / temporal_builder.OUTPUT_CSV
                ),
                "row_count": len(data.outcome_bins),
                "run_group_count": sum(len(value) for value in data.fold_groups),
                "control_rows": int(np.sum(treatment == 0)),
                "treated_rows": int(np.sum(treatment == 1)),
                "outcome_bin_counts_by_arm": {
                    str(arm): [
                        int(np.sum(data.outcome_bins[treatment == arm] == value))
                        for value in range(CLASS_COUNT)
                    ]
                    for arm in (0, 1)
                },
            },
            "cross_fitting": {
                "fold_count": FOLD_COUNT,
                "groups_per_fold": GROUPS_PER_FOLD,
                "random_seed": RANDOM_SEED,
                "assignment_algorithm": contract["cross_fitting"][
                    "assignment_algorithm"
                ],
                "fold_groups": [
                    [
                        {"seed": seed, "run_number": run_number}
                        for seed, run_number in groups
                    ]
                    for groups in data.fold_groups
                ],
            },
            "thresholds_us": list(THRESHOLDS_US),
            "dirichlet_alpha_per_class": DIRICHLET_ALPHA,
            "feature_families": {
                family: {
                    "feature_count": len(data.family_feature_names[family]),
                    "ordered_feature_names": list(data.family_feature_names[family]),
                }
                for family in FEATURE_FAMILY_ORDER
            },
            "model_specs": list(MODEL_SPECS),
            "variants": metrics,
            "interpretation_limits": contract["interpretation_limits"],
            "provenance": {
                "project_git_commit": _git_value("rev-parse", "HEAD"),
                "project_git_status_porcelain": _git_value("status", "--porcelain"),
                "tool": str(Path(__file__).resolve().relative_to(_repository_root())),
                "tool_sha256": _sha256(Path(__file__).resolve()),
                "python": platform.python_version(),
                "numpy": np.__version__,
                "scikit_learn": importlib.metadata.version("scikit-learn"),
            },
        }
        _write_json(metrics_path, result)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "analysis_id": ANALYSIS_ID,
            "artifacts_sha256": {
                OUTPUT_PREDICTIONS: _sha256(prediction_path),
                OUTPUT_METRICS: _sha256(metrics_path),
            },
            "source_artifacts_sha256": {
                str(DESIGN_CONTRACT): DESIGN_CONTRACT_SHA256,
                str(
                    Path(dataset_dir) / temporal_builder.OUTPUT_MANIFEST
                ): _sha256(Path(dataset_dir) / temporal_builder.OUTPUT_MANIFEST),
                str(
                    Path(dataset_dir) / temporal_builder.OUTPUT_METADATA
                ): _sha256(Path(dataset_dir) / temporal_builder.OUTPUT_METADATA),
                str(Path(dataset_dir) / temporal_builder.OUTPUT_CSV): _sha256(
                    Path(dataset_dir) / temporal_builder.OUTPUT_CSV
                ),
            },
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path(
            "results/randomized_full_copy_exploration_collection_v1/temporal_dataset"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = crossfit_temporal_t2_distributions(
            args.dataset_dir, args.output_dir
        )
    except DistributionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "analysis_id": result["analysis_id"],
                "row_count": result["dataset"]["row_count"],
                "variants": {
                    name: {
                        "primary_miss_auc": value[
                            "primary_deadline_information"
                        ]["no_action_miss_probability_auc"],
                        "deadline_dr_cdf_gain": value["causal_cdf_gain"][
                            str(THRESHOLDS_US[-1])
                        ]["dr_average_cdf_gain"],
                    }
                    for name, value in result["variants"].items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
