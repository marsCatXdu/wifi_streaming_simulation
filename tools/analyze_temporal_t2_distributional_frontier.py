#!/usr/bin/env python3
"""Analyze static resource frontiers from cross-fitted temporal-T2 CDFs.

The static allocator sees every predicted score in a run.  It is therefore an
information ceiling, not an implementable online policy.  Canonical airtime is
kept separate for every run and optimized exactly for the two frame costs.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import build_randomized_temporal_dataset as temporal_builder
import crossfit_temporal_t2_distributions as crossfit


ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_ID = "temporal-t2-static-distributional-frontier-v1"
RESOURCE_BUDGETS_US = (360_000, 372_000)
FRAME_GATES = ("p_frames_only", "all_frames_cost_aware")
OBJECTIVES = (
    "deadline_rescue",
    "completion_by_18ms",
    "deadline_capped_acceleration",
)
BOOTSTRAP_REPLICATIONS = 2_000
BOOTSTRAP_SEED = 20260805
CONFIDENCE_LEVEL = 0.95

OUTPUT_METRICS = "temporal_t2_static_frontier.json"
OUTPUT_REPORT = "temporal_t2_static_frontier.md"
OUTPUT_FIGURE = "temporal_t2_static_frontier.png"
OUTPUT_MANIFEST = "artifact_manifest.json"


class FrontierError(RuntimeError):
    """Raised when cross-fitted evidence cannot support the frontier."""


Unit = tuple[int, int]


@dataclass(frozen=True)
class FrontierDataset:
    """Cross-fitted CDFs joined exactly to their frozen source rows."""

    distribution_dir: Path
    temporal_dir: Path
    metrics: dict[str, Any]
    manifest: dict[str, Any]
    seeds: np.ndarray
    run_numbers: np.ndarray
    frame_ids: np.ndarray
    folds: np.ndarray
    treatment: np.ndarray
    propensity: np.ndarray
    frame_types: tuple[str, ...]
    outcome_bins: np.ndarray
    primary_deadline_miss: np.ndarray
    canonical_cost_text: tuple[str, ...]
    canonical_cost_us: np.ndarray
    primary_busy_20ms: np.ndarray
    cdf_by_variant: dict[str, np.ndarray]

    @property
    def units(self) -> tuple[Unit, ...]:
        """Return the stable sorted run groups."""

        return tuple(
            sorted(
                {
                    (int(self.seeds[index]), int(self.run_numbers[index]))
                    for index in range(len(self.seeds))
                }
            )
        )

    def indices_by_unit(self) -> dict[Unit, np.ndarray]:
        """Return row indices for each complete run group."""

        return {
            unit: np.flatnonzero(
                (self.seeds == unit[0]) & (self.run_numbers == unit[1])
            )
            for unit in self.units
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise FrontierError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FrontierError(f"cannot read {path}: {error}") from error
    if not isinstance(result, dict):
        raise FrontierError(f"{path}: expected a JSON object")
    return result


def _variant_order() -> tuple[str, ...]:
    return tuple(
        crossfit._variant_id(family, spec)
        for family in crossfit.FEATURE_FAMILY_ORDER
        for spec in crossfit.MODEL_SPECS
    )


def _verify_distribution_artifacts(
    distribution_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_json(distribution_dir / crossfit.OUTPUT_MANIFEST)
    metrics = _read_json(distribution_dir / crossfit.OUTPUT_METRICS)
    if (
        manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
        or manifest.get("analysis_id") != crossfit.ANALYSIS_ID
        or metrics.get("analysis_schema_version") != crossfit.ANALYSIS_SCHEMA_VERSION
        or metrics.get("analysis_id") != crossfit.ANALYSIS_ID
    ):
        raise FrontierError("cross-fit artifact identity differs")
    artifacts = manifest.get("artifacts_sha256")
    expected = {crossfit.OUTPUT_PREDICTIONS, crossfit.OUTPUT_METRICS}
    if not isinstance(artifacts, dict) or set(artifacts) != expected:
        raise FrontierError("cross-fit artifact closure differs")
    for name, digest in artifacts.items():
        if not isinstance(digest, str) or _sha256(distribution_dir / name) != digest:
            raise FrontierError(f"cross-fit artifact hash differs: {name}")
    if tuple(metrics.get("variants", {})) != tuple(sorted(_variant_order())):
        raise FrontierError("cross-fit metric variant closure differs")
    return metrics, manifest


def _float(row: dict[str, str], field: str, context: str) -> float:
    try:
        result = float(row[field])
    except (KeyError, ValueError) as error:
        raise FrontierError(f"{context}: invalid {field}") from error
    if not math.isfinite(result):
        raise FrontierError(f"{context}: non-finite {field}")
    return result


def _optional_latency(row: dict[str, str], field: str, context: str) -> float | None:
    if row.get(field, "") == "":
        return None
    value = _float(row, field, context)
    if value < 0:
        raise FrontierError(f"{context}: negative {field}")
    return value


def load_frontier_dataset(
    distribution_dir: Path | str,
    temporal_dir: Path | str,
) -> FrontierDataset:
    """Verify and load cross-fitted predictions in exact temporal-row order."""

    prediction_dir = Path(distribution_dir).resolve()
    source_dir = Path(temporal_dir).resolve()
    metrics, manifest = _verify_distribution_artifacts(prediction_dir)
    source_hashes = {
        temporal_builder.OUTPUT_MANIFEST: _sha256(
            source_dir / temporal_builder.OUTPUT_MANIFEST
        ),
        temporal_builder.OUTPUT_METADATA: _sha256(
            source_dir / temporal_builder.OUTPUT_METADATA
        ),
        temporal_builder.OUTPUT_CSV: _sha256(source_dir / temporal_builder.OUTPUT_CSV),
    }
    expected_source = metrics.get("dataset", {})
    if source_hashes != {
        temporal_builder.OUTPUT_MANIFEST: expected_source.get(
            "artifact_manifest_sha256"
        ),
        temporal_builder.OUTPUT_METADATA: expected_source.get(
            "dataset_metadata_sha256"
        ),
        temporal_builder.OUTPUT_CSV: expected_source.get(
            "randomized_t2_temporal_sha256"
        ),
    }:
        raise FrontierError("temporal source hashes differ from cross-fit metrics")

    row_count = expected_source.get("row_count")
    if not isinstance(row_count, int) or row_count <= 0:
        raise FrontierError("cross-fit row count is invalid")
    seeds = np.empty(row_count, dtype=np.int32)
    run_numbers = np.empty(row_count, dtype=np.int32)
    frame_ids = np.empty(row_count, dtype=np.int32)
    folds = np.empty(row_count, dtype=np.int8)
    treatment = np.empty(row_count, dtype=np.int8)
    propensity = np.empty(row_count, dtype=float)
    outcome_bins = np.empty(row_count, dtype=np.int8)
    primary_miss = np.empty(row_count, dtype=np.int8)
    costs = np.empty(row_count, dtype=float)
    busy = np.empty(row_count, dtype=float)
    frame_types: list[str] = []
    cost_text: list[str] = []
    variants = _variant_order()
    cdf_by_variant = {
        variant: np.empty(
            (row_count, 2, len(crossfit.THRESHOLDS_US)), dtype=float
        )
        for variant in variants
    }
    prediction_path = prediction_dir / crossfit.OUTPUT_PREDICTIONS
    temporal_path = source_dir / temporal_builder.OUTPUT_CSV
    try:
        with gzip.open(prediction_path, mode="rt", newline="", encoding="utf-8") as pstream:
            with temporal_path.open(newline="", encoding="utf-8") as tstream:
                predictions = csv.DictReader(pstream)
                temporal = csv.DictReader(tstream)
                if predictions.fieldnames != crossfit._prediction_header(variants):
                    raise FrontierError("cross-fit prediction schema differs")
                if temporal.fieldnames != list(temporal_builder.DATASET_COLUMNS):
                    raise FrontierError("temporal source schema differs")
                count = 0
                for count, (predicted, raw) in enumerate(
                    zip(predictions, temporal, strict=True), start=1
                ):
                    index = count - 1
                    if index >= row_count:
                        raise FrontierError("cross-fit predictions contain extra rows")
                    context = f"cross-fit prediction row {count + 1}"
                    identity = (
                        predicted["seed"],
                        predicted["run_number"],
                        predicted["run_id"],
                        predicted["frame_id"],
                    )
                    raw_identity = (
                        raw["seed"],
                        raw["run_number"],
                        raw["run_id"],
                        raw["frame_id"],
                    )
                    if identity != raw_identity:
                        raise FrontierError(f"{context}: temporal identity differs")
                    seed = int(predicted["seed"])
                    run_number = int(predicted["run_number"])
                    frame_id = int(predicted["frame_id"])
                    assigned = int(predicted["treatment"])
                    probability = _float(
                        predicted, "treatment_probability", context
                    )
                    observed_bin = int(predicted["outcome_bin"])
                    latency = _optional_latency(
                        raw, "outcome_union_latency_us", context
                    )
                    raw_primary_miss = int(raw["outcome_primary_deadline_miss"])
                    if (
                        predicted["analysis_schema_version"]
                        != str(crossfit.ANALYSIS_SCHEMA_VERSION)
                        or predicted["split_role"] != raw["split_role"]
                        or assigned != int(raw["treatment"])
                        or not math.isclose(
                            probability,
                            float(raw["treatment_probability"]),
                            rel_tol=0.0,
                            abs_tol=1e-15,
                        )
                        or predicted["frame_type"] != raw["x_f0_frame_type"]
                        or observed_bin != crossfit.latency_bin(latency)
                        or int(predicted["outcome_primary_deadline_miss"])
                        != raw_primary_miss
                    ):
                        raise FrontierError(f"{context}: temporal outcome differs")
                    serialized_cost = predicted["canonical_reservation_us"]
                    if Decimal(serialized_cost) != Decimal(
                        raw["action_estimated_airtime_us"]
                    ):
                        raise FrontierError(f"{context}: canonical cost differs")

                    seeds[index] = seed
                    run_numbers[index] = run_number
                    frame_ids[index] = frame_id
                    folds[index] = int(predicted["crossfit_fold"])
                    treatment[index] = assigned
                    propensity[index] = probability
                    outcome_bins[index] = observed_bin
                    primary_miss[index] = raw_primary_miss
                    costs[index] = float(serialized_cost)
                    busy[index] = _float(
                        raw, "x_primary_phy_busy_fraction_20ms", context
                    )
                    frame_types.append(predicted["frame_type"])
                    cost_text.append(serialized_cost)
                    for variant in variants:
                        for arm in (0, 1):
                            for threshold_index, threshold in enumerate(
                                crossfit.THRESHOLDS_US
                            ):
                                cdf_by_variant[variant][
                                    index, arm, threshold_index
                                ] = _float(
                                    predicted,
                                    f"{variant}__arm{arm}_cdf_{threshold}us",
                                    context,
                                )
    except (OSError, ValueError, KeyError, ArithmeticError) as error:
        raise FrontierError(f"cannot load cross-fit join: {error}") from error
    if count != row_count:
        raise FrontierError("cross-fit prediction row count differs")
    fold_by_group: dict[Unit, int] = {}
    for index in range(row_count):
        unit = (int(seeds[index]), int(run_numbers[index]))
        fold = int(folds[index])
        if unit in fold_by_group and fold_by_group[unit] != fold:
            raise FrontierError("cross-fit split divides a run group")
        fold_by_group[unit] = fold
    expected_fold_groups = {
        (int(item["seed"]), int(item["run_number"])): fold
        for fold, groups in enumerate(metrics["cross_fitting"]["fold_groups"])
        for item in groups
    }
    if fold_by_group != expected_fold_groups:
        raise FrontierError("prediction folds differ from cross-fit metrics")
    if int(np.sum(treatment == 0)) != expected_source.get(
        "control_rows"
    ) or int(np.sum(treatment == 1)) != expected_source.get("treated_rows"):
        raise FrontierError("prediction treatment counts differ")
    for variant, cdf in cdf_by_variant.items():
        if (
            not np.all(np.isfinite(cdf))
            or np.any((cdf < 0) | (cdf > 1))
            or np.any(np.diff(cdf, axis=2) < -1e-12)
        ):
            raise FrontierError(f"{variant}: invalid cross-fitted CDF")
    return FrontierDataset(
        distribution_dir=prediction_dir,
        temporal_dir=source_dir,
        metrics=metrics,
        manifest=manifest,
        seeds=seeds,
        run_numbers=run_numbers,
        frame_ids=frame_ids,
        folds=folds,
        treatment=treatment,
        propensity=propensity,
        frame_types=tuple(frame_types),
        outcome_bins=outcome_bins,
        primary_deadline_miss=primary_miss,
        canonical_cost_text=tuple(cost_text),
        canonical_cost_us=costs,
        primary_busy_20ms=busy,
        cdf_by_variant=cdf_by_variant,
    )


def objective_rewards(cdf: np.ndarray) -> dict[str, np.ndarray]:
    """Derive three nonnegative rewards while preserving their separation."""

    if cdf.ndim != 3 or cdf.shape[1:] != (
        2,
        len(crossfit.THRESHOLDS_US),
    ):
        raise FrontierError("objective CDF shape differs")
    gain = cdf[:, 1, :] - cdf[:, 0, :]
    deadline = np.maximum(gain[:, -1], 0.0)
    tail18 = np.maximum(gain[:, 1], 0.0)
    times = np.asarray((0, *crossfit.THRESHOLDS_US), dtype=float)
    padded = np.column_stack((np.zeros(len(gain)), gain))
    acceleration = np.maximum(np.trapz(padded, x=times, axis=1), 0.0)
    return {
        "deadline_rescue": deadline,
        "completion_by_18ms": tail18,
        "deadline_capped_acceleration": acceleration,
    }


def _sorted_positive(
    indices: np.ndarray, rewards: np.ndarray, frame_ids: np.ndarray
) -> list[int]:
    return sorted(
        (int(index) for index in indices if rewards[index] > 0),
        key=lambda index: (-float(rewards[index]), int(frame_ids[index])),
    )


def exact_two_cost_knapsack(
    indices: np.ndarray,
    rewards: np.ndarray,
    frame_types: Sequence[str],
    frame_ids: np.ndarray,
    canonical_cost_text: Sequence[str],
    budget_us: int,
    frame_gate: str,
) -> tuple[tuple[int, ...], Decimal, float]:
    """Solve one run exactly by enumerating I-frame counts and sorting per type."""

    if frame_gate not in FRAME_GATES or len(rewards) != len(frame_types):
        raise FrontierError("static knapsack inputs differ")
    by_type: dict[str, list[int]] = {}
    costs: dict[str, Decimal] = {}
    for frame_type in ("P_FRAME", "I_FRAME"):
        if frame_gate == "p_frames_only" and frame_type == "I_FRAME":
            selected_indices = np.asarray([], dtype=int)
        else:
            selected_indices = np.asarray(
                [index for index in indices if frame_types[int(index)] == frame_type],
                dtype=int,
            )
        by_type[frame_type] = _sorted_positive(
            selected_indices, rewards, frame_ids
        )
        observed_costs = {
            Decimal(canonical_cost_text[index]) for index in selected_indices
        }
        if len(observed_costs) > 1:
            raise FrontierError(f"{frame_type}: canonical cost is not constant")
        if observed_costs:
            costs[frame_type] = next(iter(observed_costs))

    p_rows = by_type["P_FRAME"]
    i_rows = by_type["I_FRAME"]
    p_cost = costs.get("P_FRAME")
    i_cost = costs.get("I_FRAME")
    if p_rows and (p_cost is None or p_cost <= 0):
        raise FrontierError("P-frame cost is absent")
    if i_rows and (i_cost is None or i_cost <= 0):
        raise FrontierError("I-frame cost is absent")
    p_prefix = np.concatenate(
        (np.asarray([0.0]), np.cumsum(rewards[p_rows], dtype=float))
    )
    i_prefix = np.concatenate(
        (np.asarray([0.0]), np.cumsum(rewards[i_rows], dtype=float))
    )
    budget = Decimal(budget_us)
    best_key: tuple[float, Decimal, int, int, int] | None = None
    best_counts = (0, 0)
    maximum_i = len(i_rows) if i_cost is not None else 0
    for i_count in range(maximum_i + 1):
        i_spend = Decimal(0) if i_cost is None else i_cost * i_count
        if i_spend > budget:
            break
        if p_cost is None:
            p_count = 0
        else:
            p_count = min(len(p_rows), int((budget - i_spend) // p_cost))
        spend = i_spend + (Decimal(0) if p_cost is None else p_cost * p_count)
        value = float(i_prefix[i_count] + p_prefix[p_count])
        key = (value, -spend, -(i_count + p_count), -i_count, -p_count)
        if best_key is None or key > best_key:
            best_key = key
            best_counts = (p_count, i_count)
    p_count, i_count = best_counts
    chosen = tuple(p_rows[:p_count] + i_rows[:i_count])
    spend = sum((Decimal(canonical_cost_text[index]) for index in chosen), Decimal(0))
    value = float(np.sum(rewards[list(chosen)])) if chosen else 0.0
    if spend > budget:
        raise FrontierError("static knapsack exceeds its exact budget")
    return chosen, spend, value


def static_policy(
    data: FrontierDataset,
    rewards: np.ndarray,
    budget_us: int,
    frame_gate: str,
) -> tuple[np.ndarray, dict[Unit, dict[str, Any]]]:
    """Apply the exact two-cost optimizer independently to every run."""

    policy = np.zeros(len(rewards), dtype=bool)
    run_details: dict[Unit, dict[str, Any]] = {}
    for unit, indices in data.indices_by_unit().items():
        chosen, spend, value = exact_two_cost_knapsack(
            indices,
            rewards,
            data.frame_types,
            data.frame_ids,
            data.canonical_cost_text,
            budget_us,
            frame_gate,
        )
        policy[list(chosen)] = True
        run_details[unit] = {
            "actions": len(chosen),
            "canonical_reservation_us": float(spend),
            "predicted_reward": value,
        }
    return policy, run_details


def _pooled_cluster_means(
    values: np.ndarray,
    indices_by_unit: dict[Unit, np.ndarray],
    bootstrap: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return pooled-frame point and whole-run bootstrap means."""

    if values.ndim != 2 or values.shape[0] == 0:
        raise FrontierError("pooled cluster values differ")
    groups = tuple(indices_by_unit.values())
    if (
        not groups
        or bootstrap.ndim != 2
        or bootstrap.dtype.kind not in "iu"
        or bootstrap.shape[1] != len(groups)
        or np.any((bootstrap < 0) | (bootstrap >= len(groups)))
    ):
        raise FrontierError("pooled cluster bootstrap differs")
    counts = np.asarray([len(indices) for indices in groups], dtype=np.int64)
    coverage = np.concatenate(groups)
    if (
        np.any(counts <= 0)
        or int(np.sum(counts)) != len(values)
        or not np.array_equal(np.sort(coverage), np.arange(len(values)))
    ):
        raise FrontierError("pooled cluster row coverage differs")
    sums = np.asarray(
        [np.sum(values[indices], axis=0) for indices in groups], dtype=float
    )
    point = np.sum(sums, axis=0) / np.sum(counts)
    sampled_counts = np.sum(counts[bootstrap], axis=1)
    sampled_sums = np.sum(sums[bootstrap], axis=1)
    replicates = sampled_sums / sampled_counts[:, None]
    if not np.all(np.isfinite(point)) or not np.all(np.isfinite(replicates)):
        raise FrontierError("pooled cluster mean is non-finite")
    return point, replicates


