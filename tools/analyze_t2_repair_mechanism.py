#!/usr/bin/env python3
"""Strictly merge, analyze, and plot the frozen T2 mechanism campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from validate_outputs import ValidationError, validate_run


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_ID = "t2-repair-mechanism-analysis-v1"
ARM_ORDER = (
    "str_mlo_nmaxinflights_1",
    "single_5ghz_no_redundancy",
    "full_copy_t0",
    "full_copy_t2",
    "oracle_eventual_missing_repair_t2",
    "ideal_systematic_fec_12p5_t2",
)
ARM_LABELS = {
    "str_mlo_nmaxinflights_1": "STR MLO",
    "single_5ghz_no_redundancy": "5 GHz only",
    "full_copy_t0": "Full copy T0",
    "full_copy_t2": "Full copy T2",
    "oracle_eventual_missing_repair_t2": "Oracle repair T2",
    "ideal_systematic_fec_12p5_t2": "Ideal FEC T2",
}
COLORS = {
    "str_mlo_nmaxinflights_1": "#4c78a8",
    "single_5ghz_no_redundancy": "#9d9da1",
    "full_copy_t0": "#e45756",
    "full_copy_t2": "#f58518",
    "oracle_eventual_missing_repair_t2": "#54a24b",
    "ideal_systematic_fec_12p5_t2": "#b279a2",
}
SCENARIO_LABELS = {
    "legacy-coexistence-qualification-p17": "Legacy p17",
    "compound-shift-qualification-p19": "Compound p19",
    "obss-intensity-qualification-p17": "OBSS p17",
    "obss-intensity-qualification-p19": "OBSS p19",
    "radio-propagation-qualification-p17": "Radio p17",
}
BOOTSTRAP_REPLICATIONS = 10_000
BOOTSTRAP_SEED = 20260807


class MechanismAnalysisError(RuntimeError):
    """Raised when mechanism campaign evidence is incomplete or inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MechanismAnalysisError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MechanismAnalysisError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: JSON root is not an object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as error:
        raise MechanismAnalysisError(f"cannot read {path}: {error}") from error
    _require(bool(rows), f"{path}: CSV is empty")
    return rows


def _flag(value: str, description: str) -> bool:
    _require(value in {"0", "1"}, f"{description}: expected 0 or 1")
    return value == "1"


def _optional_float(value: str, description: str) -> float | None:
    if value == "":
        return None
    try:
        result = float(value)
    except ValueError as error:
        raise MechanismAnalysisError(f"{description}: invalid number") from error
    _require(math.isfinite(result), f"{description}: non-finite number")
    return result


def _quantile(values: Sequence[float], probability: float) -> float:
    _require(bool(values), "cannot calculate a quantile of an empty sample")
    return float(np.quantile(np.asarray(values, dtype=np.float64), probability))


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


def _git_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    _require(len(head) == 40 and not status.strip(),
             "analyzer must run from a clean committed checkout")
    return {"project_commit": head, "worktree_clean": True}


