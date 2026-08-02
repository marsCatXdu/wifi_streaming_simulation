#!/usr/bin/env python3
"""Summarize and plot adaptive-airtime OBSS closed-loop results."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


STAGES = ("T0", "T1", "T2", "T4")
POLICIES = (
    "fixed_link_1",
    "selective_duplication",
    "adaptive_airtime_duplication",
    "full_duplication",
    "fixed_link_0",
)


def _interval(values: list[float]) -> tuple[float, float, float]:
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, 0.0, 0.0
    t95 = {
        2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
        7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262,
    }.get(len(values), 1.96)
    half = t95 * statistics.stdev(values) / math.sqrt(len(values))
    return mean, half, half


def _burst_lengths(misses: list[bool]) -> list[int]:
    lengths: list[int] = []
    current = 0
    for missed in misses:
        if missed:
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    return lengths or [0]


def _policy_label(policy: str, topology: str) -> str:
    if topology == "mlo_str":
        return "MLO"
    labels = {
        "fixed_link_1": "Single 5 GHz",
        "selective_duplication": "Selective 0.20",
        "adaptive_airtime_duplication": "Adaptive airtime",
        "full_duplication": "Full duplication",
    }
    return labels.get(policy, policy)


def summarize_adaptive_runs(
    aggregate: dict[str, Any],
    result_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in aggregate["runs"]:
        run_dir = result_root / run["run_id"]
        frames_path = run_dir / "frames.csv"
        with frames_path.open(newline="", encoding="utf-8") as source:
            frames = list(csv.DictReader(source))
        misses = [row["deadline_miss"] in {"1", "true", "True"} for row in frames]
        bursts = _burst_lengths(misses)
        airtime_fraction = 0.0
        actions = 0
        stage_counts = {stage: 0 for stage in STAGES}
        if run["policy"] in {
            "selective_duplication",
            "adaptive_airtime_duplication",
            "full_duplication",
        }:
            summary_path = run_dir / "secondary_airtime_summary.json"
            if summary_path.is_file():
                meter = json.loads(summary_path.read_text(encoding="utf-8"))
                airtime_fraction = float(
                    meter.get("tagged_secondary_tx_airtime_fraction", 0.0)
                )
        if run["policy"] == "adaptive_airtime_duplication":
            with (run_dir / "adaptive_airtime_decisions.csv").open(
                newline="", encoding="utf-8"
            ) as source:
                decisions = list(csv.DictReader(source))
            action_rows = [row for row in decisions if row["decision"] == "action"]
            actions = len(action_rows)
            for row in action_rows:
                stage = row["sample_stage"]
                if stage in stage_counts:
                    stage_counts[stage] += 1
        rows.append({
            "run_id": run["run_id"],
            "seed": run["seed"],
            "topology": run["topology"],
            "policy": run["policy"],
            "label": _policy_label(run["policy"], run["topology"]),
            "deadline_miss_ratio": run["deadline_miss_ratio"],
            "redundant_byte_ratio": run["redundant_byte_ratio"],
            "max_miss_burst": max(bursts),
            "p95_miss_burst": float(np.percentile(bursts, 95)),
            "secondary_airtime_fraction": airtime_fraction,
            "adaptive_actions": actions,
            **{f"actions_{stage.lower()}": stage_counts[stage] for stage in STAGES},
        })
    return rows


def plot_adaptive_airtime(aggregate: dict[str, Any], result_root: Path) -> None:
    rows = summarize_adaptive_runs(aggregate, result_root)
    if not rows:
        return
    output = result_root / "plots" / "adaptive_airtime"
    output.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0])
    with (result_root / "adaptive_airtime_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_label[row["label"]].append(row)
    labels = [label for label in (
        "Single 5 GHz",
        "Selective 0.20",
        "Adaptive airtime",
        "Full duplication",
        "MLO",
    ) if label in by_label]

    miss_intervals = [
        _interval([float(row["deadline_miss_ratio"]) for row in by_label[label]])
        for label in labels
    ]
    plt.figure(figsize=(9, 4.8))
    means = [item[0] for item in miss_intervals]
    errors = np.asarray([[item[1] for item in miss_intervals],
                         [item[2] for item in miss_intervals]])
    plt.bar(labels, means, yerr=errors, capsize=4)
    plt.ylabel("Deadline miss ratio")
    plt.ylim(bottom=0)
    plt.title("OBSS adaptive-airtime miss ratio (95% CI)")
    plt.xticks(rotation=15, ha="right")
    plt.tight_layout()
    plt.savefig(output / "deadline_miss_ratio.png", dpi=200)
    plt.close()

    adaptive = [row for row in rows if row["policy"] == "adaptive_airtime_duplication"]
    fixed = {
        row["seed"]: row for row in rows
        if row["policy"] == "fixed_link_1"
    }
    mlo = {
        row["seed"]: row for row in rows
        if row["topology"] == "mlo_str"
    }
    paired_fixed = [
        float(row["deadline_miss_ratio"]) - float(fixed[row["seed"]]["deadline_miss_ratio"])
        for row in adaptive if row["seed"] in fixed
    ]
    paired_mlo = [
        float(row["deadline_miss_ratio"]) - float(mlo[row["seed"]]["deadline_miss_ratio"])
        for row in adaptive if row["seed"] in mlo
    ]
    if paired_fixed or paired_mlo:
        plt.figure(figsize=(7, 4.8))
        names = []
        values = []
        errs = []
        for name, series in (
            ("Adaptive - Single 5 GHz", paired_fixed),
            ("Adaptive - MLO", paired_mlo),
        ):
            if not series:
                continue
            mean, lo, hi = _interval(series)
            names.append(name)
            values.append(mean)
            errs.append((lo, hi))
        error = np.asarray([[e[0] for e in errs], [e[1] for e in errs]])
        plt.bar(names, values, yerr=error, capsize=4)
        plt.axhline(0.0, color="black", linewidth=0.8)
        plt.ylabel("Paired miss-ratio delta")
        plt.title("Adaptive paired miss-ratio deltas")
        plt.xticks(rotation=12, ha="right")
        plt.tight_layout()
        plt.savefig(output / "paired_miss_deltas.png", dpi=200)
        plt.close()

    airtime_labels = [
        label for label in labels
        if any(row["secondary_airtime_fraction"] > 0 for row in by_label[label])
        or label in {"Selective 0.20", "Adaptive airtime", "Full duplication"}
    ]
    if airtime_labels:
        intervals = [
            _interval([float(row["secondary_airtime_fraction"]) for row in by_label[label]])
            for label in airtime_labels
        ]
        plt.figure(figsize=(8, 4.8))
        means = [item[0] for item in intervals]
        errors = np.asarray([[item[1] for item in intervals],
                             [item[2] for item in intervals]])
        plt.bar(airtime_labels, means, yerr=errors, capsize=4)
        plt.ylabel("Tagged secondary PHY TX fraction")
        plt.ylim(bottom=0)
        plt.title("Measured secondary sender airtime")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        plt.savefig(output / "secondary_airtime_fraction.png", dpi=200)
        plt.close()

    plt.figure(figsize=(7, 4.8))
    xs = [float(row["secondary_airtime_fraction"]) for row in rows]
    ys = [float(row["deadline_miss_ratio"]) for row in rows]
    colors = [row["label"] for row in rows]
    for label in labels:
        mask = [color == label for color in colors]
        plt.scatter(
            [x for x, keep in zip(xs, mask) if keep],
            [y for y, keep in zip(ys, mask) if keep],
            label=label,
            s=36,
        )
    plt.xlabel("Measured secondary airtime fraction")
    plt.ylabel("Deadline miss ratio")
    plt.title("Reliability versus measured airtime")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output / "reliability_vs_airtime.png", dpi=200)
    plt.close()

    if adaptive:
        stage_counts = [
            sum(int(row[f"actions_{stage.lower()}"]) for row in adaptive)
            for stage in STAGES
        ]
        plt.figure(figsize=(7, 4.8))
        plt.bar(STAGES, stage_counts)
        plt.xlabel("Action stage")
        plt.ylabel("Actions across all runs")
        plt.title("Adaptive action stage distribution")
        plt.tight_layout()
        plt.savefig(output / "action_stage_distribution.png", dpi=200)
        plt.close()

        first = next(
            (result_root / row["run_id"] / "adaptive_airtime_decisions.csv"
             for row in adaptive),
            None,
        )
        if first and first.is_file():
            with first.open(newline="", encoding="utf-8") as source:
                decisions = list(csv.DictReader(source))
            times = [float(row["sample_time_ns"]) / 1e9 for row in decisions
                     if row["sample_stage"] == "T0"]
            prices = [float(row["shadow_price"]) for row in decisions
                      if row["sample_stage"] == "T0"]
            balances = [float(row["bucket_balance_us"]) for row in decisions
                        if row["sample_stage"] == "T0"]
            if times:
                fig, ax1 = plt.subplots(figsize=(8, 4.8))
                ax1.plot(times, prices, color="tab:blue", label="shadow price")
                ax1.set_xlabel("Simulation time (s)")
                ax1.set_ylabel("Shadow price", color="tab:blue")
                ax2 = ax1.twinx()
                ax2.plot(times, balances, color="tab:orange", label="bucket balance")
                ax2.set_ylabel("Bucket balance (us)", color="tab:orange")
                fig.tight_layout()
                fig.savefig(output / "shadow_price_bucket_timeline.png", dpi=200)
                plt.close(fig)

            estimated = [
                float(row["estimated_airtime_us"]) for row in decisions
                if row["decision"] == "action"
            ]
            if estimated:
                meter_path = first.parent / "secondary_airtime_summary.json"
                if meter_path.is_file():
                    meter = json.loads(meter_path.read_text(encoding="utf-8"))
                    measured = float(meter.get("tagged_secondary_tx_airtime_us", 0.0))
                    estimated_sum = float(meter.get("estimated_action_airtime_us", 0.0))
                    plt.figure(figsize=(6, 4.8))
                    plt.bar(
                        ["Estimated actions", "Measured tagged"],
                        [estimated_sum, measured],
                    )
                    plt.ylabel("Airtime (us)")
                    plt.title("Estimated versus measured secondary airtime")
                    plt.tight_layout()
                    plt.savefig(output / "estimated_vs_measured_airtime.png", dpi=200)
                    plt.close()

    burst_intervals = [
        _interval([float(row["max_miss_burst"]) for row in by_label[label]])
        for label in labels
    ]
    plt.figure(figsize=(9, 4.8))
    means = [item[0] for item in burst_intervals]
    errors = np.asarray([[item[1] for item in burst_intervals],
                         [item[2] for item in burst_intervals]])
    plt.bar(labels, means, yerr=errors, capsize=4)
    plt.ylabel("Maximum miss-burst length")
    plt.ylim(bottom=0)
    plt.title("Maximum deadline-miss burst length (95% CI)")
    plt.xticks(rotation=15, ha="right")
    plt.tight_layout()
    plt.savefig(output / "max_miss_burst.png", dpi=200)
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_root", type=Path)
    args = parser.parse_args()
    with (args.result_root / "aggregate.json").open(encoding="utf-8") as source:
        aggregate = json.load(source)
    plot_adaptive_airtime(aggregate, args.result_root)


if __name__ == "__main__":
    main()
