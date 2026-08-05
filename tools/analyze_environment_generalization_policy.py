#!/usr/bin/env python3
"""Evaluate frozen cross-family resource policies on randomized outcomes."""

from __future__ import annotations

import argparse
import csv
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
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

import analyze_environment_generalization_lofo as lofo_analyzer
import environment_generalization_lofo as lofo
import environment_generalization_policy as policy


ANALYSIS_SCHEMA_VERSION = 1
OUTPUT_METRICS = "environment_policy_replay.json"
OUTPUT_REPORT = "environment_policy_replay.md"
OUTPUT_FAMILY_CSV = "environment_policy_family_values.csv"
OUTPUT_ACTIONS = "environment_policy_actions.csv.gz"
OUTPUT_MANIFEST = "artifact_manifest.json"
POLICY_ORDER = (
    "no_secondary_copy",
    "uniform_random_t2_same_canonical_budget",
    "myopic_deadline_risk_same_canonical_budget",
    "cross_fitted_scenario_resource_oracle_v1",
)


@dataclass(frozen=True)
class BootstrapPlan:
    """Shared hierarchical scenario/run resamples for every policy."""

    family_order: tuple[str, ...]
    scenario_order: dict[str, tuple[str, ...]]
    run_order: dict[tuple[str, str], tuple[str, ...]]
    scenario_draws: dict[str, np.ndarray]
    run_draws: dict[str, np.ndarray]
    replications: int


@dataclass(frozen=True)
class ReplayBundle:
    """In-memory replay result plus row artifacts needed for publication."""

    result: dict[str, Any]
    data: lofo.LofoDataset
    predictions: policy.LofoPredictions
    objectives: dict[str, np.ndarray]
    traces: dict[str, policy.PolicyTrace]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise policy.PolicyError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=lofo.ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _interval(
    estimate: float, samples: np.ndarray, confidence: float
) -> dict[str, float]:
    values = np.asarray(samples, dtype=float)
    if (
        not math.isfinite(estimate)
        or values.ndim != 1
        or len(values) == 0
        or not np.all(np.isfinite(values))
        or not 0 < confidence < 1
    ):
        raise policy.PolicyError("interval inputs differ")
    alpha = (1.0 - confidence) / 2.0
    return {
        "estimate": float(estimate),
        "ci_lower": float(np.quantile(values, alpha)),
        "ci_upper": float(np.quantile(values, 1.0 - alpha)),
    }


def _hierarchy(data: lofo.LofoDataset) -> tuple[
    tuple[str, ...],
    dict[str, tuple[str, ...]],
    dict[tuple[str, str], tuple[str, ...]],
]:
    family_order = tuple(data.contract["cross_fitting"]["outer_family_order"])
    scenario_sets = {family: set() for family in family_order}
    run_sets: dict[tuple[str, str], set[str]] = {}
    run_identity: dict[str, tuple[str, str]] = {}
    for family, scenario, run_id in zip(
        data.family_ids, data.scenario_ids, data.run_ids, strict=True
    ):
        if family not in scenario_sets:
            raise policy.PolicyError("bootstrap family differs")
        scenario_sets[family].add(scenario)
        run_sets.setdefault((family, scenario), set()).add(run_id)
        previous = run_identity.setdefault(run_id, (family, scenario))
        if previous != (family, scenario):
            raise policy.PolicyError("run belongs to multiple scenarios")
    scenarios = {
        family: tuple(sorted(scenario_sets[family])) for family in family_order
    }
    runs = {key: tuple(sorted(values)) for key, values in run_sets.items()}
    if (
        set(len(values) for values in scenarios.values()) != {16}
        or set(len(values) for values in runs.values()) != {4}
        or len(run_identity) != 384
    ):
        raise policy.PolicyError("bootstrap hierarchy differs from frozen campaign")
    return family_order, scenarios, runs


