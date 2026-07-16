#!/usr/bin/env python3
"""Aggregate validated runs using runs, not frames, as independent samples."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any

from validate_outputs import validate_run


METRICS = (
    "deadline_miss_ratio", "incomplete_ratio", "latency_p99_us",
    "redundant_byte_ratio", "duplicate_recovery_rate",
    "duplicate_no_benefit_ratio", "background_throughput_mbps",
    "max_deadline_miss_burst", "p95_deadline_miss_burst",
    "joint_copy_deadline_exceedance_rate", "cross_copy_delay_correlation",
)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = quantile * (len(ordered) - 1)
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] if lower == upper else (
        ordered[lower] * (upper - index) + ordered[upper] * (index - lower)
    )


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    mean_left, mean_right = statistics.mean(left), statistics.mean(right)
    numerator = sum((x - mean_left) * (y - mean_right) for x, y in zip(left, right))
    denominator = math.sqrt(
        sum((x - mean_left) ** 2 for x in left) *
        sum((y - mean_right) ** 2 for y in right)
    )
    return numerator / denominator if denominator else None


def _bursts(flags: list[bool]) -> list[int]:
    result, current = [], 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            result.append(current)
            current = 0
    if current:
        result.append(current)
    return result


def run_metrics(run_dir: Path) -> dict[str, Any]:
    validated = validate_run(run_dir)
    config = json.loads((run_dir / "resolved_config.json").read_text())
    summary = json.loads((run_dir / "summary.json").read_text())
    with (run_dir / "frames.csv").open(newline="", encoding="utf-8") as source:
        frames = sorted(csv.DictReader(source), key=lambda row: int(row["generation_time_us"]))
    bursts = _bursts([row["deadline_miss"] == "1" for row in frames])
    copy0, copy1, joint = [], [], 0
    duplicated = 0
    for row in frames:
        if row["duplicated"] != "1":
            continue
        duplicated += 1
        generation = int(row["generation_time_us"])
        deadline = int(row["deadline_us"])
        left = (int(row["copy_0_completion_us"]) - generation
                if row["copy_0_completion_us"] else None)
        right = (int(row["copy_1_completion_us"]) - generation
                 if row["copy_1_completion_us"] else None)
        if left is not None and right is not None:
            copy0.append(left)
            copy1.append(right)
        if deadline and (left is None or left > deadline) and (right is None or right > deadline):
            joint += 1
    return {
        "run_id": validated["run_id"], "run_dir": str(run_dir),
        "seed": config["seed"], "run": config["run"], "topology": config["topology"],
        "policy": config["policy"], "config": config,
        "deadline_miss_ratio": summary["deadline_miss_ratio"],
        "incomplete_ratio": summary["incomplete_ratio"],
        "latency_p99_us": summary["latency_p99_us"],
        "redundant_byte_ratio": summary["redundant_byte_ratio"],
        "duplicate_recovery_rate": (
            summary["duplicate_recovery_rate"] if summary["duplicate_frame_count"] else None
        ),
        "duplicate_no_benefit_ratio": (
            summary["duplicate_no_benefit_ratio"] if summary["duplicate_frame_count"] else None
        ),
        "background_throughput_mbps": summary["background_throughput_mbps"],
        "max_deadline_miss_burst": max(bursts) if bursts else 0,
        "p95_deadline_miss_burst": percentile([float(value) for value in bursts], 0.95) or 0,
        "deadline_miss_bursts": bursts,
        "joint_copy_deadline_exceedance_rate": joint / duplicated if duplicated else None,
        "cross_copy_delay_correlation": correlation(copy0, copy1),
        "redundant_airtime_ratio": None,
    }


def group_key(item: dict[str, Any]) -> str:
    config = copy.deepcopy(item["config"])
    for key in ("run_id", "seed", "run"):
        config.pop(key, None)
    # OBSS coordinates are resolved random outcomes, not nominal experiment
    # parameters. Placement bounds and stream bases remain in the key.
    obss = config.get("background", {}).get("obss")
    if isinstance(obss, dict):
        obss.pop("bsses", None)
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def confidence(values: list[float | int | None]) -> dict[str, Any]:
    observed = [float(value) for value in values if value is not None]
    if not observed:
        return {"n": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    mean = statistics.mean(observed)
    if len(observed) < 2:
        return {"n": 1, "mean": mean, "ci95_low": None, "ci95_high": None}
    t95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
           7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}.get(len(observed), 1.96)
    half = t95 * statistics.stdev(observed) / math.sqrt(len(observed))
    return {"n": len(observed), "mean": mean, "ci95_low": mean - half,
            "ci95_high": mean + half}


def summarize(run_dirs: list[Path]) -> dict[str, Any]:
    runs = [run_metrics(path) for path in run_dirs]
    groups = []
    for key in sorted({group_key(item) for item in runs}):
        members = [item for item in runs if group_key(item) == key]
        group_config = json.loads(key)
        groups.append({
            "topology": members[0]["topology"], "policy": members[0]["policy"],
            "config": group_config, "run_count": len(members),
            "metrics": {name: confidence([item[name] for item in members]) for name in METRICS},
            "deadline_miss_burst_distribution": [
                burst for item in members for burst in item["deadline_miss_bursts"]
            ],
            "redundant_airtime_ratio": {
                "n": 0, "mean": None, "ci95_low": None, "ci95_high": None,
                "unavailable_reason": "sender airtime is not attributed to redundant copies",
            },
        })
    return {"schema_version": 1, "independent_sample_unit": "run", "runs": runs, "groups": groups}


def discover(path: Path) -> list[Path]:
    if (path / "experiment_manifest.json").is_file():
        manifest = json.loads((path / "experiment_manifest.json").read_text())
        return [path / item["directory"] for item in manifest["runs"] if item["status"] == "complete"]
    if (path / "summary.json").is_file():
        return [path]
    return sorted(candidate.parent for candidate in path.glob("*/summary.json"))


def write_outputs(result: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        columns = ["topology", "policy", "run_count"]
        for metric in METRICS:
            columns += [f"{metric}_{suffix}" for suffix in ("n", "mean", "ci95_low", "ci95_high")]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for group in result["groups"]:
            row = {key: group[key] for key in ("topology", "policy", "run_count")}
            for metric in METRICS:
                for suffix, value in group["metrics"][metric].items():
                    row[f"{metric}_{suffix}"] = value
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--json", type=Path, default=Path("aggregate.json"))
    parser.add_argument("--csv", type=Path, default=Path("aggregate.csv"))
    args = parser.parse_args()
    result = summarize(discover(args.input.resolve()))
    write_outputs(result, args.json, args.csv)
    print(f"WROTE {args.json} {args.csv} groups={len(result['groups'])}")


if __name__ == "__main__":
    main()
