#!/usr/bin/env python3
"""Summarize and plot closed-loop selective-duplication control decisions."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


STAGES = ("T0", "T1", "T2", "T4")


def _interval(values: list[float]) -> tuple[float, float, float]:
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, 0.0, 0.0
    t95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
           7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}.get(len(values), 1.96)
    half = t95 * statistics.stdev(values) / math.sqrt(len(values))
    return mean, half, half


def summarize_selective_decisions(
    aggregate: dict[str, Any],
    result_root: Path,
) -> list[dict[str, Any]]:
    """Return one control summary row per selective run."""
    result: list[dict[str, Any]] = []
    for run in aggregate["runs"]:
        if run["policy"] != "selective_duplication":
            continue
        path = result_root / run["run_id"] / "selective_duplication_decisions.csv"
        with path.open(newline="", encoding="utf-8") as source:
            decisions = list(csv.DictReader(source))
        offsets = run["config"]["selectiveDuplication"]["decision_offsets_us"]
        frame_count = len(decisions) // len(offsets)
        actions = [row for row in decisions if row["decision"] == "action"]
        suppressions = [
            row for row in decisions if row["decision"] == "budget_suppressed"
        ]
        crossings = actions + suppressions
        result.append({
            "run_id": run["run_id"],
            "seed": run["seed"],
            "frame_count": frame_count,
            "actions": len(actions),
            "action_rate": len(actions) / frame_count,
            "budget_suppressions": len(suppressions),
            "budget_suppression_rate": len(suppressions) / frame_count,
            "threshold_crossings": len(crossings),
            "threshold_crossing_rate": len(crossings) / frame_count,
            "deadline_miss_ratio": run["deadline_miss_ratio"],
            "redundant_byte_ratio": run["redundant_byte_ratio"],
            **{
                f"actions_{stage.lower()}": sum(
                    row["sample_stage"] == stage for row in actions
                )
                for stage in STAGES
            },
        })
    return result


def plot_selective_control(aggregate: dict[str, Any], result_root: Path) -> None:
    """Write selective-controller tables and figures under one result root."""
    rows = summarize_selective_decisions(aggregate, result_root)
    if not rows:
        return
    output = result_root / "plots" / "selective_control"
    output.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0])
    with (result_root / "selective_duplication_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    metrics = ("action_rate", "threshold_crossing_rate", "budget_suppression_rate")
    labels = ("Secondary-copy action", "Risk threshold crossing", "Budget suppression")
    intervals = [_interval([float(row[name]) for row in rows]) for name in metrics]
    means = [item[0] for item in intervals]
    errors = np.asarray([[item[1] for item in intervals],
                         [item[2] for item in intervals]])
    plt.figure(figsize=(8, 4.8))
    plt.bar(labels, means, yerr=errors, capsize=4)
    plt.ylabel("Fraction of generated frames")
    plt.ylim(bottom=0)
    plt.title("Closed-loop selective-duplication control rates (95% CI)")
    plt.xticks(rotation=12, ha="right")
    plt.tight_layout()
    plt.savefig(output / "control_rates.png", dpi=200)
    plt.close()

    stage_counts = [sum(int(row[f"actions_{stage.lower()}"]) for row in rows)
                    for stage in STAGES]
    plt.figure(figsize=(7, 4.8))
    plt.bar(STAGES, stage_counts)
    plt.xlabel("First stage that launched the secondary copy")
    plt.ylabel("Actions across all runs")
    plt.title("Selective-duplication action timing")
    plt.tight_layout()
    plt.savefig(output / "action_stage_distribution.png", dpi=200)
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_root", type=Path)
    args = parser.parse_args()
    with (args.result_root / "aggregate.json").open(encoding="utf-8") as source:
        aggregate = json.load(source)
    plot_selective_control(aggregate, args.result_root)


if __name__ == "__main__":
    main()