def make_bootstrap_plan(
    data: lofo.LofoDataset,
    replications: int,
    random_seed: int,
) -> BootstrapPlan:
    """Create the shared frozen scenario/run bootstrap plan."""

    if replications <= 0 or random_seed < 0:
        raise policy.PolicyError("bootstrap configuration differs")
    family_order, scenarios, runs = _hierarchy(data)
    generator = np.random.default_rng(random_seed)
    scenario_draws: dict[str, np.ndarray] = {}
    run_draws: dict[str, np.ndarray] = {}
    for family in family_order:
        scenario_count = len(scenarios[family])
        run_count = len(runs[(family, scenarios[family][0])])
        scenario_draws[family] = generator.integers(
            0,
            scenario_count,
            size=(replications, scenario_count),
            endpoint=False,
            dtype=np.int16,
        )
        run_draws[family] = generator.integers(
            0,
            run_count,
            size=(replications, scenario_count, run_count),
            endpoint=False,
            dtype=np.int16,
        )
    return BootstrapPlan(
        family_order=family_order,
        scenario_order=scenarios,
        run_order=runs,
        scenario_draws=scenario_draws,
        run_draws=run_draws,
        replications=replications,
    )


def _run_means(
    data: lofo.LofoDataset, components: dict[str, np.ndarray]
) -> dict[str, np.ndarray]:
    names = tuple(components)
    matrix = np.column_stack(
        [np.asarray(components[name], dtype=float) for name in names]
    )
    if matrix.shape != (len(data.run_ids), len(names)) or not np.all(
        np.isfinite(matrix)
    ):
        raise policy.PolicyError("bootstrap component matrix differs")
    return {
        run_id: np.mean(matrix[indices], axis=0)
        for run_id, indices in policy.indices_by_run(data).items()
    }


def bootstrap_policy_values(
    data: lofo.LofoDataset,
    components: dict[str, np.ndarray],
    plan: BootstrapPlan,
) -> tuple[tuple[str, ...], np.ndarray]:
    """Reduce one policy through the shared hierarchical bootstrap."""

    names = tuple(components)
    run_means = _run_means(data, components)
    family_samples = np.empty(
        (len(plan.family_order), plan.replications, len(names)), dtype=float
    )
    for family_index, family in enumerate(plan.family_order):
        scenario_values = np.asarray(
            [
                [run_means[run_id] for run_id in plan.run_order[(family, scenario)]]
                for scenario in plan.scenario_order[family]
            ],
            dtype=float,
        )
        selected_scenarios = scenario_values[plan.scenario_draws[family]]
        selected_runs = np.take_along_axis(
            selected_scenarios,
            plan.run_draws[family][..., None],
            axis=2,
        )
        family_samples[family_index] = np.mean(selected_runs, axis=(1, 2))
    samples = np.mean(family_samples, axis=0)
    if samples.shape != (plan.replications, len(names)) or not np.all(
        np.isfinite(samples)
    ):
        raise policy.PolicyError("bootstrap result differs")
    return names, samples


def _family_values(
    data: lofo.LofoDataset, components: dict[str, np.ndarray]
) -> dict[str, dict[str, float]]:
    families = np.asarray(data.family_ids, dtype=object)
    result: dict[str, dict[str, float]] = {}
    for family in data.contract["cross_fitting"]["outer_family_order"]:
        selected = families == family
        weights = lofo_analyzer._hierarchical_weights(data, selected)
        result[family] = {
            name: float(np.sum(weights[selected] * np.asarray(values)[selected]))
            for name, values in components.items()
        }
    return result


def _resource_metrics(
    data: lofo.LofoDataset,
    trace: policy.PolicyTrace,
    budget_us: int,
) -> dict[str, Any]:
    action = np.asarray(trace.action_probability, dtype=float)
    if action.shape != data.treatment.shape or np.any((action < 0) | (action > 1)):
        raise policy.PolicyError("resource action probabilities differ")
    costs = action * data.canonical_reservation_us
    run_rows = policy.indices_by_run(data)
    family_order, scenarios, runs = _hierarchy(data)
    actions_by_run = {
        run_id: float(np.sum(action[rows])) for run_id, rows in run_rows.items()
    }
    spend_by_run = {
        run_id: float(np.sum(costs[rows])) for run_id, rows in run_rows.items()
    }
    if max(spend_by_run.values()) > budget_us + 1e-6:
        raise policy.PolicyError("policy exceeds the canonical run budget")

    def hierarchical_run_mean(values: dict[str, float]) -> float:
        return float(
            np.mean(
                [
                    np.mean(
                        [
                            np.mean(
                                [
                                    values[run_id]
                                    for run_id in runs[(family, scenario)]
                                ]
                            )
                            for scenario in scenarios[family]
                        ]
                    )
                    for family in family_order
                ]
            )
        )

    weights = lofo_analyzer._hierarchical_weights(
        data, np.ones(len(data.run_ids), dtype=bool)
    )
    return {
        "expected_action_count": float(np.sum(action)),
        "hierarchical_action_fraction": float(np.sum(weights * action)),
        "hierarchical_mean_actions_per_run": hierarchical_run_mean(actions_by_run),
        "hierarchical_mean_canonical_reservation_us_per_run": hierarchical_run_mean(
            spend_by_run
        ),
        "minimum_canonical_reservation_us_per_run": float(
            min(spend_by_run.values())
        ),
        "maximum_canonical_reservation_us_per_run": float(
            max(spend_by_run.values())
        ),
    }


