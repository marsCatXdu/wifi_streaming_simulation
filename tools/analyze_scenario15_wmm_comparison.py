#!/usr/bin/env python3
"""Analyze the paired scenario-15 WMM comparison across two campaign shards."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

from run_experiments import (
    NS3_UPSTREAM_COMMIT,
    canonical_json,
    derive_run_id,
    expand_config,
    load_yaml,
    matrix_sha256,
    validate_runtime_contract,
)
from validate_outputs import ValidationError, validate_run


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_ID = "scenario15_wmm_comparison_v1"
SCHEMA_VERSION = 1
EXPECTED_SEEDS = tuple(range(1251, 1299))
EXPECTED_RUN = 1
EXPECTED_RUN_COUNT = 288
EXPECTED_PROJECT_COMMIT = "bb0bb6160ae7dd2ea312412b0b7dfc6bf36880d5"
EXPECTED_IMPLEMENTATION_COMMIT = "f08596e26d614a97e35b91aa1960d5c40c2ae4f1"
BOOTSTRAP_REPLICATIONS = 10_000
BOOTSTRAP_SEED = 20260808
CONTRACT_PATH = ROOT / "experiments/model-selection/scenario15-wmm-comparison-v1.json"
CONTRACT_SHA256 = "e31dcff5d8642a3f4ee1b418fc22b728a7c277ab1ba9e18cb0b349d323d16e44"
SHARD_CONFIGS = {
    "scenario15-wmm-comparison-v1-shard0": (
        ROOT / "experiments/configs/scenario15_wmm_comparison_v1_shard0.yaml"
    ),
    "scenario15-wmm-comparison-v1-shard1": (
        ROOT / "experiments/configs/scenario15_wmm_comparison_v1_shard1.yaml"
    ),
}
ARM_IDENTITIES = {
    "str_mlo": ("mlo_str", "fixed_link_0"),
    "score_aware_t2_v2": ("dual_interface", "paired_value_duplication_t2"),
    "distributional_shadow_t2": (
        "dual_interface",
        "distributional_shadow_duplication_t2",
    ),
}
ARM_LABELS = {
    "str_mlo": "STR MLO",
    "score_aware_t2_v2": "Score-aware T2 V2",
    "distributional_shadow_t2": "Distributional shadow T2",
}
IDENTITY_TO_ARM = {value: key for key, value in ARM_IDENTITIES.items()}
WMM_PROFILES = {
    "off": (0, 0, "AC_BE"),
    "on": (160, 5, "AC_VI"),
}
METRICS = (
    "all_generated_deadline_miss_rate",
    "completed_frame_p99_us",
    "sender_airtime_us",
    "background_throughput_mbps",
)


class AnalysisError(ValueError):
    """Raised when campaign evidence does not satisfy the frozen contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise AnalysisError(f"{path}: expected a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise AnalysisError(f"{path}: invalid CSV header")
            rows = list(reader)
    except OSError as error:
        raise AnalysisError(f"{path}: cannot read CSV: {error}") from error
    if any(None in row for row in rows):
        raise AnalysisError(f"{path}: row exceeds declared width")
    return rows


def _hf7(values: Sequence[float], probability: float) -> float:
    if not values or not 0 <= probability <= 1:
        raise AnalysisError("invalid Hyndman-Fan type-7 quantile input")
    return _hf7_ordered(sorted(values), probability)


def _hf7_ordered(ordered: Sequence[float], probability: float) -> float:
    """Return an HF7 quantile from an already sorted nonempty sample."""
    if not ordered or not 0 <= probability <= 1:
        raise AnalysisError("invalid ordered Hyndman-Fan type-7 quantile input")
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _bootstrap_indexes() -> tuple[tuple[int, ...], ...]:
    generator = random.Random(BOOTSTRAP_SEED)
    return tuple(
        tuple(generator.randrange(len(EXPECTED_SEEDS)) for _ in EXPECTED_SEEDS)
        for _ in range(BOOTSTRAP_REPLICATIONS)
    )


def _bootstrap_hash(indexes: Sequence[Sequence[int]]) -> str:
    encoded = bytes(index for row in indexes for index in row)
    return hashlib.sha256(encoded).hexdigest()


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values)


