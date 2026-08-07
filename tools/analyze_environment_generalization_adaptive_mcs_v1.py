#!/usr/bin/env python3
"""Compare the complete adaptive-MCS qualification with fixed-MCS evidence."""

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
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import analyze_environment_generalization_complete_reliability as complete
import analyze_environment_generalization_qualification as formal
import plot_environment_generalization_complete_reliability as fixed_plot
import run_experiments


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / (
    "experiments/configs/environment_generalization_adaptive_mcs_qualification_v1.yaml"
)
CONTRACT_PATH = ROOT / (
    "experiments/model-selection/environment-generalization-adaptive-mcs-qualification-v1.json"
)
DETERMINISTIC_FAILURE_CONTRACT_PATH = ROOT / (
    "experiments/model-selection/"
    "environment-generalization-adaptive-mcs-deterministic-failure-v1.json"
)
FIXED_RESULT = ROOT / "key_experiment_results/17_environment_generalization_qualification_v1"
FIXED_ANALYSIS = FIXED_RESULT / "analysis"
FIXED_RAW_ROOT = ROOT / (
    "results/environment_generalization_closed_loop_qualification_v1_partial_47e1996/runs"
)
SIMULATION_COMMIT = "3ec03196d3ad1207b888433b134096fde1aa9670"
ANALYSIS_ID = "environment-generalization-adaptive-mcs-comparison-v1"
MODES = ("fixed", "adaptive")
ARMS = formal.ARM_IDS
ARM_LABELS = {
    "str_mlo_nmaxinflights_1": "STR MLO",
    "score_aware_t2_v2": "V2",
    "distributional_shadow_t2": "Distributional",
}
METRICS = (
    "all_generated_deadline_miss_rate",
    "sender_airtime_us",
    "background_throughput_mbps",
    "all_generated_censored_mean_us",
)
EXPECTED_PLOTS = (
    "aggregate_fixed_vs_adaptive",
    "family_deadline_miss_fixed_vs_adaptive",
    "scenario_fixed_vs_adaptive",
    "adaptive_policy_vs_str_by_scenario",
    "all_generated_censored_latency_cdf",
    "all_generated_censored_latency_pdf",
    "completion_latency_cdf",
    "completion_latency_pdf",
    "deadline_outcome_composition",
    "deadline_miss_burst_cdf",
)
COLORS = {
    "str_mlo_nmaxinflights_1": "#4c78a8",
    "score_aware_t2_v2": "#f58518",
    "distributional_shadow_t2": "#54a24b",
}
FAMILY_COLORS = {
    "radio_propagation": "#4c78a8",
    "obss_intensity": "#f58518",
    "obss_geometry_mac": "#e45756",
    "video_workload": "#72b7b2",
    "legacy_coexistence": "#54a24b",
    "compound_shift": "#b279a2",
}