def _zero_trace(data: lofo.LofoDataset) -> policy.PolicyTrace:
    return policy.PolicyTrace(
        action_probability=np.zeros(len(data.run_ids), dtype=float),
        run_details={
            run_id: {
                "actions": 0,
                "canonical_reservation_text_us": "0",
                "canonical_reservation_us": 0.0,
            }
            for run_id in policy.indices_by_run(data)
        },
    )


def _uniform_trace(
    data: lofo.LofoDataset,
    cdf: np.ndarray,
    budget_us: int,
    replications: int,
    salt_base: int,
) -> tuple[policy.PolicyTrace, dict[str, Any]]:
    action_sum = np.zeros(len(data.run_ids), dtype=float)
    run_actions: dict[str, list[float]] = {
        run_id: [] for run_id in policy.indices_by_run(data)
    }
    run_spend: dict[str, list[float]] = {run_id: [] for run_id in run_actions}
    point_values: list[dict[str, float]] = []
    replication_resources: list[dict[str, float]] = []
    weights = lofo_analyzer._hierarchical_weights(
        data, np.ones(len(data.run_ids), dtype=bool)
    )
    none_components = policy.policy_value_components(
        data, cdf, np.zeros(len(data.run_ids), dtype=float)
    )
    all_components = policy.policy_value_components(
        data, cdf, np.ones(len(data.run_ids), dtype=float)
    )
    component_deltas = {
        name: all_components[name] - none_components[name]
        for name in none_components
    }
    for replication, trace in policy.uniform_policy_replications(
        data, budget_us, replications, salt_base
    ):
        if replication != len(point_values):
            raise policy.PolicyError("uniform replication order differs")
        action_sum += trace.action_probability
        point_values.append(
            {
                name: float(
                    np.sum(
                        weights
                        * (
                            none_components[name]
                            + trace.action_probability * component_deltas[name]
                        )
                    )
                )
                for name in none_components
            }
        )
        for run_id, details in trace.run_details.items():
            run_actions[run_id].append(float(details["actions"]))
            run_spend[run_id].append(details["canonical_reservation_us"])
        replication_resources.append(
            {
                "action_count": float(np.sum(trace.action_probability)),
                "mean_actions_per_run": float(
                    np.mean([row["actions"] for row in trace.run_details.values()])
                ),
                "mean_reservation_us_per_run": float(
                    np.mean(
                        [
                            row["canonical_reservation_us"]
                            for row in trace.run_details.values()
                        ]
                    )
                ),
                "maximum_reservation_us_per_run": float(
                    max(
                        row["canonical_reservation_us"]
                        for row in trace.run_details.values()
                    )
                ),
            }
        )
    mean_action = action_sum / replications
    details = {
        run_id: {
            "actions": float(np.mean(run_actions[run_id])),
            "canonical_reservation_text_us": "monte_carlo_mean",
            "canonical_reservation_us": float(np.mean(run_spend[run_id])),
            "minimum_replication_reservation_us": float(min(run_spend[run_id])),
            "maximum_replication_reservation_us": float(max(run_spend[run_id])),
        }
        for run_id in run_actions
    }

    def spread(name: str) -> dict[str, float]:
        values = np.asarray([row[name] for row in point_values], dtype=float)
        return {
            "minimum": float(np.min(values)),
            "p05": float(np.quantile(values, 0.05)),
            "median": float(np.median(values)),
            "p95": float(np.quantile(values, 0.95)),
            "maximum": float(np.max(values)),
        }

    def resource_spread(name: str) -> dict[str, float]:
        values = np.asarray([row[name] for row in replication_resources], dtype=float)
        return {
            "minimum": float(np.min(values)),
            "median": float(np.median(values)),
            "maximum": float(np.max(values)),
        }

    return policy.PolicyTrace(mean_action, details), {
        "replications": replications,
        "salt_base": salt_base,
        "point_value_spread": {
            name: spread(name) for name in point_values[0]
        },
        "resource_spread": {
            name: resource_spread(name) for name in replication_resources[0]
        },
    }