def _mean_delta(left: Sequence[float], right: Sequence[float]) -> float:
    return statistics.mean(a - b for a, b in zip(left, right))


def _ratio_of_means(left: Sequence[float], right: Sequence[float]) -> float:
    denominator = statistics.mean(right)
    if denominator <= 0:
        raise AnalysisError("ratio denominator is nonpositive")
    return statistics.mean(left) / denominator


def _background_loss(left: Sequence[float], right: Sequence[float]) -> float:
    return 1.0 - _ratio_of_means(left, right)


def _bootstrap_one(
    values: Sequence[float],
    indexes: Sequence[Sequence[int]],
) -> dict[str, Any]:
    if len(values) != len(EXPECTED_SEEDS):
        raise AnalysisError("single-arm bootstrap requires 48 runs")
    samples = [statistics.mean(values[index] for index in row) for row in indexes]
    return {
        "estimate": statistics.mean(values),
        "ci95_low": _hf7(samples, 0.025),
        "ci95_high": _hf7(samples, 0.975),
    }


def _bootstrap_pair(
    left: Sequence[float],
    right: Sequence[float],
    indexes: Sequence[Sequence[int]],
    statistic: Callable[[Sequence[float], Sequence[float]], float],
    description: str,
) -> dict[str, Any]:
    if len(left) != len(EXPECTED_SEEDS) or len(right) != len(EXPECTED_SEEDS):
        raise AnalysisError("paired bootstrap requires two 48-run samples")
    samples = [
        statistic(
            [left[index] for index in row],
            [right[index] for index in row],
        )
        for row in indexes
    ]
    return {
        "method": "deterministic paired whole-run percentile bootstrap",
        "statistic": description,
        "paired_unit_count": len(EXPECTED_SEEDS),
        "replications": BOOTSTRAP_REPLICATIONS,
        "seed": BOOTSTRAP_SEED,
        "estimate": statistic(left, right),
        "ci95_low": _hf7(samples, 0.025),
        "ci95_high": _hf7(samples, 0.975),
    }


def _verify_contract() -> dict[str, Any]:
    if _sha256_file(CONTRACT_PATH) != CONTRACT_SHA256:
        raise AnalysisError("scenario-15 WMM runtime contract checksum changed")
    contract = _read_json(CONTRACT_PATH)
    campaign = contract.get("campaign", {})
    if (
        contract.get("runtime_contract_id") != "scenario15-wmm-comparison-v1"
        or contract.get("status") != "frozen_before_wmm_outcomes"
        or contract.get("implementation_commit") != EXPECTED_IMPLEMENTATION_COMMIT
        or campaign.get("seed_first") != EXPECTED_SEEDS[0]
        or campaign.get("seed_last") != EXPECTED_SEEDS[-1]
        or campaign.get("simulation_run_count") != EXPECTED_RUN_COUNT
        or campaign.get("reserved_confirmation_seeds_used") is not False
    ):
        raise AnalysisError("scenario-15 WMM runtime contract content changed")
    return contract