def _interval(estimate: float, samples: np.ndarray) -> dict[str, float]:
    if (
        not math.isfinite(estimate)
        or samples.ndim != 1
        or len(samples) == 0
        or not np.all(np.isfinite(samples))
    ):
        raise FrontierError("interval values are non-finite")
    alpha = (1.0 - CONFIDENCE_LEVEL) / 2.0
    return {
        "estimate": float(estimate),
        "ci_lower": float(np.quantile(samples, alpha)),
        "ci_upper": float(np.quantile(samples, 1.0 - alpha)),
    }


def evaluate_policy(
    data: FrontierDataset,
    cdf: np.ndarray,
    policy: np.ndarray,
    run_details: dict[Unit, dict[str, Any]],
    bootstrap: np.ndarray,
) -> dict[str, Any]:
    """Evaluate direct primary capture and cross-fitted DR outcome primitives."""

    phi0, phi1 = crossfit.doubly_robust_cdf_components(
        data.outcome_bins, data.treatment, data.propensity, cdf
    )
    selected = policy[:, None]
    policy_phi = phi0 + selected * (phi1 - phi0)
    indices_by_unit = data.indices_by_unit()
    none_cdf, none_bootstrap = _pooled_cluster_means(
        phi0, indices_by_unit, bootstrap
    )
    policy_cdf, policy_bootstrap = _pooled_cluster_means(
        policy_phi, indices_by_unit, bootstrap
    )
    if (
        none_cdf[-1] <= 0
        or policy_cdf[-1] <= 0
        or np.any(none_bootstrap[:, -1] <= 0)
        or np.any(policy_bootstrap[:, -1] <= 0)
    ):
        raise FrontierError("DR completion probability is non-positive")
    none_late18 = (none_cdf[-1] - none_cdf[1]) / none_cdf[-1]
    policy_late18 = (policy_cdf[-1] - policy_cdf[1]) / policy_cdf[-1]
    miss_delta_bootstrap = -(
        policy_bootstrap[:, -1] - none_bootstrap[:, -1]
    )
    none_late18_bootstrap = (
        none_bootstrap[:, -1] - none_bootstrap[:, 1]
    ) / none_bootstrap[:, -1]
    policy_late18_bootstrap = (
        policy_bootstrap[:, -1] - policy_bootstrap[:, 1]
    ) / policy_bootstrap[:, -1]
    tail_delta_bootstrap = policy_late18_bootstrap - none_late18_bootstrap
    total_primary = int(np.sum(data.primary_deadline_miss))
    captured_primary = int(np.sum(policy & (data.primary_deadline_miss == 1)))
    reservations = [
        value["canonical_reservation_us"] for value in run_details.values()
    ]
    actions = [value["actions"] for value in run_details.values()]
    return {
        "action_count": int(np.sum(policy)),
        "action_fraction": float(np.mean(policy)),
        "mean_actions_per_run": float(np.mean(actions)),
        "minimum_actions_per_run": int(min(actions)),
        "maximum_actions_per_run": int(max(actions)),
        "mean_canonical_reservation_us_per_run": float(np.mean(reservations)),
        "maximum_canonical_reservation_us_per_run": float(max(reservations)),
        "primary_deadline_misses": total_primary,
        "captured_primary_deadline_misses": captured_primary,
        "primary_miss_capture_fraction": captured_primary / total_primary,
        "perfect_rescue_residual_primary_misses": total_primary - captured_primary,
        "selected_primary_miss_fraction": (
            captured_primary / int(np.sum(policy)) if np.any(policy) else 0.0
        ),
        "dr_treat_none_deadline_miss_probability": float(1.0 - none_cdf[-1]),
        "dr_policy_deadline_miss_probability": float(1.0 - policy_cdf[-1]),
        "dr_policy_minus_none_deadline_miss": _interval(
            float(-(policy_cdf[-1] - none_cdf[-1])),
            miss_delta_bootstrap,
        ),
        "dr_treat_none_completed_late18_ratio": float(none_late18),
        "dr_policy_completed_late18_ratio": float(policy_late18),
        "dr_policy_minus_none_completed_late18_ratio": _interval(
            float(policy_late18 - none_late18), tail_delta_bootstrap
        ),
        "dr_policy_completion_cdf": {
            str(threshold): float(policy_cdf[index])
            for index, threshold in enumerate(crossfit.THRESHOLDS_US)
        },
    }