def _evaluate_policy(
    data: lofo.LofoDataset,
    cdf: np.ndarray,
    trace: policy.PolicyTrace,
    plan: BootstrapPlan,
    confidence: float,
    budget_us: int,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    components = policy.policy_value_components(data, cdf, trace.action_probability)
    points = policy.weighted_policy_values(data, components)
    names, samples = bootstrap_policy_values(data, components, plan)
    sample_map = {name: samples[:, index] for index, name in enumerate(names)}
    result = {
        "policy_value": {
            name: _interval(points[name], sample_map[name], confidence)
            for name in names
        },
        "resource": _resource_metrics(data, trace, budget_us),
        "family_value": _family_values(data, components),
    }
    return result, sample_map


def _fraction_gain(
    no_copy: float,
    candidate: float,
    oracle: float,
) -> float:
    denominator = no_copy - oracle
    if denominator <= 0:
        raise policy.PolicyError("oracle has no positive deadline gain")
    return (no_copy - candidate) / denominator


def _fraction_gain_interval(
    no_copy: float,
    candidate: float,
    oracle: float,
    no_copy_samples: np.ndarray,
    candidate_samples: np.ndarray,
    oracle_samples: np.ndarray,
    confidence: float,
) -> dict[str, Any]:
    denominators = no_copy_samples - oracle_samples
    valid = np.isfinite(denominators) & (denominators > 0)
    ratios = (no_copy_samples[valid] - candidate_samples[valid]) / denominators[valid]
    estimate = None
    if no_copy > oracle:
        estimate = _fraction_gain(no_copy, candidate, oracle)
    if len(ratios) < 100:
        return {
            "estimate": estimate,
            "ci_lower": None,
            "ci_upper": None,
            "bootstrap_valid_fraction": float(np.mean(valid)),
            "validity_rule": "oracle deadline gain strictly positive",
        }
    alpha = (1.0 - confidence) / 2.0
    return {
        "estimate": estimate,
        "ci_lower": float(np.quantile(ratios, alpha)),
        "ci_upper": float(np.quantile(ratios, 1.0 - alpha)),
        "bootstrap_valid_fraction": float(np.mean(valid)),
        "validity_rule": "oracle deadline gain strictly positive",
    }


def _analyze_bundle(
    dataset_dir: Path | str,
    prediction_dir: Path | str,
) -> ReplayBundle:
    """Run every frozen replay policy and shared uncertainty calculation."""

    contract = policy.load_policy_contract()
    data = lofo.load_dataset(dataset_dir)
    predictions = policy.load_lofo_predictions(prediction_dir, data)
    objectives = policy.predicted_objectives(data, predictions.cdf)
    budget = contract["resource"]["budget_us_per_60s_run"]
    uniform_spec = next(
        row
        for row in contract["policies"]
        if row["id"] == "uniform_random_t2_same_canonical_budget"
    )
    traces: dict[str, policy.PolicyTrace] = {
        "no_secondary_copy": _zero_trace(data),
        "myopic_deadline_risk_same_canonical_budget": policy.exact_policy(
            data,
            objectives["primary_deadline_risk"],
            np.zeros(len(data.run_ids), dtype=float),
            budget,
        ),
        "cross_fitted_scenario_resource_oracle_v1": policy.exact_policy(
            data,
            objectives["deadline_rescue"],
            objectives["tail18_acceleration"],
            budget,
        ),
    }
    traces["uniform_random_t2_same_canonical_budget"], uniform_diagnostics = (
        _uniform_trace(
            data,
            predictions.cdf,
            budget,
            uniform_spec["replications"],
            uniform_spec["salt_base"],
        )
    )
    uncertainty = contract["uncertainty"]
    plan = make_bootstrap_plan(
        data, uncertainty["replications"], uncertainty["random_seed"]
    )
    evaluated: dict[str, Any] = {}
    bootstrap: dict[str, dict[str, np.ndarray]] = {}
    for policy_id in POLICY_ORDER:
        evaluated[policy_id], bootstrap[policy_id] = _evaluate_policy(
            data,
            predictions.cdf,
            traces[policy_id],
            plan,
            uncertainty["confidence_level"],
            budget,
        )
    evaluated["uniform_random_t2_same_canonical_budget"][
        "monte_carlo"
    ] = uniform_diagnostics
    oracle_id = "cross_fitted_scenario_resource_oracle_v1"
    none_id = "no_secondary_copy"
    none_point = evaluated[none_id]["policy_value"]["deadline_miss"]["estimate"]
    oracle_point = evaluated[oracle_id]["policy_value"]["deadline_miss"]["estimate"]
    oracle_samples = bootstrap[oracle_id]["deadline_miss"]
    none_samples = bootstrap[none_id]["deadline_miss"]
    contrasts: dict[str, Any] = {}
    for policy_id in POLICY_ORDER:
        point = evaluated[policy_id]["policy_value"]["deadline_miss"]["estimate"]
        samples = bootstrap[policy_id]["deadline_miss"]
        policy_contrasts = {
            f"policy_minus_oracle_{metric}": _interval(
                evaluated[policy_id]["policy_value"][metric]["estimate"]
                - evaluated[oracle_id]["policy_value"][metric]["estimate"],
                bootstrap[policy_id][metric] - bootstrap[oracle_id][metric],
                uncertainty["confidence_level"],
            )
            for metric in (
                "deadline_miss",
                "completed_late18",
                "bounded_on_time_late18",
            )
        }
        contrasts[policy_id] = {
            **policy_contrasts,
            "fraction_of_oracle_deadline_gain_realized": _fraction_gain_interval(
                none_point,
                point,
                oracle_point,
                none_samples,
                samples,
                oracle_samples,
                uncertainty["confidence_level"],
            ),
        }
    oracle_action = traces[oracle_id].action_probability > 0
    selected_oracle = int(np.sum(oracle_action))
    oracle_fallback = int(
        np.sum(oracle_action & (predictions.ood_fallback.astype(bool)))
    )
    target = contract["go_no_go"]
    if none_point <= 0:
        raise policy.PolicyError("no-copy deadline-miss estimate is non-positive")
    oracle_improvement = (none_point - oracle_point) / none_point
    valid_improvement = np.isfinite(none_samples) & (none_samples > 0)
    improvement_samples = (
        none_samples[valid_improvement] - oracle_samples[valid_improvement]
    ) / none_samples[valid_improvement]
    if len(improvement_samples) < max(100, int(0.5 * len(none_samples))):
        raise policy.PolicyError("too few bootstrap samples identify relative gain")
    improvement_interval = _interval(
        oracle_improvement,
        improvement_samples,
        uncertainty["confidence_level"],
    )
    improvement_interval["bootstrap_valid_fraction"] = float(
        np.mean(valid_improvement)
    )
    result = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_id": contract["analysis_id"],
        "evidence_role": "predeclared_randomized_lofo_resource_ceiling",
        "population": {
            "row_count": len(data.run_ids),
            "run_count": len(set(data.run_ids)),
            "scenario_count": len(set(data.scenario_ids)),
            "family_count": len(set(data.family_ids)),
            "scope": contract["population"]["scope"],
            "all_generated_frame_claim_permitted": False,
        },
        "resource": contract["resource"],
        "uncertainty": {
            **uncertainty,
            "shared_resamples_for_all_policy_contrasts": True,
        },
        "policies": evaluated,
        "contrasts_against_resource_oracle": contrasts,
        "oracle_diagnostics": {
            "selected_rows": selected_oracle,
            "selected_rows_marked_ood_fallback": oracle_fallback,
            "selected_ood_fallback_fraction": (
                oracle_fallback / selected_oracle if selected_oracle else 0.0
            ),
            "relative_deadline_miss_improvement_over_no_copy": improvement_interval,
            "go_no_go": {
                "deadline_target": {
                    "threshold": target[
                        "oracle_target_deadline_miss_probability_below"
                    ],
                    "actual": oracle_point,
                    "ci_upper": evaluated[oracle_id]["policy_value"][
                        "deadline_miss"
                    ]["ci_upper"],
                    "pass": evaluated[oracle_id]["policy_value"]["deadline_miss"][
                        "ci_upper"
                    ]
                    < target["oracle_target_deadline_miss_probability_below"],
                },
                "relative_improvement_target": {
                    "threshold": target[
                        "oracle_fractional_improvement_over_no_copy_at_least"
                    ],
                    "actual": oracle_improvement,
                    "ci_lower": improvement_interval["ci_lower"],
                    "pass": improvement_interval["ci_lower"]
                    >= target[
                        "oracle_fractional_improvement_over_no_copy_at_least"
                    ],
                },
            },
        },
        "interpretation_limits": contract["interpretation_limits"],
    }
    return ReplayBundle(result, data, predictions, objectives, traces)