def _manifest_jobs(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = root.resolve()
    manifest_path = root / "experiment_manifest.json"
    manifest = _read_json(manifest_path)
    experiment = manifest.get("experiment")
    if experiment not in SHARD_CONFIGS:
        raise AnalysisError(f"{manifest_path}: unexpected shard experiment {experiment!r}")
    config_path = SHARD_CONFIGS[experiment]
    document = load_yaml(config_path)
    runtime = validate_runtime_contract(document)
    if runtime is None:
        raise AnalysisError(f"{config_path}: runtime contract is absent")
    expected_manifest = {
        "schema_version": 2,
        "matrix_sha256": matrix_sha256(document),
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
        "runtime_contract_id": runtime["runtime_contract_id"],
        "runtime_contract_sha256": runtime["runtime_contract_sha256"],
        "source_artifacts": runtime["source_artifacts"],
    }
    for key, expected in expected_manifest.items():
        if canonical_json(manifest.get(key)) != canonical_json(expected):
            raise AnalysisError(f"{manifest_path}: {key} differs from frozen shard")
    project_commit = manifest.get("project_commit")
    if project_commit != EXPECTED_PROJECT_COMMIT:
        raise AnalysisError(f"{manifest_path}: execution commit differs from formal launch")
    specs: dict[str, dict[str, Any]] = {}
    for spec in expand_config(document):
        run_id = derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            NS3_UPSTREAM_COMMIT,
            project_commit,
            runtime,
        )
        specs[run_id] = spec
    entries = manifest.get("runs")
    if not isinstance(entries, list) or len(entries) != 144:
        raise AnalysisError(f"{manifest_path}: shard does not contain 144 completed runs")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise AnalysisError(f"{manifest_path}: invalid run entry")
        run_id = entry.get("run_id")
        if run_id in seen or run_id not in specs:
            raise AnalysisError(f"{manifest_path}: unexpected or duplicate run {run_id}")
        seen.add(run_id)
        spec = specs[run_id]
        if (
            entry.get("status") != "complete"
            or entry.get("directory") != run_id
            or entry.get("seed") != spec["seed"]
            or entry.get("run") != spec["run"]
            or canonical_json(entry.get("config")) != canonical_json(spec["config"])
        ):
            raise AnalysisError(f"{manifest_path}: run identity differs for {run_id}")
        run_dir = root / run_id
        if not run_dir.is_dir():
            raise AnalysisError(f"{manifest_path}: missing run directory {run_id}")
        jobs.append(
            {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "project_commit": project_commit,
                "seed": spec["seed"],
                "run": spec["run"],
                "input_config": spec["config"],
            }
        )
    if seen != set(specs):
        raise AnalysisError(f"{manifest_path}: completed run set differs from matrix")
    identity = {
        "experiment": experiment,
        "path": str(manifest_path),
        "sha256": _sha256_file(manifest_path),
        "matrix_sha256": manifest["matrix_sha256"],
        "project_commit": project_commit,
        "completed_run_count": len(entries),
    }
    return identity, jobs


def _burst_lengths(flags: Sequence[bool]) -> list[int]:
    bursts: list[int] = []
    current = 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            bursts.append(current)
            current = 0
    if current:
        bursts.append(current)
    return bursts