def _descends_from(ancestor: str, descendant: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    _require(result.returncode == 0,
             "analyzer commit is not descended from the simulation commit")


def _background_bytes(run_dir: Path) -> int:
    path = run_dir / "background_flows.csv"
    if not path.exists():
        return 0
    rows = _read_csv(path)
    return sum(int(row["bytes_received"]) for row in rows)


def _snapshot_values(
    run_dir: Path,
) -> dict[str, list[float]]:
    path = run_dir / "mechanism_t2_snapshots.csv"
    if not path.exists():
        return {}
    result = {
        "primary_mac_queue_packets": [],
        "secondary_mac_queue_packets": [],
        "primary_frame_queue_packets": [],
        "secondary_frame_queue_packets": [],
        "primary_ack_deficit_packets": [],
    }
    for row in _read_csv(path):
        primary = row["path_id"] == "1"
        prefix = "primary" if primary else "secondary"
        if row["mac_queue_packets"] != "":
            result[f"{prefix}_mac_queue_packets"].append(
                float(row["mac_queue_packets"])
            )
        if row["frame_packets_currently_queued"] != "":
            result[f"{prefix}_frame_queue_packets"].append(
                float(row["frame_packets_currently_queued"])
            )
        if primary:
            result["primary_ack_deficit_packets"].append(
                float(row["primary_ack_deficit_count"])
            )
    return result


def _action_values(
    run_dir: Path,
    arm_id: str,
    frames_by_id: dict[int, dict[str, str]],
) -> tuple[list[float], int, int, int]:
    if arm_id == "full_copy_t0":
        rows = frames_by_id.values()
        latencies = []
        complete = 0
        for frame in rows:
            start = float(frame["generation_time_us"])
            deadline = float(frame["deadline_us"])
            completion = _optional_float(
                frame["copy_1_completion_us"], "copy_1_completion_us"
            )
            if completion is not None:
                complete += 1
                latencies.append(min(completion - start, deadline))
            else:
                latencies.append(deadline)
        return latencies, len(latencies), complete, sum(
            int(row["packet_count"]) for row in rows
        )
    path = run_dir / "mechanism_t2_actions.csv"
    if not path.exists() or arm_id in {"single_5ghz_no_redundancy"}:
        return [], 0, 0, 0
    latencies: list[float] = []
    launched = 0
    complete = 0
    packets = 0
    for row in _read_csv(path):
        if row["launched"] != "1":
            continue
        frame_id = int(row["frame_id"])
        frame = frames_by_id[frame_id]
        action_time = float(row["action_time_us"])
        available = (
            float(frame["generation_time_us"])
            + float(frame["deadline_us"])
            - action_time
        )
        completion = _optional_float(
            frame["copy_1_completion_us"], "copy_1_completion_us"
        )
        if completion is not None:
            complete += 1
            latencies.append(min(max(0.0, completion - action_time), available))
        else:
            latencies.append(available)
        launched += 1
        packets += int(row["action_packet_count"])
    return latencies, launched, complete, packets


def _validate_and_reduce(job: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    try:
        result = validate_run(
            run_dir,
            expected_run_id=job["run_id"],
            expected_project_commit=job["project_commit"],
            expected_ns3_commit=job["ns3_upstream_commit"],
        )
    except ValidationError as error:
        raise MechanismAnalysisError(f"{run_dir}: strict validation failed: {error}") from error
    _require(result.get("valid") is True, f"{run_dir}: validator did not return valid")
    config = _read_json(run_dir / "resolved_config.json")
    _require(
        config.get("run_id") == job["run_id"]
        and config.get("seed") == job["seed"]
        and config.get("run") == job["run"]
        and config.get("topology") == job["config"]["topology"]
        and config.get("policy") == job["config"]["policy"],
        f"{run_dir}: resolved identity differs from the manifest",
    )
    frames = _read_csv(run_dir / "frames.csv")
    frames.sort(key=lambda row: int(row["frame_id"]))
    frames_by_id = {int(row["frame_id"]): row for row in frames}
    _require(len(frames_by_id) == len(frames), f"{run_dir}: duplicate frame ID")
    misses: list[bool] = []
    censored: list[float] = []
    completed: list[float] = []
    incomplete_count = 0
    late_complete_count = 0
    for row in frames:
        deadline = float(row["deadline_us"])
        miss = _flag(row["deadline_miss"], "deadline_miss")
        incomplete = _flag(row["incomplete"], "incomplete")
        latency = _optional_float(row["union_latency_us"], "union_latency_us")
        _require((latency is None) == incomplete,
                 f"{run_dir}: completion/incomplete evidence differs")
        misses.append(miss)
        incomplete_count += int(incomplete)
        late_complete_count += int(miss and not incomplete)
        if latency is not None:
            completed.append(latency)
            censored.append(min(latency, deadline))
        else:
            censored.append(deadline)
    links = _read_csv(run_dir / "link_intervals.csv")
    airtime_by_link = {
        int(row["link_id"]): float(row["phy_tx_time_us"]) for row in links
    }
    _require(set(airtime_by_link) == {0, 1}, f"{run_dir}: expected two target links")
    action_latency, actions, action_complete, action_packets = _action_values(
        run_dir, job["arm_id"], frames_by_id
    )
    build = _read_json(run_dir / "build_info.json")
    return {
        "family_id": job["scenario"]["family_id"],
        "scenario_id": job["scenario"]["scenario_id"],
        "parameter_sample": job["scenario"]["parameter_sample"],
        "seed": job["seed"],
        "run": job["run"],
        "run_id": job["run_id"],
        "arm_id": job["arm_id"],
        "generated_frames": len(frames),
        "completed_frames": len(completed),
        "incomplete_frames": incomplete_count,
        "late_completed_frames": late_complete_count,
        "deadline_misses": sum(misses),
        "deadline_miss_rate": sum(misses) / len(frames),
        "completed_p99_us": _quantile(completed, 0.99) if completed else None,
        "censored_mean_us": statistics.fmean(censored),
        "censored_latencies_us": censored,
        "completed_latencies_us": completed,
        "miss_flags": misses,
        "miss_bursts": _burst_lengths(misses),
        "airtime_link_0_us": airtime_by_link[0],
        "airtime_link_1_us": airtime_by_link[1],
        "sender_airtime_us": sum(airtime_by_link.values()),
        "background_bytes_received": _background_bytes(run_dir),
        "snapshots": _snapshot_values(run_dir),
        "action_censored_latencies_us": action_latency,
        "actions": actions,
        "completed_actions": action_complete,
        "action_packets": action_packets,
        "build_host": build.get("host"),
    }


def _load_manifests(
    shard_roots: Sequence[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _require(len(shard_roots) == 2, "exactly two campaign shard roots are required")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    source = []
    commits: set[str] = set()
    shards: set[tuple[int, int]] = set()
    for root in shard_roots:
        manifest_path = root / "experiment_manifest.json"
        pair_path = root / "oracle_pair_validation.json"
        manifest = _read_json(manifest_path)
        pair = _read_json(pair_path)
        shard = manifest.get("shard", {})
        shards.add((int(shard.get("index", -1)), int(shard.get("count", -1))))
        _require(
            manifest.get("schema_version") == 2
            and manifest.get("mechanism_contract", {}).get("id")
            == "t2_repair_mechanism_v1"
            and len(manifest.get("runs", [])) == 60,
            f"{root}: incomplete or incompatible campaign manifest",
        )
        _require(
            pair.get("all_primary_packet_outcomes_identical") is True
            and pair.get("all_repair_plans_match_baseline_eventual_missing_sets") is True
            and pair.get("pair_count") == 10,
            f"{root}: oracle pair closure failed",
        )
        commits.add(manifest["project_commit"])
        for item in manifest["runs"]:
            run_id = item["run_id"]
            _require(run_id not in seen, f"duplicate run ID across shards: {run_id}")
            seen.add(run_id)
            jobs.append({
                **item,
                "run_dir": str(root / item["directory"]),
                "project_commit": manifest["project_commit"],
                "ns3_upstream_commit": manifest["ns3_upstream_commit"],
            })
        source.append({
            "root": str(root),
            "manifest": {
                "path": str(manifest_path),
                "bytes": manifest_path.stat().st_size,
                "sha256": _sha256(manifest_path),
            },
            "oracle_pair_validation": {
                "path": str(pair_path),
                "bytes": pair_path.stat().st_size,
                "sha256": _sha256(pair_path),
            },
        })
    _require(shards == {(0, 2), (1, 2)}, "campaign shard identities differ")
    _require(len(commits) == 1 and len(jobs) == 120,
             "campaign commit or run count differs")
    counts = {arm: 0 for arm in ARM_ORDER}
    grid: dict[tuple[str, int, int], set[str]] = {}
    for job in jobs:
        arm = job.get("arm_id")
        _require(arm in counts, f"unknown campaign arm: {arm}")
        counts[arm] += 1
        key = (job["scenario"]["scenario_id"], job["seed"], job["run"])
        grid.setdefault(key, set()).add(arm)
    _require(all(count == 20 for count in counts.values()), "arm counts are unbalanced")
    _require(len(grid) == 20 and all(arms == set(ARM_ORDER) for arms in grid.values()),
             "paired scenario/seed grid is incomplete")
    return jobs, {
        "simulation_project_commit": next(iter(commits)),
        "shards": source,
        "run_count": len(jobs),
        "paired_unit_count": len(grid),
    }


def _collect(jobs: Sequence[dict[str, Any]], workers: int) -> list[dict[str, Any]]:
    _require(workers > 0, "validation workers must be positive")
    observations: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_validate_and_reduce, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                observations.append(future.result())
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                if isinstance(error, MechanismAnalysisError):
                    raise error
                raise MechanismAnalysisError(
                    f"validation worker failed for {job['run_id']}: {error}"
                ) from error
    observations.sort(key=lambda row: row["run_id"])
    return observations


def _summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    generated = sum(row["generated_frames"] for row in rows)
    completed = sum(row["completed_frames"] for row in rows)
    incomplete = sum(row["incomplete_frames"] for row in rows)
    late = sum(row["late_completed_frames"] for row in rows)
    misses = sum(row["deadline_misses"] for row in rows)
    censored = [value for row in rows for value in row["censored_latencies_us"]]
    completed_latency = [
        value for row in rows for value in row["completed_latencies_us"]
    ]
    bursts = [value for row in rows for value in row["miss_bursts"]]
    action_latency = [
        value for row in rows for value in row["action_censored_latencies_us"]
    ]
    snapshot_values = {
        field: [
            value for row in rows for value in row["snapshots"].get(field, [])
        ]
        for field in (
            "primary_mac_queue_packets",
            "secondary_mac_queue_packets",
            "primary_frame_queue_packets",
            "secondary_frame_queue_packets",
            "primary_ack_deficit_packets",
        )
    }
    run_p99 = [row["completed_p99_us"] for row in rows if row["completed_p99_us"]]
    actions = sum(row["actions"] for row in rows)
    action_complete = sum(row["completed_actions"] for row in rows)
    summary = {
        "run_count": len(rows),
        "generated_frames": generated,
        "completed_frames": completed,
        "incomplete_frames": incomplete,
        "late_completed_frames": late,
        "deadline_misses": misses,
        "all_generated_deadline_miss_rate": misses / generated,
        "all_generated_censored_mean_us": statistics.fmean(censored),
        "all_generated_censored_p50_us": _quantile(censored, 0.50),
        "all_generated_censored_p90_us": _quantile(censored, 0.90),
        "all_generated_censored_p99_us": _quantile(censored, 0.99),
        "completed_frame_p99_descriptive_us": (
            _quantile(completed_latency, 0.99) if completed_latency else None
        ),
        "mean_per_run_completed_p99_descriptive_us": (
            statistics.fmean(run_p99) if run_p99 else None
        ),
        "sender_airtime_mean_us": statistics.fmean(
            row["sender_airtime_us"] for row in rows
        ),
        "link_0_airtime_mean_us": statistics.fmean(
            row["airtime_link_0_us"] for row in rows
        ),
        "link_1_airtime_mean_us": statistics.fmean(
            row["airtime_link_1_us"] for row in rows
        ),
        "background_bytes_received": sum(row["background_bytes_received"] for row in rows),
        "miss_burst_count": len(bursts),
        "miss_burst_mean_frames": statistics.fmean(bursts) if bursts else 0.0,
        "miss_burst_p95_frames": _quantile(bursts, 0.95) if bursts else 0.0,
        "miss_burst_max_frames": max(bursts, default=0),
        "actions": actions,
        "action_packets": sum(row["action_packets"] for row in rows),
        "action_completion_rate": action_complete / actions if actions else None,
        "action_censored_mean_us": (
            statistics.fmean(action_latency) if action_latency else None
        ),
        "action_censored_p95_us": (
            _quantile(action_latency, 0.95) if action_latency else None
        ),
    }
    for field, values in snapshot_values.items():
        summary[f"{field}_mean"] = statistics.fmean(values) if values else None
        summary[f"{field}_p95"] = _quantile(values, 0.95) if values else None
    return summary


def _paired_grid(
    observations: Sequence[dict[str, Any]],
) -> dict[tuple[str, int, int], dict[str, dict[str, Any]]]:
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]] = {}
    for row in observations:
        key = (row["scenario_id"], row["seed"], row["run"])
        target = grid.setdefault(key, {})
        _require(row["arm_id"] not in target, "duplicate arm within a paired unit")
        target[row["arm_id"]] = row
    _require(len(grid) == 20 and all(set(unit) == set(ARM_ORDER) for unit in grid.values()),
             "analyzed paired grid is incomplete")
    return grid


def _bootstrap(
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    scenarios = sorted({key[0] for key in grid})
    units = {scenario: sorted(key for key in grid if key[0] == scenario)
             for scenario in scenarios}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    samples = {
        arm: {"miss_rate": [], "censored_mean_us": [], "airtime_us": []}
        for arm in ARM_ORDER
    }
    comparisons = {
        arm: {"miss_delta": [], "relative_miss_reduction": [],
              "airtime_ratio": [], "censored_mean_delta_us": []}
        for arm in ARM_ORDER if arm != "str_mlo_nmaxinflights_1"
    }
    joint_success = 0
    for _ in range(BOOTSTRAP_REPLICATIONS):
        selected = [
            units[scenario][index]
            for scenario in scenarios
            for index in rng.integers(0, len(units[scenario]), len(units[scenario]))
        ]
        draw: dict[str, dict[str, float]] = {}
        for arm in ARM_ORDER:
            members = [grid[key][arm] for key in selected]
            generated = sum(row["generated_frames"] for row in members)
            misses = sum(row["deadline_misses"] for row in members)
            censored_sum = sum(
                sum(row["censored_latencies_us"]) for row in members
            )
            draw[arm] = {
                "miss_rate": misses / generated,
                "censored_mean_us": censored_sum / generated,
                "airtime_us": sum(row["sender_airtime_us"] for row in members),
            }
            for metric in samples[arm]:
                samples[arm][metric].append(draw[arm][metric])
        baseline = draw["str_mlo_nmaxinflights_1"]
        for arm, target in comparisons.items():
            candidate = draw[arm]
            target["miss_delta"].append(candidate["miss_rate"] - baseline["miss_rate"])
            target["relative_miss_reduction"].append(
                1.0 - candidate["miss_rate"] / baseline["miss_rate"]
            )
            target["airtime_ratio"].append(
                candidate["airtime_us"] / baseline["airtime_us"]
            )
            target["censored_mean_delta_us"].append(
                candidate["censored_mean_us"] - baseline["censored_mean_us"]
            )
        oracle = comparisons["oracle_eventual_missing_repair_t2"]
        joint_success += int(
            oracle["miss_delta"][-1] < 0 and oracle["airtime_ratio"][-1] <= 1
        )

    def interval(values: Sequence[float], estimate: float) -> dict[str, float]:
        return {
            "estimate": estimate,
            "ci95_low": _quantile(values, 0.025),
            "ci95_high": _quantile(values, 0.975),
        }

    aggregate = {
        arm: _summarize([row for unit in grid.values() for key, row in unit.items()
                         if key == arm])
        for arm in ARM_ORDER
    }
    treatments = {}
    for arm in ARM_ORDER:
        summary = aggregate[arm]
        treatments[arm] = {
            "all_generated_deadline_miss_rate": interval(
                samples[arm]["miss_rate"], summary["all_generated_deadline_miss_rate"]
            ),
            "all_generated_censored_mean_us": interval(
                samples[arm]["censored_mean_us"],
                summary["all_generated_censored_mean_us"],
            ),
            "sender_airtime_mean_us": interval(
                [value / 20 for value in samples[arm]["airtime_us"]],
                summary["sender_airtime_mean_us"],
            ),
        }
    baseline = aggregate["str_mlo_nmaxinflights_1"]
    contrast_report = {}
    for arm, target in comparisons.items():
        candidate = aggregate[arm]
        point = {
            "miss_delta": candidate["all_generated_deadline_miss_rate"]
            - baseline["all_generated_deadline_miss_rate"],
            "relative_miss_reduction": 1.0
            - candidate["all_generated_deadline_miss_rate"]
            / baseline["all_generated_deadline_miss_rate"],
            "airtime_ratio": candidate["sender_airtime_mean_us"]
            / baseline["sender_airtime_mean_us"],
            "censored_mean_delta_us": candidate["all_generated_censored_mean_us"]
            - baseline["all_generated_censored_mean_us"],
        }
        contrast_report[arm] = {
            metric: interval(target[metric], point[metric]) for metric in point
        }
    return {
        "method": "paired stratified bootstrap; resample four units within each scenario",
        "replications": BOOTSTRAP_REPLICATIONS,
        "random_seed": BOOTSTRAP_SEED,
        "treatments": treatments,
        "versus_str": contrast_report,
        "oracle_joint_point_success_bootstrap_probability": (
            joint_success / BOOTSTRAP_REPLICATIONS
        ),
    }


def _oracle_decomposition(
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    primary_misses = 0
    oracle_misses = 0
    primary_misses_rescued = 0
    actions = 0
    action_packets = 0
    for unit in grid.values():
        primary = unit["single_5ghz_no_redundancy"]
        oracle = unit["oracle_eventual_missing_repair_t2"]
        _require(len(primary["miss_flags"]) == len(oracle["miss_flags"]),
                 "oracle/primary frame count differs")
        for primary_miss, oracle_miss in zip(
            primary["miss_flags"], oracle["miss_flags"]
        ):
            primary_misses += int(primary_miss)
            oracle_misses += int(oracle_miss)
            primary_misses_rescued += int(primary_miss and not oracle_miss)
        actions += oracle["actions"]
        action_packets += oracle["action_packets"]
    return {
        "primary_only_deadline_misses": primary_misses,
        "oracle_deadline_misses": oracle_misses,
        "primary_deadline_misses_rescued": primary_misses_rescued,
        "rescue_fraction_of_primary_misses": (
            primary_misses_rescued / primary_misses if primary_misses else None
        ),
        "oracle_actions": actions,
        "oracle_repair_packets": action_packets,
    }


def _flat_summary_rows(
    observations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    fields = (
        "family_id", "scenario_id", "parameter_sample", "seed", "run", "run_id",
        "arm_id", "generated_frames", "completed_frames", "incomplete_frames",
        "late_completed_frames", "deadline_misses", "deadline_miss_rate",
        "completed_p99_us", "censored_mean_us", "airtime_link_0_us",
        "airtime_link_1_us", "sender_airtime_us", "background_bytes_received",
        "actions", "completed_actions", "action_packets", "build_host",
    )
    return [{field: row[field] for field in fields} for row in observations]


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _require(bool(rows), f"refusing to write empty table {path.name}")
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _plot_modules() -> tuple[Any, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return matplotlib, plt


def _finish(figure: Any, output: Path, name: str) -> list[Path]:
    figure.tight_layout()
    paths = [output / f"{name}.png", output / f"{name}.pdf"]
    figure.savefig(paths[0], dpi=180, bbox_inches="tight")
    figure.savefig(paths[1], bbox_inches="tight")
    return paths


def _ecdf(values: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    ordered = np.sort(np.asarray(values, dtype=np.float64))
    return ordered, np.arange(1, len(ordered) + 1) / len(ordered)


def _render_plots(
    observations: Sequence[dict[str, Any]],
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]],
    output: Path,
) -> list[Path]:
    _, plt = _plot_modules()
    artifacts: list[Path] = []
    scenarios = list(SCENARIO_LABELS)
    width = 0.13

    figure, axis = plt.subplots(figsize=(12, 5.5))
    x = np.arange(len(scenarios))
    for index, arm in enumerate(ARM_ORDER):
        values = [
            100 * _summarize([
                row for row in observations
                if row["scenario_id"] == scenario and row["arm_id"] == arm
            ])["all_generated_deadline_miss_rate"]
            for scenario in scenarios
        ]
        axis.bar(x + (index - 2.5) * width, values, width,
                 label=ARM_LABELS[arm], color=COLORS[arm])
    axis.set_xticks(x, [SCENARIO_LABELS[value] for value in scenarios])
    axis.set_ylabel("All-generated deadline misses (%)")
    axis.set_title("Reliability by representative scenario")
    axis.legend(ncol=3, fontsize=8)
    axis.grid(axis="y", alpha=0.25)
    artifacts.extend(_finish(figure, output, "deadline_miss_rate_by_scenario"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.2, 5.4))
    for arm in ARM_ORDER:
        values = [value / 1000 for row in observations if row["arm_id"] == arm
                  for value in row["censored_latencies_us"]]
        xx, yy = _ecdf(values)
        axis.step(xx, yy, where="post", label=ARM_LABELS[arm], color=COLORS[arm])
    axis.set_xlim(left=0)
    axis.set_ylim(0, 1.002)
    axis.set_xlabel("Deadline-censored frame latency (ms)")
    axis.set_ylabel("CDF over all generated frames")
    axis.set_title("All-generated latency CDF (misses censored at deadline)")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.25)
    artifacts.extend(_finish(figure, output, "all_generated_censored_latency_cdf"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.2, 5.4))
    bins = np.linspace(0, 33.333, 90)
    for arm in ARM_ORDER:
        values = [min(value / 1000, bins[-1]) for row in observations
                  if row["arm_id"] == arm for value in row["censored_latencies_us"]]
        axis.hist(values, bins=bins, density=True, histtype="step", linewidth=1.5,
                  label=ARM_LABELS[arm], color=COLORS[arm])
    axis.set_xlabel("Deadline-censored frame latency (ms)")
    axis.set_ylabel("Density")
    axis.set_title("All-generated latency PDF (deadline spike retained)")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.2)
    artifacts.extend(_finish(figure, output, "all_generated_censored_latency_pdf"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.5, 5.4))
    for scenario in scenarios:
        members = [unit for key, unit in grid.items() if key[0] == scenario]
        oracle = _summarize([unit["oracle_eventual_missing_repair_t2"] for unit in members])
        baseline = _summarize([unit["str_mlo_nmaxinflights_1"] for unit in members])
        axis.scatter(
            oracle["sender_airtime_mean_us"] / baseline["sender_airtime_mean_us"],
            100 * (oracle["all_generated_deadline_miss_rate"]
                   - baseline["all_generated_deadline_miss_rate"]),
            s=70,
            label=SCENARIO_LABELS[scenario],
        )
    oracle = _summarize([row for row in observations
                         if row["arm_id"] == "oracle_eventual_missing_repair_t2"])
    baseline = _summarize([row for row in observations
                           if row["arm_id"] == "str_mlo_nmaxinflights_1"])
    axis.scatter(
        oracle["sender_airtime_mean_us"] / baseline["sender_airtime_mean_us"],
        100 * (oracle["all_generated_deadline_miss_rate"]
               - baseline["all_generated_deadline_miss_rate"]),
        marker="*", s=220, color="black", label="Overall",
    )
    axis.axvline(1, color="black", linestyle="--", linewidth=1)
    axis.axhline(0, color="black", linestyle="--", linewidth=1)
    axis.set_xlabel("Oracle / STR measured sender-airtime ratio")
    axis.set_ylabel("Oracle - STR deadline misses (percentage points)")
    axis.set_title("Decisive packet-repair mechanism frontier")
    axis.legend(fontsize=8, ncol=2)
    axis.grid(alpha=0.25)
    artifacts.extend(_finish(figure, output, "oracle_equal_airtime_frontier"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(10, 5.3))
    link0 = [statistics.fmean(row["airtime_link_0_us"] for row in observations
                             if row["arm_id"] == arm) / 1000 for arm in ARM_ORDER]
    link1 = [statistics.fmean(row["airtime_link_1_us"] for row in observations
                             if row["arm_id"] == arm) / 1000 for arm in ARM_ORDER]
    labels = [ARM_LABELS[arm] for arm in ARM_ORDER]
    axis.bar(labels, link1, label="5 GHz / link 1", color="#4c78a8")
    axis.bar(labels, link0, bottom=link1, label="2.4 GHz / link 0", color="#f2cf5b")
    axis.set_ylabel("Mean target-sender PHY TX airtime (ms/run)")
    axis.set_title("Measured sender airtime by physical link")
    axis.tick_params(axis="x", rotation=22)
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    artifacts.extend(_finish(figure, output, "sender_airtime_by_link"))
    plt.close(figure)

    dual_arms = ARM_ORDER[1:]
    figure, axes = plt.subplots(1, 3, figsize=(13, 4.8))
    for axis, field, title in (
        (axes[0], "primary_mac_queue_packets", "Primary MAC queue at T2"),
        (axes[1], "secondary_mac_queue_packets", "Secondary MAC queue at T2"),
        (axes[2], "primary_ack_deficit_packets", "Primary ACK deficit at T2"),
    ):
        values = [
            [item for row in observations if row["arm_id"] == arm
             for item in row["snapshots"].get(field, [])]
            for arm in dual_arms
        ]
        axis.boxplot(values, labels=[ARM_LABELS[arm] for arm in dual_arms], showfliers=False)
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=28, labelsize=7)
        axis.set_ylabel("Packets")
        axis.grid(axis="y", alpha=0.2)
    artifacts.extend(_finish(figure, output, "t2_queue_and_ack_state"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.2, 5.2))
    for arm in ("full_copy_t0", "full_copy_t2",
                "oracle_eventual_missing_repair_t2", "ideal_systematic_fec_12p5_t2"):
        values = [value / 1000 for row in observations if row["arm_id"] == arm
                  for value in row["action_censored_latencies_us"]]
        if values:
            xx, yy = _ecdf(values)
            axis.step(xx, yy, where="post", label=ARM_LABELS[arm], color=COLORS[arm])
    axis.set_xlabel("Action-to-copy/repair completion, deadline-censored (ms)")
    axis.set_ylabel("CDF over launched actions")
    axis.set_ylim(0, 1.002)
    axis.set_title("Secondary action completion")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.25)
    artifacts.extend(_finish(figure, output, "action_completion_cdf"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.2, 5.2))
    for arm in ARM_ORDER:
        values = [value for row in observations if row["arm_id"] == arm
                  for value in row["miss_bursts"]]
        if not values:
            values = [0]
        xx, yy = _ecdf(values)
        axis.step(xx, yy, where="post", label=ARM_LABELS[arm], color=COLORS[arm])
    axis.set_xlabel("Consecutive deadline misses (frames)")
    axis.set_ylabel("CDF over miss bursts")
    axis.set_title("Deadline-miss burst length")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.25)
    artifacts.extend(_finish(figure, output, "deadline_miss_burst_cdf"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(10, 5.2))
    summaries = [_summarize([row for row in observations if row["arm_id"] == arm])
                 for arm in ARM_ORDER]
    total = np.asarray([row["generated_frames"] for row in summaries], dtype=float)
    on_time = np.asarray([
        row["generated_frames"] - row["deadline_misses"] for row in summaries
    ]) / total
    late = np.asarray([row["late_completed_frames"] for row in summaries]) / total
    incomplete = np.asarray([row["incomplete_frames"] for row in summaries]) / total
    axis.bar(labels, 100 * on_time, label="On time", color="#54a24b")
    axis.bar(labels, 100 * late, bottom=100 * on_time,
             label="Late complete", color="#f2cf5b")
    axis.bar(labels, 100 * incomplete, bottom=100 * (on_time + late),
             label="Incomplete", color="#e45756")
    axis.set_ylabel("All generated frames (%)")
    axis.set_title("Deadline outcome composition")
    axis.tick_params(axis="x", rotation=22)
    axis.legend()
    artifacts.extend(_finish(figure, output, "deadline_outcome_composition"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(10, 5.2))
    family_colors = {name: color for name, color in zip(
        SCENARIO_LABELS, ("#4c78a8", "#f58518", "#54a24b", "#e45756", "#b279a2")
    )}
    for scenario in scenarios:
        points = []
        for key, unit in sorted(grid.items()):
            if key[0] != scenario:
                continue
            points.append(100 * (
                unit["oracle_eventual_missing_repair_t2"]["deadline_miss_rate"]
                - unit["str_mlo_nmaxinflights_1"]["deadline_miss_rate"]
            ))
        axis.scatter([SCENARIO_LABELS[scenario]] * len(points), points,
                     color=family_colors[scenario], s=45)
    axis.axhline(0, color="black", linestyle="--", linewidth=1)
    axis.set_ylabel("Oracle - STR misses (percentage points per unit)")
    axis.set_title("Paired unit reliability effects")
    axis.tick_params(axis="x", rotation=20)
    axis.grid(axis="y", alpha=0.25)
    artifacts.extend(_finish(figure, output, "paired_oracle_miss_delta"))
    plt.close(figure)
    return artifacts


def _report_markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    decisive = report["decisive_test"]
    lines = [
        "# T2 packet-repair mechanism experiment",
        "",
        "Primary outcomes include every generated frame. Completed-frame P99 is",
        "reported only as a descriptive, survivor-conditioned diagnostic.",
        "",
        "## Aggregate result",
        "",
        "| Arm | Misses | Miss rate | Censored mean | Sender airtime |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARM_ORDER:
        row = aggregate[arm]
        lines.append(
            f"| {ARM_LABELS[arm]} | {row['deadline_misses']:,} | "
            f"{100 * row['all_generated_deadline_miss_rate']:.4f}% | "
            f"{row['all_generated_censored_mean_us'] / 1000:.3f} ms | "
            f"{row['sender_airtime_mean_us'] / 1000:.2f} ms/run |"
        )
    lines += [
        "",
        "## Decisive oracle question",
        "",
        f"Point decision: **{'PASS' if decisive['point_success'] else 'FAIL'}**.",
        "",
        f"- Oracle minus STR miss rate: "
        f"{100 * decisive['miss_delta']['estimate']:+.4f} percentage points "
        f"(95% CI {100 * decisive['miss_delta']['ci95_low']:+.4f} to "
        f"{100 * decisive['miss_delta']['ci95_high']:+.4f}).",
        f"- Oracle / STR sender-airtime ratio: "
        f"{decisive['airtime_ratio']['estimate']:.4f} "
        f"(95% CI {decisive['airtime_ratio']['ci95_low']:.4f} to "
        f"{decisive['airtime_ratio']['ci95_high']:.4f}).",
        f"- Bootstrap probability of satisfying both point inequalities: "
        f"{100 * decisive['joint_success_bootstrap_probability']:.2f}%.",
        "",
        decisive["interpretation"],
        "",
        "## Oracle repair decomposition",
        "",
    ]
    decomposition = report["oracle_decomposition"]
    for key, value in decomposition.items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    lines += [
        "",
        "## Next boundary",
        "",
        report["next_boundary"],
        "",
    ]
    return "\n".join(lines)


def _raw_tree_rows(shard_roots: Sequence[Path]) -> list[dict[str, Any]]:
    rows = []
    for shard_index, root in enumerate(shard_roots):
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            rows.append({
                "shard_index": shard_index,
                "relative_path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return rows


def analyze(shard_roots: Sequence[Path], output: Path, workers: int) -> dict[str, Any]:
    identity = _git_identity()
    jobs, source = _load_manifests(shard_roots)
    _descends_from(source["simulation_project_commit"], identity["project_commit"])
    observations = _collect(jobs, workers)
    grid = _paired_grid(observations)
    aggregate = {
        arm: _summarize([row for row in observations if row["arm_id"] == arm])
        for arm in ARM_ORDER
    }
    scenarios = {
        scenario: {
            arm: _summarize([
                row for row in observations
                if row["scenario_id"] == scenario and row["arm_id"] == arm
            ])
            for arm in ARM_ORDER
        }
        for scenario in SCENARIO_LABELS
    }
    bootstrap = _bootstrap(grid)
    oracle_comparison = bootstrap["versus_str"][
        "oracle_eventual_missing_repair_t2"
    ]
    point_success = (
        oracle_comparison["miss_delta"]["estimate"] < 0
        and oracle_comparison["airtime_ratio"]["estimate"] <= 1
    )
    if point_success:
        interpretation = (
            "The privileged packet-level action forms a lower-left point than STR. "
            "This supports redesigning redundancy around partial or coded repair, "
            "but it does not make the hindsight oracle implementable."
        )
        next_boundary = (
            "Stop here for review. If continued, redesign the repair action first; "
            "only then train a causal model to choose among repair levels."
        )
    else:
        interpretation = (
            "The privileged packet-level action does not beat STR at equal measured "
            "sender airtime. A larger predictor cannot repair this action-space limit."
        )
        next_boundary = (
            "Stop here for review. If continued, reconsider the multipath action "
            "architecture rather than training a larger predictor."
        )
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "analyzer_identity": identity,
        "source_closure": source,
        "estimands": {
            "primary": "deadline misses over every generated frame",
            "stable_latency": (
                "min(union completion latency, frame deadline) over every generated frame"
            ),
            "completed_p99": "descriptive only; survivor-conditioned",
        },
        "aggregate": aggregate,
        "scenarios": scenarios,
        "bootstrap": bootstrap,
        "oracle_decomposition": _oracle_decomposition(grid),
        "decisive_test": {
            "point_success": point_success,
            "miss_delta": oracle_comparison["miss_delta"],
            "airtime_ratio": oracle_comparison["airtime_ratio"],
            "joint_success_bootstrap_probability": bootstrap[
                "oracle_joint_point_success_bootstrap_probability"
            ],
            "interpretation": interpretation,
        },
        "next_boundary": next_boundary,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    _require(not output.exists(), f"output already exists: {output}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (temporary / "mechanism_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "REPORT.md").write_text(
            _report_markdown(report), encoding="utf-8"
        )
        run_rows = _flat_summary_rows(observations)
        _write_csv(temporary / "run_metrics.csv", run_rows)
        aggregate_rows = [{"arm_id": arm, **aggregate[arm]} for arm in ARM_ORDER]
        _write_csv(temporary / "aggregate_metrics.csv", aggregate_rows)
        scenario_rows = [
            {"scenario_id": scenario, "arm_id": arm, **scenarios[scenario][arm]}
            for scenario in SCENARIO_LABELS for arm in ARM_ORDER
        ]
        _write_csv(temporary / "scenario_metrics.csv", scenario_rows)
        _write_csv(temporary / "raw_run_tree_manifest.csv", _raw_tree_rows(shard_roots))
        plot_dir = temporary / "plots"
        plot_dir.mkdir()
        _render_plots(observations, grid, plot_dir)
        artifacts = {}
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            relative = str(path.relative_to(temporary))
            artifacts[relative] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        manifest = {
            "schema_version": 1,
            "analysis": ANALYSIS_ID,
            "analyzer_identity": identity,
            "source_closure": source,
            "counts": {
                "strictly_validated_runs": len(observations),
                "paired_units": len(grid),
                "figures": len(list(plot_dir.glob("*.png"))),
                "raw_files": len(_raw_tree_rows(shard_roots)),
            },
            "artifacts": artifacts,
        }
        (temporary / "analysis_artifact_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
    except Exception:
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard_roots", nargs=2, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    report = analyze(
        [path.resolve() for path in args.shard_roots],
        args.output.resolve(),
        args.workers,
    )
    decisive = report["decisive_test"]
    print(
        f"DECISIVE {'PASS' if decisive['point_success'] else 'FAIL'} "
        f"miss_delta={decisive['miss_delta']['estimate']:.8f} "
        f"airtime_ratio={decisive['airtime_ratio']['estimate']:.6f}"
    )
    print(f"REPORT {args.output.resolve() / 'REPORT.md'}")


if __name__ == "__main__":
    main()
