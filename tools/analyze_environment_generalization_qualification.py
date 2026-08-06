#!/usr/bin/env python3
"""Analyze the frozen held-out environment closed-loop qualification."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import run_experiments
from analyze_paired_value_t2_str_qualification import (
    BUILD_IDENTITY_FIELDS,
    MINIMUM_COMPLETED_FRAMES,
    _background_metrics,
    _frame_metrics,
    _sender_airtime_us,
    _type7_quantile,
)
from generate_environment_generalization_qualification_v1 import (
    load_contract as load_execution_contract,
)
from validate_outputs import ValidationError, validate_run


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_CONTRACT_PATH = ROOT / (
    "experiments/model-selection/"
    "environment-generalization-qualification-analysis-v1.json"
)
EXECUTION_CONTRACT_PATH = ROOT / (
    "experiments/model-selection/"
    "environment-generalization-qualification-execution-v1.json"
)
CONFIG_PATH = ROOT / (
    "experiments/configs/"
    "environment_generalization_closed_loop_qualification_v1.yaml"
)
ARTIFACT_MANIFEST_PATH = ROOT / (
    "experiments/model-selection/"
    "environment-generalization-qualification-artifacts-v1.json"
)

ANALYSIS_ID = "environment-generalization-qualification-analysis-v1"
EXPECTED_EXPERIMENT = "environment-generalization-closed-loop-qualification-v1"
MANIFEST_SCHEMA_VERSION = 2
ARM_IDS = (
    "str_mlo_nmaxinflights_1",
    "score_aware_t2_v2",
    "distributional_shadow_t2",
)
ARM_LABELS = {
    "str_mlo_nmaxinflights_1": "STR MLO",
    "score_aware_t2_v2": "Score-aware T2 V2",
    "distributional_shadow_t2": "Distributional-shadow T2",
}
METRICS = (
    "all_generated_deadline_miss_rate",
    "completed_frame_hf7_p99_us",
    "sender_airtime_us",
    "background_throughput_mbps",
)
CONTRAST_METRICS = (
    "deadline_miss_delta",
    "completed_p99_delta_us",
    "sender_airtime_ratio",
    "background_throughput_loss",
    "relative_deadline_miss_reduction",
)
EXPECTED_PLOTS = (
    "aggregate_policy_metrics",
    "family_deadline_miss_delta",
    "family_completed_p99_delta",
    "family_resource_effects",
    "scenario_miss_p99_delta",
    "scenario_distributional_vs_v2",
    "completion_latency_cdf",
    "completion_latency_pdf",
    "deadline_miss_and_completion",
    "deadline_miss_burst_cdf",
    "resource_summary",
)


class QualificationAnalysisError(RuntimeError):
    """Raised when qualification evidence or analysis differs from contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationAnalysisError(message)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationAnalysisError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise QualificationAnalysisError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _validate_source(spec: dict[str, Any], label: str) -> Path:
    _require(
        isinstance(spec, dict) and set(spec) == {"path", "sha256"},
        f"{label} source declaration differs",
    )
    path = (ROOT / spec["path"]).resolve()
    _require(path.is_relative_to(ROOT), f"{label} escapes the repository")
    _require(path.is_file() and not path.is_symlink(), f"{label} is absent")
    _require(_sha256(path) == spec["sha256"], f"{label} hash differs")
    return path


def load_analysis_contract(
    path: Path = ANALYSIS_CONTRACT_PATH,
) -> dict[str, Any]:
    """Load and fail closed on the frozen analysis semantics."""

    contract = _read_json(path.resolve())
    expected_keys = {
        "schema_version",
        "analysis_contract_id",
        "status",
        "purpose",
        "execution_contract",
        "generated_matrix",
        "population",
        "arms",
        "raw_evidence",
        "per_run_metrics",
        "estimand",
        "bootstrap",
        "contrasts",
        "aggregate_gates_against_str",
        "per_family_safety_gates_against_str",
        "oracle_estimand_boundary",
        "outputs",
        "plots",
        "interpretation_limits",
    }
    _require(set(contract) == expected_keys, "analysis contract keys differ")
    _require(
        contract["schema_version"] == 1
        and contract["analysis_contract_id"] == ANALYSIS_ID
        and contract["status"]
        == "frozen_before_closed_loop_qualification_outcomes",
        "analysis contract identity differs",
    )
    execution_path = _validate_source(
        contract["execution_contract"], "execution contract"
    )
    _require(
        execution_path == EXECUTION_CONTRACT_PATH.resolve(),
        "execution contract path differs",
    )
    execution = load_execution_contract(execution_path)
    amendment = execution.get("pre_outcome_runtime_amendment")
    _require(
        isinstance(amendment, dict)
        and amendment.get("performance_outcomes_inspected") is False
        and amendment.get("failed_attempt_root_is_not_qualification_evidence") is True
        and amendment.get(
            "all_576_runs_must_be_reexecuted_from_one_clean_repaired_commit"
        )
        is True,
        "qualification pre-outcome runtime amendment differs",
    )
    matrix = contract["generated_matrix"]
    _require(
        set(matrix)
        == {
            "path",
            "sha256",
            "artifact_manifest_path",
            "artifact_manifest_sha256",
            "resolved_matrix_sha256",
        },
        "generated matrix declaration differs",
    )
    config_path = _validate_source(
        {"path": matrix["path"], "sha256": matrix["sha256"]},
        "generated qualification matrix",
    )
    artifact_path = _validate_source(
        {
            "path": matrix["artifact_manifest_path"],
            "sha256": matrix["artifact_manifest_sha256"],
        },
        "qualification artifact manifest",
    )
    _require(config_path == CONFIG_PATH.resolve(), "qualification config path differs")
    _require(
        artifact_path == ARTIFACT_MANIFEST_PATH.resolve(),
        "qualification artifact manifest path differs",
    )
    document = run_experiments.load_yaml(config_path)
    _require(
        run_experiments.matrix_sha256(document) == matrix["resolved_matrix_sha256"],
        "resolved qualification matrix hash differs",
    )
    artifact = _read_json(artifact_path)
    _require(
        artifact.get("resolved_matrix_sha256") == matrix["resolved_matrix_sha256"],
        "qualification artifact manifest matrix hash differs",
    )
    population = contract["population"]
    expected_population = {
        "family_count": 6,
        "scenarios_per_family": 8,
        "replicates_per_scenario": 4,
        "paired_unit_count": 192,
        "arm_count": 3,
        "simulation_run_count": 576,
    }
    _require(
        all(population.get(key) == value for key, value in expected_population.items()),
        "analysis population dimensions differ",
    )
    _require(
        population.get("reserved_confirmation_seeds")
        == {"minimum": 1301, "maximum": 1348, "required_overlap_count": 0},
        "reserved confirmation boundary differs",
    )
    raw_evidence = contract["raw_evidence"]
    _require(
        raw_evidence.get(
            "require_generalization_frame_profile_on_both_selective_policy_arms"
        )
        is True
        and raw_evidence.get(
            "exclude_failed_attempt_checkout_ff6d8b8_from_all_estimands"
        )
        is True,
        "qualification runtime-repair evidence boundary differs",
    )
    _require(
        tuple(arm.get("arm_id") for arm in contract["arms"]) == ARM_IDS,
        "analysis arm order differs",
    )
    execution_arms = {
        arm["arm_id"]: (arm["topology"], arm["policy"])
        for arm in execution["arms"]
    }
    _require(
        all(
            execution_arms.get(arm["arm_id"])
            == (arm.get("topology"), arm.get("policy"))
            for arm in contract["arms"]
        ),
        "analysis arms differ from execution arms",
    )
    bootstrap = contract["bootstrap"]
    execution_bootstrap = execution["analysis"]["bootstrap"]
    _require(
        bootstrap["method"] == execution_bootstrap["method"]
        and bootstrap["replications"] == execution_bootstrap["replications"] == 10_000
        and bootstrap["confidence_level"]
        == execution_bootstrap["confidence_level"]
        == 0.95
        and bootstrap["random_seed"]
        == execution_bootstrap["random_seed"]
        == 8231415969872262531,
        "analysis bootstrap differs from execution freeze",
    )
    _require(
        contract["per_run_metrics"]["completed_frame_hf7_p99_us"][
            "minimum_completed_frames_per_run"
        ]
        == MINIMUM_COMPLETED_FRAMES,
        "minimum completed-frame support differs from implementation",
    )
    parent = _read_json(ROOT / execution["parent_generalization_contract"]["path"])
    parent_qualification = parent["closed_loop_qualification"]
    parent_aggregate = copy.deepcopy(parent_qualification["aggregate_gates"])
    parent_aggregate.pop("fraction_of_oracle_deadline_gain_realized_at_least")
    _require(
        contract["aggregate_gates_against_str"] == parent_aggregate,
        "assessable aggregate gates differ from parent contract",
    )
    _require(
        contract["per_family_safety_gates_against_str"]
        == parent_qualification["per_family_safety_gates"],
        "per-family gates differ from parent contract",
    )
    _require(
        contract["oracle_estimand_boundary"]
        ["fraction_of_oracle_deadline_gain_realized"]["status"]
        == "not_assessable"
        and contract["oracle_estimand_boundary"]["parent_promotion_readiness"][
            "status"
        ]
        == "not_assessable",
        "oracle estimand boundary differs",
    )
    _require(tuple(contract["plots"]) == EXPECTED_PLOTS, "plot contract differs")
    return contract