def _observe(job: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=job["run_id"],
            expected_project_commit=job["project_commit"],
            expected_ns3_commit=NS3_UPSTREAM_COMMIT,
        )
    except ValidationError as error:
        raise AnalysisError(f"{run_dir}: strict validation failed: {error}") from error
    config = _read_json(run_dir / "resolved_config.json")
    if (
        config.get("run_id") != job["run_id"]
        or config.get("seed") != job["seed"]
        or config.get("run") != job["run"]
    ):
        raise AnalysisError(f"{run_dir}: resolved identity differs")
    identity = (config.get("topology"), config.get("policy"))
    arm = IDENTITY_TO_ARM.get(identity)
    if arm is None:
        raise AnalysisError(f"{run_dir}: unexpected arm identity {identity}")
    wifi = config.get("wifi")
    if not isinstance(wifi, dict):
        raise AnalysisError(f"{run_dir}: Wi-Fi config is missing")
    mode = wifi.get("wmm_mode")
    if mode not in WMM_PROFILES:
        raise AnalysisError(f"{run_dir}: WMM mode is invalid")
    tos, tid, access_category = WMM_PROFILES[mode]
    if (
        wifi.get("stream_ip_tos") != tos
        or wifi.get("stream_tid") != tid
        or wifi.get("access_category") != access_category
    ):
        raise AnalysisError(f"{run_dir}: WMM stream profile differs")
    frames = _read_csv(run_dir / "frames.csv")
    if len(frames) != 1800:
        raise AnalysisError(f"{run_dir}: expected 1800 generated frames")
    completed: list[float] = []
    censored: list[float] = []
    miss_flags: list[bool] = []
    seen_frames: set[int] = set()
    for row in sorted(frames, key=lambda item: int(item["frame_id"])):
        frame_id = int(row["frame_id"])
        if frame_id in seen_frames:
            raise AnalysisError(f"{run_dir}: duplicate frame {frame_id}")
        seen_frames.add(frame_id)
        deadline = float(row["deadline_us"])
        incomplete = row["incomplete"] == "1"
        miss = row["deadline_miss"] == "1"
        if row["incomplete"] not in {"0", "1"} or row["deadline_miss"] not in {"0", "1"}:
            raise AnalysisError(f"{run_dir}: invalid frame flags")
        if incomplete:
            if row["union_latency_us"]:
                raise AnalysisError(f"{run_dir}: incomplete frame has latency")
            latency = None
        else:
            latency = float(row["union_latency_us"])
            completed.append(latency)
        expected_miss = incomplete or (latency is not None and latency > deadline)
        if miss != expected_miss:
            raise AnalysisError(f"{run_dir}: deadline flag differs from latency")
        miss_flags.append(miss)
        censored.append(deadline if latency is None else min(latency, deadline))
    if not completed:
        raise AnalysisError(f"{run_dir}: no completed frames")
    link_rows = _read_csv(run_dir / "link_intervals.csv")
    link_airtime = {int(row["link_id"]): float(row["phy_tx_time_us"]) for row in link_rows}
    if set(link_airtime) != {0, 1} or len(link_airtime) != len(link_rows):
        raise AnalysisError(f"{run_dir}: sender airtime links differ")
    background_rows = _read_csv(run_dir / "background_flows.csv")
    duration = float(config["measurement_stop_s"]) - float(config["measurement_start_s"])
    background_bytes = sum(int(row["bytes_received"]) for row in background_rows)
    summary = _read_json(run_dir / "summary.json")
    bursts = _burst_lengths(miss_flags)
    build_info = _read_json(run_dir / "build_info.json")
    return {
        "run_id": job["run_id"],
        "run_dir": str(run_dir),
        "seed": job["seed"],
        "run": job["run"],
        "arm": arm,
        "wmm_mode": mode,
        "config": config,
        "build_identity": {
            key: build_info.get(key)
            for key in ("ns3_version", "ns3_upstream_commit", "project_git_commit")
        },
        "generated_frame_count": len(frames),
        "completed_frame_count": len(completed),
        "incomplete_frame_count": len(frames) - len(completed),
        "deadline_miss_count": sum(miss_flags),
        "all_generated_deadline_miss_rate": sum(miss_flags) / len(frames),
        "completed_frame_p99_us": _hf7(completed, 0.99),
        "sender_airtime_us": sum(link_airtime.values()),
        "background_throughput_mbps": background_bytes * 8 / duration / 1_000_000,
        "action_count": int(summary.get("duplicate_frame_count", 0)),
        "max_deadline_miss_burst": max(bursts, default=0),
        "deadline_miss_bursts": bursts,
        "completed_latencies_us": completed,
        "deadline_censored_latencies_us": censored,
        "strict_validation": validation,
    }


def _treatment_summary(
    rows: Sequence[dict[str, Any]],
    indexes: Sequence[Sequence[int]],
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda item: item["seed"])
    if [row["seed"] for row in ordered] != list(EXPECTED_SEEDS):
        raise AnalysisError("treatment seed set differs")
    result = {
        "run_count": len(ordered),
        "generated_frame_count": sum(row["generated_frame_count"] for row in ordered),
        "completed_frame_count": sum(row["completed_frame_count"] for row in ordered),
        "incomplete_frame_count": sum(row["incomplete_frame_count"] for row in ordered),
        "deadline_miss_count": sum(row["deadline_miss_count"] for row in ordered),
        "action_count": sum(row["action_count"] for row in ordered),
        "max_deadline_miss_burst": max(row["max_deadline_miss_burst"] for row in ordered),
    }
    for metric in METRICS:
        result[metric] = _bootstrap_one([row[metric] for row in ordered], indexes)
    return result


