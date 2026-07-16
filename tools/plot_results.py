#!/usr/bin/env python3
"""Create the standard wifi-streaming analysis plot set."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _finish(path: Path, title: str, has_data: bool) -> None:
    if not has_data:
        plt.text(0.5, 0.5, "Insufficient data", ha="center", va="center",
                 transform=plt.gca().transAxes)
    plt.title(title)
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def plot(aggregate: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    runs = aggregate.get("runs", [])
    groups = aggregate.get("groups", [])

    plt.figure()
    cdf_data = False
    for run in runs:
        frame_path = Path(run["run_dir"]) / "frames.csv"
        with frame_path.open(newline="", encoding="utf-8") as source:
            values = sorted(float(row["union_latency_us"]) for row in csv.DictReader(source)
                            if row["union_latency_us"])
        if values:
            cdf_data = True
            y = [(index + 1) / len(values) for index in range(len(values))]
            plt.plot(values, y, label=f"{run['topology']}/{run['policy']}")
    if cdf_data:
        plt.xlabel("Union latency (us)")
        plt.ylabel("CDF")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "latency_cdf.png", "Frame latency CDF", cdf_data)

    plt.figure()
    labels = [f"{g['topology']}\n{g['policy']}" for g in groups]
    misses = [g["metrics"]["deadline_miss_ratio"]["mean"] for g in groups]
    available = [index for index, value in enumerate(misses) if value is not None]
    if available:
        plt.bar(available, [misses[index] for index in available])
        plt.xticks(available, [labels[index] for index in available], fontsize="x-small")
        plt.ylabel("Deadline miss ratio (run mean)")
    _finish(output_dir / "deadline_miss.png", "Deadline misses", bool(available))

    plt.figure()
    pareto = [(run["redundant_byte_ratio"], run["latency_p99_us"], run) for run in runs
              if run["latency_p99_us"] is not None]
    for x, y, run in pareto:
        plt.scatter(x, y, label=f"{run['topology']}/{run['policy']}")
    if pareto:
        plt.xlabel("Redundant byte ratio (airtime proxy; not measured airtime)")
        plt.ylabel("P99 latency (us)")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "p99_redundancy_pareto.png",
            "P99 latency vs redundant-byte proxy", bool(pareto))

    plt.figure()
    background = [(run["background_throughput_mbps"], run["deadline_miss_ratio"], run)
                  for run in runs if run["background_throughput_mbps"] is not None]
    for x, y, run in background:
        plt.scatter(x, y, label=f"{run['topology']}/{run['policy']}")
    if background:
        plt.xlabel("Background delivered throughput (Mbps)")
        plt.ylabel("Streaming deadline miss ratio")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "background_degradation.png",
            "Background throughput and streaming degradation", bool(background))

    plt.figure()
    duplicate = [(run["cross_copy_delay_correlation"], run["duplicate_recovery_rate"], run)
                 for run in runs if run["cross_copy_delay_correlation"] is not None
                 and run["duplicate_recovery_rate"] is not None]
    for x, y, run in duplicate:
        plt.scatter(x, y, label=f"{run['topology']}/{run['policy']}")
    if duplicate:
        plt.xlabel("Cross-copy delay correlation")
        plt.ylabel("Duplicate recovery rate")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "duplication_benefit_correlation.png",
            "Duplication benefit vs cross-copy correlation", bool(duplicate))

    plt.figure()
    bursts = [value for group in groups for value in group["deadline_miss_burst_distribution"]]
    if bursts:
        bins = range(1, max(bursts) + 2)
        plt.hist(bursts, bins=bins, align="left", rwidth=0.85)
        plt.xlabel("Consecutive missed frames")
        plt.ylabel("Burst count")
    _finish(output_dir / "miss_burst_distribution.png",
            "Deadline-miss burst distribution", bool(bursts))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aggregate_json", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("plots"))
    args = parser.parse_args()
    aggregate = json.loads(args.aggregate_json.read_text(encoding="utf-8"))
    plot(aggregate, args.output_dir)
    print(f"WROTE {args.output_dir} plots=6")


if __name__ == "__main__":
    main()
