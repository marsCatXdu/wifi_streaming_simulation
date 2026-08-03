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
    "adaptive_deficit_duplication",
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


def _dual_shadow_price(row: dict[str, str]) -> float:
    """Return the controller dual variable, with historical-schema fallback."""
    value = row.get("dual_shadow_price", "")
    return float(value if value != "" else row["shadow_price"])


def _policy_label(policy: str, topology: str) -> str:
    if topology == "mlo_str":
        return "STR MLO"
    if topology == "mlo_emlsr":
        return "EMLSR MLO"
    labels = {
        "fixed_link_1": "Single 5 GHz",
        "selective_duplication": "Selective 0.20",
        "adaptive_airtime_duplication": "Adaptive airtime",
        "adaptive_deficit_duplication": "Adaptive deficit",
        "full_duplication": "Full duplication",
    }
    return labels.get(policy, policy)


def _run_directory(run: dict[str, Any], result_root: Path) -> Path:
    local_directory = result_root / run["run_id"]
    serialized_directory = run.get("run_dir")
    if serialized_directory:
        candidate = Path(serialized_directory)
        if candidate.is_dir():
            return candidate
    return local_directory


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


def _ordered_policy_labels(
    by_label: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Return the stable display order for every supported treatment."""
    return [label for label in (
        "Single 5 GHz",
        "Selective 0.20",
        "Adaptive airtime",
        "Adaptive deficit",
        "Full duplication",
        "STR MLO",
        "EMLSR MLO",
    ) if label in by_label]


def _paired_mlo_diagnostics(
    adaptive_by_policy: dict[str, list[dict[str, Any]]],
    mlo_by_topology: dict[str, dict[tuple[int, int], dict[str, Any]]],
) -> tuple[
    list[tuple[str, list[float]]],
    list[tuple[str, list[float]]],
    list[tuple[str, list[float]]],
]:
    """Build paired miss, P99, and airtime series for each MLO baseline."""
    paired_miss_series: list[tuple[str, list[float]]] = []
    paired_p99_series: list[tuple[str, list[float]]] = []
    paired_airtime_series: list[tuple[str, list[float]]] = []
    for policy, policy_rows in adaptive_by_policy.items():
        treatment_label = _policy_label(policy, "dual_interface")
        for topology, mlo in mlo_by_topology.items():
            baseline_label = _policy_label("fixed_link_0", topology)
            comparison_label = f"{treatment_label} - {baseline_label}"
            paired_miss_series.append((
                comparison_label,
                [
                    float(row["deadline_miss_ratio"]) -
                    float(mlo[_pair_key(row)]["deadline_miss_ratio"])
                    for row in policy_rows if _pair_key(row) in mlo
                ],
            ))
            paired_p99_series.append((
                comparison_label,
                [
                    float(row["latency_p99_us"]) -
                    float(mlo[_pair_key(row)]["latency_p99_us"])
                    for row in policy_rows
                    if _pair_key(row) in mlo and row["latency_p99_us"] is not None and
                    mlo[_pair_key(row)]["latency_p99_us"] is not None
                ],
            ))
            paired_airtime_series.append((
                comparison_label,
                [
                    float(row["target_phy_tx_time_us"]) /
                    float(mlo[_pair_key(row)]["target_phy_tx_time_us"]) - 1.0
                    for row in policy_rows
                    if _pair_key(row) in mlo and
                    float(mlo[_pair_key(row)]["target_phy_tx_time_us"]) > 0
                ],
            ))
    return paired_miss_series, paired_p99_series, paired_airtime_series


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"missing adaptive analysis input: {path}")
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _primary_deadline_miss(frame: dict[str, str]) -> bool:
    """Return the pre-intervention primary-copy deadline outcome."""
    completion = frame["copy_0_completion_us"]
    if not completion:
        return True
    latency_us = int(completion) - int(frame["generation_time_us"])
    return latency_us > int(frame["deadline_us"])


def _link_phy_tx_us(run_dir: Path) -> dict[int, float]:
    rows = _read_csv(run_dir / "link_intervals.csv")
    result: dict[int, float] = {}
    for row in rows:
        link_id = int(row["link_id"])
        if link_id in result:
            raise ValueError(f"{run_dir}: duplicate link-{link_id} interval")
        result[link_id] = float(row["phy_tx_time_us"])
    if 0 not in result:
        raise ValueError(f"{run_dir}: missing link-0 interval")
    return result


def summarize_adaptive_runs(
    aggregate: dict[str, Any],
    result_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in aggregate["runs"]:
        run_dir = _run_directory(run, result_root)
        frames = sorted(
            _read_csv(run_dir / "frames.csv"),
            key=lambda row: int(row["generation_time_us"]),
        )
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
        primary_diagnostics: dict[str, int | float | None] = {
            "primary_deadline_misses": None,
            "acted_primary_deadline_misses": None,
            "rescued_primary_deadline_misses": None,
            "failed_rescue_primary_deadline_misses": None,
            "unacted_primary_deadline_misses": None,
            "primary_hit_actions": None,
            "primary_miss_action_precision": None,
            "primary_miss_action_recall": None,
            "deadline_rescue_per_action": None,
            "deadline_rescue_given_acted_primary_miss": None,
            "rescued_primary_miss_airtime_us": None,
            "failed_rescue_airtime_us": None,
            "primary_hit_action_airtime_us": None,
            "primary_hit_action_airtime_share": None,
        }
        duration_us = float(run["config"]["duration_s"]) * 1_000_000
        link_phy_tx_us = _link_phy_tx_us(run_dir)
        link0_phy_tx_us = link_phy_tx_us[0]
        target_phy_tx_us = sum(link_phy_tx_us.values())
        if run["policy"] in {
            "selective_duplication",
            "adaptive_airtime_duplication",
            "adaptive_deficit_duplication",
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
        if run["policy"] in {
            "adaptive_airtime_duplication", "adaptive_deficit_duplication",
        }:
            decisions = _read_csv(run_dir / "adaptive_airtime_decisions.csv")
            action_rows = [row for row in decisions if row["decision"] == "action"]
            actions = len(action_rows)
            for row in action_rows:
                stage = row["sample_stage"]
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
            action_ids = {int(row["frame_id"]) for row in action_rows}
            if len(action_ids) != actions:
                raise ValueError(f"{run_dir}: duplicate adaptive action frame")
            settlement_rows = _read_csv(
                run_dir / "secondary_airtime_settlements.csv"
            )
            settlements = {
                int(row["frame_id"]): float(row["measured_airtime_us"])
                for row in settlement_rows
            }
            if len(settlements) != len(settlement_rows):
                raise ValueError(f"{run_dir}: duplicate adaptive settlement frame")
            if set(settlements) != action_ids:
                raise ValueError(
                    f"{run_dir}: adaptive actions and settlements do not match"
                )
            frame_by_id = {int(row["frame_id"]): row for row in frames}
            if len(frame_by_id) != len(frames):
                raise ValueError(f"{run_dir}: duplicate frame in adaptive analysis")
            if not action_ids <= set(frame_by_id):
                raise ValueError(f"{run_dir}: adaptive action refers to an unknown frame")

            primary_miss_ids = {
                frame_id for frame_id, frame in frame_by_id.items()
                if _primary_deadline_miss(frame)
            }
            union_miss_ids = {
                frame_id for frame_id, frame in frame_by_id.items()
                if frame["deadline_miss"] in {"1", "true", "True"}
            }
            acted_primary_ids = action_ids & primary_miss_ids
            rescued_ids = acted_primary_ids - union_miss_ids
            failed_ids = acted_primary_ids & union_miss_ids
            unacted_primary_ids = primary_miss_ids - action_ids
            primary_hit_ids = action_ids - primary_miss_ids

            primary_count = len(primary_miss_ids)
            acted_primary_count = len(acted_primary_ids)
            rescued_count = len(rescued_ids)
            primary_hit_airtime_us = sum(
                settlements[frame_id] for frame_id in primary_hit_ids
            )
            primary_diagnostics = {
                "primary_deadline_misses": primary_count,
                "acted_primary_deadline_misses": acted_primary_count,
                "rescued_primary_deadline_misses": rescued_count,
                "failed_rescue_primary_deadline_misses": len(failed_ids),
                "unacted_primary_deadline_misses": len(unacted_primary_ids),
                "primary_hit_actions": len(primary_hit_ids),
                "primary_miss_action_precision": (
                    acted_primary_count / actions if actions else None
                ),
                "primary_miss_action_recall": (
                    acted_primary_count / primary_count if primary_count else None
                ),
                "deadline_rescue_per_action": (
                    rescued_count / actions if actions else None
                ),
                "deadline_rescue_given_acted_primary_miss": (
                    rescued_count / acted_primary_count
                    if acted_primary_count else None
                ),
                "rescued_primary_miss_airtime_us": sum(
                    settlements[frame_id] for frame_id in rescued_ids
                ),
                "failed_rescue_airtime_us": sum(
                    settlements[frame_id] for frame_id in failed_ids
                ),
                "primary_hit_action_airtime_us": primary_hit_airtime_us,
                "primary_hit_action_airtime_share": (
                    primary_hit_airtime_us / tagged_airtime_us
                    if tagged_airtime_us else None
                ),
            }
        rows.append({
            "run_id": run["run_id"],
            "run_dir": str(run_dir),
            "seed": run["seed"],
            "run": run.get("run", 1),
            "topology": run["topology"],
            "policy": run["policy"],
            "label": _policy_label(run["policy"], run["topology"]),
            "deadline_miss_ratio": run["deadline_miss_ratio"],
            "latency_p99_us": run.get("latency_p99_us"),
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
            "target_phy_tx_time_us": target_phy_tx_us,
            "target_phy_tx_fraction": target_phy_tx_us / duration_us,
            "adaptive_actions": actions,
            **primary_diagnostics,
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
    labels = _ordered_policy_labels(by_label)

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

    p99_labels = [
        label for label in labels
        if all(row["latency_p99_us"] is not None for row in by_label[label])
    ]
    if p99_labels:
        p99_intervals = [
            _interval([float(row["latency_p99_us"]) for row in by_label[label]])
            for label in p99_labels
        ]
        plt.figure(figsize=(9, 4.8))
        means = [item[0] for item in p99_intervals]
        errors = np.asarray([
            [item[1] for item in p99_intervals],
            [item[2] for item in p99_intervals],
        ])
        plt.bar(p99_labels, means, yerr=errors, capsize=4)
        plt.ylabel("P99 frame latency (us)")
        plt.ylim(bottom=0)
        plt.title("OBSS P99 frame latency (95% CI)")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        plt.savefig(output / "latency_p99.png", dpi=200)
        plt.close()

    adaptive_by_policy = {
        policy: [row for row in rows if row["policy"] == policy]
        for policy in ("adaptive_airtime_duplication", "adaptive_deficit_duplication")
        if any(row["policy"] == policy for row in rows)
    }
    fixed = _unique_pair_index(
        [row for row in rows if row["policy"] == "fixed_link_1"],
        "single-5-GHz",
    )
    mlo_by_topology = {
        topology: _unique_pair_index(
            [row for row in rows if row["topology"] == topology],
            _policy_label("fixed_link_0", topology),
        )
        for topology in ("mlo_str", "mlo_emlsr")
        if any(row["topology"] == topology for row in rows)
    }
    paired_series: list[tuple[str, list[float]]] = []
    for policy, policy_rows in adaptive_by_policy.items():
        label = _policy_label(policy, "dual_interface")
        paired_series.append((
            f"{label} - Single 5 GHz",
            [
                float(row["deadline_miss_ratio"]) -
                float(fixed[_pair_key(row)]["deadline_miss_ratio"])
                for row in policy_rows if _pair_key(row) in fixed
            ],
        ))
    paired_mlo_miss, paired_p99_series, paired_airtime_series = (
        _paired_mlo_diagnostics(adaptive_by_policy, mlo_by_topology)
    )
    paired_series.extend(paired_mlo_miss)
    if any(series for _, series in paired_series):
        plt.figure(figsize=(7, 4.8))
        names = []
        values = []
        errs = []
        for name, series in paired_series:
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
        plt.title("Adaptive-treatment paired miss-ratio deltas")
        plt.xticks(rotation=12, ha="right")
        plt.tight_layout()
        plt.savefig(output / "paired_miss_deltas.png", dpi=200)
        plt.close()

    for series_with_names, ylabel, title, file_name, reference in (
        (
            paired_p99_series,
            "Paired P99 latency delta (us)",
            "Adaptive-treatment P99 deltas versus MLO baselines",
            "paired_p99_deltas.png",
            0.0,
        ),
        (
            paired_airtime_series,
            "Total sender PHY TX increase versus MLO",
            "Adaptive-treatment total airtime cost versus MLO baselines",
            "paired_total_airtime_increase_vs_mlo.png",
            0.20,
        ),
    ):
        populated = [(name, series) for name, series in series_with_names if series]
        if not populated:
            continue
        intervals = [_interval(series) for _, series in populated]
        plt.figure(figsize=(8, 4.8))
        means = [item[0] for item in intervals]
        errors = np.asarray([
            [item[1] for item in intervals],
            [item[2] for item in intervals],
        ])
        plt.bar([name for name, _ in populated], means, yerr=errors, capsize=4)
        plt.axhline(reference, color="black", linewidth=0.8, linestyle="--")
        plt.ylabel(ylabel)
        plt.title(title)
        plt.xticks(rotation=12, ha="right")
        plt.tight_layout()
        plt.savefig(output / file_name, dpi=200)
        plt.close()

    airtime_labels = [
        label for label in labels
        if any(row["secondary_airtime_fraction"] > 0 for row in by_label[label])
        or label in {
            "Selective 0.20", "Adaptive airtime", "Adaptive deficit",
            "Full duplication",
        }
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

    for metric, ylabel, title, file_name in (
        (
            "deadline_miss_ratio",
            "Deadline miss ratio",
            "Reliability versus total sender PHY TX airtime",
            "reliability_vs_total_airtime.png",
        ),
        (
            "latency_p99_us",
            "P99 frame latency (us)",
            "P99 latency versus total sender PHY TX airtime",
            "p99_vs_total_airtime.png",
        ),
    ):
        plt.figure(figsize=(7, 4.8))
        plotted = False
        for label in labels:
            points = [row for row in by_label[label] if row[metric] is not None]
            if not points:
                continue
            plotted = True
            plt.scatter(
                [float(row["target_phy_tx_fraction"]) for row in points],
                [float(row[metric]) for row in points],
                label=label,
                s=36,
            )
        if plotted:
            plt.xlabel("Total target sender PHY TX fraction")
            plt.ylabel(ylabel)
            plt.title(title)
            plt.legend(fontsize=8)
            plt.tight_layout()
            plt.savefig(output / file_name, dpi=200)
        plt.close()

    if adaptive_by_policy:
        stage_columns = sorted(
            column for column in rows[0] if column.startswith("actions_stage_")
        )
        stage_labels = [column.removeprefix("actions_stage_") for column in stage_columns]
        multiple_adaptive_policies = len(adaptive_by_policy) > 1
        for policy, policy_rows in adaptive_by_policy.items():
            suffix = f"_{policy}" if multiple_adaptive_policies else ""
            label = _policy_label(policy, "dual_interface")
            stage_counts = [
                sum(int(row[column]) for row in policy_rows) for column in stage_columns
            ]
            if stage_columns:
                plt.figure(figsize=(7, 4.8))
                plt.bar(stage_labels, stage_counts)
                plt.xlabel("Action stage")
                plt.ylabel("Actions across all runs")
                plt.title(f"{label} action stage distribution")
                plt.tight_layout()
                plt.savefig(output / f"action_stage_distribution{suffix}.png", dpi=200)
                plt.close()

            first_run = min(policy_rows, key=_pair_key)
            first = Path(first_run["run_dir"]) / "adaptive_airtime_decisions.csv"
            if first.is_file():
                decisions = _read_csv(first)
                times = [float(row["sample_time_ns"]) / 1e9 for row in decisions
                         if row["sample_stage"] == "T0"]
                prices = [_dual_shadow_price(row) for row in decisions
                          if row["sample_stage"] == "T0"]
                balances = [float(row["bucket_balance_us"]) for row in decisions
                            if row["sample_stage"] == "T0"]
                if times:
                    fig, ax1 = plt.subplots(figsize=(8, 4.8))
                    ax1.plot(times, prices, color="tab:blue", label="dual shadow price")
                    ax1.set_xlabel("Simulation time (s)")
                    ax1.set_ylabel("Shadow price", color="tab:blue")
                    ax2 = ax1.twinx()
                    ax2.plot(times, balances, color="tab:orange", label="bucket balance")
                    ax2.set_ylabel("Bucket balance (us)", color="tab:orange")
                    fig.suptitle(label)
                    fig.tight_layout()
                    fig.savefig(
                        output / f"shadow_price_bucket_timeline{suffix}.png",
                        dpi=200,
                    )
                    plt.close(fig)

            estimated: list[float] = []
            measured: list[float] = []
            for adaptive_run in policy_rows:
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
                plt.title(f"{label} airtime estimate calibration")
                plt.legend()
                plt.tight_layout()
                plt.savefig(output / f"estimated_vs_measured_airtime{suffix}.png", dpi=200)
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