def _comparison(
    left: Sequence[dict[str, Any]],
    right: Sequence[dict[str, Any]],
    indexes: Sequence[Sequence[int]],
) -> dict[str, Any]:
    left = sorted(left, key=lambda item: item["seed"])
    right = sorted(right, key=lambda item: item["seed"])
    if [row["seed"] for row in left] != [row["seed"] for row in right]:
        raise AnalysisError("comparison is not paired by seed")
    miss_left = [row["all_generated_deadline_miss_rate"] for row in left]
    miss_right = [row["all_generated_deadline_miss_rate"] for row in right]
    p99_left = [row["completed_frame_p99_us"] for row in left]
    p99_right = [row["completed_frame_p99_us"] for row in right]
    airtime_left = [row["sender_airtime_us"] for row in left]
    airtime_right = [row["sender_airtime_us"] for row in right]
    background_left = [row["background_throughput_mbps"] for row in left]
    background_right = [row["background_throughput_mbps"] for row in right]
    directions = Counter(
        "better" if a < b else "worse" if a > b else "tie"
        for a, b in zip(miss_left, miss_right)
    )
    return {
        "deadline_miss_rate_delta": _bootstrap_pair(
            miss_left,
            miss_right,
            indexes,
            _mean_delta,
            "mean paired left-minus-right all-generated deadline-miss rate",
        ),
        "completed_frame_p99_delta_us": _bootstrap_pair(
            p99_left,
            p99_right,
            indexes,
            _mean_delta,
            "mean paired left-minus-right completed-frame HF7 P99",
        ),
        "sender_airtime_ratio": _bootstrap_pair(
            airtime_left,
            airtime_right,
            indexes,
            _ratio_of_means,
            "ratio of left and right mean sender PHY airtime",
        ),
        "background_throughput_loss": _bootstrap_pair(
            background_left,
            background_right,
            indexes,
            _background_loss,
            "one minus left/right mean background throughput ratio",
        ),
        "deadline_miss_direction_counts": {
            key: directions.get(key, 0) for key in ("better", "tie", "worse")
        },
        "relative_deadline_miss_reduction": (
            1.0 - statistics.mean(miss_left) / statistics.mean(miss_right)
            if statistics.mean(miss_right) > 0
            else None
        ),
    }


def _curve_summary(
    samples: Sequence[Sequence[float]], probabilities: Sequence[float]
) -> dict[str, Any]:
    ordered_samples = [sorted(values) for values in samples]
    run_curves = [
        [_hf7_ordered(values, probability) for probability in probabilities]
        for values in ordered_samples
    ]
    pooled = sorted(value for values in samples for value in values)
    columns = list(zip(*run_curves))
    return {
        "probabilities": list(probabilities),
        "pooled_latency_us": [
            _hf7_ordered(pooled, probability) for probability in probabilities
        ],
        "run_hf7_median_latency_us": [_hf7(column, 0.5) for column in columns],
        "run_hf7_p10_latency_us": [_hf7(column, 0.1) for column in columns],
        "run_hf7_p90_latency_us": [_hf7(column, 0.9) for column in columns],
    }