def _bootstrap_indices(run_count: int) -> np.ndarray:
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    return generator.integers(
        0, run_count, size=(BOOTSTRAP_REPLICATIONS, run_count), endpoint=False
    )


def analyze_frontiers(data: FrontierDataset) -> dict[str, Any]:
    """Build every predeclared static predictor/resource frontier."""

    units = data.units
    bootstrap = _bootstrap_indices(len(units))
    policies: list[dict[str, Any]] = []
    for variant in _variant_order():
        cdf = data.cdf_by_variant[variant]
        rewards = objective_rewards(cdf)
        for objective in OBJECTIVES:
            for gate in FRAME_GATES:
                for budget in RESOURCE_BUDGETS_US:
                    policy, run_details = static_policy(
                        data, rewards[objective], budget, gate
                    )
                    policies.append(
                        {
                            "variant": variant,
                            "feature_family": data.metrics["variants"][variant][
                                "feature_family"
                            ],
                            "model_spec_id": data.metrics["variants"][variant][
                                "model_spec_id"
                            ],
                            "objective": objective,
                            "frame_gate": gate,
                            "budget_us_per_run": budget,
                            "future_score_visibility": True,
                            **evaluate_policy(
                                data,
                                cdf,
                                policy,
                                run_details,
                                bootstrap,
                            ),
                        }
                    )
    predictors = {
        variant: {
            "feature_family": data.metrics["variants"][variant]["feature_family"],
            "model_spec_id": data.metrics["variants"][variant]["model_spec_id"],
            "primary_deadline_information": data.metrics["variants"][variant][
                "primary_deadline_information"
            ],
            "observed_arm_prediction": data.metrics["variants"][variant][
                "observed_arm_prediction"
            ],
            "causal_cdf_gain": data.metrics["variants"][variant][
                "causal_cdf_gain"
            ],
        }
        for variant in _variant_order()
    }
    return {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_id": ANALYSIS_ID,
        "evidence_role": "retrospective_cross_fitted_static_engineering_screen",
        "population": {
            "row_count": len(data.seeds),
            "run_group_count": len(units),
            "primary_deadline_misses": int(np.sum(data.primary_deadline_miss)),
            "scope": (
                "action-clean lag-8 temporal T2 rows; not all generated frames"
            ),
        },
        "resource": {
            "budgets_us_per_run": list(RESOURCE_BUDGETS_US),
            "unused_credit_transfer_between_runs": False,
            "canonical_costs_us_by_frame_type": {
                frame_type: sorted(
                    {
                        text
                        for text, observed_type in zip(
                            data.canonical_cost_text, data.frame_types, strict=True
                        )
                        if observed_type == frame_type
                    }
                )
                for frame_type in ("P_FRAME", "I_FRAME")
            },
            "static_frontier_future_score_visibility": True,
        },
        "bootstrap": {
            "unit": "(seed, run_number)",
            "point_estimand": "pooled action-clean frame mean",
            "resample_reduction": (
                "sum sampled whole-run numerators divided by sum sampled "
                "whole-run row counts"
            ),
            "replications": BOOTSTRAP_REPLICATIONS,
            "confidence_level": CONFIDENCE_LEVEL,
            "random_seed": BOOTSTRAP_SEED,
            "shared_resample_matrix_for_all_policies": True,
        },
        "predictors": predictors,
        "policies": policies,
        "interpretation_limits": [
            "Static policies inspect all predicted scores in a run and are not deployable online.",
            (
                "The randomized temporal population excludes startup and "
                "action-dirty rows, so absolute risks cannot be compared "
                "directly with 86,400-frame closed-loop campaigns."
            ),
            (
                "Doubly robust frame replay does not reproduce policy-induced "
                "queue or interference feedback."
            ),
            (
                "Primary-copy capture is directly observed; counterfactual "
                "final misses and completed-frame P99 still require closed-loop "
                "measurement."
            ),
        ],
    }


