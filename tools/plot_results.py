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
import numpy as np


def _finish(path: Path, title: str, has_data: bool) -> None:
    if not has_data:
        plt.text(0.5, 0.5, "Insufficient data", ha="center", va="center",
                 transform=plt.gca().transAxes)
    plt.title(title)
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def _latencies(run: dict) -> np.ndarray:
    frame_path = Path(run["run_dir"]) / "frames.csv"
    with frame_path.open(newline="", encoding="utf-8") as source:
        values = [
            float(row["union_latency_us"])
            for row in csv.DictReader(source)
            if row["union_latency_us"]
        ]
    return np.asarray(values, dtype=float)


def _approach_key(item: dict) -> tuple[str, str, int]:
    inflights = int(item.get("config", {}).get("wifi", {}).get("sta_max_inflights", 1))
    return item["topology"], item["policy"], inflights


def _approach_label(item: dict, run_count: int | None = None) -> str:
    topology, policy, inflights = _approach_key(item)
    if topology == "dual_interface" and policy == "full_duplication":
        label = "Application full duplication"
    elif topology == "mlo_str":
        label = f"MLO NMaxInflights={inflights}"
    else:
        label = f"{topology}/{policy}"
    return f"{label} (n={run_count} runs)" if run_count is not None else label


def _run_groups(runs: list[dict]) -> dict[tuple[str, str, int], list[dict]]:
    grouped: dict[tuple[str, str, int], list[dict]] = {}
    for run in runs:
        grouped.setdefault(_approach_key(run), []).append(run)
    return grouped


def _latency_groups(runs: list[dict]) -> dict[tuple[str, str, int], list[np.ndarray]]:
    grouped: dict[tuple[str, str, int], list[np.ndarray]] = {}
    for run in runs:
        values = _latencies(run)
        if values.size:
            grouped.setdefault(_approach_key(run), []).append(values)
    return grouped


def plot(aggregate: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    runs = aggregate.get("runs", [])
    groups = aggregate.get("groups", [])
    latency_groups = _latency_groups(runs)

    plt.figure()
    probabilities = np.linspace(0.0, 1.0, 201)
    for key, samples in sorted(latency_groups.items()):
        run_quantiles = np.vstack(
            [np.quantile(values, probabilities, method="linear") for values in samples]
        )
        center = np.median(run_quantiles, axis=0)
        lower = np.quantile(run_quantiles, 0.10, axis=0)
        upper = np.quantile(run_quantiles, 0.90, axis=0)
        label = _approach_label(
            {"topology": key[0], "policy": key[1],
             "config": {"wifi": {"sta_max_inflights": key[2]}}},
            len(samples),
        )
        line = plt.plot(center, probabilities, label=label)[0]
        plt.fill_betweenx(probabilities, lower, upper, color=line.get_color(), alpha=0.2)
    cdf_data = bool(latency_groups)
    if cdf_data:
        plt.xlabel("Union latency (us)")
        plt.ylabel("CDF")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "latency_cdf.png",
            "Frame latency CDF (median and 10–90% run band)", cdf_data)

    plt.figure()
    all_latencies = [
        value
        for samples in latency_groups.values()
        for values in samples
        for value in values
    ]
    pdf_data = bool(all_latencies)
    if pdf_data:
        lower_bound = min(all_latencies)
        upper_bound = max(all_latencies)
        if lower_bound == upper_bound:
            lower_bound -= 0.5
            upper_bound += 0.5
        bins = np.linspace(lower_bound, upper_bound, 81)
        centers = (bins[:-1] + bins[1:]) / 2
        for key, samples in sorted(latency_groups.items()):
            run_densities = np.vstack(
                [np.histogram(values, bins=bins, density=True)[0] for values in samples]
            )
            center = np.median(run_densities, axis=0)
            lower = np.quantile(run_densities, 0.10, axis=0)
            upper = np.quantile(run_densities, 0.90, axis=0)
            label = _approach_label(
                {"topology": key[0], "policy": key[1],
                 "config": {"wifi": {"sta_max_inflights": key[2]}}},
                len(samples),
            )
            line = plt.plot(centers, center, label=label)[0]
            plt.fill_between(centers, lower, upper, color=line.get_color(), alpha=0.2)
        plt.xlabel("Union latency (us)")
        plt.ylabel("Probability density")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "latency_pdf.png",
            "Frame latency PDF (median and 10–90% run band)", pdf_data)

    plt.figure()
    labels = [_approach_label(group).replace(" ", "\n", 1) for group in groups]
    misses = [g["metrics"]["deadline_miss_ratio"]["mean"] for g in groups]
    available = [index for index, value in enumerate(misses) if value is not None]
    if available:
        plt.bar(available, [misses[index] for index in available])
        plt.xticks(available, [labels[index] for index in available], fontsize="x-small")
        plt.ylabel("Deadline miss ratio (run mean)")
    _finish(output_dir / "deadline_miss.png", "Deadline misses", bool(available))

    plt.figure()
    pareto = [run for run in runs if run["latency_p99_us"] is not None]
    for members in _run_groups(pareto).values():
        plt.scatter([run["redundant_byte_ratio"] for run in members],
                    [run["latency_p99_us"] for run in members],
                    label=_approach_label(members[0]))
    if pareto:
        plt.xlabel("Redundant byte ratio (airtime proxy; not measured airtime)")
        plt.ylabel("P99 latency (us)")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "p99_redundancy_pareto.png",
            "P99 latency vs redundant-byte proxy", bool(pareto))

    plt.figure()
    background = [run for run in runs if run["background_throughput_mbps"] is not None]
    for members in _run_groups(background).values():
        plt.scatter([run["background_throughput_mbps"] for run in members],
                    [run["deadline_miss_ratio"] for run in members],
                    label=_approach_label(members[0]))
    if background:
        plt.xlabel("Background delivered throughput (Mbps)")
        plt.ylabel("Streaming deadline miss ratio")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "background_degradation.png",
            "Background throughput and streaming degradation", bool(background))

    plt.figure()
    duplicate = [run for run in runs if run["cross_copy_delay_correlation"] is not None
                 and run["duplicate_recovery_rate"] is not None]
    for members in _run_groups(duplicate).values():
        plt.scatter([run["cross_copy_delay_correlation"] for run in members],
                    [run["duplicate_recovery_rate"] for run in members],
                    label=_approach_label(members[0]))
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

    plt.figure()
    burst_groups = [
        (
            _approach_label(group, group["run_count"]),
            group["deadline_miss_burst_distribution"],
        )
        for group in groups
        if group["deadline_miss_burst_distribution"]
    ]
    if burst_groups:
        maximum = max(value for _, values in burst_groups for value in values)
        bins = range(1, maximum + 2)
        for label, values in burst_groups:
            plt.hist(values, bins=bins, align="left", histtype="step",
                     linewidth=2, label=label)
        plt.xlabel("Consecutive missed frames")
        plt.ylabel("Burst count")
        plt.legend(fontsize="x-small")
    _finish(output_dir / "miss_burst_distribution_by_group.png",
            "Deadline-miss burst distribution by approach", bool(burst_groups))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aggregate_json", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("plots"))
    args = parser.parse_args()
    aggregate = json.loads(args.aggregate_json.read_text(encoding="utf-8"))
    plot(aggregate, args.output_dir)
    print(f"WROTE {args.output_dir} plots=8")


if __name__ == "__main__":
    main()
