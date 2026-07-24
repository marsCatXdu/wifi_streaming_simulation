#!/usr/bin/env python3
"""Build the Increment-2 load-pilot review table."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from summarize_runs import discover
from validate_outputs import validate_run


def _duty(on_mean_ms: float, off_mean_ms: float) -> float:
    return on_mean_ms / (on_mean_ms + off_mean_ms)


def _target_band(miss_rate: float) -> str:
    if 0.01 <= miss_rate <= 0.03:
        return "low"
    if 0.05 <= miss_rate <= 0.10:
        return "medium"
    if 0.15 <= miss_rate <= 0.25:
        return "high"
    return "outside_target_bands"


def _load_key(config: dict[str, Any]) -> tuple[Any, ...]:
    policy = config["policy"]
    if policy not in {"fixed_link_0", "fixed_link_1"}:
        raise ValueError(f"pilot has non-fixed policy: {policy}")
    link = int(policy.rsplit("_", 1)[1])
    background = config["background"]
    if background["profile"] == "none":
        return link, "none", 0.0, 0.0, 0.0, 0.0
    if background["profile"] != "legacy_mixed8":
        raise ValueError(f"pilot has unexpected background profile: {background['profile']}")
    correlation = background["correlation"]
    mode = correlation["mode"]
    common_duty = _duty(
        float(correlation["common_on_mean_ms"]),
        float(correlation["common_off_mean_ms"]),
    )
    local_duty = _duty(
        float(correlation["local_on_mean_ms"]),
        float(correlation["local_off_mean_ms"]),
    )
    if mode == "common_bursts":
        effective_duty = common_duty
    elif mode == "independent":
        effective_duty = local_duty
    elif mode == "mixed_common_and_independent":
        effective_duty = 1.0 - (1.0 - common_duty) * (1.0 - local_duty)
    else:
        raise ValueError(f"pilot has unsupported correlation mode: {mode}")
    return (
        link,
        mode,
        float(background["rate_mbps_per_station"]),
        common_duty,
        local_duty,
        effective_duty,
    )


def _candidate_id(key: tuple[Any, ...]) -> str:
    link, mode, rate, _, _, effective_duty = key
    if mode == "none":
        return f"baseline-link{link}"
    mode_label = {
        "independent": "independent",
        "common_bursts": "common",
        "mixed_common_and_independent": "mixed",
    }[mode]
    return f"{mode_label}-r{rate:g}-d{100 * effective_duty:.1f}-link{link}"


def summarize(roots: list[Path]) -> dict[str, Any]:
    run_dirs = [run_dir for root in roots for run_dir in discover(root)]
    if not run_dirs:
        raise ValueError("no complete pilot runs found")
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    project_commits: set[str] = set()
    upstream_commits: set[str] = set()
    for run_dir in run_dirs:
        validate_run(run_dir)
        config = json.loads((run_dir / "resolved_config.json").read_text(encoding="utf-8"))
        prediction = config.get("predictionTelemetry")
        if prediction is not None and prediction.get("enabled", False):
            raise ValueError(f"pilot telemetry is enabled: {run_dir}")
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        build = json.loads((run_dir / "build_info.json").read_text(encoding="utf-8"))
        project_commits.add(build["project_git_commit"])
        upstream_commits.add(build["ns3_upstream_commit"])
        grouped[_load_key(config)].append(
            {
                "seed": int(config["seed"]),
                "run": int(config["run"]),
                "frame_count": int(summary["frame_count"]),
                "miss_count": int(summary["deadline_miss_count"]),
                "run_id": config["run_id"],
            }
        )

    rows: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda value: (value[0], value[1], value[2], value[5])):
        members = grouped[key]
        per_seed_counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for member in members:
            per_seed_counts[member["seed"]][0] += member["frame_count"]
            per_seed_counts[member["seed"]][1] += member["miss_count"]
        seed_rates = {
            seed: misses / frames
            for seed, (frames, misses) in sorted(per_seed_counts.items())
        }
        rates = list(seed_rates.values())
        frame_count = sum(member["frame_count"] for member in members)
        miss_count = sum(member["miss_count"] for member in members)
        pooled_rate = miss_count / frame_count
        link, mode, rate, common_duty, local_duty, effective_duty = key
        rows.append(
            {
                "candidate_id": _candidate_id(key),
                "link": link,
                "background_mode": mode,
                "rate_mbps_per_station": rate,
                "common_duty_cycle": common_duty,
                "local_duty_cycle": local_duty,
                "nominal_effective_duty_cycle": effective_duty,
                "seed_count": len(seed_rates),
                "run_count": len(members),
                "frame_count": frame_count,
                "miss_count": miss_count,
                "miss_rate": pooled_rate,
                "seed_miss_rate_mean": statistics.mean(rates),
                "seed_miss_rate_stddev": statistics.stdev(rates) if len(rates) > 1 else 0.0,
                "seed_miss_rate_min": min(rates),
                "seed_miss_rate_max": max(rates),
                "seed_miss_rates": ";".join(
                    f"{seed}:{value:.9f}" for seed, value in seed_rates.items()
                ),
                "target_band": "baseline" if mode == "none" else _target_band(pooled_rate),
                "run_ids": [member["run_id"] for member in members],
            }
        )
    return {
        "schema_version": 1,
        "telemetry_enabled": False,
        "input_roots": [str(root.resolve()) for root in roots],
        "project_commits": sorted(project_commits),
        "ns3_upstream_commits": sorted(upstream_commits),
        "candidate_count": len(rows),
        "run_count": len(run_dirs),
        "rows": rows,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "candidate_id",
        "link",
        "background_mode",
        "rate_mbps_per_station",
        "common_duty_cycle",
        "local_duty_cycle",
        "nominal_effective_duty_cycle",
        "seed_count",
        "run_count",
        "frame_count",
        "miss_count",
        "miss_rate",
        "seed_miss_rate_mean",
        "seed_miss_rate_stddev",
        "seed_miss_rate_min",
        "seed_miss_rate_max",
        "seed_miss_rates",
        "target_band",
    ]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in columns})


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Increment 2 Load Pilot Review",
        "",
        f"Runs: {report['run_count']}",
        "",
        f"Candidates: {report['candidate_count']}",
        "",
        "Telemetry: disabled",
        "",
        "| Candidate | Link | Background mode | Rate/STA (Mbps) | Effective duty | "
        "Frames | Misses | Miss rate | Seed range | Seed SD | Target band |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['candidate_id']} | {row['link']} | {row['background_mode']} | "
            f"{row['rate_mbps_per_station']:g} | "
            f"{100 * row['nominal_effective_duty_cycle']:.1f}% | "
            f"{row['frame_count']} | {row['miss_count']} | "
            f"{100 * row['miss_rate']:.2f}% | "
            f"{100 * row['seed_miss_rate_min']:.2f}-{100 * row['seed_miss_rate_max']:.2f}% | "
            f"{100 * row['seed_miss_rate_stddev']:.2f}% | {row['target_band']} |"
        )
    lines += [
        "",
        "Target bands are low 1-3%, medium 5-10%, and high 15-25%. "
        "They classify the pooled pilot rate only; load selection remains a review decision.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    args = parser.parse_args()
    report = summarize([root.resolve() for root in args.roots])
    for path in (args.csv, args.json, args.markdown):
        path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(args.csv, report["rows"])
    args.json.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown(args.markdown, report)
    print(
        f"WROTE {args.csv} {args.json} {args.markdown} "
        f"runs={report['run_count']} candidates={report['candidate_count']}"
    )


if __name__ == "__main__":
    main()