def _pdf_summary(
    samples: Sequence[Sequence[float]], bin_width_us: int, bin_count: int
) -> dict[str, Any]:
    def counts(values: Sequence[float]) -> list[int]:
        result = [0] * bin_count
        for value in values:
            index = min(int(value // bin_width_us), bin_count - 1)
            result[index] += 1
        return result

    run_counts = [counts(values) for values in samples]
    width_ms = bin_width_us / 1000
    run_densities = [
        [count / len(values) / width_ms for count in row]
        for values, row in zip(samples, run_counts)
    ]
    columns = list(zip(*run_densities))
    pooled_counts = [sum(row[index] for row in run_counts) for index in range(bin_count)]
    pooled_size = sum(len(values) for values in samples)
    return {
        "bin_width_us": bin_width_us,
        "bin_left_us": [index * bin_width_us for index in range(bin_count)],
        "pooled_counts": pooled_counts,
        "pooled_density_per_ms": [count / pooled_size / width_ms for count in pooled_counts],
        "run_density_median_per_ms": [_hf7(column, 0.5) for column in columns],
        "run_density_p10_per_ms": [_hf7(column, 0.1) for column in columns],
        "run_density_p90_per_ms": [_hf7(column, 0.9) for column in columns],
    }


def _plot_series(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda item: item["seed"])
    completed_samples = [row["completed_latencies_us"] for row in ordered_rows]
    censored_samples = [row["deadline_censored_latencies_us"] for row in ordered_rows]
    completed = [value for values in completed_samples for value in values]
    probabilities = sorted(
        set([index / 1000 for index in range(1001)] + [0.99 + index / 100_000 for index in range(1001)])
    )
    bin_width_us = 500
    upper_us = max(60_000, math.ceil(max(completed) / bin_width_us) * bin_width_us)
    bin_count = int(upper_us / bin_width_us)
    bursts = [value for row in ordered_rows for value in row["deadline_miss_bursts"]]
    return {
        "completed_frame_count": len(completed),
        "completed_cdf": {
            **_curve_summary(completed_samples, probabilities),
        },
        "deadline_censored_cdf": {
            "definition": "min(completion latency, deadline); incomplete frames equal deadline",
            **_curve_summary(censored_samples, probabilities),
        },
        "completed_pdf": _pdf_summary(completed_samples, bin_width_us, bin_count),
        "deadline_censored_pdf": {
            "definition": "min(completion latency, deadline); incomplete frames equal deadline",
            **_pdf_summary(censored_samples, bin_width_us, bin_count),
        },
        "deadline_miss_burst_cdf": {
            "burst_count": len(bursts),
            "lengths": sorted(bursts),
        },
    }


def _historical_comparison(treatments: dict[str, Any]) -> dict[str, Any]:
    report = _read_json(
        ROOT
        / "key_experiment_results/15_distributional_shadow_t2_str_engineering_v1/"
        "distributional_shadow_t2_str_engineering.json"
    )
    v2 = _read_json(
        ROOT
        / "key_experiment_results/15_distributional_shadow_t2_str_engineering_v1/"
        "v2_comparison/distributional_shadow_t2_v2_comparison.json"
    )
    historical = {
        "str_mlo": {
            "misses": report["treatments"]["str_mlo"]["all_generated_deadline_miss_rate"]["total_misses"],
            "p99_us": report["treatments"]["str_mlo"]["completed_frame_p99_us"]["mean"],
        },
        "score_aware_t2_v2": {
            "misses": v2["headline"]["v2_final_misses"],
            "p99_us": v2["headline"]["v2_mean_per_run_completed_p99_us"],
        },
        "distributional_shadow_t2": {
            "misses": report["treatments"]["policy"]["all_generated_deadline_miss_rate"]["total_misses"],
            "p99_us": report["treatments"]["policy"]["completed_frame_p99_us"]["mean"],
        },
    }
    return {
        arm: {
            "historical": values,
            "current_wmm_off": {
                "misses": treatments["off"][arm]["deadline_miss_count"],
                "p99_us": treatments["off"][arm]["completed_frame_p99_us"]["estimate"],
            },
            "delta": {
                "misses": treatments["off"][arm]["deadline_miss_count"] - values["misses"],
                "p99_us": treatments["off"][arm]["completed_frame_p99_us"]["estimate"] - values["p99_us"],
            },
        }
        for arm, values in historical.items()
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Scenario-15 WMM comparison",
        "",
        "This is opened-seed engineering evidence on seeds 1251 through 1298. "
        "WMM off means historical CS0/TID 0/AC_BE streaming; WMM on means "
        "CS5/TID 5/AC_VI streaming with standard EDCA defaults.",
        "",
        "| WMM | Approach | Misses | Miss rate | Mean per-run P99 | Sender airtime | Actions |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in ("off", "on"):
        for arm in ARM_IDENTITIES:
            item = report["treatments"][mode][arm]
            lines.append(
                f"| {mode} | {ARM_LABELS[arm]} | {item['deadline_miss_count']:,} | "
                f"{item['all_generated_deadline_miss_rate']['estimate'] * 100:.4f}% | "
                f"{item['completed_frame_p99_us']['estimate'] / 1000:.3f} ms | "
                f"{item['sender_airtime_us']['estimate'] / 1_000_000:.3f} s/run | "
                f"{item['action_count']:,} |"
            )
    lines.extend(["", "## Paired WMM effect (on minus off)", ""])
    for arm in ARM_IDENTITIES:
        item = report["wmm_effects"][arm]
        miss = item["deadline_miss_rate_delta"]
        p99 = item["completed_frame_p99_delta_us"]
        airtime = item["sender_airtime_ratio"]
        lines.append(
            f"- {ARM_LABELS[arm]}: miss delta {miss['estimate'] * 100:+.4f} pp "
            f"[{miss['ci95_low'] * 100:+.4f}, {miss['ci95_high'] * 100:+.4f}], "
            f"P99 delta {p99['estimate'] / 1000:+.3f} ms "
            f"[{p99['ci95_low'] / 1000:+.3f}, {p99['ci95_high'] / 1000:+.3f}], "
            f"airtime ratio {airtime['estimate']:.4f} "
            f"[{airtime['ci95_low']:.4f}, {airtime['ci95_high']:.4f}]."
        )
    lines.extend(["", "## Within-mode comparisons", ""])
    for mode in ("off", "on"):
        lines.append(f"### WMM {mode}")
        lines.append("")
        for key in (
            "score_aware_t2_v2_minus_str_mlo",
            "distributional_shadow_t2_minus_str_mlo",
            "distributional_shadow_t2_minus_score_aware_t2_v2",
        ):
            item = report["within_mode_comparisons"][mode][key]
            miss = item["deadline_miss_rate_delta"]
            p99 = item["completed_frame_p99_delta_us"]
            lines.append(
                f"- `{key}`: miss delta {miss['estimate'] * 100:+.4f} pp "
                f"[{miss['ci95_low'] * 100:+.4f}, {miss['ci95_high'] * 100:+.4f}]; "
                f"P99 delta {p99['estimate'] / 1000:+.3f} ms "
                f"[{p99['ci95_low'] / 1000:+.3f}, {p99['ci95_high'] / 1000:+.3f}]."
            )
        lines.append("")
    lines.extend(
        [
            "## Evidence boundary",
            "",
            f"All {report['campaign_checks']['strictly_validated_run_count']} runs passed "
            "strict validation. Reserved seeds 1301 through 1348 were not used.",
            "",
        ]
    )
    return "\n".join(lines)


def analyze(roots: Sequence[Path], workers: int) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    contract = _verify_contract()
    identities: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for root in roots:
        identity, shard_jobs = _manifest_jobs(root)
        identities.append(identity)
        jobs.extend(shard_jobs)
    if len(identities) != 2 or {item["experiment"] for item in identities} != set(SHARD_CONFIGS):
        raise AnalysisError("exactly the two frozen formal shards are required")
    if len(jobs) != EXPECTED_RUN_COUNT or len({job["run_id"] for job in jobs}) != EXPECTED_RUN_COUNT:
        raise AnalysisError("merged run set is not the exact 288-run matrix")
    with ProcessPoolExecutor(max_workers=workers) as executor:
        observations = list(executor.map(_observe, jobs))
    build_identities = {canonical_json(row["build_identity"]) for row in observations}
    if len(build_identities) != 1:
        raise AnalysisError("runs contain multiple build identities")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in observations:
        grouped.setdefault((row["wmm_mode"], row["arm"]), []).append(row)
    expected_groups = {(mode, arm) for mode in WMM_PROFILES for arm in ARM_IDENTITIES}
    if set(grouped) != expected_groups or any(len(rows) != 48 for rows in grouped.values()):
        raise AnalysisError("treatment groups do not form six complete 48-run cells")
    for seed in EXPECTED_SEEDS:
        units = [row for row in observations if row["seed"] == seed]
        if len(units) != 6:
            raise AnalysisError(f"seed {seed} does not contain six treatments")
        geometries = {
            canonical_json(row["config"]["background"]["obss"].get("bsses", []))
            for row in units
        }
        if len(geometries) != 1:
            raise AnalysisError(f"seed {seed} OBSS realization differs across treatments")
    indexes = _bootstrap_indexes()
    treatments = {
        mode: {
            arm: _treatment_summary(grouped[(mode, arm)], indexes)
            for arm in ARM_IDENTITIES
        }
        for mode in WMM_PROFILES
    }
    within = {}
    for mode in WMM_PROFILES:
        within[mode] = {
            "score_aware_t2_v2_minus_str_mlo": _comparison(
                grouped[(mode, "score_aware_t2_v2")], grouped[(mode, "str_mlo")], indexes
            ),
            "distributional_shadow_t2_minus_str_mlo": _comparison(
                grouped[(mode, "distributional_shadow_t2")], grouped[(mode, "str_mlo")], indexes
            ),
            "distributional_shadow_t2_minus_score_aware_t2_v2": _comparison(
                grouped[(mode, "distributional_shadow_t2")],
                grouped[(mode, "score_aware_t2_v2")],
                indexes,
            ),
        }
    wmm_effects = {
        arm: _comparison(grouped[("on", arm)], grouped[("off", arm)], indexes)
        for arm in ARM_IDENTITIES
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "analysis": ANALYSIS_ID,
        "evidence_role": "opened-seed WMM engineering comparison",
        "contract": {
            "path": str(CONTRACT_PATH.relative_to(ROOT)),
            "sha256": CONTRACT_SHA256,
            "status": contract["status"],
        },
        "bootstrap": {
            "replications": BOOTSTRAP_REPLICATIONS,
            "seed": BOOTSTRAP_SEED,
            "paired_unit_count": len(EXPECTED_SEEDS),
            "index_matrix_sha256": _bootstrap_hash(indexes),
        },
        "campaign_checks": {
            "formal_shards": identities,
            "merged_run_count": len(observations),
            "strictly_validated_run_count": len(observations),
            "single_build_identity": True,
            "build_identity": json.loads(next(iter(build_identities))),
            "exact_six_treatment_cells": True,
            "exact_48_paired_seeds": True,
            "paired_obss_realizations_match": True,
            "reserved_confirmation_seeds_used": False,
        },
        "treatments": treatments,
        "within_mode_comparisons": within,
        "wmm_effects": wmm_effects,
        "historical_wmm_off_comparison": _historical_comparison(treatments),
    }
    plot_data = {
        "schema_version": 1,
        "analysis": "scenario15_wmm_plot_data_v1",
        "series": {
            f"{mode}:{arm}": {
                "wmm_mode": mode,
                "arm": arm,
                "label": ARM_LABELS[arm],
                **_plot_series(grouped[(mode, arm)]),
            }
            for mode in WMM_PROFILES
            for arm in ARM_IDENTITIES
        },
    }
    compact_rows = []
    for row in sorted(observations, key=lambda item: (item["seed"], item["wmm_mode"], item["arm"])):
        compact_rows.append({key: row[key] for key in (
            "run_id", "seed", "run", "wmm_mode", "arm", "generated_frame_count",
            "completed_frame_count", "incomplete_frame_count", "deadline_miss_count",
            "all_generated_deadline_miss_rate", "completed_frame_p99_us",
            "sender_airtime_us", "background_throughput_mbps", "action_count",
            "max_deadline_miss_burst",
        )})
    return report, plot_data, compact_rows


def _write_outputs(
    output: Path,
    report: dict[str, Any],
    plot_data: dict[str, Any],
    rows: Sequence[dict[str, Any]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "scenario15_wmm_comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "scenario15_wmm_comparison.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    (output / "plot_data.json").write_text(
        json.dumps(plot_data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output / "run_metrics.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    comparison_rows = []
    for mode, comparisons in report["within_mode_comparisons"].items():
        for name, values in comparisons.items():
            comparison_rows.append({"scope": f"wmm_{mode}", "comparison": name, **{
                f"{metric}_{field}": values[metric][field]
                for metric in (
                    "deadline_miss_rate_delta",
                    "completed_frame_p99_delta_us",
                    "sender_airtime_ratio",
                    "background_throughput_loss",
                )
                for field in ("estimate", "ci95_low", "ci95_high")
            }})
    for arm, values in report["wmm_effects"].items():
        comparison_rows.append({"scope": "wmm_on_minus_off", "comparison": arm, **{
            f"{metric}_{field}": values[metric][field]
            for metric in (
                "deadline_miss_rate_delta",
                "completed_frame_p99_delta_us",
                "sender_airtime_ratio",
                "background_throughput_loss",
            )
            for field in ("estimate", "ci95_low", "ci95_high")
        }})
    with (output / "paired_comparisons.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(comparison_rows[0]))
        writer.writeheader()
        writer.writerows(comparison_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs=2, type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    report, plot_data, rows = analyze(args.roots, args.workers)
    _write_outputs(args.output_directory.resolve(), report, plot_data, rows)
    print(
        f"WROTE {args.output_directory.resolve()} "
        f"runs={report['campaign_checks']['strictly_validated_run_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