def _policy_lookup(
    result: dict[str, Any],
    variant: str,
    objective: str = "deadline_rescue",
    gate: str = "p_frames_only",
    budget: int = 372_000,
) -> dict[str, Any]:
    matches = [
        row
        for row in result["policies"]
        if row["variant"] == variant
        and row["objective"] == objective
        and row["frame_gate"] == gate
        and row["budget_us_per_run"] == budget
    ]
    if len(matches) != 1:
        raise FrontierError("cannot resolve static frontier record")
    return matches[0]


def write_report(path: Path, result: dict[str, Any]) -> None:
    """Write a compact interpretation-focused Markdown report."""

    lines = [
        "# Temporal-T2 cross-fitted static frontier",
        "",
        (
            "This is a retrospective engineering screen on the action-clean "
            "randomized temporal population. The static allocator sees future "
            "predicted scores, so it is a predictor ceiling, not an online policy."
        ),
        "",
        "## Deadline-rescue frontier at 372 ms/run",
        "",
        (
            "| Predictor | Primary AUC | Actions | Captured primary misses | "
            "Perfect-rescue residual | DR miss risk | DR late18 ratio |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant in _variant_order():
        policy = _policy_lookup(result, variant)
        auc = result["predictors"][variant]["primary_deadline_information"][
            "no_action_miss_probability_auc"
        ]
        lines.append(
            f"| {variant} | {auc:.4f} | {policy['action_count']:,} | "
            f"{policy['captured_primary_deadline_misses']:,} | "
            f"{policy['perfect_rescue_residual_primary_misses']:,} | "
            f"{100 * policy['dr_policy_deadline_miss_probability']:.3f}% | "
            f"{100 * policy['dr_policy_completed_late18_ratio']:.3f}% |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            *[f"- {item}" for item in result["interpretation_limits"]],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_figure(path: Path, result: dict[str, Any]) -> None:
    """Plot predictor discrimination and the exact static resource frontiers."""

    variants = _variant_order()
    labels = [
        "Primary\nHGB64",
        "Primary\nHGB128",
        "+ secondary\nHGB64",
        "+ secondary\nHGB128",
    ]
    colors = ("#4477AA", "#66CCEE", "#CC6677", "#AA3377")
    figure, axes = plt.subplots(1, 3, figsize=(15, 4.8))

    aucs = [
        result["predictors"][variant]["primary_deadline_information"][
            "no_action_miss_probability_auc"
        ]
        for variant in variants
    ]
    axes[0].bar(labels, aucs, color=colors)
    axes[0].set_ylim(max(0.5, min(aucs) - 0.03), min(1.0, max(aucs) + 0.03))
    axes[0].set_ylabel("Cross-fitted primary-miss AUC")
    axes[0].set_title("No-action deadline information")
    axes[0].grid(axis="y", alpha=0.25)

    width = 0.35
    x = np.arange(len(variants))
    for offset, budget in ((-width / 2, 360_000), (width / 2, 372_000)):
        captured = [
            _policy_lookup(result, variant, budget=budget)[
                "primary_miss_capture_fraction"
            ]
            for variant in variants
        ]
        axes[1].bar(
            x + offset,
            captured,
            width,
            label=f"{budget / 1000:.0f} ms/run",
        )
    axes[1].set_xticks(x, labels)
    axes[1].set_ylabel("Primary misses captured")
    axes[1].set_ylim(0, 1)
    axes[1].set_title("Static deadline-rescue frontier")
    axes[1].legend(frameon=False)
    axes[1].grid(axis="y", alpha=0.25)

    for variant, label, color in zip(variants, labels, colors, strict=True):
        policy = _policy_lookup(result, variant)
        axes[2].scatter(
            100 * policy["dr_policy_deadline_miss_probability"],
            100 * policy["dr_policy_completed_late18_ratio"],
            color=color,
            s=80,
            label=label.replace("\n", " "),
        )
    axes[2].set_xlabel("DR deadline-miss probability (%)")
    axes[2].set_ylabel("DR completed late18 ratio (%)")
    axes[2].set_title("Offline causal screen (372 ms/run)")
    axes[2].grid(alpha=0.25)
    axes[2].legend(frameon=False, fontsize=8)
    figure.suptitle("Temporal-T2 distributional predictor ceiling")
    figure.tight_layout()
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def analyze_to_directory(
    distribution_dir: Path | str,
    temporal_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Run the static analysis and write a checksum-closed artifact set."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FrontierError(f"refusing to overwrite output directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        data = load_frontier_dataset(distribution_dir, temporal_dir)
        result = analyze_frontiers(data)
        result["source"] = {
            "crossfit_manifest_sha256": _sha256(
                data.distribution_dir / crossfit.OUTPUT_MANIFEST
            ),
            "crossfit_metrics_sha256": _sha256(
                data.distribution_dir / crossfit.OUTPUT_METRICS
            ),
            "crossfit_predictions_sha256": _sha256(
                data.distribution_dir / crossfit.OUTPUT_PREDICTIONS
            ),
            "temporal_manifest_sha256": _sha256(
                data.temporal_dir / temporal_builder.OUTPUT_MANIFEST
            ),
            "temporal_metadata_sha256": _sha256(
                data.temporal_dir / temporal_builder.OUTPUT_METADATA
            ),
            "temporal_csv_sha256": _sha256(
                data.temporal_dir / temporal_builder.OUTPUT_CSV
            ),
        }
        result["provenance"] = {
            "project_git_commit": _git_value("rev-parse", "HEAD"),
            "project_git_status_porcelain": _git_value("status", "--porcelain"),
            "tool": str(
                Path(__file__).resolve().relative_to(
                    Path(__file__).resolve().parents[1]
                )
            ),
            "tool_sha256": _sha256(Path(__file__).resolve()),
        }
        metrics_path = temporary / OUTPUT_METRICS
        report_path = temporary / OUTPUT_REPORT
        figure_path = temporary / OUTPUT_FIGURE
        _write_json(metrics_path, result)
        write_report(report_path, result)
        write_figure(figure_path, result)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "analysis_id": ANALYSIS_ID,
            "artifacts_sha256": {
                OUTPUT_METRICS: _sha256(metrics_path),
                OUTPUT_REPORT: _sha256(report_path),
                OUTPUT_FIGURE: _sha256(figure_path),
            },
            "source_artifacts_sha256": result["source"],
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution-dir", type=Path, required=True)
    parser.add_argument(
        "--temporal-dir",
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
        result = analyze_to_directory(
            args.distribution_dir, args.temporal_dir, args.output_dir
        )
    except FrontierError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    summary = {
        variant: {
            "primary_miss_auc": result["predictors"][variant][
                "primary_deadline_information"
            ]["no_action_miss_probability_auc"],
            "captured_primary_misses_372ms": _policy_lookup(result, variant)[
                "captured_primary_deadline_misses"
            ],
            "dr_deadline_miss_372ms": _policy_lookup(result, variant)[
                "dr_policy_deadline_miss_probability"
            ],
        }
        for variant in _variant_order()
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