def analyze(
    dataset_dir: Path | str,
    prediction_dir: Path | str,
) -> dict[str, Any]:
    """Return frozen replay metrics without publishing artifacts."""

    return _analyze_bundle(dataset_dir, prediction_dir).result


def _write_family_csv(path: Path, result: dict[str, Any]) -> None:
    fields = [
        "policy_id",
        "family_id",
        "deadline_miss",
        "completed_late18",
        "bounded_on_time_late18",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for policy_id in POLICY_ORDER:
            families = result["policies"][policy_id]["family_value"]
            for family_id, values in families.items():
                writer.writerow(
                    {
                        "policy_id": policy_id,
                        "family_id": family_id,
                        **{name: format(values[name], ".17g") for name in fields[2:]},
                    }
                )


def _write_actions(path: Path, bundle: ReplayBundle) -> None:
    fields = [
        "analysis_schema_version",
        "seed",
        "run_number",
        "run_id",
        "frame_id",
        "scenario_id",
        "family_id",
        "parameter_sample",
        "deadline_rescue_probability",
        "tail18_acceleration_probability",
        "primary_deadline_risk",
        "canonical_reservation_us",
        "ood_fallback",
        *[f"action_probability_{policy_id}" for policy_id in POLICY_ORDER],
    ]
    data = bundle.data
    with path.open("wb") as binary:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=binary, mtime=0
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                for index in range(len(data.run_ids)):
                    writer.writerow(
                        {
                            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
                            "seed": int(data.seeds[index]),
                            "run_number": int(data.run_numbers[index]),
                            "run_id": data.run_ids[index],
                            "frame_id": int(data.frame_ids[index]),
                            "scenario_id": data.scenario_ids[index],
                            "family_id": data.family_ids[index],
                            "parameter_sample": int(data.parameter_samples[index]),
                            "deadline_rescue_probability": format(
                                bundle.objectives["deadline_rescue"][index], ".17g"
                            ),
                            "tail18_acceleration_probability": format(
                                bundle.objectives["tail18_acceleration"][index],
                                ".17g",
                            ),
                            "primary_deadline_risk": format(
                                bundle.objectives["primary_deadline_risk"][index],
                                ".17g",
                            ),
                            "canonical_reservation_us": (
                                data.canonical_reservation_texts[index]
                            ),
                            "ood_fallback": int(
                                bundle.predictions.ood_fallback[index]
                            ),
                            **{
                                f"action_probability_{policy_id}": format(
                                    bundle.traces[policy_id].action_probability[index],
                                    ".17g",
                                )
                                for policy_id in POLICY_ORDER
                            },
                        }
                    )


def _percent(value: float) -> str:
    return f"{100 * value:.4f}%"


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Environment-generalization policy replay",
        "",
        (
            "This is a randomized, leave-one-family-out resource-ceiling analysis "
            "on action-clean eligible frames. It is not an all-generated-frame or "
            "closed-loop qualification result."
        ),
        "",
        (
            "| Policy | Deadline miss (95% CI) | Completed late18 (HT) | "
            "Actions/run | Reservation/run |"
        ),
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    labels = {
        "no_secondary_copy": "No secondary copy",
        "uniform_random_t2_same_canonical_budget": "Uniform random",
        "myopic_deadline_risk_same_canonical_budget": "Myopic primary risk",
        "cross_fitted_scenario_resource_oracle_v1": "Cross-fitted resource oracle",
    }
    for policy_id in POLICY_ORDER:
        row = result["policies"][policy_id]
        miss = row["policy_value"]["deadline_miss"]
        late = row["policy_value"]["completed_late18"]
        resource = row["resource"]
        reservation_ms = (
            resource["hierarchical_mean_canonical_reservation_us_per_run"] / 1000
        )
        lines.append(
            f"| {labels[policy_id]} | {_percent(miss['estimate'])} "
            f"[{_percent(miss['ci_lower'])}, {_percent(miss['ci_upper'])}] | "
            f"{_percent(late['estimate'])} | "
            f"{resource['hierarchical_mean_actions_per_run']:.2f} | "
            f"{reservation_ms:.2f} ms |"
        )
    oracle = result["oracle_diagnostics"]
    relative_improvement = oracle[
        "relative_deadline_miss_improvement_over_no_copy"
    ]["estimate"]
    lines.extend(
        [
            "",
            "## Ceiling decision",
            "",
            (
                f"The cross-fitted resource oracle changes the eligible-frame "
                f"deadline-miss estimate by "
                f"{100 * relative_improvement:.2f}% "
                "relative to no copy. This oracle sees all predicted scores in a run "
                "and is not deployable or perfect-information."
            ),
            "",
            (
                f"It selected {oracle['selected_rows']} rows; "
                f"{oracle['selected_rows_marked_ood_fallback']} "
                "were marked for conservative OOD fallback."
            ),
            "",
            (
                "Completed-late18 uses the declared high-variance "
                "Horvitz-Thompson estimator. Completed-frame P99 still requires "
                "actual closed-loop simulation."
            ),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_analysis(
    dataset_dir: Path | str,
    prediction_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Analyze and atomically publish checksum-closed policy outputs."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise policy.PolicyError(
            f"refusing to overwrite output directory: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
    )
    try:
        bundle = _analyze_bundle(dataset_dir, prediction_dir)
        result = bundle.result
        contract = policy.load_policy_contract()
        result["provenance"] = {
            "policy_contract": {
                "path": str(policy.POLICY_CONTRACT_PATH),
                "sha256": policy.POLICY_CONTRACT_SHA256,
            },
            "dataset_dir": str(Path(dataset_dir).resolve()),
            "dataset_manifest_sha256": _sha256(
                Path(dataset_dir).resolve() / "artifact_manifest.json"
            ),
            "prediction_dir": str(Path(prediction_dir).resolve()),
            "prediction_manifest_sha256": _sha256(
                Path(prediction_dir).resolve() / lofo_analyzer.OUTPUT_MANIFEST
            ),
            "analysis_sources_sha256": {
                str(Path(__file__).resolve().relative_to(lofo.ROOT)): _sha256(
                    Path(__file__).resolve()
                ),
                str(Path(policy.__file__).resolve().relative_to(lofo.ROOT)): _sha256(
                    Path(policy.__file__).resolve()
                ),
            },
            "software": {
                "python": platform.python_version(),
                "numpy": importlib.metadata.version("numpy"),
                "git_commit": _git_value("rev-parse", "HEAD"),
                "git_status_porcelain": _git_value(
                    "status", "--porcelain", "--untracked-files=all"
                ),
            },
            "contract_source_count": len(contract["sources"]),
        }
        metrics_path = temporary / OUTPUT_METRICS
        report_path = temporary / OUTPUT_REPORT
        family_path = temporary / OUTPUT_FAMILY_CSV
        actions_path = temporary / OUTPUT_ACTIONS
        _write_json(metrics_path, result)
        _write_report(report_path, result)
        _write_family_csv(family_path, result)
        _write_actions(actions_path, bundle)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "artifacts_sha256": {
                OUTPUT_METRICS: _sha256(metrics_path),
                OUTPUT_REPORT: _sha256(report_path),
                OUTPUT_FAMILY_CSV: _sha256(family_path),
                OUTPUT_ACTIONS: _sha256(actions_path),
            },
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="evaluate frozen environment-generalization resource policies"
    )
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--prediction-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    result = run_analysis(args.dataset_dir, args.prediction_dir, args.output_dir)
    oracle = result["policies"]["cross_fitted_scenario_resource_oracle_v1"]
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "row_count": result["population"]["row_count"],
                "oracle_deadline_miss": oracle["policy_value"]["deadline_miss"][
                    "estimate"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
