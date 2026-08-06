#!/usr/bin/env python3
"""Frozen policy and value primitives for environment generalization."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from numbers import Integral
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np

import analyze_environment_generalization_lofo as analyzer
import environment_generalization_lofo as lofo
import randomized_frame_assignment


POLICY_CONTRACT_PATH = Path(
    "experiments/model-selection/environment-generalization-policy-replay-v1.json"
)
POLICY_CONTRACT_SHA256 = (
    "4922b85afb5dd5341733dcd455584c29ab5a54f2ad2bea562fd2573efa9d5e31"
)
LOADER_COMPATIBILITY_PATH = Path(
    "experiments/model-selection/"
    "environment-generalization-loader-compatibility-v1.json"
)
LOADER_COMPATIBILITY_SHA256 = (
    "c6214e7f1140b9e09536e23d139910e9b9704e92eb2aacbf7e375818ad0b34cd"
)


class PolicyError(RuntimeError):
    """Raised when a frozen policy replay invariant differs."""


@dataclass(frozen=True)
class LofoPredictions:
    """Validated row-aligned LOFO completion and OOD predictions."""

    path: Path
    metrics: dict[str, Any]
    manifest: dict[str, Any]
    cdf: np.ndarray
    ood_score: np.ndarray
    ood_threshold: np.ndarray
    ood_hard_failure: np.ndarray
    ood_soft: np.ndarray
    ood_fallback: np.ndarray


@dataclass(frozen=True)
class PolicyTrace:
    """One deterministic or averaged policy and its exact resource details."""

    action_probability: np.ndarray
    run_details: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ExplorationTrace:
    """Logged deployment exploration layered over one deterministic policy."""

    executed_action: np.ndarray
    assignment_action_probability: np.ndarray
    assigned_forced_t2: np.ndarray
    assigned_forced_control: np.ndarray
    execution_compliance: np.ndarray
    route: tuple[str, ...]
    run_details: dict[str, dict[str, Any]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise PolicyError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PolicyError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise PolicyError(f"{path}: expected a JSON object")
    return value


def _validate_dataset_loader_compatibility(
    source: dict[str, Any], actual_sha256: str
) -> None:
    """Validate the current loader without changing archived source identity."""

    path = lofo.ROOT / LOADER_COMPATIBILITY_PATH
    if _sha256(path) != LOADER_COMPATIBILITY_SHA256:
        raise PolicyError("dataset-loader compatibility contract hash differs")
    compatibility = _read_json(path)
    parent = compatibility.get("parent_policy_contract")
    builder = compatibility.get("builder_compatibility_amendment")
    archived = compatibility.get("archived_dataset_loader")
    current = compatibility.get("current_dataset_loader")
    usage = compatibility.get("usage")
    if (
        compatibility.get("schema_version") != 1
        or compatibility.get("compatibility_id")
        != "environment-generalization-loader-compatibility-v1"
        or compatibility.get("status")
        != "recorded_before_closed_loop_qualification_outcomes_read"
        or parent
        != {
            "path": str(POLICY_CONTRACT_PATH),
            "sha256": POLICY_CONTRACT_SHA256,
        }
        or builder
        != {
            "path": str(lofo.BUILDER_AMENDMENT_PATH),
            "sha256": lofo.BUILDER_AMENDMENT_SHA256,
        }
        or archived != source
        or not isinstance(current, dict)
        or current.get("path") != source.get("path")
        or current.get("sha256") != actual_sha256
        or not isinstance(usage, dict)
        or any(
            usage.get(name) is not True
            for name in (
                "archived_prediction_artifacts_keep_archived_loader_hash",
                "current_loader_must_validate_exact_builder_source_profiles",
                "does_not_recharacterize_archived_policy_results",
            )
        )
    ):
        raise PolicyError("dataset-loader compatibility contract differs")
    lofo._load_builder_amendment(lofo.load_contract())


def load_policy_contract() -> dict[str, Any]:
    """Load and fully bind the policy replay contract."""

    path = lofo.ROOT / POLICY_CONTRACT_PATH
    if _sha256(path) != POLICY_CONTRACT_SHA256:
        raise PolicyError("policy replay contract hash differs")
    contract = _read_json(path)
    if (
        contract.get("schema_version") != 1
        or contract.get("analysis_id")
        != "environment-generalization-policy-replay-v1"
        or contract.get("status")
        != "support_amended_before_policy_outcomes_read"
    ):
        raise PolicyError("policy replay contract identity differs")
    sources = contract.get("sources", {})
    if not isinstance(sources, dict) or set(sources) != {
        "lofo_contract",
        "dataset_loader",
        "lofo_predictor",
    }:
        raise PolicyError("policy replay source closure differs")
    for name, source in sources.items():
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("path"), str)
            or not isinstance(source.get("sha256"), str)
        ):
            raise PolicyError("policy replay source hash differs")
        actual_sha256 = _sha256(lofo.ROOT / source["path"])
        if actual_sha256 != source["sha256"]:
            if name != "dataset_loader":
                raise PolicyError("policy replay source hash differs")
            _validate_dataset_loader_compatibility(source, actual_sha256)
    policy_ids = [row.get("id") for row in contract.get("policies", [])]
    if (
        policy_ids
        != [
            "no_secondary_copy",
            "uniform_random_t2_same_canonical_budget",
            "myopic_deadline_risk_same_canonical_budget",
            "cross_fitted_scenario_resource_oracle_v1",
        ]
        or contract.get("resource", {}).get("budget_us_per_60s_run") != 372_000
        or contract.get("uncertainty", {}).get("replications") != 10_000
        or contract.get("population", {}).get("expected_represented_run_count")
        != 383
        or contract.get("population", {}).get(
            "minimum_represented_replicates_per_scenario"
        )
        != 3
        or contract.get("population", {}).get("maximum_zero_eligible_source_runs")
        != 1
    ):
        raise PolicyError("policy replay contract semantics differ")
    return contract


def _prediction_artifacts(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = path / analyzer.OUTPUT_MANIFEST
    metrics_path = path / analyzer.OUTPUT_METRICS
    manifest = _read_json(manifest_path)
    metrics = _read_json(metrics_path)
    if (
        set(manifest)
        != {"manifest_schema_version", "hash_algorithm", "artifacts_sha256"}
        or manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
        or set(manifest.get("artifacts_sha256", {}))
        != {analyzer.OUTPUT_PREDICTIONS, analyzer.OUTPUT_METRICS}
    ):
        raise PolicyError("LOFO prediction manifest schema differs")
    for name, expected_hash in manifest["artifacts_sha256"].items():
        if not isinstance(expected_hash, str) or _sha256(path / name) != expected_hash:
            raise PolicyError(f"LOFO prediction artifact hash differs: {name}")
    if (
        metrics.get("analysis_id") != "environment-generalization-lofo-v1"
        or metrics.get("analysis_schema_version") != analyzer.ANALYSIS_SCHEMA_VERSION
        or metrics.get("design_contract", {}).get("sha256")
        != lofo.CONTRACT_SHA256
    ):
        raise PolicyError("LOFO prediction metrics identity differs")
    return metrics, manifest


def load_lofo_predictions(
    prediction_dir: Path | str, data: lofo.LofoDataset
) -> LofoPredictions:
    """Load checksum-closed LOFO predictions in exact dataset row order."""

    path = Path(prediction_dir).resolve()
    contract = load_policy_contract()
    metrics, manifest = _prediction_artifacts(path)
    expected_analysis_sources = {
        contract["sources"][name]["path"]: contract["sources"][name]["sha256"]
        for name in ("dataset_loader", "lofo_predictor")
    }
    if (
        metrics.get("analysis_sources_sha256") != expected_analysis_sources
        or metrics.get("dataset", {}).get("artifact_manifest_sha256")
        != _sha256(data.path / "artifact_manifest.json")
        or metrics["dataset"].get("dataset_metadata_sha256")
        != _sha256(data.path / "dataset_metadata.json")
        or metrics["dataset"].get("dataset_csv_sha256")
        != _sha256(data.path / "environment_randomized_t2_temporal.csv")
        or metrics["dataset"].get("row_count") != len(data.run_ids)
    ):
        raise PolicyError("LOFO predictions reference a different dataset")
    thresholds = data.contract["completion_distribution"]["thresholds_us"]
    row_count = len(data.run_ids)
    cdf = np.empty((row_count, 2, len(thresholds)), dtype=float)
    score = np.empty(row_count, dtype=float)
    threshold = np.empty(row_count, dtype=float)
    hard = np.empty(row_count, dtype=np.int8)
    soft = np.empty(row_count, dtype=np.int8)
    fallback = np.empty(row_count, dtype=np.int8)
    prediction_path = path / analyzer.OUTPUT_PREDICTIONS
    observed_rows = 0
    try:
        with gzip.open(prediction_path, mode="rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != analyzer._prediction_header(thresholds):
                raise PolicyError("LOFO prediction CSV schema differs")
            for index, row in enumerate(reader):
                if index >= row_count:
                    raise PolicyError("LOFO prediction CSV has extra rows")
                context = f"LOFO prediction line {index + 2}"
                try:
                    identity_matches = (
                        row.get("run_id") == data.run_ids[index]
                        and int(row.get("seed", "-1")) == data.seeds[index]
                        and int(row.get("run_number", "-1"))
                        == data.run_numbers[index]
                        and int(row.get("frame_id", "-1")) == data.frame_ids[index]
                        and row.get("scenario_id") == data.scenario_ids[index]
                        and row.get("family_id") == data.family_ids[index]
                        and int(row.get("treatment", "-1")) == data.treatment[index]
                        and int(row.get("outcome_bin", "-1"))
                        == data.outcome_bins[index]
                    )
                    if not identity_matches:
                        raise PolicyError(f"{context}: row identity differs")
                    score[index] = float(row["ood_score"])
                    threshold[index] = float(row["ood_threshold"])
                    hard[index] = int(row["ood_hard_failure"])
                    soft[index] = int(row["ood_soft"])
                    fallback[index] = int(row["ood_fallback"])
                    for arm in (0, 1):
                        for threshold_index, value in enumerate(thresholds):
                            cdf[index, arm, threshold_index] = float(
                                row[f"arm{arm}_cdf_{value}us"]
                            )
                except (KeyError, ValueError) as error:
                    raise PolicyError(f"{context}: numeric value differs") from error
                observed_rows += 1
    except OSError as error:
        raise PolicyError(f"cannot read {prediction_path}: {error}") from error
    if observed_rows != row_count:
        raise PolicyError("LOFO prediction CSV row count differs")
    if (
        not np.all(np.isfinite(cdf))
        or np.any(np.diff(cdf, axis=2) < -1e-6)
        or np.any((cdf < 0) | (cdf > 1))
        or not np.all(np.isfinite(score))
        or not np.all(np.isfinite(threshold))
        or not set(hard.tolist()) <= {0, 1}
        or not set(soft.tolist()) <= {0, 1}
        or not np.array_equal(fallback, np.maximum(hard, soft))
    ):
        raise PolicyError("LOFO prediction values differ")
    return LofoPredictions(
        path=path,
        metrics=metrics,
        manifest=manifest,
        cdf=cdf,
        ood_score=score,
        ood_threshold=threshold,
        ood_hard_failure=hard,
        ood_soft=soft,
        ood_fallback=fallback,
    )


def indices_by_run(data: lofo.LofoDataset) -> dict[str, np.ndarray]:
    """Return exact row coverage grouped by run ID."""

    grouped: defaultdict[str, list[int]] = defaultdict(list)
    for index, run_id in enumerate(data.run_ids):
        grouped[run_id].append(index)
    result = {
        run_id: np.asarray(indices, dtype=int)
        for run_id, indices in sorted(grouped.items())
    }
    coverage = np.concatenate(tuple(result.values())) if result else np.asarray([])
    if not result or not np.array_equal(np.sort(coverage), np.arange(len(data.run_ids))):
        raise PolicyError("run index coverage differs")
    for run_id, indices in result.items():
        if (
            len(set(np.asarray(data.scenario_ids, dtype=object)[indices].tolist())) != 1
            or len(set(np.asarray(data.family_ids, dtype=object)[indices].tolist()))
            != 1
            or len(set(data.seeds[indices].tolist())) != 1
            or len(set(data.run_numbers[indices].tolist())) != 1
            or len(set(data.frame_ids[indices].tolist())) != len(indices)
        ):
            raise PolicyError(f"{run_id}: run identity differs")
    return result


def predicted_objectives(
    data: lofo.LofoDataset, cdf: np.ndarray
) -> dict[str, np.ndarray]:
    """Derive the three frozen nonnegative prediction objectives."""

    thresholds = data.contract["completion_distribution"]["thresholds_us"]
    if cdf.shape != (len(data.run_ids), 2, len(thresholds)):
        raise PolicyError("prediction objective CDF shape differs")
    rows = np.arange(len(data.run_ids))
    deadline_indices = data.deadline_threshold_indices.astype(int)
    tail_index = thresholds.index(18_000)
    control_deadline = cdf[rows, 0, deadline_indices]
    treated_deadline = cdf[rows, 1, deadline_indices]
    return {
        "deadline_rescue": np.maximum(treated_deadline - control_deadline, 0.0),
        "tail18_acceleration": np.maximum(
            cdf[:, 1, tail_index] - cdf[:, 0, tail_index], 0.0
        ),
        "primary_deadline_risk": 1.0 - control_deadline,
    }


def _decimal_costs(texts: Sequence[str]) -> list[Decimal]:
    costs: list[Decimal] = []
    for text in texts:
        try:
            cost = Decimal(text)
        except (InvalidOperation, TypeError) as error:
            raise PolicyError("canonical cost text differs") from error
        if not cost.is_finite() or cost <= 0:
            raise PolicyError("canonical cost must be finite and positive")
        costs.append(cost)
    return costs


def exact_two_cost_knapsack(
    indices: np.ndarray,
    primary_reward: np.ndarray,
    secondary_reward: np.ndarray,
    frame_types: Sequence[str],
    frame_ids: np.ndarray,
    canonical_cost_texts: Sequence[str],
    budget_us: int,
) -> tuple[tuple[int, ...], Decimal, tuple[float, float]]:
    """Solve the frozen lexicographic two-cost resource problem exactly."""

    rows = np.asarray(indices, dtype=int)
    if (
        rows.ndim != 1
        or len(set(rows.tolist())) != len(rows)
        or np.any((rows < 0) | (rows >= len(primary_reward)))
        or primary_reward.shape != secondary_reward.shape
        or len(primary_reward) != len(frame_types)
        or frame_ids.shape != primary_reward.shape
        or len(canonical_cost_texts) != len(primary_reward)
        or isinstance(budget_us, bool)
        or budget_us <= 0
        or np.any(~np.isfinite(primary_reward))
        or np.any(~np.isfinite(secondary_reward))
        or np.any(primary_reward < 0)
        or np.any(secondary_reward < 0)
    ):
        raise PolicyError("exact knapsack inputs differ")
    decimal_costs = {
        int(index): _decimal_costs([canonical_cost_texts[int(index)]])[0]
        for index in rows
    }
    by_type: dict[str, list[int]] = {}
    type_cost: dict[str, Decimal] = {}
    for frame_type in ("P_FRAME", "I_FRAME"):
        observed = [int(index) for index in rows if frame_types[int(index)] == frame_type]
        costs = {decimal_costs[index] for index in observed}
        if len(costs) > 1:
            raise PolicyError(f"{frame_type}: canonical cost is not constant in run")
        if costs:
            type_cost[frame_type] = next(iter(costs))
        candidates = [
            index
            for index in observed
            if primary_reward[index] > 0 or secondary_reward[index] > 0
        ]
        by_type[frame_type] = sorted(
            candidates,
            key=lambda index: (
                -float(primary_reward[index]),
                -float(secondary_reward[index]),
                int(frame_ids[index]),
            ),
        )
    unknown_types = {frame_types[int(index)] for index in rows} - set(by_type)
    if unknown_types:
        raise PolicyError("knapsack frame type differs")
    p_rows = by_type["P_FRAME"]
    i_rows = by_type["I_FRAME"]
    p_primary = np.concatenate(
        ([0.0], np.cumsum(primary_reward[p_rows], dtype=float))
    )
    p_secondary = np.concatenate(
        ([0.0], np.cumsum(secondary_reward[p_rows], dtype=float))
    )
    i_primary = np.concatenate(
        ([0.0], np.cumsum(primary_reward[i_rows], dtype=float))
    )
    i_secondary = np.concatenate(
        ([0.0], np.cumsum(secondary_reward[i_rows], dtype=float))
    )
    budget = Decimal(budget_us)
    p_cost = type_cost.get("P_FRAME")
    i_cost = type_cost.get("I_FRAME")
    best_key: tuple[float, float, Decimal, int] | None = None
    best_ids: tuple[int, ...] | None = None
    best_counts = (0, 0)
    for i_count in range(len(i_rows) + 1):
        i_spend = Decimal(0) if i_cost is None else i_cost * i_count
        if i_spend > budget:
            break
        p_count = (
            0
            if p_cost is None
            else min(len(p_rows), int((budget - i_spend) // p_cost))
        )
        spend = i_spend + (Decimal(0) if p_cost is None else p_cost * p_count)
        primary = float(i_primary[i_count] + p_primary[p_count])
        secondary = float(i_secondary[i_count] + p_secondary[p_count])
        chosen = p_rows[:p_count] + i_rows[:i_count]
        chosen_ids = tuple(sorted(int(frame_ids[index]) for index in chosen))
        key = (primary, secondary, -spend, -len(chosen))
        if (
            best_key is None
            or key > best_key
            or (key == best_key and chosen_ids < (best_ids or ()))
        ):
            best_key = key
            best_ids = chosen_ids
            best_counts = (p_count, i_count)
    p_count, i_count = best_counts
    chosen = tuple(p_rows[:p_count] + i_rows[:i_count])
    spend = sum((decimal_costs[index] for index in chosen), Decimal(0))
    reward = (
        float(np.sum(primary_reward[list(chosen)])) if chosen else 0.0,
        float(np.sum(secondary_reward[list(chosen)])) if chosen else 0.0,
    )
    if spend > budget or best_key is None:
        raise PolicyError("exact knapsack result differs")
    return chosen, spend, reward


def exact_policy(
    data: lofo.LofoDataset,
    primary_reward: np.ndarray,
    secondary_reward: np.ndarray,
    budget_us: int,
) -> PolicyTrace:
    """Apply exact two-cost optimization independently to every run."""

    action = np.zeros(len(data.run_ids), dtype=float)
    details: dict[str, dict[str, Any]] = {}
    for run_id, indices in indices_by_run(data).items():
        chosen, spend, reward = exact_two_cost_knapsack(
            indices,
            primary_reward,
            secondary_reward,
            data.frame_types,
            data.frame_ids,
            data.canonical_reservation_texts,
            budget_us,
        )
        action[list(chosen)] = 1.0
        details[run_id] = {
            "actions": len(chosen),
            "canonical_reservation_text_us": str(spend),
            "canonical_reservation_us": float(spend),
            "predicted_primary_reward": reward[0],
            "predicted_secondary_reward": reward[1],
        }
    return PolicyTrace(action_probability=action, run_details=details)


def _uniform_replication(
    data: lofo.LofoDataset,
    grouped: dict[str, np.ndarray],
    budget_us: int,
    salt: int,
) -> tuple[np.ndarray, dict[str, dict[str, Any]]]:
    action = np.zeros(len(data.run_ids), dtype=float)
    costs = _decimal_costs(data.canonical_reservation_texts)
    budget = Decimal(budget_us)
    details: dict[str, dict[str, Any]] = {}
    for run_id, indices in grouped.items():
        ordered = sorted(
            (int(index) for index in indices),
            key=lambda index: (
                randomized_frame_assignment.assign_frame(
                    salt,
                    int(data.seeds[index]),
                    int(data.run_numbers[index]),
                    int(data.frame_ids[index]),
                    0.0,
                    0.0,
                ).raw_draw,
                int(data.frame_ids[index]),
            ),
        )
        spend = Decimal(0)
        chosen: list[int] = []
        for index in ordered:
            if spend + costs[index] <= budget:
                spend += costs[index]
                chosen.append(index)
        action[chosen] = 1.0
        details[run_id] = {
            "actions": len(chosen),
            "canonical_reservation_text_us": str(spend),
            "canonical_reservation_us": float(spend),
        }
    return action, details


def uniform_policy_replications(
    data: lofo.LofoDataset,
    budget_us: int,
    replication_count: int,
    salt_base: int,
) -> Iterator[tuple[int, PolicyTrace]]:
    """Yield each deterministic SplitMix64 uniform-budget replication."""

    if replication_count <= 0 or salt_base < 0:
        raise PolicyError("uniform policy configuration differs")
    grouped = indices_by_run(data)
    for replication in range(replication_count):
        action, details = _uniform_replication(
            data, grouped, budget_us, salt_base + replication
        )
        yield replication, PolicyTrace(action, details)


def apply_deployment_exploration(
    data: lofo.LofoDataset,
    base_action: np.ndarray,
    ood_hard_failure: np.ndarray,
    ood_soft: np.ndarray,
    budget_us: int,
    *,
    contract: dict[str, Any] | None = None,
) -> ExplorationTrace:
    """Apply frozen logged exploration with exact causal budget compliance."""

    replay_contract = load_policy_contract() if contract is None else contract
    exploration = replay_contract["deployment_exploration"]
    action = np.asarray(base_action, dtype=float)
    hard_values = np.asarray(ood_hard_failure)
    soft_values = np.asarray(ood_soft)
    hard = hard_values.astype(bool)
    soft = soft_values.astype(bool)
    row_count = len(data.run_ids)
    if (
        action.shape != (row_count,)
        or not set(action.tolist()) <= {0.0, 1.0}
        or hard.shape != (row_count,)
        or soft.shape != (row_count,)
        or not set(hard_values.tolist()) <= {0, 1, False, True}
        or not set(soft_values.tolist()) <= {0, 1, False, True}
        or np.any(hard & soft)
        or isinstance(budget_us, bool)
        or not isinstance(budget_us, Integral)
        or budget_us <= 0
        or exploration.get("assignment_algorithm")
        != randomized_frame_assignment.ALGORITHM_ID
    ):
        raise PolicyError("deployment exploration inputs differ")
    in_support = exploration["in_support"]
    soft_policy = exploration["soft_ood"]
    hard_policy = exploration["hard_failure"]
    forced_t2_probability = in_support["forced_t2_probability"]
    forced_control_probability = in_support["forced_control_probability"]
    base_probability = in_support["base_policy_probability"]
    soft_t2_probability = soft_policy["forced_t2_probability"]
    if (
        not math.isclose(
            forced_t2_probability + forced_control_probability + base_probability,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
        or hard_policy["forced_t2_probability"] != 0.0
        or soft_policy["base_policy"] != "no_secondary_copy"
        or hard_policy["base_policy"] != "no_secondary_copy"
        or not exploration["forced_t2_requires_exact_remaining_budget"]
        or not exploration["forced_control_overrides_base_action"]
        or not exploration["log_realized_propensity_and_compliance"]
    ):
        raise PolicyError("deployment exploration probabilities differ")
    costs = _decimal_costs(data.canonical_reservation_texts)
    budget = Decimal(budget_us)
    executed = np.zeros(row_count, dtype=np.int8)
    propensity = np.zeros(row_count, dtype=float)
    assigned_t2 = np.zeros(row_count, dtype=np.int8)
    assigned_control = np.zeros(row_count, dtype=np.int8)
    compliance = np.ones(row_count, dtype=np.int8)
    routes = [""] * row_count
    details: dict[str, dict[str, Any]] = {}
    for run_id, indices in indices_by_run(data).items():
        ordered = sorted(
            (int(index) for index in indices),
            key=lambda index: int(data.frame_ids[index]),
        )
        spend = Decimal(0)
        route_counts: defaultdict[str, int] = defaultdict(int)
        for index in ordered:
            desired = False
            route: str
            if hard[index]:
                route = "hard_ood_fallback"
                propensity[index] = 0.0
            else:
                assignment = randomized_frame_assignment.assign_frame(
                    exploration["assignment_salt"],
                    int(data.seeds[index]),
                    int(data.run_numbers[index]),
                    int(data.frame_ids[index]),
                    0.0,
                    0.0,
                )
                draw = assignment.unit_draw
                if soft[index]:
                    propensity[index] = soft_t2_probability
                    if draw < soft_t2_probability:
                        desired = True
                        assigned_t2[index] = 1
                        route = "soft_ood_forced_t2"
                    else:
                        route = "soft_ood_fallback"
                else:
                    propensity[index] = forced_t2_probability + (
                        base_probability if action[index] == 1.0 else 0.0
                    )
                    if draw < forced_t2_probability:
                        desired = True
                        assigned_t2[index] = 1
                        route = "in_support_forced_t2"
                    elif draw < forced_t2_probability + forced_control_probability:
                        if action[index] == 1.0:
                            assigned_control[index] = 1
                            route = "in_support_forced_control"
                        else:
                            route = "in_support_control_noop"
                    else:
                        desired = action[index] == 1.0
                        route = (
                            "in_support_base_action"
                            if desired
                            else "in_support_base_control"
                        )
            if desired:
                if spend + costs[index] <= budget:
                    spend += costs[index]
                    executed[index] = 1
                else:
                    compliance[index] = 0
                    route += "_budget_rejected"
            routes[index] = route
            route_counts[route] += 1
        details[run_id] = {
            "executed_actions": int(np.sum(executed[indices])),
            "canonical_reservation_text_us": str(spend),
            "canonical_reservation_us": float(spend),
            "noncompliant_assignments": int(np.sum(1 - compliance[indices])),
            "route_counts": dict(sorted(route_counts.items())),
        }
    if (
        np.any(executed[hard] != 0)
        or np.any(propensity[hard] != 0)
        or max(row["canonical_reservation_us"] for row in details.values())
        > budget_us
    ):
        raise PolicyError("deployment exploration result violates fallback or budget")
    return ExplorationTrace(
        executed_action=executed,
        assignment_action_probability=propensity,
        assigned_forced_t2=assigned_t2,
        assigned_forced_control=assigned_control,
        execution_compliance=compliance,
        route=tuple(routes),
        run_details=details,
    )


def policy_value_components(
    data: lofo.LofoDataset,
    cdf: np.ndarray,
    action_probability: np.ndarray,
) -> dict[str, np.ndarray]:
    """Return row-level frozen DR and HT policy-value components."""

    action = np.asarray(action_probability, dtype=float)
    if (
        action.shape != data.treatment.shape
        or not np.all(np.isfinite(action))
        or np.any((action < 0) | (action > 1))
    ):
        raise PolicyError("policy action probabilities differ")
    phi0, phi1 = analyzer.doubly_robust_cdf_components(
        data.outcome_bins, data.treatment, data.propensity, cdf
    )
    rows = np.arange(len(action))
    deadline_indices = data.deadline_threshold_indices.astype(int)
    tail_index = data.contract["completion_distribution"]["thresholds_us"].index(
        18_000
    )
    policy_deadline_cdf = phi0[rows, deadline_indices] + action * (
        phi1[rows, deadline_indices] - phi0[rows, deadline_indices]
    )
    policy_tail18_cdf = phi0[:, tail_index] + action * (
        phi1[:, tail_index] - phi0[:, tail_index]
    )
    treatment = data.treatment.astype(float)
    outcome_late = data.completed_late18.astype(float)
    late0 = (1.0 - treatment) * outcome_late / (1.0 - data.propensity)
    late1 = treatment * outcome_late / data.propensity
    completed_late18 = late0 + action * (late1 - late0)
    return {
        "deadline_miss": 1.0 - policy_deadline_cdf,
        "completed_late18": completed_late18,
        "bounded_on_time_late18": policy_deadline_cdf - policy_tail18_cdf,
    }


def weighted_policy_values(
    data: lofo.LofoDataset, components: dict[str, np.ndarray]
) -> dict[str, float]:
    """Reduce row components with the frozen hierarchy."""

    weights = analyzer._hierarchical_weights(
        data, np.ones(len(data.run_ids), dtype=bool)
    )
    values: dict[str, float] = {}
    for name, component in components.items():
        vector = np.asarray(component, dtype=float)
        if vector.shape != weights.shape or not np.all(np.isfinite(vector)):
            raise PolicyError(f"{name}: policy value component differs")
        values[name] = float(np.sum(weights * vector))
    return values
