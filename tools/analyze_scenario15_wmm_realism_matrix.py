#!/usr/bin/env python3
"""Analyze the paired scenario-15 WMM realism matrix across two shards."""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

from analyze_scenario15_wmm_comparison import (
    AnalysisError,
    _bootstrap_hash,
    _burst_lengths,
    _hf7,
    _plot_series,
    _read_csv,
    _read_json,
    _sha256_file,
)
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
ANALYSIS_ID = "scenario15_wmm_realism_matrix_v1"
RUNTIME_CONTRACT_ID = "scenario15-wmm-realism-matrix-v1"
SCHEMA_VERSION = 1
EXPECTED_SEEDS = tuple(range(1251, 1261))
EXPECTED_RUN = 1
EXPECTED_RUN_COUNT = 120
EXPECTED_PROJECT_COMMIT = "d9867b13b7fac8df9b936e717855017a22e0b5fa"
EXPECTED_IMPLEMENTATION_COMMIT = "29417c40324857a5a6e29fddfdc7ee8c3648da4e"
BOOTSTRAP_REPLICATIONS = 10_000
BOOTSTRAP_SEED = 20260808
CONTRACT_PATH = ROOT / "experiments/model-selection/scenario15-wmm-realism-matrix-v1.json"
CONTRACT_SHA256 = "252fae821e78892f46addddf29f6ab919afce880a48b4f6d9815bdf97fa51d6e"
SHARD_CONFIGS = {
    "scenario15-wmm-realism-matrix-v1-shard0": (
        ROOT / "experiments/configs/scenario15_wmm_realism_matrix_v1_shard0.yaml"
    ),
    "scenario15-wmm-realism-matrix-v1-shard1": (
        ROOT / "experiments/configs/scenario15_wmm_realism_matrix_v1_shard1.yaml"
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
PROFILE_SPECS = {
    "be_be": {
        "label": "Target BE; competitors BE",
        "target": ("off", 0, 0, "AC_BE"),
        "competitor_profile": "be",
        "vi_flow_ordinals": [],
    },
    "af41_vi_be": {
        "label": "Target AF41/VI; competitors BE",
        "target": ("af41", 136, 4, "AC_VI"),
        "competitor_profile": "be",
        "vi_flow_ordinals": [],
    },
    "af41_vi_one_vi_per_channel": {
        "label": "Target AF41/VI; one VI competitor/channel",
        "target": ("af41", 136, 4, "AC_VI"),
        "competitor_profile": "one_vi_per_channel",
        "vi_flow_ordinals": [0, 16],
    },
    "af41_vi_all_vi": {
        "label": "Target AF41/VI; all competitors VI",
        "target": ("af41", 136, 4, "AC_VI"),
        "competitor_profile": "all_vi",
        "vi_flow_ordinals": list(range(32)),
    },
}
METRICS = (
    "all_generated_deadline_miss_rate",
    "completed_frame_p99_us",
    "deadline_censored_p99_us",
    "deadline_censored_mean_us",
    "sender_airtime_us",
    "background_throughput_mbps",
    "measured_secondary_airtime_us",
)


def _bootstrap_indexes() -> tuple[tuple[int, ...], ...]:
    generator = random.Random(BOOTSTRAP_SEED)
    return tuple(
        tuple(generator.randrange(len(EXPECTED_SEEDS)) for _ in EXPECTED_SEEDS)
        for _ in range(BOOTSTRAP_REPLICATIONS)
    )


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
    values: Sequence[float], indexes: Sequence[Sequence[int]]
) -> dict[str, float]:
    if len(values) != len(EXPECTED_SEEDS):
        raise AnalysisError("single-arm bootstrap requires ten runs")
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
        raise AnalysisError("paired bootstrap requires two ten-run samples")
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
        raise AnalysisError("WMM realism runtime contract checksum changed")
    contract = _read_json(CONTRACT_PATH)
    campaign = contract.get("campaign", {})
    if (
        contract.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
        or contract.get("status") != "frozen_before_outcomes"
        or contract.get("implementation_commit") != EXPECTED_IMPLEMENTATION_COMMIT
        or campaign.get("seeds") != list(EXPECTED_SEEDS)
        or campaign.get("simulation_run_count") != EXPECTED_RUN_COUNT
        or campaign.get("reserved_confirmation_seeds_used") is not False
    ):
        raise AnalysisError("WMM realism runtime contract content changed")
    if [item.get("profile_id") for item in contract.get("profiles", [])] != list(
        PROFILE_SPECS
    ):
        raise AnalysisError("WMM realism profile order differs")
    return contract


def _manifest_jobs(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = root.resolve()
    manifest_path = root / "experiment_manifest.json"
    manifest = _read_json(manifest_path)
    experiment = manifest.get("experiment")
    if experiment not in SHARD_CONFIGS:
        raise AnalysisError(f"{manifest_path}: unexpected shard {experiment!r}")
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
        raise AnalysisError(f"{manifest_path}: execution commit differs from launch")
    specs: dict[str, dict[str, Any]] = {}
    for spec in expand_config(document):
        run_id = derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            NS3_UPSTREAM_COMMIT,
            project_commit,
            runtime,
            spec.get("scenario"),
        )
        specs[run_id] = spec
    entries = manifest.get("runs")
    if not isinstance(entries, list) or len(entries) != 60:
        raise AnalysisError(f"{manifest_path}: shard does not contain 60 completed runs")
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
            or canonical_json(entry.get("scenario")) != canonical_json(spec["scenario"])
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
                "scenario": spec["scenario"],
            }
        )
    if seen != set(specs):
        raise AnalysisError(f"{manifest_path}: completed run set differs from matrix")
    return {
        "experiment": experiment,
        "path": str(manifest_path),
        "sha256": _sha256_file(manifest_path),
        "matrix_sha256": manifest["matrix_sha256"],
        "project_commit": project_commit,
        "completed_run_count": len(entries),
    }, jobs