def _git_identity() -> dict[str, Any]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise QualificationAnalysisError(f"cannot inspect Git identity: {error}") from error
    _require(len(head) == 40 and all(char in "0123456789abcdef" for char in head),
             "invalid analyzer Git identity")
    _require(not status.strip(), "analyzer checkout is not clean")
    return {"project_commit": head, "worktree_clean": True}


def _arm_map(contract: dict[str, Any]) -> dict[tuple[str, str], str]:
    result = {
        (arm["topology"], arm["policy"]): arm["arm_id"]
        for arm in contract["arms"]
    }
    _require(len(result) == len(ARM_IDS), "analysis arm identities are not unique")
    return result


def _manifest_root(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve()
    if resolved.is_file():
        _require(
            resolved.name == "experiment_manifest.json",
            "campaign input file must be experiment_manifest.json",
        )
        return resolved.parent, resolved
    candidates = [
        resolved / "experiment_manifest.json",
        resolved / "runs" / "experiment_manifest.json",
    ]
    matches = [candidate for candidate in candidates if candidate.is_file()]
    _require(
        len(matches) == 1,
        "expected exactly one qualification experiment manifest under campaign input",
    )
    return matches[0].parent, matches[0]


def _family_scenario_order(
    specs: Sequence[dict[str, Any]],
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    families: list[str] = []
    scenarios: dict[str, list[str]] = {}
    for spec in specs:
        scenario = spec["scenario"]
        family = scenario["family_id"]
        scenario_id = scenario["scenario_id"]
        if family not in scenarios:
            families.append(family)
            scenarios[family] = []
        if scenario_id not in scenarios[family]:
            scenarios[family].append(scenario_id)
    return tuple(families), {
        family: tuple(scenarios[family]) for family in families
    }


def validate_campaign_manifest(
    campaign_input: Path,
    contract: dict[str, Any],
    analyzer_git: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], tuple[str, ...], dict[str, tuple[str, ...]]]:
    """Validate exact expansion, run identities, and complete manifest closure."""

    run_root, manifest_path = _manifest_root(campaign_input)
    manifest = _read_json(manifest_path)
    required = {
        "schema_version",
        "experiment",
        "matrix_sha256",
        "config_file",
        "project_commit",
        "ns3_upstream_commit",
        "runtime_contract_id",
        "runtime_contract_sha256",
        "source_artifacts",
        "scenario_schema_version",
        "scenario_instances",
        "runs",
    }
    _require(not (required - set(manifest)), "qualification manifest fields are missing")
    _require(
        manifest["schema_version"] == MANIFEST_SCHEMA_VERSION,
        "qualification manifest schema differs",
    )
    _require(manifest["experiment"] == EXPECTED_EXPERIMENT,
             "qualification experiment identity differs")
    matrix = contract["generated_matrix"]
    _require(
        manifest["matrix_sha256"] == matrix["resolved_matrix_sha256"],
        "qualification manifest matrix hash differs",
    )
    _require(
        manifest["project_commit"] == analyzer_git["project_commit"],
        "campaign project commit differs from clean analyzer checkout",
    )
    _require(
        manifest["ns3_upstream_commit"] == run_experiments.NS3_UPSTREAM_COMMIT,
        "qualification ns-3 commit differs",
    )
    config_serialized = manifest["config_file"]
    _require(
        isinstance(config_serialized, str)
        and Path(config_serialized).name == CONFIG_PATH.name,
        "qualification manifest config identity differs",
    )
    document = run_experiments.load_yaml(CONFIG_PATH)
    _require(
        run_experiments.matrix_sha256(document) == manifest["matrix_sha256"],
        "local qualification matrix differs from manifest",
    )
    runtime = run_experiments.validate_runtime_contract(document)
    _require(runtime is not None, "qualification runtime closure is absent")
    _require(
        manifest["runtime_contract_id"] == runtime["runtime_contract_id"]
        and manifest["runtime_contract_sha256"] == runtime["runtime_contract_sha256"]
        and _canonical_json(manifest["source_artifacts"])
        == _canonical_json(runtime["source_artifacts"]),
        "qualification manifest runtime closure differs",
    )
    specs = run_experiments.expand_config(document)
    expected: dict[str, dict[str, Any]] = {}
    arm_map = _arm_map(contract)
    for spec in specs:
        run_id = run_experiments.derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            manifest["ns3_upstream_commit"],
            manifest["project_commit"],
            runtime,
            spec["scenario"],
        )
        _require(run_id not in expected, "qualification expansion has a duplicate run ID")
        identity = (spec["config"]["topology"], spec["config"]["policy"])
        _require(identity in arm_map, "qualification expansion contains an unknown arm")
        expected[run_id] = {**spec, "run_id": run_id, "arm_id": arm_map[identity]}
    population = contract["population"]
    _require(
        len(expected) == population["simulation_run_count"] == 576,
        "qualification expansion count differs",
    )
    expected_scenarios: dict[str, dict[str, Any]] = {}
    for spec in specs:
        scenario = spec["scenario"]
        previous = expected_scenarios.setdefault(scenario["scenario_id"], scenario)
        _require(
            _canonical_json(previous) == _canonical_json(scenario),
            "scenario identity differs across expanded arms",
        )
    _require(
        manifest["scenario_schema_version"] == 1
        and _canonical_json(manifest["scenario_instances"])
        == _canonical_json(
            [expected_scenarios[key] for key in sorted(expected_scenarios)]
        ),
        "qualification manifest scenario catalog differs",
    )
    runs = manifest["runs"]
    _require(isinstance(runs, list), "qualification manifest runs are invalid")
    manifest_by_id: dict[str, dict[str, Any]] = {}
    for row in runs:
        _require(isinstance(row, dict), "qualification manifest run is not an object")
        run_id = row.get("run_id")
        _require(
            isinstance(run_id, str) and run_id not in manifest_by_id,
            "qualification manifest has a duplicate or invalid run ID",
        )
        _require(
            row.get("status") == "complete" and row.get("directory") == run_id,
            f"qualification run {run_id} is not canonical and complete",
        )
        manifest_by_id[run_id] = row
    _require(
        set(manifest_by_id) == set(expected),
        "qualification manifest does not contain the exact 576-run matrix",
    )
    reserved = population["reserved_confirmation_seeds"]
    jobs: list[dict[str, Any]] = []
    for run_id, spec in expected.items():
        row = manifest_by_id[run_id]
        _require(
            row.get("seed") == spec["seed"]
            and row.get("run") == spec["run"]
            and _canonical_json(row.get("config"))
            == _canonical_json(spec["config"])
            and _canonical_json(row.get("scenario"))
            == _canonical_json(spec["scenario"]),
            f"qualification run {run_id} identity differs from frozen expansion",
        )
        _require(
            not reserved["minimum"] <= spec["seed"] <= reserved["maximum"],
            "qualification matrix consumes a reserved confirmation seed",
        )
        run_dir = run_root / run_id
        _require(run_dir.is_dir() and not run_dir.is_symlink(),
                 f"qualification run directory is absent: {run_id}")
        scenario = spec["scenario"]
        jobs.append(
            {
                "run_id": run_id,
                "run_dir": str(run_dir.resolve()),
                "project_commit": manifest["project_commit"],
                "ns3_upstream_commit": manifest["ns3_upstream_commit"],
                "arm_id": spec["arm_id"],
                "family_id": scenario["family_id"],
                "scenario_id": scenario["scenario_id"],
                "parameter_sample": scenario["parameter_sample"],
                "seed": spec["seed"],
                "run": spec["run"],
                "expected_config": spec["config"],
            }
        )
    families, scenarios = _family_scenario_order(specs)
    _require(
        len(families) == population["family_count"]
        and all(
            len(scenarios[family]) == population["scenarios_per_family"]
            for family in families
        ),
        "qualification family/scenario dimensions differ",
    )
    arm_order = {arm: index for index, arm in enumerate(ARM_IDS)}
    family_order = {family: index for index, family in enumerate(families)}
    scenario_order = {
        scenario: index
        for family in families
        for index, scenario in enumerate(scenarios[family])
    }
    jobs.sort(
        key=lambda row: (
            family_order[row["family_id"]],
            scenario_order[row["scenario_id"]],
            row["seed"],
            row["run"],
            arm_order[row["arm_id"]],
        )
    )
    identity = {
        "path": str(manifest_path),
        "sha256": _sha256(manifest_path),
        "run_root": str(run_root),
        "schema_version": manifest["schema_version"],
        "experiment": manifest["experiment"],
        "matrix_sha256": manifest["matrix_sha256"],
        "project_commit": manifest["project_commit"],
        "ns3_upstream_commit": manifest["ns3_upstream_commit"],
        "runtime_contract_id": manifest["runtime_contract_id"],
        "runtime_contract_sha256": manifest["runtime_contract_sha256"],
        "source_artifacts": copy.deepcopy(manifest["source_artifacts"]),
        "complete_run_count": len(jobs),
        "expanded_matrix_identity_verified": True,
        "derived_run_ids_verified": True,
        "scenario_identities_verified": True,
    }
    return identity, jobs, families, scenarios