class AdaptiveMcsAnalysisError(RuntimeError):
    """Raised when raw evidence or the paired comparison differs."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AdaptiveMcsAnalysisError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdaptiveMcsAnalysisError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="ascii") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as error:
        raise AdaptiveMcsAnalysisError(f"cannot read {path}: {error}") from error
    _require(bool(rows), f"{path}: table is empty")
    return rows


def _git_identity() -> dict[str, Any]:
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
    _require(len(head) == 40 and not status.strip(), "analyzer checkout is not clean")
    descendant = subprocess.run(
        ["git", "merge-base", "--is-ancestor", SIMULATION_COMMIT, head],
        cwd=ROOT,
    )
    _require(descendant.returncode == 0, "analyzer does not descend from simulation commit")
    return {"project_commit": head, "worktree_clean": True}


def _arm_map() -> dict[tuple[str, str], str]:
    return {
        ("mlo_str", "fixed_link_0"): "str_mlo_nmaxinflights_1",
        ("dual_interface", "paired_value_duplication_t2"): "score_aware_t2_v2",
        ("dual_interface", "distributional_shadow_duplication_t2"):
            "distributional_shadow_t2",
    }


def _validate_adaptive_mcs_resolved(config: dict[str, Any], label: str) -> None:
    """Bind an adaptive matrix entry to its resolved manager provenance."""

    wifi = config.get("wifi")
    expected = {
        "mcs_mode": "adaptive",
        "station_manager": "MinstrelHtWifiManager",
        "adaptive_mcs_update_interval_ms": 50,
        "adaptive_mcs_use_latest_amendment_only": True,
        "adaptive_mcs_random_stream_base": 900000,
        "adaptive_mcs_random_stream_count": 8,
        "data_mode": "manager_selected",
        "control_mode": "manager_selected,manager_selected",
        "data_modes_per_link": ["manager_selected", "manager_selected"],
    }
    _require(
        isinstance(wifi, dict)
        and all(wifi.get(key) == value for key, value in expected.items()),
        f"{label}: resolved adaptive-MCS provenance differs",
    )


def validate_adaptive_shards(
    shard_roots: Sequence[Path],
    missing_run_id: str | None = None,
    missing_shard_index: int | None = None,
) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, tuple[str, ...]], list[dict[str, Any]]]:
    """Validate exact shard manifests and construct strict replay jobs."""

    _require(len(shard_roots) == 2, "exactly two adaptive shard roots are required")
    _require(
        (missing_run_id is None and missing_shard_index is None)
        or (
            isinstance(missing_run_id, str)
            and len(missing_run_id) == 20
            and missing_shard_index in {0, 1}
        ),
        "deterministic-failure shard identity is incomplete",
    )
    document = run_experiments.load_yaml(CONFIG_PATH)
    runtime = run_experiments.validate_runtime_contract(document)
    _require(runtime is not None, "adaptive runtime contract is absent")
    specs = run_experiments.expand_config(document)
    _require(len(specs) == 576, "adaptive expansion count differs")
    matrix_sha = run_experiments.matrix_sha256(document)
    arm_map = _arm_map()
    expected: dict[str, dict[str, Any]] = {}
    for spec in specs:
        run_id = run_experiments.derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            run_experiments.NS3_UPSTREAM_COMMIT,
            SIMULATION_COMMIT,
            runtime,
            spec["scenario"],
        )
        arm_id = arm_map.get((spec["config"]["topology"], spec["config"]["policy"]))
        _require(arm_id is not None and run_id not in expected,
                 "adaptive expansion arm or run identity differs")
        expected[run_id] = {**spec, "run_id": run_id, "arm_id": arm_id}

    observed: dict[str, tuple[dict[str, Any], Path]] = {}
    identities: list[dict[str, Any]] = []
    for index, root_input in enumerate(shard_roots):
        root = root_input.resolve()
        manifest_path = root / "experiment_manifest.json"
        manifest = _read_json(manifest_path)
        required_shard = {
            "schema_version": 1,
            "selection": "paired_unit_round_robin_v1",
            "index": index,
            "count": 2,
            "full_matrix_run_count": 576,
            "selected_run_count": 288,
        }
        expected_complete_count = 288 - int(index == missing_shard_index)
        _require(
            manifest.get("schema_version") == 2
            and manifest.get("experiment")
            == "environment-generalization-adaptive-mcs-qualification-v1"
            and manifest.get("matrix_sha256") == matrix_sha
            and manifest.get("project_commit") == SIMULATION_COMMIT
            and manifest.get("ns3_upstream_commit") == run_experiments.NS3_UPSTREAM_COMMIT
            and manifest.get("runtime_contract_id") == runtime["runtime_contract_id"]
            and manifest.get("runtime_contract_sha256")
            == runtime["runtime_contract_sha256"]
            and _canonical(manifest.get("source_artifacts"))
            == _canonical(runtime["source_artifacts"])
            and manifest.get("shard") == required_shard,
            f"adaptive shard {index} identity differs",
        )
        rows = manifest.get("runs")
        _require(isinstance(rows, list) and len(rows) == expected_complete_count,
                 f"adaptive shard {index} is incomplete")
        for row in rows:
            run_id = row.get("run_id") if isinstance(row, dict) else None
            _require(
                isinstance(run_id, str)
                and run_id in expected
                and run_id not in observed
                and row.get("status") == "complete"
                and row.get("directory") == run_id,
                f"adaptive shard {index} contains a noncanonical run",
            )
            spec = expected[run_id]
            _require(
                row.get("seed") == spec["seed"]
                and row.get("run") == spec["run"]
                and _canonical(row.get("config")) == _canonical(spec["config"])
                and _canonical(row.get("scenario")) == _canonical(spec["scenario"]),
                f"adaptive run {run_id} differs from frozen expansion",
            )
            run_dir = root / run_id
            _require(run_dir.is_dir() and not run_dir.is_symlink(),
                     f"adaptive run directory is absent: {run_id}")
            _validate_adaptive_mcs_resolved(
                _read_json(run_dir / "resolved_config.json"), run_id
            )
            observed[run_id] = (row, run_dir)
        identities.append({
            "index": index,
            "path": str(manifest_path),
            "sha256": _sha256(manifest_path),
            "complete_run_count": len(rows),
            "shard": copy.deepcopy(required_shard),
        })
    expected_observed = set(expected)
    if missing_run_id is not None:
        _require(missing_run_id in expected, "missing run is outside frozen matrix")
        expected_observed.remove(missing_run_id)
    _require(set(observed) == expected_observed, "adaptive shard union is not exact")

    jobs: list[dict[str, Any]] = []
    for run_id, spec in expected.items():
        if run_id not in observed:
            continue
        scenario = spec["scenario"]
        jobs.append({
            "run_id": run_id,
            "run_dir": str(observed[run_id][1]),
            "project_commit": SIMULATION_COMMIT,
            "ns3_upstream_commit": run_experiments.NS3_UPSTREAM_COMMIT,
            "arm_id": spec["arm_id"],
            "family_id": scenario["family_id"],
            "scenario_id": scenario["scenario_id"],
            "parameter_sample": scenario["parameter_sample"],
            "seed": spec["seed"],
            "run": spec["run"],
            "expected_config": spec["config"],
            "required_secondary_airtime_event_schema_version": (
                None if spec["arm_id"] == ARMS[0] else 2
            ),
        })
    families, scenarios = formal._family_scenario_order(specs)
    arm_order = {arm: index for index, arm in enumerate(ARMS)}
    family_order = {family: index for index, family in enumerate(families)}
    scenario_order = {
        scenario: index
        for family in families
        for index, scenario in enumerate(scenarios[family])
    }
    jobs.sort(key=lambda row: (
        family_order[row["family_id"]],
        scenario_order[row["scenario_id"]],
        row["seed"],
        row["run"],
        arm_order[row["arm_id"]],
    ))
    return jobs, families, scenarios, identities


def load_fixed_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load the checksum-closed fixed analysis and bind it to local raw data."""

    report, run_rows, _family, _scenario, analysis_identity = fixed_plot.load_analysis(
        FIXED_ANALYSIS
    )
    raw_manifest = FIXED_RAW_ROOT / "experiment_manifest.json"
    archived_manifest = FIXED_RESULT / "experiment_manifest.json"
    _require(
        raw_manifest.is_file()
        and archived_manifest.is_file()
        and _sha256(raw_manifest) == _sha256(archived_manifest)
        and _sha256(raw_manifest)
        == report["source_closure"]["campaign_manifest"]["sha256"],
        "fixed raw manifest differs from archived strict analysis",
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    for source in run_rows:
        key = (
            source["family_id"],
            source["scenario_id"],
            int(source["seed"]),
            int(source["run"]),
            source["arm_id"],
        )
        _require(key not in seen and source["arm_id"] in ARMS,
                 "fixed run table has duplicate or unknown identity")
        seen.add(key)
        run_id = source["run_id"]
        run_dir = FIXED_RAW_ROOT / run_id
        _require(run_dir.is_dir(), f"fixed raw run is absent: {run_id}")
        rows.append({
            "mode": "fixed",
            "family_id": source["family_id"],
            "scenario_id": source["scenario_id"],
            "parameter_sample": int(source["parameter_sample"]),
            "seed": int(source["seed"]),
            "run": int(source["run"]),
            "arm_id": source["arm_id"],
            "run_id": run_id,
            "run_dir": str(run_dir),
            "generated_frame_count": int(source["generated_frame_count"]),
            "completed_frame_count": int(source["completed_frame_count"]),
            "incomplete_frame_count": int(source["incomplete_frame_count"]),
            "deadline_miss_count": int(source["deadline_miss_count"]),
            "all_generated_deadline_miss_rate": float(
                source["all_generated_deadline_miss_rate"]
            ),
            "completed_frame_hf7_p99_supported": bool(
                int(source["completed_frame_hf7_p99_supported"])
            ),
            "completed_frame_hf7_p99_us": (
                None
                if source["completed_frame_hf7_p99_us"] == ""
                else float(source["completed_frame_hf7_p99_us"])
            ),
            "sender_airtime_us": float(source["sender_airtime_us"]),
            "background_throughput_mbps": float(
                source["background_throughput_mbps"]
            ),
        })
    _require(len(rows) == 576, "fixed run table count differs")
    return rows, {
        "raw_manifest": {
            "path": str(raw_manifest),
            "sha256": _sha256(raw_manifest),
        },
        "analysis_artifact_manifest": analysis_identity,
        "strict_validation": report["strict_validation"],
    }


def adaptive_rows(
    observations: Sequence[dict[str, Any]], expected_count: int = 576
) -> list[dict[str, Any]]:
    """Normalize strict adaptive observations into the comparison table."""

    rows = []
    for source in observations:
        rows.append({
            "mode": "adaptive",
            **{
                key: source[key]
                for key in (
                    "family_id", "scenario_id", "parameter_sample", "seed", "run",
                    "arm_id", "run_id", "run_dir", "generated_frame_count",
                    "completed_frame_count", "incomplete_frame_count",
                    "deadline_miss_count", "all_generated_deadline_miss_rate",
                    "completed_frame_hf7_p99_supported",
                    "completed_frame_hf7_p99_us", "sender_airtime_us",
                    "background_throughput_mbps",
                )
            },
        })
    _require(len(rows) == expected_count, "adaptive strict observation count differs")
    return rows


def _flag(value: str, label: str) -> bool:
    _require(value in {"0", "1"}, f"invalid flag {label}")
    return value == "1"


def _burst_lengths(flags: Iterable[bool]) -> list[int]:
    result: list[int] = []
    current = 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            result.append(current)
            current = 0
    if current:
        result.append(current)
    return result


def _history_job(row: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(row["run_dir"])
    frames = _read_csv(run_dir / "frames.csv")
    completed: list[float] = []
    censored: list[float] = []
    miss_flags: list[bool] = []
    incomplete = 0
    late_completed = 0
    for frame in frames:
        _require(frame.get("run_id") == row["run_id"],
                 f"{run_dir}: frame run identity differs")
        deadline = float(frame["deadline_us"])
        _require(math.isfinite(deadline) and deadline > 0,
                 f"{run_dir}: invalid deadline")
        miss = _flag(frame["deadline_miss"], "deadline_miss")
        is_incomplete = _flag(frame["incomplete"], "incomplete")
        miss_flags.append(miss)
        if is_incomplete:
            _require(frame["union_latency_us"] == "" and miss,
                     f"{run_dir}: invalid incomplete outcome")
            incomplete += 1
            censored.append(deadline)
        else:
            latency = float(frame["union_latency_us"])
            _require(math.isfinite(latency) and latency >= 0,
                     f"{run_dir}: invalid completed latency")
            _require(miss == (latency > deadline),
                     f"{run_dir}: deadline outcome differs")
            completed.append(latency)
            censored.append(min(latency, deadline))
            late_completed += int(miss)
    _require(
        len(frames) == row["generated_frame_count"]
        and len(completed) == row["completed_frame_count"]
        and incomplete == row["incomplete_frame_count"]
        and sum(miss_flags) == row["deadline_miss_count"],
        f"{run_dir}: raw historical reduction differs",
    )
    return {
        "key": (row["mode"], row["run_id"]),
        "mode": row["mode"],
        "arm_id": row["arm_id"],
        "completed": completed,
        "censored": censored,
        "bursts": _burst_lengths(miss_flags),
        "generated": len(frames),
        "completed_count": len(completed),
        "late_completed": late_completed,
        "incomplete": incomplete,
        "misses": sum(miss_flags),
        "censored_mean_us": statistics.fmean(censored),
    }


def collect_history(
    rows: Sequence[dict[str, Any]], workers: int
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], dict[str, Any]]]:
    """Reduce raw frame histories for censored and descriptive distributions."""

    _require(workers > 0, "history workers must be positive")
    grouped = {
        (mode, arm): {
            "completed": [], "censored": [], "bursts": [], "generated": 0,
            "completed_count": 0, "late_completed": 0, "incomplete": 0,
            "misses": 0,
        }
        for mode in MODES
        for arm in ARMS
    }
    means: dict[tuple[str, str], float] = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_history_job, row): row for row in rows}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                raise AdaptiveMcsAnalysisError(
                    f"frame-history worker failed for {source['run_id']}: {error}"
                ) from error
            _require(result["key"] not in means, "duplicate raw history result")
            means[result["key"]] = result["censored_mean_us"]
            target = grouped[(result["mode"], result["arm_id"])]
            for field in ("completed", "censored", "bursts"):
                target[field].extend(result[field])
            for field in (
                "generated", "completed_count", "late_completed", "incomplete", "misses"
            ):
                target[field] += result[field]
    _require(len(means) == len(rows), "raw history result count differs")
    return means, grouped


