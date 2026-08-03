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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import t as student_t


POLICIES = (
    "fixed_link_1",
    "selective_duplication",
    "adaptive_airtime_duplication",
    "full_duplication",
    "fixed_link_0",
)


def _interval(values: list[float]) -> tuple[float, float, float]:
    if not values:
        raise ValueError("cannot calculate an interval from no runs")
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, 0.0, 0.0
    t95 = float(student_t.ppf(0.975, len(values) - 1))
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


def _run_directory(run: dict[str, Any], result_root: Path) -> Path:
    return Path(run.get("run_dir", result_root / run["run_id"]))


def _pair_key(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["seed"]), int(row["run"])


def _unique_pair_index(
    rows: list[dict[str, Any]],
    description: str,
) -> dict[tuple[int, int], dict[str, Any]]:
    result: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        key = _pair_key(row)
        if key in result:
            raise ValueError(f"duplicate {description} treatment for seed/run {key}")
        result[key] = row
    return result


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"missing adaptive analysis input: {path}")
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _link0_phy_tx_us(run_dir: Path) -> float:
    rows = _read_csv(run_dir / "link_intervals.csv")
    matches = [row for row in rows if int(row["link_id"]) == 0]
    if len(matches) != 1:
        raise ValueError(f"{run_dir}: expected one link-0 interval")
    return float(matches[0]["phy_tx_time_us"])