def _validate_observation(job: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=job["run_id"],
            expected_project_commit=job["project_commit"],
            expected_ns3_commit=job["ns3_upstream_commit"],
        )
    except ValidationError as error:
        raise QualificationAnalysisError(
            f"{run_dir}: strict output validation failed: {error}"
        ) from error
    _require(validation.get("valid") is True, f"{run_dir}: validator did not return valid")
    config = _read_json(run_dir / "resolved_config.json")
    expected = job["expected_config"]
    expected_frame_profile = expected.get("prediction", {}).get(
        "paired_temporal_t2_frame_profile"
    )
    _require(
        config.get("run_id") == job["run_id"]
        and config.get("seed") == job["seed"]
        and config.get("run") == job["run"]
        and config.get("topology") == expected.get("topology")
        and config.get("policy") == expected.get("policy"),
        f"{run_dir}: resolved run/arm identity differs from frozen expansion",
    )
    if job["arm_id"] == "str_mlo_nmaxinflights_1":
        _require(
            expected_frame_profile is None
            and "pairedTemporalT2FrameProfile" not in config,
            f"{run_dir}: STR arm unexpectedly selects a paired temporal-T2 frame profile",
        )
    else:
        _require(
            expected_frame_profile == "environment_generalization_v1"
            and config.get("pairedTemporalT2FrameProfile")
            == expected_frame_profile
            and config.get("environment")
            == "held_out_environment_generalization_v1",
            f"{run_dir}: selective arm generalization frame profile differs",
        )
    build = _read_json(run_dir / "build_info.json")
    build_identity: dict[str, str] = {}
    for field in BUILD_IDENTITY_FIELDS:
        value = build.get(field)
        _require(isinstance(value, str) and value, f"{run_dir}: invalid build {field}")
        build_identity[field] = value
    metrics = _frame_metrics(run_dir, config)
    background = _background_metrics(run_dir, config)
    return {
        "family_id": job["family_id"],
        "scenario_id": job["scenario_id"],
        "parameter_sample": job["parameter_sample"],
        "seed": job["seed"],
        "run": job["run"],
        "arm_id": job["arm_id"],
        "run_id": job["run_id"],
        "run_dir": job["run_dir"],
        "generated_frame_count": metrics["generated_frame_count"],
        "completed_frame_count": metrics["completed_frame_count"],
        "deadline_miss_count": metrics["deadline_miss_count"],
        "all_generated_deadline_miss_rate": metrics[
            "all_generated_deadline_miss_rate"
        ],
        "completed_frame_hf7_p99_us": metrics["completed_frame_p99_us"],
        "sender_airtime_us": _sender_airtime_us(run_dir),
        "background_bytes_received": background["background_bytes_received"],
        "background_throughput_mbps": background["background_throughput_mbps"],
        "build_identity": build_identity,
        "strict_validation": validation,
    }