def _observe(job: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=job["run_id"],
            expected_project_commit=job["project_commit"],
            expected_ns3_commit=NS3_UPSTREAM_COMMIT,
            expected_experiment_runtime_contract_id=job.get(
                "experiment_runtime_contract_id"
            ),
            expected_experiment_runtime_contract_sha256=job.get(
                "experiment_runtime_contract_sha256"
            ),
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
    profile = job["scenario"].get("scenario_id")
    specification = PROFILE_SPECS.get(profile)
    if specification is None:
        raise AnalysisError(f"{run_dir}: unexpected profile {profile!r}")
    wifi = config.get("wifi", {})
    mode, tos, tid, access_category = specification["target"]
    if (
        wifi.get("wmm_mode") != mode
        or wifi.get("stream_ip_tos") != tos
        or wifi.get("stream_tid") != tid
        or wifi.get("access_category") != access_category
        or wifi.get("mcs_mode", "fixed") != "fixed"
    ):
        raise AnalysisError(f"{run_dir}: target WMM/MCS profile differs")
    obss = config.get("background", {}).get("obss", {})
    if (
        obss.get("wmm_profile") != specification["competitor_profile"]
        or obss.get("vi_ip_tos") != 136
        or obss.get("vi_tid") != 4
        or obss.get("vi_access_category") != "AC_VI"
        or obss.get("vi_flow_ordinals") != specification["vi_flow_ordinals"]
    ):
        raise AnalysisError(f"{run_dir}: competitor WMM profile differs")
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
        latency = None if incomplete else float(row["union_latency_us"])
        if incomplete and row["union_latency_us"]:
            raise AnalysisError(f"{run_dir}: incomplete frame has latency")
        if latency is not None:
            completed.append(latency)
        if miss != (incomplete or (latency is not None and latency > deadline)):
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
    if len(background_rows) != 32:
        raise AnalysisError(f"{run_dir}: expected 32 OBSS flows")
    duration = float(config["measurement_stop_s"]) - float(config["measurement_start_s"])
    background_bytes = sum(int(row["bytes_received"]) for row in background_rows)
    summary = _read_json(run_dir / "summary.json")
    meter_path = run_dir / "secondary_airtime_summary.json"
    measured_secondary_airtime_us = (
        float(_read_json(meter_path)["tagged_secondary_tx_airtime_us"])
        if meter_path.is_file()
        else 0.0
    )
    bursts = _burst_lengths(miss_flags)
    build_info = _read_json(run_dir / "build_info.json")
    return {
        "run_id": job["run_id"],
        "run_dir": str(run_dir),
        "seed": job["seed"],
        "run": job["run"],
        "profile": profile,
        "arm": arm,
        "config": config,
        "build_identity": {
            key: build_info.get(key)
            for key in (
                "ns3_version",
                "ns3_upstream_commit",
                "project_git_commit",
                "compiler",
                "build_profile",
            )
        },
        "generated_frame_count": len(frames),
        "completed_frame_count": len(completed),
        "incomplete_frame_count": len(frames) - len(completed),
        "deadline_miss_count": sum(miss_flags),
        "all_generated_deadline_miss_rate": sum(miss_flags) / len(frames),
        "completed_frame_p99_us": _hf7(completed, 0.99),
        "deadline_censored_p99_us": _hf7(censored, 0.99),
        "deadline_censored_mean_us": statistics.mean(censored),
        "sender_airtime_us": sum(link_airtime.values()),
        "background_throughput_mbps": background_bytes * 8 / duration / 1_000_000,
        "measured_secondary_airtime_us": measured_secondary_airtime_us,
        "action_count": int(summary.get("duplicate_frame_count", 0)),
        "max_deadline_miss_burst": max(bursts, default=0),
        "deadline_miss_bursts": bursts,
        "completed_latencies_us": completed,
        "deadline_censored_latencies_us": censored,
        "strict_validation": validation,
    }


def _treatment_summary(
    rows: Sequence[dict[str, Any]], indexes: Sequence[Sequence[int]]
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

    def values(rows: Sequence[dict[str, Any]], metric: str) -> list[float]:
        return [row[metric] for row in rows]

    miss_left = values(left, "all_generated_deadline_miss_rate")
    miss_right = values(right, "all_generated_deadline_miss_rate")
    directions = Counter(
        "better" if a < b else "worse" if a > b else "tie"
        for a, b in zip(miss_left, miss_right)
    )
    result = {
        "deadline_miss_rate_delta": _bootstrap_pair(
            miss_left,
            miss_right,
            indexes,
            _mean_delta,
            "mean paired left-minus-right all-generated deadline-miss rate",
        ),
        "completed_frame_p99_delta_us": _bootstrap_pair(
            values(left, "completed_frame_p99_us"),
            values(right, "completed_frame_p99_us"),
            indexes,
            _mean_delta,
            "mean paired left-minus-right completed-frame HF7 P99",
        ),
        "deadline_censored_mean_delta_us": _bootstrap_pair(
            values(left, "deadline_censored_mean_us"),
            values(right, "deadline_censored_mean_us"),
            indexes,
            _mean_delta,
            "mean paired left-minus-right all-generated censored mean latency",
        ),
        "sender_airtime_ratio": _bootstrap_pair(
            values(left, "sender_airtime_us"),
            values(right, "sender_airtime_us"),
            indexes,
            _ratio_of_means,
            "ratio of left and right mean sender PHY airtime",
        ),
        "background_throughput_loss": _bootstrap_pair(
            values(left, "background_throughput_mbps"),
            values(right, "background_throughput_mbps"),
            indexes,
            _background_loss,
            "one minus left/right mean OBSS throughput ratio",
        ),
        "measured_secondary_airtime_delta_us": _bootstrap_pair(
            values(left, "measured_secondary_airtime_us"),
            values(right, "measured_secondary_airtime_us"),
            indexes,
            _mean_delta,
            "mean paired left-minus-right tagged secondary airtime",
        ),
        "deadline_miss_direction_counts": {
            key: directions.get(key, 0) for key in ("better", "tie", "worse")
        },
    }
    right_mean = statistics.mean(miss_right)
    result["relative_deadline_miss_reduction"] = (
        1.0 - statistics.mean(miss_left) / right_mean if right_mean > 0 else None
    )
    return result


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Scenario-15 WMM realism matrix",
        "",
        "This is a ten-seed opened-data screen. The primary outcome is deadline "
        "misses over all generated frames; completed-frame P99 remains secondary.",
        "",
        "| Profile | Approach | Misses | Miss rate | Mean per-run P99 | "
        "Sender airtime | OBSS goodput | Actions |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile, specification in PROFILE_SPECS.items():
        for arm in ARM_IDENTITIES:
            item = report["treatments"][profile][arm]
            lines.append(
                f"| {specification['label']} | {ARM_LABELS[arm]} | "
                f"{item['deadline_miss_count']:,} | "
                f"{item['all_generated_deadline_miss_rate']['estimate'] * 100:.4f}% | "
                f"{item['completed_frame_p99_us']['estimate'] / 1000:.3f} ms | "
                f"{item['sender_airtime_us']['estimate'] / 1_000_000:.3f} s/run | "
                f"{item['background_throughput_mbps']['estimate']:.3f} Mbps | "
                f"{item['action_count']:,} |"
            )
    lines.extend(["", "## Selective approaches compared with STR", ""])
    for profile in PROFILE_SPECS:
        lines.append(f"### {PROFILE_SPECS[profile]['label']}")
        lines.append("")
        for comparison in (
            "score_aware_t2_v2_minus_str_mlo",
            "distributional_shadow_t2_minus_str_mlo",
        ):
            item = report["within_profile_comparisons"][profile][comparison]
            miss = item["deadline_miss_rate_delta"]
            p99 = item["completed_frame_p99_delta_us"]
            airtime = item["sender_airtime_ratio"]
            lines.append(
                f"- `{comparison}`: miss delta {miss['estimate'] * 100:+.4f} pp "
                f"[{miss['ci95_low'] * 100:+.4f}, {miss['ci95_high'] * 100:+.4f}]; "
                f"P99 delta {p99['estimate'] / 1000:+.3f} ms "
                f"[{p99['ci95_low'] / 1000:+.3f}, {p99['ci95_high'] / 1000:+.3f}]; "
                f"airtime ratio {airtime['estimate']:.4f} "
                f"[{airtime['ci95_low']:.4f}, {airtime['ci95_high']:.4f}]."
            )
        lines.append("")
    lines.extend(
        [
            "## Evidence boundary",
            "",
            f"All {report['campaign_checks']['strictly_validated_run_count']} runs passed "
            "strict validation. Seeds 1301 through 1348 were not used. Ten paired seeds "
            "make this directional evidence, not final qualification.",
            "",
        ]
    )
    return "\n".join(lines)


def analyze(
    roots: Sequence[Path], workers: int
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    contract = _verify_contract()
    identities: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for root in roots:
        identity, shard_jobs = _manifest_jobs(root)
        identities.append(identity)
        jobs.extend(shard_jobs)
    if len(identities) != 2 or {item["experiment"] for item in identities} != set(
        SHARD_CONFIGS
    ):
        raise AnalysisError("exactly the two frozen formal shards are required")
    if (
        len(jobs) != EXPECTED_RUN_COUNT
        or len({job["run_id"] for job in jobs}) != EXPECTED_RUN_COUNT
    ):
        raise AnalysisError("merged run set is not the exact 120-run matrix")
    with ProcessPoolExecutor(max_workers=workers) as executor:
        observations = list(executor.map(_observe, jobs))
    build_identities = {canonical_json(row["build_identity"]) for row in observations}
    if len(build_identities) != 1:
        raise AnalysisError("runs contain multiple build identities")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in observations:
        grouped.setdefault((row["profile"], row["arm"]), []).append(row)
    expected_groups = {
        (profile, arm) for profile in PROFILE_SPECS for arm in ARM_IDENTITIES
    }
    if set(grouped) != expected_groups or any(len(rows) != 10 for rows in grouped.values()):
        raise AnalysisError("matrix does not form twelve complete ten-run cells")
    for seed in EXPECTED_SEEDS:
        units = [row for row in observations if row["seed"] == seed]
        if len(units) != 12:
            raise AnalysisError(f"seed {seed} does not contain twelve treatments")
        geometries = {
            canonical_json(row["config"]["background"]["obss"].get("bsses", []))
            for row in units
        }
        if len(geometries) != 1:
            raise AnalysisError(f"seed {seed} OBSS realization differs across treatments")
    indexes = _bootstrap_indexes()
    treatments = {
        profile: {
            arm: _treatment_summary(grouped[(profile, arm)], indexes)
            for arm in ARM_IDENTITIES
        }
        for profile in PROFILE_SPECS
    }
    within_profile = {
        profile: {
            "score_aware_t2_v2_minus_str_mlo": _comparison(
                grouped[(profile, "score_aware_t2_v2")],
                grouped[(profile, "str_mlo")],
                indexes,
            ),
            "distributional_shadow_t2_minus_str_mlo": _comparison(
                grouped[(profile, "distributional_shadow_t2")],
                grouped[(profile, "str_mlo")],
                indexes,
            ),
            "distributional_shadow_t2_minus_score_aware_t2_v2": _comparison(
                grouped[(profile, "distributional_shadow_t2")],
                grouped[(profile, "score_aware_t2_v2")],
                indexes,
            ),
        }
        for profile in PROFILE_SPECS
    }
    profile_effects = {
        profile: {
            arm: _comparison(
                grouped[(profile, arm)], grouped[("be_be", arm)], indexes
            )
            for arm in ARM_IDENTITIES
        }
        for profile in PROFILE_SPECS
        if profile != "be_be"
    }
    competitor_effects = {
        profile: {
            arm: _comparison(
                grouped[(profile, arm)], grouped[("af41_vi_be", arm)], indexes
            )
            for arm in ARM_IDENTITIES
        }
        for profile in (
            "af41_vi_one_vi_per_channel",
            "af41_vi_all_vi",
        )
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "analysis": ANALYSIS_ID,
        "evidence_role": "opened-seed WMM realism mechanism screen",
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
            "exact_twelve_treatment_cells": True,
            "exact_ten_paired_seeds": True,
            "paired_obss_realizations_match": True,
            "reserved_confirmation_seeds_used": False,
        },
        "treatments": treatments,
        "within_profile_comparisons": within_profile,
        "profile_effects_vs_be_be": profile_effects,
        "competitor_effects_with_target_vi": competitor_effects,
    }
    plot_data = {
        "schema_version": 1,
        "analysis": "scenario15_wmm_realism_plot_data_v1",
        "series": {
            f"{profile}:{arm}": {
                "profile": profile,
                "arm": arm,
                "profile_label": PROFILE_SPECS[profile]["label"],
                "arm_label": ARM_LABELS[arm],
                **_plot_series(grouped[(profile, arm)]),
            }
            for profile in PROFILE_SPECS
            for arm in ARM_IDENTITIES
        },
    }
    compact_rows = []
    for row in sorted(
        observations, key=lambda item: (item["seed"], item["profile"], item["arm"])
    ):
        compact_rows.append(
            {
                key: row[key]
                for key in (
                    "run_id",
                    "seed",
                    "run",
                    "profile",
                    "arm",
                    "generated_frame_count",
                    "completed_frame_count",
                    "incomplete_frame_count",
                    "deadline_miss_count",
                    "all_generated_deadline_miss_rate",
                    "completed_frame_p99_us",
                    "deadline_censored_p99_us",
                    "deadline_censored_mean_us",
                    "sender_airtime_us",
                    "background_throughput_mbps",
                    "measured_secondary_airtime_us",
                    "action_count",
                    "max_deadline_miss_burst",
                )
            }
        )
    return report, plot_data, compact_rows


def _write_outputs(
    output: Path,
    report: dict[str, Any],
    plot_data: dict[str, Any],
    rows: Sequence[dict[str, Any]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "scenario15_wmm_realism_matrix.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "scenario15_wmm_realism_matrix.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    (output / "plot_data.json").write_text(
        json.dumps(plot_data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output / "run_metrics.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    comparison_rows: list[dict[str, Any]] = []
    scopes = {
        "within_profile": report["within_profile_comparisons"],
        "profile_vs_be_be": report["profile_effects_vs_be_be"],
        "competitor_with_target_vi": report["competitor_effects_with_target_vi"],
    }
    for scope, groups in scopes.items():
        for group, comparisons in groups.items():
            for name, values in comparisons.items():
                comparison_rows.append(
                    {
                        "scope": scope,
                        "group": group,
                        "comparison": name,
                        **{
                            f"{metric}_{field}": values[metric][field]
                            for metric in (
                                "deadline_miss_rate_delta",
                                "completed_frame_p99_delta_us",
                                "deadline_censored_mean_delta_us",
                                "sender_airtime_ratio",
                                "background_throughput_loss",
                                "measured_secondary_airtime_delta_us",
                            )
                            for field in ("estimate", "ci95_low", "ci95_high")
                        },
                    }
                )
    with (output / "paired_comparisons.csv").open(
        "w", newline="", encoding="utf-8"
    ) as target:
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