def summarize_adaptive_runs(
    aggregate: dict[str, Any],
    result_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in aggregate["runs"]:
        run_dir = _run_directory(run, result_root)
        frames = _read_csv(run_dir / "frames.csv")
        misses = [row["deadline_miss"] in {"1", "true", "True"} for row in frames]
        bursts = _burst_lengths(misses)
        airtime_fraction = 0.0
        tagged_airtime_us = 0.0
        estimated_airtime_us = 0.0
        estimate_ratio = 0.0
        maximum_debt_us = 0.0
        forced_settlements = 0
        finite_run_budget_us: float | None = None
        budget_excess_us: float | None = None
        actions = 0
        stage_counts: dict[str, int] = {}
        duration_us = float(run["config"]["duration_s"]) * 1_000_000
        link0_phy_tx_us = _link0_phy_tx_us(run_dir)
        if run["policy"] in {
            "selective_duplication",
            "adaptive_airtime_duplication",
            "full_duplication",
        }:
            summary_path = run_dir / "secondary_airtime_summary.json"
            if not summary_path.is_file():
                raise ValueError(f"missing adaptive analysis input: {summary_path}")
            meter = json.loads(summary_path.read_text(encoding="utf-8"))
            required = {
                "tagged_secondary_tx_airtime_us",
                "tagged_secondary_tx_airtime_fraction",
                "measurement_duration_us",
                "maximum_budget_debt_us",
                "estimated_action_airtime_us",
                "actual_to_estimated_airtime_ratio",
                "forced_reservation_settlements",
                "finite_run_budget_us",
                "budget_excess_us",
            }
            missing = sorted(required - meter.keys())
            if missing:
                raise ValueError(
                    f"{summary_path}: missing fields {', '.join(missing)}"
                )
            duration_us = float(meter["measurement_duration_us"])
            tagged_airtime_us = float(meter["tagged_secondary_tx_airtime_us"])
            airtime_fraction = float(meter["tagged_secondary_tx_airtime_fraction"])
            estimated_airtime_us = float(meter["estimated_action_airtime_us"])
            estimate_ratio = float(meter["actual_to_estimated_airtime_ratio"])
            maximum_debt_us = float(meter["maximum_budget_debt_us"])
            forced_settlements = int(meter["forced_reservation_settlements"])
            finite_run_budget_us = (
                None if meter["finite_run_budget_us"] is None
                else float(meter["finite_run_budget_us"])
            )
            budget_excess_us = (
                None if meter["budget_excess_us"] is None
                else float(meter["budget_excess_us"])
            )
            if tagged_airtime_us > link0_phy_tx_us + 1.0:
                raise ValueError(
                    f"{run_dir}: tagged airtime exceeds link-0 PHY TX airtime"
                )
        if run["policy"] == "adaptive_airtime_duplication":
            decisions = _read_csv(run_dir / "adaptive_airtime_decisions.csv")
            action_rows = [row for row in decisions if row["decision"] == "action"]
            actions = len(action_rows)
            for row in action_rows:
                stage = row["sample_stage"]
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
        rows.append({
            "run_id": run["run_id"],
            "run_dir": str(run_dir),
            "seed": run["seed"],
            "run": run.get("run", 1),
            "topology": run["topology"],
            "policy": run["policy"],
            "label": _policy_label(run["policy"], run["topology"]),
            "deadline_miss_ratio": run["deadline_miss_ratio"],
            "redundant_byte_ratio": run["redundant_byte_ratio"],
            "max_miss_burst": max(bursts),
            "p95_miss_burst": float(np.percentile(bursts, 95)),
            "measurement_duration_us": duration_us,
            "tagged_secondary_airtime_us": tagged_airtime_us,
            "secondary_airtime_fraction": airtime_fraction,
            "estimated_action_airtime_us": estimated_airtime_us,
            "actual_to_estimated_airtime_ratio": estimate_ratio,
            "maximum_budget_debt_us": maximum_debt_us,
            "forced_reservation_settlements": forced_settlements,
            "finite_run_budget_us": finite_run_budget_us,
            "budget_excess_us": budget_excess_us,
            "link0_phy_tx_time_us": link0_phy_tx_us,
            "link0_phy_tx_fraction": link0_phy_tx_us / duration_us,
            "adaptive_actions": actions,
            "_stage_counts": stage_counts,
        })

    fixed_by_pair = _unique_pair_index(
        [
            row for row in rows
            if row["topology"] == "dual_interface" and row["policy"] == "fixed_link_1"
        ],
        "single-5-GHz",
    )
    stages = sorted({stage for row in rows for stage in row["_stage_counts"]})
    for row in rows:
        baseline = fixed_by_pair.get(_pair_key(row))
        if baseline is None or row["topology"] != "dual_interface":
            incremental_fraction: float | None = None
            diagnostic_difference: float | None = None
        else:
            incremental_fraction = (
                float(row["link0_phy_tx_time_us"]) -
                float(baseline["link0_phy_tx_time_us"])
            ) / float(row["measurement_duration_us"])
            diagnostic_difference = (
                incremental_fraction - float(row["secondary_airtime_fraction"])
            )
        row["incremental_link0_airtime_fraction"] = incremental_fraction
        row["incremental_minus_tagged_airtime_fraction"] = diagnostic_difference
        stage_counts = row.pop("_stage_counts")
        for stage in stages:
            row[f"actions_stage_{stage}"] = stage_counts.get(stage, 0)
    return rows


def plot_adaptive_airtime(aggregate: dict[str, Any], result_root: Path) -> None:
    rows = summarize_adaptive_runs(aggregate, result_root)
    if not rows:
        return
    output = result_root / "plots" / "adaptive_airtime"
    output.mkdir(parents=True, exist_ok=True)
    columns = [column for column in rows[0] if column != "run_dir"]
    with (result_root / "adaptive_airtime_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as target:
        writer = csv.DictWriter(target, fieldnames=columns, extrasaction="ignore")
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
    fixed = _unique_pair_index(
        [row for row in rows if row["policy"] == "fixed_link_1"],
        "single-5-GHz",
    )
    mlo = _unique_pair_index(
        [row for row in rows if row["topology"] == "mlo_str"],
        "MLO",
    )
    paired_fixed = [
        float(row["deadline_miss_ratio"]) -
        float(fixed[_pair_key(row)]["deadline_miss_ratio"])
        for row in adaptive if _pair_key(row) in fixed
    ]
    paired_mlo = [
        float(row["deadline_miss_ratio"]) -
        float(mlo[_pair_key(row)]["deadline_miss_ratio"])
        for row in adaptive if _pair_key(row) in mlo
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

        diagnostic_labels = [
            label for label in airtime_labels
            if all(
                row["incremental_link0_airtime_fraction"] is not None
                for row in by_label[label]
            )
        ]
        if diagnostic_labels:
            tagged_means = [
                statistics.mean(
                    float(row["secondary_airtime_fraction"])
                    for row in by_label[label]
                )
                for label in diagnostic_labels
            ]
            incremental_means = [
                statistics.mean(
                    float(row["incremental_link0_airtime_fraction"])
                    for row in by_label[label]
                )
                for label in diagnostic_labels
            ]
            positions = np.arange(len(diagnostic_labels))
            width = 0.38
            plt.figure(figsize=(8, 4.8))
            plt.bar(positions - width / 2, tagged_means, width, label="Tagged meter")
            plt.bar(
                positions + width / 2,
                incremental_means,
                width,
                label="Incremental link-0 PHY TX",
            )
            plt.ylabel("Secondary PHY TX fraction")
            plt.title("Tagged versus incremental link-0 airtime")
            plt.xticks(positions, diagnostic_labels, rotation=15, ha="right")
            plt.legend()
            plt.tight_layout()
            plt.savefig(output / "tagged_vs_incremental_link0_airtime.png", dpi=200)
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
        stage_columns = sorted(
            column for column in rows[0] if column.startswith("actions_stage_")
        )
        stage_labels = [column.removeprefix("actions_stage_") for column in stage_columns]
        stage_counts = [
            sum(int(row[column]) for row in adaptive) for column in stage_columns
        ]
        if stage_columns:
            plt.figure(figsize=(7, 4.8))
            plt.bar(stage_labels, stage_counts)
            plt.xlabel("Action stage")
            plt.ylabel("Actions across all runs")
            plt.title("Adaptive action stage distribution")
            plt.tight_layout()
            plt.savefig(output / "action_stage_distribution.png", dpi=200)
            plt.close()

        first_run = min(adaptive, key=_pair_key)
        first = Path(first_run["run_dir"]) / "adaptive_airtime_decisions.csv"
        if first.is_file():
            decisions = _read_csv(first)
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

        estimated: list[float] = []
        measured: list[float] = []
        for adaptive_run in adaptive:
            adaptive_dir = Path(adaptive_run["run_dir"])
            action_rows = {
                int(row["frame_id"]): float(row["estimated_airtime_us"])
                for row in _read_csv(adaptive_dir / "adaptive_airtime_decisions.csv")
                if row["decision"] == "action"
            }
            settlement_rows = {
                int(row["frame_id"]): float(row["measured_airtime_us"])
                for row in _read_csv(adaptive_dir / "secondary_airtime_settlements.csv")
            }
            if set(action_rows) != set(settlement_rows):
                raise ValueError(
                    f"{adaptive_dir}: adaptive actions and settlements do not match"
                )
            for frame_id in sorted(action_rows):
                estimated.append(action_rows[frame_id])
                measured.append(settlement_rows[frame_id])
        if estimated:
            limit = max(estimated + measured)
            plt.figure(figsize=(6, 5.2))
            plt.scatter(estimated, measured, s=12, alpha=0.25)
            plt.plot([0, limit], [0, limit], linestyle="--", color="black", label="ideal")
            plt.xlabel("Estimated airtime per action (us)")
            plt.ylabel("Measured airtime per action (us)")
            plt.title("Secondary airtime estimate calibration")
            plt.legend()
            plt.tight_layout()
            plt.savefig(output / "estimated_vs_measured_airtime.png", dpi=200)
            plt.close()

    for metric, ylabel, title, file_name in (
        (
            "max_miss_burst",
            "Maximum miss-burst length",
            "Maximum deadline-miss burst length (95% CI)",
            "max_miss_burst.png",
        ),
        (
            "p95_miss_burst",
            "P95 miss-burst length",
            "P95 deadline-miss burst length (95% CI)",
            "p95_miss_burst.png",
        ),
    ):
        burst_intervals = [
            _interval([float(row[metric]) for row in by_label[label]])
            for label in labels
        ]
        plt.figure(figsize=(9, 4.8))
        means = [item[0] for item in burst_intervals]
        errors = np.asarray([
            [item[1] for item in burst_intervals],
            [item[2] for item in burst_intervals],
        ])
        plt.bar(labels, means, yerr=errors, capsize=4)
        plt.ylabel(ylabel)
        plt.ylim(bottom=0)
        plt.title(title)
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        plt.savefig(output / file_name, dpi=200)
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