def collect_observations(
    jobs: Sequence[dict[str, Any]], workers: int
) -> list[dict[str, Any]]:
    """Strictly validate and reduce all raw runs in isolated workers."""

    _require(workers > 0, "validation worker count must be positive")
    observations: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_validate_observation, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                observations.append(future.result())
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                if isinstance(error, QualificationAnalysisError):
                    raise error
                raise QualificationAnalysisError(
                    f"validation worker failed for {job['run_id']}: {error}"
                ) from error
    _require(len(observations) == len(jobs), "strict validation result count differs")
    order = {job["run_id"]: index for index, job in enumerate(jobs)}
    observations.sort(key=lambda row: order[row["run_id"]])
    return observations


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    _require(bool(materialized), "cannot average an empty sample")
    _require(all(math.isfinite(value) for value in materialized),
             "analysis sample contains a non-finite value")
    return statistics.mean(materialized)


def build_observation_grid(
    observations: Sequence[dict[str, Any]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    contract: dict[str, Any],
) -> dict[str, dict[str, list[dict[str, dict[str, Any]]]]]:
    """Bind every family/scenario/replicate to all three paired arms."""

    indexed: dict[tuple[str, str, int, int, str], dict[str, Any]] = {}
    for row in observations:
        key = (
            row["family_id"],
            row["scenario_id"],
            row["seed"],
            row["run"],
            row["arm_id"],
        )
        _require(key not in indexed, "duplicate observation identity")
        indexed[key] = row
    grid: dict[str, dict[str, list[dict[str, dict[str, Any]]]]] = {}
    for family in families:
        grid[family] = {}
        for scenario in scenarios[family]:
            units = sorted(
                {
                    (row["seed"], row["run"])
                    for row in observations
                    if row["family_id"] == family and row["scenario_id"] == scenario
                }
            )
            _require(
                len(units) == contract["population"]["replicates_per_scenario"],
                f"scenario {scenario} replicate count differs",
            )
            paired: list[dict[str, dict[str, Any]]] = []
            for seed, run in units:
                arms: dict[str, dict[str, Any]] = {}
                for arm in ARM_IDS:
                    key = (family, scenario, seed, run, arm)
                    _require(key in indexed, f"paired unit {scenario}/{seed}/{run} lacks {arm}")
                    arms[arm] = indexed[key]
                paired.append(arms)
            grid[family][scenario] = paired
    expected = contract["population"]["simulation_run_count"]
    _require(len(indexed) == expected, "observation grid does not consume every run")
    builds = {
        _canonical_json(row["build_identity"])
        for row in observations
    }
    _require(len(builds) == 1, "qualification campaign mixes build identities")
    return grid


def _weighted_arm_means(
    grid: dict[str, dict[str, list[dict[str, dict[str, Any]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
) -> tuple[
    dict[str, dict[str, float]],
    dict[str, dict[str, dict[str, float]]],
    dict[str, dict[str, dict[str, dict[str, float]]]],
]:
    scenario_means: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    family_means: dict[str, dict[str, dict[str, float]]] = {}
    for family in families:
        scenario_means[family] = {}
        for scenario in scenarios[family]:
            scenario_means[family][scenario] = {
                arm: {
                    metric: _mean(
                        unit[arm][metric] for unit in grid[family][scenario]
                    )
                    for metric in METRICS
                }
                for arm in ARM_IDS
            }
        family_means[family] = {
            arm: {
                metric: _mean(
                    scenario_means[family][scenario][arm][metric]
                    for scenario in scenarios[family]
                )
                for metric in METRICS
            }
            for arm in ARM_IDS
        }
    aggregate = {
        arm: {
            metric: _mean(family_means[family][arm][metric] for family in families)
            for metric in METRICS
        }
        for arm in ARM_IDS
    }
    return aggregate, family_means, scenario_means


def _contrast(
    candidate: dict[str, float], baseline: dict[str, float]
) -> dict[str, float | None]:
    _require(
        baseline["sender_airtime_us"] > 0
        and baseline["background_throughput_mbps"] > 0,
        "resource comparison denominator is nonpositive",
    )
    baseline_miss = baseline["all_generated_deadline_miss_rate"]
    _require(baseline_miss >= 0, "baseline miss rate is negative")
    return {
        "deadline_miss_delta": candidate["all_generated_deadline_miss_rate"]
        - baseline["all_generated_deadline_miss_rate"],
        "completed_p99_delta_us": candidate["completed_frame_hf7_p99_us"]
        - baseline["completed_frame_hf7_p99_us"],
        "sender_airtime_ratio": candidate["sender_airtime_us"]
        / baseline["sender_airtime_us"],
        "background_throughput_loss": 1.0
        - candidate["background_throughput_mbps"]
        / baseline["background_throughput_mbps"],
        "relative_deadline_miss_reduction": (
            1.0 - candidate["all_generated_deadline_miss_rate"] / baseline_miss
            if baseline_miss > 0
            else None
        ),
    }


def _interval(point: float, samples: Sequence[float], description: str) -> dict[str, Any]:
    _require(bool(samples) and all(math.isfinite(value) for value in samples),
             "bootstrap sample is empty or non-finite")
    return {
        "estimate": point,
        "ci95_low": _type7_quantile(samples, 0.025),
        "ci95_high": _type7_quantile(samples, 0.975),
        "statistic": description,
    }


def _optional_interval(
    point: float | None,
    samples: Sequence[float | None],
    description: str,
) -> dict[str, Any]:
    if point is None or any(value is None for value in samples):
        return {
            "status": "not_assessable",
            "estimate": point,
            "ci95_low": None,
            "ci95_high": None,
            "statistic": description,
            "reason": (
                "baseline miss rate is zero in the point estimate or at least "
                "one bootstrap resample"
            ),
        }
    return {
        "status": "assessable",
        **_interval(point, [float(value) for value in samples], description),
    }


def hierarchical_bootstrap(
    grid: dict[str, dict[str, list[dict[str, dict[str, Any]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Apply the fixed-family, scenario-then-replicate shared bootstrap."""

    aggregate_point, family_point, scenario_point = _weighted_arm_means(
        grid, families, scenarios
    )
    contrasts = {
        item["comparison_id"]: (item["candidate_arm_id"], item["baseline_arm_id"])
        for item in contract["contrasts"]
    }
    point_contrasts: dict[str, dict[str, float | None]] = {
        comparison: _contrast(aggregate_point[candidate], aggregate_point[baseline])
        for comparison, (candidate, baseline) in contrasts.items()
    }
    family_point_contrasts = {
        comparison: {
            family: _contrast(
                family_point[family][candidate], family_point[family][baseline]
            )
            for family in families
        }
        for comparison, (candidate, baseline) in contrasts.items()
    }
    scenario_point_contrasts = {
        comparison: {
            family: {
                scenario: _contrast(
                    scenario_point[family][scenario][candidate],
                    scenario_point[family][scenario][baseline],
                )
                for scenario in scenarios[family]
            }
            for family in families
        }
        for comparison, (candidate, baseline) in contrasts.items()
    }
    arm_samples = {
        arm: {metric: [] for metric in METRICS} for arm in ARM_IDS
    }
    comparison_samples: dict[str, dict[str, list[float | None]]] = {
        comparison: {metric: [] for metric in CONTRAST_METRICS}
        for comparison in contrasts
    }
    family_comparison_samples: dict[
        str, dict[str, dict[str, list[float | None]]]
    ] = {
        comparison: {
            family: {metric: [] for metric in CONTRAST_METRICS}
            for family in families
        }
        for comparison in contrasts
    }
    bootstrap = contract["bootstrap"]
    generator = random.Random(bootstrap["random_seed"])
    draw_digest = hashlib.sha256()
    scenario_count = contract["population"]["scenarios_per_family"]
    replicate_count = contract["population"]["replicates_per_scenario"]
    for _replication in range(bootstrap["replications"]):
        draw_encoding = bytearray()
        sampled_family: dict[str, dict[str, dict[str, float]]] = {}
        for family in families:
            sums = {
                arm: {metric: 0.0 for metric in METRICS} for arm in ARM_IDS
            }
            for _scenario_draw in range(scenario_count):
                scenario_index = generator.randrange(scenario_count)
                scenario = scenarios[family][scenario_index]
                for _replicate_draw in range(replicate_count):
                    replicate_index = generator.randrange(replicate_count)
                    draw_encoding.extend((scenario_index, replicate_index))
                    unit = grid[family][scenario][replicate_index]
                    for arm in ARM_IDS:
                        for metric in METRICS:
                            sums[arm][metric] += unit[arm][metric]
            denominator = scenario_count * replicate_count
            sampled_family[family] = {
                arm: {
                    metric: sums[arm][metric] / denominator for metric in METRICS
                }
                for arm in ARM_IDS
            }
        draw_digest.update(draw_encoding)
        sampled_aggregate = {
            arm: {
                metric: _mean(
                    sampled_family[family][arm][metric] for family in families
                )
                for metric in METRICS
            }
            for arm in ARM_IDS
        }
        for arm in ARM_IDS:
            for metric in METRICS:
                arm_samples[arm][metric].append(sampled_aggregate[arm][metric])
        for comparison, (candidate, baseline) in contrasts.items():
            aggregate_values = _contrast(
                sampled_aggregate[candidate], sampled_aggregate[baseline]
            )
            for metric in CONTRAST_METRICS:
                comparison_samples[comparison][metric].append(aggregate_values[metric])
            for family in families:
                family_values = _contrast(
                    sampled_family[family][candidate],
                    sampled_family[family][baseline],
                )
                for metric in CONTRAST_METRICS:
                    family_comparison_samples[comparison][family][metric].append(
                        family_values[metric]
                    )
    arm_intervals = {
        arm: {
            metric: _interval(
                aggregate_point[arm][metric],
                arm_samples[arm][metric],
                "equal-family mean of equal-scenario means of replicate-run metric",
            )
            for metric in METRICS
        }
        for arm in ARM_IDS
    }
    comparison_intervals = {
        comparison: {
            metric: _optional_interval(
                point_contrasts[comparison][metric],
                comparison_samples[comparison][metric],
                {
                    "deadline_miss_delta": "candidate minus baseline weighted mean run miss rate",
                    "completed_p99_delta_us": "candidate minus baseline weighted mean run P99",
                    "sender_airtime_ratio": "ratio of candidate and baseline weighted mean airtime",
                    "background_throughput_loss": (
                        "one minus ratio of candidate and baseline weighted mean "
                        "throughput"
                    ),
                    "relative_deadline_miss_reduction": (
                        "one minus ratio of candidate and baseline weighted mean "
                        "miss rate"
                    ),
                }[metric],
            )
            for metric in CONTRAST_METRICS
        }
        for comparison in contrasts
    }
    family_intervals = {
        comparison: {
            family: {
                metric: _optional_interval(
                    family_point_contrasts[comparison][family][metric],
                    family_comparison_samples[comparison][family][metric],
                    "within-family equal-scenario paired contrast",
                )
                for metric in CONTRAST_METRICS
            }
            for family in families
        }
        for comparison in contrasts
    }
    return {
        "method": bootstrap["method"],
        "replications": bootstrap["replications"],
        "confidence_level": bootstrap["confidence_level"],
        "seed": bootstrap["random_seed"],
        "draw_encoding": (
            "for each replication and fixed family: uint8 scenario index then "
            "uint8 replicate index for every replicate draw"
        ),
        "shared_draw_sha256": draw_digest.hexdigest(),
        "arm_intervals": arm_intervals,
        "comparison_intervals": comparison_intervals,
        "family_comparison_intervals": family_intervals,
        "aggregate_point": aggregate_point,
        "family_point": family_point,
        "scenario_point": scenario_point,
        "point_contrasts": point_contrasts,
        "family_point_contrasts": family_point_contrasts,
        "scenario_point_contrasts": scenario_point_contrasts,
    }


def _criterion(
    passed: bool, rule: str, observed: Any, threshold: float | int
) -> dict[str, Any]:
    return {
        "status": "pass" if passed else "fail",
        "rule": rule,
        "observed": observed,
        "threshold": threshold,
    }


def evaluate_str_gates(
    comparison: dict[str, dict[str, Any]],
    family_comparison: dict[str, dict[str, dict[str, Any]]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate all assessable aggregate and held-out-family gates."""

    aggregate_limits = contract["aggregate_gates_against_str"]
    relative_reduction = comparison["relative_deadline_miss_reduction"]["estimate"]
    aggregate = {
        "deadline_miss_delta": _criterion(
            comparison["deadline_miss_delta"]["ci95_high"]
            < aggregate_limits["deadline_miss_delta_95pct_upper_less_than"],
            "95% upper endpoint < 0",
            comparison["deadline_miss_delta"]["ci95_high"],
            aggregate_limits["deadline_miss_delta_95pct_upper_less_than"],
        ),
        "completed_p99_delta_us": _criterion(
            comparison["completed_p99_delta_us"]["ci95_high"]
            < aggregate_limits["completed_p99_delta_us_95pct_upper_less_than"],
            "95% upper endpoint < 0 us",
            comparison["completed_p99_delta_us"]["ci95_high"],
            aggregate_limits["completed_p99_delta_us_95pct_upper_less_than"],
        ),
        "sender_airtime_ratio": _criterion(
            comparison["sender_airtime_ratio"]["ci95_high"]
            <= aggregate_limits["sender_airtime_ratio_95pct_upper_at_most"],
            "95% upper endpoint <= 1.20",
            comparison["sender_airtime_ratio"]["ci95_high"],
            aggregate_limits["sender_airtime_ratio_95pct_upper_at_most"],
        ),
        "background_throughput_loss": _criterion(
            comparison["background_throughput_loss"]["ci95_high"]
            <= aggregate_limits["background_throughput_loss_95pct_upper_at_most"],
            "95% upper endpoint <= 0.01",
            comparison["background_throughput_loss"]["ci95_high"],
            aggregate_limits["background_throughput_loss_95pct_upper_at_most"],
        ),
        "relative_deadline_miss_reduction": _criterion(
            isinstance(relative_reduction, (int, float))
            and math.isfinite(relative_reduction)
            and relative_reduction
            >= aggregate_limits["relative_deadline_miss_reduction_at_least"],
            "point estimate >= 0.50",
            relative_reduction,
            aggregate_limits["relative_deadline_miss_reduction_at_least"],
        ),
    }
    aggregate_status = (
        "pass" if all(item["status"] == "pass" for item in aggregate.values()) else "fail"
    )
    family_limits = contract["per_family_safety_gates_against_str"]
    family_results: dict[str, Any] = {}
    worse_both = 0
    for family, metrics in family_comparison.items():
        miss = metrics["deadline_miss_delta"]["estimate"]
        p99 = metrics["completed_p99_delta_us"]["estimate"]
        airtime = metrics["sender_airtime_ratio"]["estimate"]
        background = metrics["background_throughput_loss"]["estimate"]
        if miss > 0 and p99 > 0:
            worse_both += 1
        criteria = {
            "deadline_miss_delta": _criterion(
                miss <= family_limits["deadline_miss_delta_at_most"],
                "family point estimate <= 0.002",
                miss,
                family_limits["deadline_miss_delta_at_most"],
            ),
            "completed_p99_delta_us": _criterion(
                p99 <= family_limits["completed_p99_delta_us_at_most"],
                "family point estimate <= 2000 us",
                p99,
                family_limits["completed_p99_delta_us_at_most"],
            ),
            "sender_airtime_ratio": _criterion(
                airtime <= family_limits["sender_airtime_ratio_at_most"],
                "family point estimate <= 1.25",
                airtime,
                family_limits["sender_airtime_ratio_at_most"],
            ),
            "background_throughput_loss": _criterion(
                background <= family_limits["background_throughput_loss_at_most"],
                "family point estimate <= 0.02",
                background,
                family_limits["background_throughput_loss_at_most"],
            ),
        }
        family_results[family] = {
            "status": "pass"
            if all(item["status"] == "pass" for item in criteria.values())
            else "fail",
            "criteria": criteria,
        }
    worse_gate = _criterion(
        worse_both <= family_limits["families_worse_on_both_miss_and_p99_at_most"],
        "families worse on both miss and P99 <= 0",
        worse_both,
        family_limits["families_worse_on_both_miss_and_p99_at_most"],
    )
    family_status = (
        "pass"
        if worse_gate["status"] == "pass"
        and all(item["status"] == "pass" for item in family_results.values())
        else "fail"
    )
    return {
        "aggregate": {"status": aggregate_status, "criteria": aggregate},
        "per_family": {
            "status": family_status,
            "families": family_results,
            "families_worse_on_both_miss_and_p99": worse_gate,
        },
        "direct_str_victory": {
            "status": "pass"
            if aggregate_status == "pass" and family_status == "pass"
            else "fail",
            "definition": "all assessable aggregate and per-family STR gates pass",
        },
    }


def _arm_summary(
    arm: str,
    observations: Sequence[dict[str, Any]],
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    rows = [row for row in observations if row["arm_id"] == arm]
    return {
        "label": ARM_LABELS[arm],
        "run_count": len(rows),
        "generated_frame_count": {
            "total": sum(row["generated_frame_count"] for row in rows),
            "per_run_minimum": min(row["generated_frame_count"] for row in rows),
            "per_run_maximum": max(row["generated_frame_count"] for row in rows),
        },
        "completed_frame_count": {
            "total": sum(row["completed_frame_count"] for row in rows),
            "per_run_minimum": min(row["completed_frame_count"] for row in rows),
            "per_run_maximum": max(row["completed_frame_count"] for row in rows),
        },
        "deadline_miss_count": {
            "total": sum(row["deadline_miss_count"] for row in rows),
        },
        "metrics": bootstrap["arm_intervals"][arm],
    }


def _paired_rows(
    grid: dict[str, dict[str, list[dict[str, dict[str, Any]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    contrasts = {
        item["comparison_id"]: (item["candidate_arm_id"], item["baseline_arm_id"])
        for item in contract["contrasts"]
    }
    rows: list[dict[str, Any]] = []
    for family in families:
        for scenario in scenarios[family]:
            for unit in grid[family][scenario]:
                first = unit[ARM_IDS[0]]
                row: dict[str, Any] = {
                    "family_id": family,
                    "scenario_id": scenario,
                    "parameter_sample": first["parameter_sample"],
                    "seed": first["seed"],
                    "run": first["run"],
                }
                for arm in ARM_IDS:
                    for metric in METRICS:
                        row[f"{arm}__{metric}"] = unit[arm][metric]
                for comparison, (candidate, baseline) in contrasts.items():
                    values = _contrast(
                        {metric: unit[candidate][metric] for metric in METRICS},
                        {metric: unit[baseline][metric] for metric in METRICS},
                    )
                    for metric in CONTRAST_METRICS:
                        row[f"{comparison}__{metric}"] = values[metric]
                rows.append(row)
    return rows


def _comparison_tables(
    result: dict[str, Any],
    contract: dict[str, Any],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    family_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
    contrast_by_id = {
        item["comparison_id"]: item for item in contract["contrasts"]
    }
    for comparison, declaration in contrast_by_id.items():
        candidate = declaration["candidate_arm_id"]
        baseline = declaration["baseline_arm_id"]
        for family in families:
            arm_values = result["family_point"][family]
            contrasts = result["family_comparison_intervals"][comparison][family]
            row: dict[str, Any] = {
                "comparison_id": comparison,
                "candidate_arm_id": candidate,
                "baseline_arm_id": baseline,
                "family_id": family,
            }
            for metric in METRICS:
                row[f"candidate__{metric}"] = arm_values[candidate][metric]
                row[f"baseline__{metric}"] = arm_values[baseline][metric]
            for metric in CONTRAST_METRICS:
                row[metric] = contrasts[metric]["estimate"]
                row[f"{metric}__ci95_low"] = contrasts[metric]["ci95_low"]
                row[f"{metric}__ci95_high"] = contrasts[metric]["ci95_high"]
            family_rows.append(row)
            for scenario in scenarios[family]:
                scenario_arms = result["scenario_point"][family][scenario]
                values = result["scenario_point_contrasts"][comparison][family][scenario]
                first = {
                    "comparison_id": comparison,
                    "candidate_arm_id": candidate,
                    "baseline_arm_id": baseline,
                    "family_id": family,
                    "scenario_id": scenario,
                }
                for metric in METRICS:
                    first[f"candidate__{metric}"] = scenario_arms[candidate][metric]
                    first[f"baseline__{metric}"] = scenario_arms[baseline][metric]
                first.update(values)
                scenario_rows.append(first)
    return family_rows, scenario_rows


def build_report(
    observations: Sequence[dict[str, Any]],
    grid: dict[str, dict[str, list[dict[str, dict[str, Any]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    contract: dict[str, Any],
    manifest_identity: dict[str, Any],
    analyzer_git: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result = hierarchical_bootstrap(grid, families, scenarios, contract)
    comparisons: dict[str, Any] = {}
    direct_str: dict[str, Any] = {}
    for declaration in contract["contrasts"]:
        comparison = declaration["comparison_id"]
        entry: dict[str, Any] = {
            "candidate_arm_id": declaration["candidate_arm_id"],
            "baseline_arm_id": declaration["baseline_arm_id"],
            "aggregate": result["comparison_intervals"][comparison],
            "families": result["family_comparison_intervals"][comparison],
            "scenarios": result["scenario_point_contrasts"][comparison],
        }
        if declaration["baseline_arm_id"] == "str_mlo_nmaxinflights_1":
            gates = evaluate_str_gates(
                entry["aggregate"], entry["families"], contract
            )
            entry["str_gates"] = gates
            direct_str[declaration["candidate_arm_id"]] = gates[
                "direct_str_victory"
            ]
        comparisons[comparison] = entry
    build_identity = json.loads(
        next(iter({_canonical_json(row["build_identity"]) for row in observations}))
    )
    oracle_boundary = copy.deepcopy(contract["oracle_estimand_boundary"])
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "evidence_role": "held-out closed-loop environment qualification",
        "source_closure": {
            "analysis_contract": {
                "path": str(ANALYSIS_CONTRACT_PATH.relative_to(ROOT)),
                "sha256": _sha256(ANALYSIS_CONTRACT_PATH),
            },
            "analyzer": {
                "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": _sha256(Path(__file__).resolve()),
            },
            "execution_contract": copy.deepcopy(contract["execution_contract"]),
            "generated_matrix": copy.deepcopy(contract["generated_matrix"]),
            "campaign_manifest": manifest_identity,
            "analyzer_git": analyzer_git,
            "build_identity": build_identity,
        },
        "population": {
            **copy.deepcopy(contract["population"]),
            "family_ids": list(families),
            "scenario_ids_by_family": {
                family: list(scenarios[family]) for family in families
            },
            "strictly_validated_run_count": len(observations),
        },
        "estimand": copy.deepcopy(contract["estimand"]),
        "bootstrap": {
            key: result[key]
            for key in (
                "method",
                "replications",
                "confidence_level",
                "seed",
                "draw_encoding",
                "shared_draw_sha256",
            )
        },
        "treatments": {
            arm: _arm_summary(arm, observations, result) for arm in ARM_IDS
        },
        "comparisons": comparisons,
        "direct_str_victory": direct_str,
        "oracle_estimand_boundary": oracle_boundary,
        "parent_promotion_readiness": {
            **oracle_boundary["parent_promotion_readiness"],
            "assessable_direct_str_gate_status": {
                arm: value["status"] for arm, value in direct_str.items()
            },
        },
        "completed_p99_interpretation": {
            "population": "completed frames only",
            "warning": contract["interpretation_limits"][0],
            "minimum_completed_frames_per_run": MINIMUM_COMPLETED_FRAMES,
        },
        "interpretation_limits": copy.deepcopy(contract["interpretation_limits"]),
    }
    paired = _paired_rows(grid, families, scenarios, contract)
    family_rows, scenario_rows = _comparison_tables(
        result, contract, families, scenarios
    )
    return report, paired, family_rows, scenario_rows


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Held-out environment closed-loop qualification",
        "",
        "This report compares the three frozen policies on 48 unseen scenarios "
        "from six fixed families. All 576 raw runs passed strict validation before "
        "analysis.",
        "",
        "## Aggregate results",
        "",
        "| Arm | Miss rate | Completed P99 | Sender airtime | Background throughput |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARM_IDS:
        treatment = report["treatments"][arm]
        metrics = treatment["metrics"]
        lines.append(
            f"| {treatment['label']} | "
            f"{100 * metrics['all_generated_deadline_miss_rate']['estimate']:.4f}% | "
            f"{metrics['completed_frame_hf7_p99_us']['estimate'] / 1000:.3f} ms | "
            f"{metrics['sender_airtime_us']['estimate'] / 1000:.3f} ms | "
            f"{metrics['background_throughput_mbps']['estimate']:.3f} Mbit/s |"
        )
    lines.extend(["", "## Direct STR comparisons", ""])
    for comparison, entry in report["comparisons"].items():
        if entry["baseline_arm_id"] != "str_mlo_nmaxinflights_1":
            continue
        values = entry["aggregate"]
        status = entry["str_gates"]["direct_str_victory"]["status"]
        relative = values["relative_deadline_miss_reduction"]["estimate"]
        relative_text = (
            f"{100 * relative:.2f}%" if relative is not None else "not assessable"
        )
        lines.extend(
            [
                f"### {ARM_LABELS[entry['candidate_arm_id']]} versus STR MLO",
                "",
                f"Direct assessable STR status: **{status}**.",
                "",
                f"- Deadline-miss delta: {100 * values['deadline_miss_delta']['estimate']:.4f} "
                f"percentage points, 95% interval "
                f"[{100 * values['deadline_miss_delta']['ci95_low']:.4f}, "
                f"{100 * values['deadline_miss_delta']['ci95_high']:.4f}].",
                f"- Completed-frame P99 delta: "
                f"{values['completed_p99_delta_us']['estimate'] / 1000:.3f} ms, "
                f"95% interval [{values['completed_p99_delta_us']['ci95_low'] / 1000:.3f}, "
                f"{values['completed_p99_delta_us']['ci95_high'] / 1000:.3f}].",
                f"- Sender-airtime ratio: {values['sender_airtime_ratio']['estimate']:.4f}, "
                f"95% interval [{values['sender_airtime_ratio']['ci95_low']:.4f}, "
                f"{values['sender_airtime_ratio']['ci95_high']:.4f}].",
                f"- Background-throughput loss: "
                f"{100 * values['background_throughput_loss']['estimate']:.4f}%, "
                f"95% interval [{100 * values['background_throughput_loss']['ci95_low']:.4f}%, "
                f"{100 * values['background_throughput_loss']['ci95_high']:.4f}%].",
                f"- Relative miss reduction: "
                f"{relative_text}.",
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            report["oracle_estimand_boundary"][
                "fraction_of_oracle_deadline_gain_realized"
            ]["reason"],
            "",
            "Accordingly, parent promotion readiness is **not assessable** from this "
            "campaign alone. The direct STR gates above remain valid.",
            "",
            "Completed-frame P99 is survivor-conditioned. Read it together with the "
            "all-generated miss rate and completed-frame counts shown in the JSON report.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
        + "\n",
        encoding="ascii",
    )


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _require(bool(rows), f"cannot write empty table {path.name}")
    fieldnames = list(rows[0])
    _require(
        all(list(row) == fieldnames for row in rows),
        f"table {path.name} has inconsistent columns",
    )
    with path.open("w", newline="", encoding="ascii") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _run_metric_rows(observations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "family_id",
        "scenario_id",
        "parameter_sample",
        "seed",
        "run",
        "arm_id",
        "run_id",
        "run_dir",
        "generated_frame_count",
        "completed_frame_count",
        "deadline_miss_count",
        "all_generated_deadline_miss_rate",
        "completed_frame_hf7_p99_us",
        "sender_airtime_us",
        "background_bytes_received",
        "background_throughput_mbps",
    )
    return [{field: row[field] for field in fields} for row in observations]


def write_analysis_outputs(
    output_directory: Path,
    report: dict[str, Any],
    observations: Sequence[dict[str, Any]],
    paired_rows: Sequence[dict[str, Any]],
    family_rows: Sequence[dict[str, Any]],
    scenario_rows: Sequence[dict[str, Any]],
    contract: dict[str, Any],
) -> Path:
    """Atomically publish every checksum-bound analysis output."""

    output = output_directory.resolve()
    _require(not output.exists(), f"analysis output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
    )
    try:
        names = contract["outputs"]
        report_path = temporary / names["report_json"]
        markdown_path = temporary / names["report_markdown"]
        run_path = temporary / names["run_metrics_csv"]
        paired_path = temporary / names["paired_metrics_csv"]
        scenario_path = temporary / names["scenario_metrics_csv"]
        family_path = temporary / names["family_metrics_csv"]
        _write_json(report_path, report)
        markdown_path.write_text(render_markdown(report), encoding="ascii")
        _write_csv(run_path, _run_metric_rows(observations))
        _write_csv(paired_path, paired_rows)
        _write_csv(scenario_path, scenario_rows)
        _write_csv(family_path, family_rows)
        artifacts = [
            report_path,
            markdown_path,
            run_path,
            paired_path,
            scenario_path,
            family_path,
        ]
        artifact_manifest = {
            "schema_version": 1,
            "manifest_id": "environment-generalization-qualification-analysis-artifacts-v1",
            "analysis": ANALYSIS_ID,
            "analysis_contract": report["source_closure"]["analysis_contract"],
            "analyzer": report["source_closure"]["analyzer"],
            "campaign_manifest": report["source_closure"]["campaign_manifest"],
            "bootstrap_shared_draw_sha256": report["bootstrap"][
                "shared_draw_sha256"
            ],
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in artifacts
            },
            "counts": {
                "strictly_validated_runs": len(observations),
                "paired_units": len(paired_rows),
                "family_comparison_rows": len(family_rows),
                "scenario_comparison_rows": len(scenario_rows),
            },
            "reproduction": [
                "python3 tools/analyze_environment_generalization_qualification.py "
                "<campaign-run-root> --output-directory <new-output-directory> --workers 64"
            ],
        }
        _write_json(
            temporary / names["analysis_artifact_manifest_json"], artifact_manifest
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


def analyze_campaign(
    campaign_input: Path,
    *,
    workers: int,
    contract: dict[str, Any] | None = None,
    analyzer_git: dict[str, Any] | None = None,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Validate and analyze the exact qualification campaign."""

    frozen = contract or load_analysis_contract()
    git_identity = analyzer_git or _git_identity()
    manifest, jobs, families, scenarios = validate_campaign_manifest(
        campaign_input, frozen, git_identity
    )
    observations = collect_observations(jobs, workers)
    grid = build_observation_grid(observations, families, scenarios, frozen)
    report, paired, family_rows, scenario_rows = build_report(
        observations,
        grid,
        families,
        scenarios,
        frozen,
        manifest,
        git_identity,
    )
    return report, observations, paired, family_rows, scenario_rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_input", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument(
        "--require-direct-str-victory",
        action="append",
        choices=("score_aware_t2_v2", "distributional_shadow_t2"),
        default=[],
    )
    arguments = parser.parse_args(argv)
    if arguments.workers <= 0:
        parser.error("--workers must be positive")
    try:
        contract = load_analysis_contract()
        report, observations, paired, family_rows, scenario_rows = analyze_campaign(
            arguments.campaign_input,
            workers=arguments.workers,
            contract=contract,
        )
        output = write_analysis_outputs(
            arguments.output_directory,
            report,
            observations,
            paired,
            family_rows,
            scenario_rows,
            contract,
        )
    except QualificationAnalysisError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    statuses = report["direct_str_victory"]
    print(
        f"WROTE {output} v2={statuses['score_aware_t2_v2']['status']} "
        f"distributional={statuses['distributional_shadow_t2']['status']} "
        "parent_promotion=not_assessable"
    )
    if any(statuses[arm]["status"] != "pass" for arm in arguments.require_direct_str_victory):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