def build_grid(
    rows: Sequence[dict[str, Any]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    expected_unit_count: int = 192,
) -> dict[str, dict[str, list[dict[str, dict[str, dict[str, float]]]]]]:
    """Build exact mode/arm observations for every paired unit."""

    indexed: dict[tuple[str, str, int, int], dict[str, dict[str, dict[str, float]]]] = {}
    for row in rows:
        unit_key = (
            row["family_id"], row["scenario_id"], row["seed"], row["run"]
        )
        unit = indexed.setdefault(unit_key, {mode: {} for mode in MODES})
        _require(row["arm_id"] not in unit[row["mode"]], "duplicate mode/arm unit")
        unit[row["mode"]][row["arm_id"]] = {
            metric: float(row[metric]) for metric in METRICS
        }
    grid: dict[str, dict[str, list[dict[str, dict[str, dict[str, float]]]]]] = {}
    for family in families:
        grid[family] = {}
        for scenario in scenarios[family]:
            members = [
                value
                for key, value in indexed.items()
                if key[0] == family and key[1] == scenario
            ]
            _require(0 < len(members) <= 4,
                     f"paired replicates differ for {scenario}")
            for unit in members:
                _require(
                    all(set(unit[mode]) == set(ARMS) for mode in MODES),
                    f"mode/arm closure differs for {scenario}",
                )
            grid[family][scenario] = members
    _require(len(indexed) == expected_unit_count, "paired unit count differs")
    return grid


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    _require(bool(materialized), "cannot average empty values")
    return statistics.fmean(materialized)


def treatment_points(
    grid: dict[str, dict[str, list[dict[str, dict[str, dict[str, float]]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Calculate equal-family, equal-scenario, equal-replicate point estimates."""

    scenario_points: dict[str, Any] = {}
    family_points: dict[str, Any] = {}
    for family in families:
        scenario_points[family] = {}
        for scenario in scenarios[family]:
            scenario_points[family][scenario] = {
                mode: {
                    arm: {
                        metric: _mean(
                            unit[mode][arm][metric]
                            for unit in grid[family][scenario]
                        )
                        for metric in METRICS
                    }
                    for arm in ARMS
                }
                for mode in MODES
            }
        family_points[family] = {
            mode: {
                arm: {
                    metric: _mean(
                        scenario_points[family][scenario][mode][arm][metric]
                        for scenario in scenarios[family]
                    )
                    for metric in METRICS
                }
                for arm in ARMS
            }
            for mode in MODES
        }
    aggregate = {
        mode: {
            arm: {
                metric: _mean(
                    family_points[family][mode][arm][metric]
                    for family in families
                )
                for metric in METRICS
            }
            for arm in ARMS
        }
        for mode in MODES
    }
    return aggregate, family_points, scenario_points


def _comparison(candidate: dict[str, float], baseline: dict[str, float]) -> dict[str, float]:
    _require(
        baseline["all_generated_deadline_miss_rate"] > 0
        and baseline["sender_airtime_us"] > 0
        and baseline["background_throughput_mbps"] > 0,
        "comparison denominator is nonpositive",
    )
    return {
        "deadline_miss_delta": (
            candidate["all_generated_deadline_miss_rate"]
            - baseline["all_generated_deadline_miss_rate"]
        ),
        "relative_deadline_miss_change": (
            candidate["all_generated_deadline_miss_rate"]
            / baseline["all_generated_deadline_miss_rate"] - 1
        ),
        "sender_airtime_ratio": (
            candidate["sender_airtime_us"] / baseline["sender_airtime_us"]
        ),
        "background_throughput_loss": (
            1
            - candidate["background_throughput_mbps"]
            / baseline["background_throughput_mbps"]
        ),
        "censored_mean_delta_us": (
            candidate["all_generated_censored_mean_us"]
            - baseline["all_generated_censored_mean_us"]
        ),
    }


def _type7(values: Sequence[float], probability: float) -> float:
    return complete._type7_quantile(values, probability)


def _interval(point: float, samples: Sequence[float]) -> dict[str, float]:
    return {
        "estimate": point,
        "ci95_low": _type7(samples, 0.025),
        "ci95_high": _type7(samples, 0.975),
    }


def bootstrap(
    grid: dict[str, dict[str, list[dict[str, dict[str, dict[str, float]]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    replications: int = 10000,
    random_seed: int = 8231415969872262531,
) -> dict[str, Any]:
    """Apply shared paired hierarchical resampling to MCS and STR contrasts."""

    point, family_points, scenario_points = treatment_points(grid, families, scenarios)
    treatment_samples = {
        mode: {arm: {metric: [] for metric in METRICS} for arm in ARMS}
        for mode in MODES
    }
    comparison_pairs = {
        **{
            f"adaptive_minus_fixed__{arm}": (("adaptive", arm), ("fixed", arm))
            for arm in ARMS
        },
        "adaptive_v2_minus_adaptive_str": (
            ("adaptive", "score_aware_t2_v2"),
            ("adaptive", "str_mlo_nmaxinflights_1"),
        ),
        "adaptive_distributional_minus_adaptive_str": (
            ("adaptive", "distributional_shadow_t2"),
            ("adaptive", "str_mlo_nmaxinflights_1"),
        ),
    }
    comparison_samples = {
        name: {metric: [] for metric in _comparison(
            point[candidate[0]][candidate[1]], point[baseline[0]][baseline[1]]
        )}
        for name, (candidate, baseline) in comparison_pairs.items()
    }
    rng = random.Random(random_seed)
    for _ in range(replications):
        family_draws: list[dict[str, Any]] = []
        for family in families:
            sums = {
                mode: {arm: {metric: 0.0 for metric in METRICS} for arm in ARMS}
                for mode in MODES
            }
            count = 0
            available = scenarios[family]
            for _scenario in available:
                scenario = available[rng.randrange(len(available))]
                units = grid[family][scenario]
                for _replicate in units:
                    unit = units[rng.randrange(len(units))]
                    for mode in MODES:
                        for arm in ARMS:
                            for metric in METRICS:
                                sums[mode][arm][metric] += unit[mode][arm][metric]
                    count += 1
            family_draws.append({
                mode: {
                    arm: {
                        metric: sums[mode][arm][metric] / count
                        for metric in METRICS
                    }
                    for arm in ARMS
                }
                for mode in MODES
            })
        aggregate = {
            mode: {
                arm: {
                    metric: _mean(
                        family[mode][arm][metric] for family in family_draws
                    )
                    for metric in METRICS
                }
                for arm in ARMS
            }
            for mode in MODES
        }
        for mode in MODES:
            for arm in ARMS:
                for metric in METRICS:
                    treatment_samples[mode][arm][metric].append(
                        aggregate[mode][arm][metric]
                    )
        for name, (candidate, baseline) in comparison_pairs.items():
            values = _comparison(
                aggregate[candidate[0]][candidate[1]],
                aggregate[baseline[0]][baseline[1]],
            )
            for metric, value in values.items():
                comparison_samples[name][metric].append(value)
    treatments = {
        mode: {
            arm: {
                metric: _interval(
                    point[mode][arm][metric], treatment_samples[mode][arm][metric]
                )
                for metric in METRICS
            }
            for arm in ARMS
        }
        for mode in MODES
    }
    comparisons = {}
    for name, (candidate, baseline) in comparison_pairs.items():
        values = _comparison(
            point[candidate[0]][candidate[1]], point[baseline[0]][baseline[1]]
        )
        comparisons[name] = {
            metric: _interval(value, comparison_samples[name][metric])
            for metric, value in values.items()
        }
    return {
        "method": "shared paired family/scenario/replicate hierarchical bootstrap",
        "replications": replications,
        "random_seed": random_seed,
        "treatments": treatments,
        "comparisons": comparisons,
        "family_points": family_points,
        "scenario_points": scenario_points,
    }


def _p99_support(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        mode: {
            arm: {
                "supported_run_count": sum(
                    row["completed_frame_hf7_p99_supported"]
                    for row in rows
                    if row["mode"] == mode and row["arm_id"] == arm
                ),
                "total_run_count": sum(
                    row["mode"] == mode and row["arm_id"] == arm for row in rows
                ),
            }
            for arm in ARMS
        }
        for mode in MODES
    }


def _plot_modules() -> tuple[Any, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as error:
        raise AdaptiveMcsAnalysisError("matplotlib and numpy are required") from error
    return plt, np


def _finish(figure: Any, directory: Path, name: str) -> list[Path]:
    figure.tight_layout()
    paths = []
    for suffix in ("png", "pdf"):
        path = directory / f"{name}.{suffix}"
        figure.savefig(path, dpi=180 if suffix == "png" else None)
        paths.append(path)
    return paths


def _empirical_cdf(np: Any, values: Sequence[float]) -> tuple[Any, Any]:
    ordered = np.sort(np.asarray(values, dtype=float))
    return ordered, np.arange(1, len(ordered) + 1, dtype=float) / len(ordered)


def render_plots(
    report: dict[str, Any],
    history: dict[tuple[str, str], dict[str, Any]],
    directory: Path,
) -> list[Path]:
    """Render fixed/adaptive reliability, resource, CDF, and PDF comparisons."""

    plt, np = _plot_modules()
    artifacts: list[Path] = []
    x = np.arange(len(ARMS))
    width = 0.36
    figure, axes = plt.subplots(2, 2, figsize=(11, 7.5))
    panels = (
        ("all_generated_deadline_miss_rate", 100.0, "Deadline misses (%)"),
        ("sender_airtime_us", 0.001, "Sender airtime (ms/run)"),
        ("background_throughput_mbps", 1.0, "Background throughput (Mbit/s)"),
        ("all_generated_censored_mean_us", 0.001, "Censored mean latency (ms)"),
    )
    for axis, (metric, scale, label) in zip(axes.flat, panels, strict=True):
        for mode_index, mode in enumerate(MODES):
            values = [
                scale * report["treatments"][mode][arm][metric]["estimate"]
                for arm in ARMS
            ]
            axis.bar(
                x + (mode_index - 0.5) * width,
                values,
                width,
                label=mode.capitalize(),
                color=[COLORS[arm] for arm in ARMS],
                alpha=0.55 if mode == "fixed" else 1.0,
                hatch="//" if mode == "fixed" else None,
            )
        axis.set_xticks(x, [ARM_LABELS[arm] for arm in ARMS])
        axis.set_ylabel(label)
        axis.grid(axis="y", alpha=0.25)
    axes[0, 0].legend()
    figure.suptitle(
        "Held-out MCS ablation: "
        f"{report['population']['paired_unit_count']} matched units"
    )
    artifacts.extend(_finish(figure, directory, "aggregate_fixed_vs_adaptive"))

    families = list(report["family_points"])
    family_x = np.arange(len(families))
    figure, axes = plt.subplots(3, 1, figsize=(11.5, 9.5), sharex=True)
    for axis, arm in zip(axes, ARMS, strict=True):
        for mode in MODES:
            values = [
                100
                * report["family_points"][family][mode][arm][
                    "all_generated_deadline_miss_rate"
                ]
                for family in families
            ]
            axis.plot(
                family_x,
                values,
                marker="o",
                linestyle="--" if mode == "fixed" else "-",
                label=mode.capitalize(),
                color=COLORS[arm],
                alpha=0.65 if mode == "fixed" else 1.0,
            )
        axis.set_ylabel(f"{ARM_LABELS[arm]} misses (%)")
        axis.grid(alpha=0.25)
        axis.legend()
    axes[-1].set_xticks(
        family_x, [family.replace("_", " ") for family in families], rotation=20
    )
    figure.suptitle("All-generated reliability by scenario family")
    artifacts.extend(
        _finish(figure, directory, "family_deadline_miss_fixed_vs_adaptive")
    )

    points = report["scenario_points"]
    ordered = [(family, scenario) for family in families for scenario in points[family]]
    figure, axes = plt.subplots(1, 3, figsize=(14, 4.8))
    for axis, arm in zip(axes, ARMS, strict=True):
        for family in families:
            members = [(f, s) for f, s in ordered if f == family]
            fixed = [
                100 * points[f][s]["fixed"][arm]["all_generated_deadline_miss_rate"]
                for f, s in members
            ]
            adaptive = [
                100 * points[f][s]["adaptive"][arm]["all_generated_deadline_miss_rate"]
                for f, s in members
            ]
            axis.scatter(
                fixed,
                adaptive,
                label=family.replace("_", " "),
                color=FAMILY_COLORS[family],
                alpha=0.8,
            )
        maximum = max(axis.get_xlim()[1], axis.get_ylim()[1])
        axis.plot([0, maximum], [0, maximum], color="black", linewidth=1)
        axis.set_xlabel("Fixed MCS misses (%)")
        axis.set_ylabel("Adaptive MCS misses (%)")
        axis.set_title(ARM_LABELS[arm])
        axis.grid(alpha=0.2)
    axes[-1].legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=8)
    figure.suptitle("Scenario-level fixed/adaptive reliability")
    artifacts.extend(_finish(figure, directory, "scenario_fixed_vs_adaptive"))

    scenario_x = np.arange(len(ordered))
    figure, axes = plt.subplots(2, 1, figsize=(13, 7.2), sharex=True)
    for axis, arm in zip(axes, ARMS[1:], strict=True):
        values = [
            100
            * (
                points[family][scenario]["adaptive"][arm][
                    "all_generated_deadline_miss_rate"
                ]
                - points[family][scenario]["adaptive"][ARMS[0]][
                    "all_generated_deadline_miss_rate"
                ]
            )
            for family, scenario in ordered
        ]
        axis.bar(scenario_x, values, color=COLORS[arm])
        axis.axhline(0, color="black", linewidth=1)
        axis.set_ylabel(f"{ARM_LABELS[arm]} - STR (pp)")
        axis.grid(axis="y", alpha=0.2)
        for boundary in range(8, 48, 8):
            axis.axvline(boundary - 0.5, color="grey", linewidth=0.7)
    axes[-1].set_xlabel("Frozen scenario order (8 per family)")
    figure.suptitle("Adaptive-MCS policies versus adaptive-MCS STR")
    artifacts.extend(
        _finish(figure, directory, "adaptive_policy_vs_str_by_scenario")
    )

    for field, prefix, title, xlabel in (
        (
            "censored",
            "all_generated_censored_latency",
            "All-generated deadline-censored latency",
            "Deadline-censored latency (ms)",
        ),
        (
            "completed",
            "completion_latency",
            "Completed-frame latency (survivor-conditioned)",
            "Completed-frame latency (ms)",
        ),
    ):
        figure, axes = plt.subplots(1, 3, figsize=(14, 4.6), sharey=True)
        for axis, arm in zip(axes, ARMS, strict=True):
            for mode in MODES:
                values = history[(mode, arm)][field]
                cdf_x, cdf_y = _empirical_cdf(np, values)
                axis.plot(
                    cdf_x / 1000,
                    cdf_y,
                    label=mode.capitalize(),
                    linestyle="--" if mode == "fixed" else "-",
                    color=COLORS[arm],
                    alpha=0.7 if mode == "fixed" else 1.0,
                )
            axis.set_xlabel(xlabel)
            axis.set_title(ARM_LABELS[arm])
            axis.grid(alpha=0.25)
        axes[0].set_ylabel("Empirical CDF")
        axes[-1].legend()
        figure.suptitle(title)
        artifacts.extend(_finish(figure, directory, f"{prefix}_cdf"))

        all_values = [
            value
            for mode in MODES
            for arm in ARMS
            for value in history[(mode, arm)][field]
        ]
        xmax = float(np.quantile(np.asarray(all_values), 0.999)) / 1000
        bins = np.linspace(0, max(1.0, xmax), 90)
        figure, axes = plt.subplots(1, 3, figsize=(14, 4.6), sharey=True)
        for axis, arm in zip(axes, ARMS, strict=True):
            for mode in MODES:
                values = np.asarray(history[(mode, arm)][field], dtype=float) / 1000
                axis.hist(
                    values,
                    bins=bins,
                    density=True,
                    histtype="step",
                    linewidth=1.5,
                    label=mode.capitalize(),
                    linestyle="--" if mode == "fixed" else "-",
                    color=COLORS[arm],
                )
            axis.set_xlabel(xlabel)
            axis.set_title(ARM_LABELS[arm])
            axis.grid(alpha=0.25)
        axes[0].set_ylabel("Density")
        axes[-1].legend()
        figure.suptitle(f"{title} PDF")
        artifacts.extend(_finish(figure, directory, f"{prefix}_pdf"))

    figure, axes = plt.subplots(1, 2, figsize=(11, 5.0))
    positions = np.arange(len(ARMS))
    for mode_index, mode in enumerate(MODES):
        on_time = []
        late = []
        incomplete = []
        for arm in ARMS:
            source = history[(mode, arm)]
            generated = source["generated"]
            late.append(100 * source["late_completed"] / generated)
            incomplete.append(100 * source["incomplete"] / generated)
            on_time.append(100 - late[-1] - incomplete[-1])
        axis = axes[mode_index]
        axis.bar(positions, on_time, label="On-time", color="#72b7b2")
        axis.bar(positions, late, bottom=on_time, label="Late", color="#eeca3b")
        axis.bar(
            positions,
            incomplete,
            bottom=np.asarray(on_time) + np.asarray(late),
            label="Incomplete",
            color="#e45756",
        )
        axis.set_xticks(positions, [ARM_LABELS[arm] for arm in ARMS], rotation=12)
        axis.set_ylabel("All generated frames (%)")
        axis.set_title(f"{mode.capitalize()} MCS")
        axis.grid(axis="y", alpha=0.2)
    axes[-1].legend()
    figure.suptitle("Deadline outcome composition")
    artifacts.extend(_finish(figure, directory, "deadline_outcome_composition"))

    figure, axes = plt.subplots(1, 3, figsize=(14, 4.6), sharey=True)
    for axis, arm in zip(axes, ARMS, strict=True):
        for mode in MODES:
            values = history[(mode, arm)]["bursts"]
            cdf_x, cdf_y = _empirical_cdf(np, values)
            axis.step(
                cdf_x,
                cdf_y,
                where="post",
                label=mode.capitalize(),
                linestyle="--" if mode == "fixed" else "-",
                color=COLORS[arm],
            )
        axis.set_xscale("log")
        axis.set_xlabel("Consecutive misses per burst")
        axis.set_title(ARM_LABELS[arm])
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Empirical CDF")
    axes[-1].legend()
    figure.suptitle("Deadline-miss burst length")
    artifacts.extend(_finish(figure, directory, "deadline_miss_burst_cdf"))
    plt.close("all")
    expected = {
        f"{name}.{suffix}" for name in EXPECTED_PLOTS for suffix in ("png", "pdf")
    }
    _require({path.name for path in artifacts} == expected, "plot set differs")
    return artifacts


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _require(bool(rows), f"cannot write empty table {path.name}")
    fields = list(rows[0])
    _require(all(list(row) == fields for row in rows), f"columns differ in {path.name}")
    with path.open("w", newline="", encoding="ascii") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="ascii",
    )


def paired_delta_rows(
    rows: Sequence[dict[str, Any]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    expected_pair_count: int = 576,
) -> list[dict[str, Any]]:
    """Preserve exact run identities for each adaptive-minus-fixed delta."""

    indexed: dict[
        tuple[str, str, int, int, str], dict[str, dict[str, Any]]
    ] = {}
    for row in rows:
        key = (
            row["family_id"], row["scenario_id"], row["seed"], row["run"],
            row["arm_id"],
        )
        pair = indexed.setdefault(key, {})
        _require(row["mode"] not in pair, "duplicate paired-delta row")
        pair[row["mode"]] = row
    _require(
        len(indexed) == expected_pair_count
        and all(set(pair) == set(MODES) for pair in indexed.values()),
        "paired-delta closure differs",
    )
    family_order = {family: index for index, family in enumerate(families)}
    scenario_order = {
        scenario: index
        for family in families
        for index, scenario in enumerate(scenarios[family])
    }
    arm_order = {arm: index for index, arm in enumerate(ARMS)}
    result = []
    for key in sorted(indexed, key=lambda value: (
        family_order[value[0]], scenario_order[value[1]], value[2], value[3],
        arm_order[value[4]],
    )):
        fixed = indexed[key]["fixed"]
        adaptive = indexed[key]["adaptive"]
        result.append({
            "family_id": key[0],
            "scenario_id": key[1],
            "seed": key[2],
            "run": key[3],
            "arm_id": key[4],
            "fixed_run_id": fixed["run_id"],
            "adaptive_run_id": adaptive["run_id"],
            "deadline_miss_delta": (
                adaptive["all_generated_deadline_miss_rate"]
                - fixed["all_generated_deadline_miss_rate"]
            ),
            "sender_airtime_ratio": (
                adaptive["sender_airtime_us"] / fixed["sender_airtime_us"]
            ),
            "censored_mean_delta_us": (
                adaptive["all_generated_censored_mean_us"]
                - fixed["all_generated_censored_mean_us"]
            ),
        })
    return result


def _markdown(report: dict[str, Any]) -> str:
    population = report["population"]
    lines = [
        "# Fixed versus adaptive target-MCS qualification",
        "",
        f"All {population['adaptive_promoted_run_count']} promoted adaptive runs "
        "passed fresh strict validation.  The comparison uses "
        f"{population['paired_unit_count']} complete matched three-arm units per "
        "MCS mode. Deadline misses and deadline-censored latency use every "
        "generated frame; completion CDF/PDF and run-level P99 remain "
        "survivor-conditioned.",
        "",
        "## Aggregate results",
        "",
        "| MCS | Arm | Miss rate | Censored mean | Sender airtime | Background throughput | P99 support |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in MODES:
        for arm in ARMS:
            values = report["treatments"][mode][arm]
            support = report["p99_support"][mode][arm]
            lines.append(
                f"| {mode.capitalize()} | {ARM_LABELS[arm]} | "
                f"{100 * values['all_generated_deadline_miss_rate']['estimate']:.4f}% | "
                f"{values['all_generated_censored_mean_us']['estimate'] / 1000:.3f} ms | "
                f"{values['sender_airtime_us']['estimate'] / 1000:.3f} ms | "
                f"{values['background_throughput_mbps']['estimate']:.3f} Mbit/s | "
                f"{support['supported_run_count']}/{support['total_run_count']} |"
            )
    lines.extend(["", "## Adaptive minus fixed MCS", ""])
    for arm in ARMS:
        values = report["comparisons"][f"adaptive_minus_fixed__{arm}"]
        lines.extend([
            f"### {ARM_LABELS[arm]}",
            "",
            f"- Miss-rate delta: {100 * values['deadline_miss_delta']['estimate']:.4f} "
            f"percentage points (95% interval "
            f"[{100 * values['deadline_miss_delta']['ci95_low']:.4f}, "
            f"{100 * values['deadline_miss_delta']['ci95_high']:.4f}]).",
            f"- Sender-airtime ratio: {values['sender_airtime_ratio']['estimate']:.4f} "
            f"(95% interval [{values['sender_airtime_ratio']['ci95_low']:.4f}, "
            f"{values['sender_airtime_ratio']['ci95_high']:.4f}]).",
            f"- Deadline-censored mean delta: "
            f"{values['censored_mean_delta_us']['estimate'] / 1000:.3f} ms.",
            "",
        ])
    lines.extend([
        "## Adaptive-MCS policies versus adaptive-MCS STR",
        "",
    ])
    for name, label in (
        ("adaptive_v2_minus_adaptive_str", "V2"),
        ("adaptive_distributional_minus_adaptive_str", "Distributional"),
    ):
        values = report["comparisons"][name]
        lines.append(
            f"- {label}: miss delta "
            f"{100 * values['deadline_miss_delta']['estimate']:.4f} percentage points "
            f"(95% interval [{100 * values['deadline_miss_delta']['ci95_low']:.4f}, "
            f"{100 * values['deadline_miss_delta']['ci95_high']:.4f}]); sender-airtime "
            f"ratio {values['sender_airtime_ratio']['estimate']:.4f}."
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "This is a controlled MCS ablation. The selective predictors, admission "
        "rules, and conservative EhtMcs5-derived reservations were intentionally "
        "not retrained or retuned. Differences therefore include closed-loop "
        "interaction between rate adaptation and the frozen policies.",
        "The aggregate estimates and intervals weight families, scenarios, and "
        "replicates equally. CDF/PDF figures pool frames and are descriptive.",
        "",
    ])
    return "\n".join(lines)


def _load_deterministic_failure_contract(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.resolve()
    _require(
        resolved == DETERMINISTIC_FAILURE_CONTRACT_PATH.resolve(),
        "unknown deterministic-failure contract",
    )
    contract = _read_json(resolved)
    failure = contract.get("deterministic_failure")
    resolution = contract.get("analysis_resolution")
    parent = contract.get("parent_runtime_contract")
    _require(
        contract.get("schema_version") == 1
        and contract.get("contract_id")
        == "environment-generalization-adaptive-mcs-deterministic-failure-v1"
        and contract.get("status")
        == "post_execution_exception_frozen_before_final_analysis"
        and isinstance(parent, dict)
        and parent.get("path") == str(CONTRACT_PATH.relative_to(ROOT))
        and parent.get("sha256") == _sha256(CONTRACT_PATH)
        and contract.get("simulation_project_commit") == SIMULATION_COMMIT
        and isinstance(failure, dict)
        and failure.get("run_id") == "5663378425ecf42d9a21"
        and failure.get("shard_index") == 1
        and failure.get("family_id") == "compound_shift"
        and failure.get("scenario_id") == "compound-shift-qualification-p22"
        and failure.get("seed") == 21188
        and failure.get("run") == 1
        and failure.get("arm_id") == "str_mlo_nmaxinflights_1"
        and failure.get("attempt_count") == 2
        and isinstance(resolution, dict)
        and resolution.get("strictly_validate_all_promoted_adaptive_runs") == 575
        and resolution.get("matched_paired_unit_count") == 191
        and resolution.get("analyzed_run_count_per_mcs_mode") == 573
        and resolution.get("replacement_seed_allowed") is False
        and resolution.get("patched_binary_allowed") is False
        and resolution.get("further_retry_allowed") is False,
        "deterministic-failure contract differs",
    )
    return contract


def _unit_key(row: dict[str, Any]) -> tuple[str, str, int, int]:
    return (
        row["family_id"], row["scenario_id"], int(row["seed"]), int(row["run"])
    )


def analyze(
    shard_roots: Sequence[Path],
    output: Path,
    workers: int,
    deterministic_failure_contract: Path | None = None,
) -> Path:
    """Strictly validate adaptive evidence, compare, plot, and archive atomically."""

    _require(workers > 0, "workers must be positive")
    _require(not output.exists(), f"output already exists: {output}")
    git_identity = _git_identity()
    failure_contract = _load_deterministic_failure_contract(
        deterministic_failure_contract
    )
    contract = _read_json(CONTRACT_PATH)
    _require(
        contract.get("runtime_contract_id")
        == "environment-generalization-adaptive-mcs-qualification-v1"
        and contract.get("analysis", {}).get("stop_after_archive") is True,
        "adaptive analysis contract differs",
    )
    failure = (
        failure_contract["deterministic_failure"]
        if failure_contract is not None
        else None
    )
    jobs, families, scenarios, shard_identities = validate_adaptive_shards(
        shard_roots,
        None if failure is None else failure["run_id"],
        None if failure is None else failure["shard_index"],
    )
    if failure_contract is not None:
        expected_manifests = {
            row["shard_index"]: row
            for row in failure_contract["final_shard_manifests"]
        }
        _require(
            len(expected_manifests) == 2
            and all(
                identity["sha256"] == expected_manifests[identity["index"]]["sha256"]
                and identity["complete_run_count"]
                == expected_manifests[identity["index"]]["promoted_run_count"]
                for identity in shard_identities
            ),
            "final shard manifests differ from failure contract",
        )
    observations = complete.collect_observations(jobs, workers)
    fixed_rows, fixed_identity = load_fixed_rows()
    expected_adaptive_count = 575 if failure_contract is not None else 576
    adaptive = adaptive_rows(observations, expected_adaptive_count)
    if failure is not None:
        excluded_unit = (
            failure["family_id"], failure["scenario_id"], failure["seed"],
            failure["run"],
        )
        adaptive = [row for row in adaptive if _unit_key(row) != excluded_unit]
        fixed = [row for row in fixed_rows if _unit_key(row) != excluded_unit]
    else:
        excluded_unit = None
        fixed = fixed_rows
    paired_unit_count = 191 if failure_contract is not None else 192
    analyzed_run_count = paired_unit_count * len(ARMS)
    _require(
        len(adaptive) == len(fixed) == analyzed_run_count,
        "matched analysis population differs",
    )
    rows = fixed + adaptive
    history_means, history = collect_history(rows, workers)
    for row in rows:
        row["all_generated_censored_mean_us"] = history_means[
            (row["mode"], row["run_id"])
        ]
    grid = build_grid(rows, families, scenarios, paired_unit_count)
    result = bootstrap(grid, families, scenarios)
    p99_support = _p99_support(rows)
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "source_closure": {
            "adaptive_shard_manifests": shard_identities,
            "fixed_evidence": fixed_identity,
            "simulation_project_commit": SIMULATION_COMMIT,
            "analyzer": {
                **git_identity,
                "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": _sha256(Path(__file__).resolve()),
            },
            "runtime_contract": {
                "path": str(CONTRACT_PATH.relative_to(ROOT)),
                "sha256": _sha256(CONTRACT_PATH),
            },
            "deterministic_failure_contract": (
                None
                if failure_contract is None
                else {
                    "path": str(
                        DETERMINISTIC_FAILURE_CONTRACT_PATH.relative_to(ROOT)
                    ),
                    "sha256": _sha256(DETERMINISTIC_FAILURE_CONTRACT_PATH),
                }
            ),
            "strict_validator": {
                "path": "tools/validate_outputs.py",
                "sha256": _sha256(ROOT / "tools/validate_outputs.py"),
            },
        },
        "population": {
            "modes": list(MODES),
            "adaptive_promoted_run_count": len(observations),
            "archived_fixed_run_count": len(fixed_rows),
            "analyzed_run_count_per_mode": analyzed_run_count,
            "paired_unit_count": paired_unit_count,
            "arm_count": 3,
            "family_count": 6,
            "scenario_count": 48,
            "excluded_unit": (
                None
                if failure is None
                else {
                    key: failure[key]
                    for key in (
                        "family_id", "scenario_id", "parameter_sample", "seed",
                        "run", "run_id", "arm_id",
                    )
                }
            ),
        },
        "strict_validation": {
            "adaptive": f"pass: {len(observations)} freshly validated runs",
            "fixed": "checksum-bound archived strict validation: 576 runs",
        },
        "bootstrap": {
            "method": result["method"],
            "replications": result["replications"],
            "random_seed": result["random_seed"],
        },
        "treatments": result["treatments"],
        "comparisons": result["comparisons"],
        "family_points": result["family_points"],
        "scenario_points": result["scenario_points"],
        "p99_support": p99_support,
        "interpretation_limits": [
            "Deadline misses and deadline-censored latency include every generated frame.",
            "Completion CDF/PDF and completed-frame P99 are survivor-conditioned.",
            "Predictors, actions, admission policies, and canonical reservations are unchanged.",
            "The fixed campaign is reused from its checksum-closed archived strict analysis.",
            "One deterministic native ns-3 failure is excluded as a complete three-arm unit."
            if failure is not None
            else "The adaptive campaign is complete.",
        ],
        "execution_exception": failure_contract,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        analysis_dir = temporary / "analysis"
        plots_dir = temporary / "plots"
        manifests_dir = temporary / "source_manifests"
        analysis_dir.mkdir()
        plots_dir.mkdir()
        manifests_dir.mkdir()
        for identity in shard_identities:
            shutil.copyfile(
                identity["path"],
                manifests_dir
                / f"adaptive_shard{identity['index']}_experiment_manifest.json",
            )
        _write_json(analysis_dir / "comparison_report.json", report)
        (analysis_dir / "comparison_report.md").write_text(
            _markdown(report), encoding="ascii"
        )
        run_table = []
        for row in rows:
            run_table.append({
                key: row[key]
                for key in (
                    "mode", "family_id", "scenario_id", "parameter_sample", "seed",
                    "run", "arm_id", "run_id", "generated_frame_count",
                    "completed_frame_count", "incomplete_frame_count",
                    "deadline_miss_count", "all_generated_deadline_miss_rate",
                    "all_generated_censored_mean_us",
                    "completed_frame_hf7_p99_supported",
                    "completed_frame_hf7_p99_us", "sender_airtime_us",
                    "background_throughput_mbps",
                )
            })
        _write_csv(analysis_dir / "run_metrics.csv", run_table)
        family_table = [
            {
                "family_id": family,
                "mode": mode,
                "arm_id": arm,
                **result["family_points"][family][mode][arm],
            }
            for family in families
            for mode in MODES
            for arm in ARMS
        ]
        _write_csv(analysis_dir / "family_metrics.csv", family_table)
        scenario_table = [
            {
                "family_id": family,
                "scenario_id": scenario,
                "mode": mode,
                "arm_id": arm,
                **result["scenario_points"][family][scenario][mode][arm],
            }
            for family in families
            for scenario in scenarios[family]
            for mode in MODES
            for arm in ARMS
        ]
        _write_csv(analysis_dir / "scenario_metrics.csv", scenario_table)
        comparison_table = [
            {
                "comparison": name,
                "metric": metric,
                **interval,
            }
            for name, values in result["comparisons"].items()
            for metric, interval in values.items()
        ]
        _write_csv(analysis_dir / "comparison_intervals.csv", comparison_table)
        paired_rows = paired_delta_rows(
            rows, families, scenarios, analyzed_run_count
        )
        _write_csv(analysis_dir / "paired_mcs_deltas.csv", paired_rows)
        plot_paths = render_plots(report, history, plots_dir)
        _write_json(
            plots_dir / "plot_artifact_manifest.json",
            {
                "schema_version": 1,
                "analysis": ANALYSIS_ID,
                "plots": {
                    path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
                    for path in plot_paths
                },
            },
        )
        readme = _markdown(report)
        (temporary / "README.md").write_text(readme, encoding="ascii")
        artifacts = {
            str(path.relative_to(temporary)): {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(temporary.rglob("*"))
            if path.is_file() and path.name != "artifact_manifest.json"
        }
        _write_json(
            temporary / "artifact_manifest.json",
            {
                "schema_version": 1,
                "analysis": ANALYSIS_ID,
                "counts": {
                    "adaptive_strictly_validated_runs": len(observations),
                    "fixed_archived_runs": len(fixed_rows),
                    "analyzed_runs_per_mode": analyzed_run_count,
                    "paired_units": paired_unit_count,
                    "figures": len(EXPECTED_PLOTS),
                    "figure_formats": 2,
                },
                "artifacts": artifacts,
            },
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output.resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adaptive-shard-root", type=Path, action="append", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--deterministic-failure-contract", type=Path)
    args = parser.parse_args(argv)
    if len(args.adaptive_shard_root) != 2:
        parser.error("--adaptive-shard-root must be supplied exactly twice")
    try:
        output = analyze(
            [path.resolve() for path in args.adaptive_shard_root],
            args.output_directory.resolve(),
            args.workers,
            args.deterministic_failure_contract,
        )
    except (
        AdaptiveMcsAnalysisError,
        complete.CompleteReliabilityError,
        formal.QualificationAnalysisError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(2, f"ERROR: {error}\n")
    print(f"WROTE {output} figures={len(EXPECTED_PLOTS)} formats=png,pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
