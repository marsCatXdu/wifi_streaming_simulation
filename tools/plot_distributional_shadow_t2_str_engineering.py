#!/usr/bin/env python3
"""Plot the distributional shadow-T2 engineering qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze_distributional_shadow_t2_str_engineering import (
    ANALYSIS_ID,
    DISTRIBUTIONAL_SHADOW_T2_STATUSES,
    QualificationError,
)


POLICY_COLOR = "#4c78a8"
BASELINE_COLOR = "#6f6f6f"
FAVORABLE_COLOR = "#00796b"
UNFAVORABLE_COLOR = "#c44536"
STATUS_LABELS = {
    "outside_decision_window": "Outside window",
    "history_warmup": "History warmup",
    "not_actionable": "Not actionable",
    "frame_type_restricted": "I-frame restricted",
    "descriptor_unavailable": "Descriptor unavailable",
    "nonpositive_reward": "Nonpositive reward",
    "opportunity_price_rejected": "Shadow-price rejected",
    "horizon_credit_rejected": "Credit rejected",
    "launch_rejected": "Launch rejected",
    "action": "Action",
}
ACTION_LABELS = {
    "primary_on_time_accelerated": "On-time primary, accelerated",
    "primary_on_time_no_completion_benefit": "On-time primary, no benefit",
    "primary_miss_rescued": "Primary miss rescued",
    "primary_miss_late_or_incomplete": "Primary miss still late/incomplete",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{path}: expected a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inputs(report_path: Path, diagnostics_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    report = _read_json(report_path)
    diagnostics = _read_json(diagnostics_path)
    rows = report.get("paired_metrics")
    if (
        report.get("schema_version") != 1
        or report.get("analysis") != ANALYSIS_ID
        or report.get("paired_unit_count") != 48
        or not isinstance(rows, list)
        or len(rows) != 48
        or diagnostics.get("schema_version") != 1
        or diagnostics.get("analysis")
        != "distributional_shadow_t2_policy_diagnostics_v1"
        or diagnostics.get("source_report_analysis") != ANALYSIS_ID
    ):
        raise QualificationError("plot inputs do not form the frozen engineering report")
    return report, diagnostics


def _paired_delta_plot(report: dict[str, Any], output: Path) -> None:
    rows = report["paired_metrics"]
    seeds = np.asarray([row["seed"] for row in rows], dtype=int)
    miss_delta = np.asarray(
        [row["miss_delta_percentage_points"] for row in rows], dtype=float
    )
    p99_delta = np.asarray([row["completed_p99_delta_us"] for row in rows]) / 1000.0
    comparison = report["comparison_against_str"]
    miss_interval = comparison["deadline_miss_rate"]
    p99_interval = comparison["completed_p99_us"]
    figure, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for axis, values, interval, scale, ylabel in (
        (
            axes[0],
            miss_delta,
            miss_interval,
            100.0,
            "Miss-rate delta (percentage points)",
        ),
        (axes[1], p99_delta, p99_interval, 0.001, "P99 delta (ms)"),
    ):
        colors = [FAVORABLE_COLOR if value < 0 else UNFAVORABLE_COLOR for value in values]
        axis.scatter(seeds, values, c=colors, s=30, zorder=3)
        axis.axhline(0, color="black", linewidth=1)
        low = scale * interval["ci95_low"]
        high = scale * interval["ci95_high"]
        axis.axhspan(low, high, color=POLICY_COLOR, alpha=0.16)
        axis.axhline(scale * interval["estimate"], color=POLICY_COLOR, linewidth=2)
        axis.set_ylabel(ylabel)
        axis.grid(alpha=0.25)
    axes[1].set_xlabel("Matched seed")
    figure.suptitle(
        "Distributional shadow T2 minus STR MLO\n"
        "Negative values favor the selective-duplication policy"
    )
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def _tradeoff_plot(report: dict[str, Any], output: Path) -> None:
    rows = report["paired_metrics"]
    x_values = np.asarray(
        [row["miss_delta_percentage_points"] for row in rows], dtype=float
    )
    y_values = np.asarray([row["completed_p99_delta_us"] for row in rows]) / 1000.0
    colors = [
        FAVORABLE_COLOR if x_value < 0 and y_value < 0 else UNFAVORABLE_COLOR
        for x_value, y_value in zip(x_values, y_values)
    ]
    figure, axis = plt.subplots(figsize=(8, 6))
    axis.scatter(x_values, y_values, c=colors, s=42, alpha=0.85)
    axis.axvline(0, color="black", linewidth=1)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xlabel("Miss-rate delta (percentage points)")
    axis.set_ylabel("Completed-P99 delta (ms)")
    axis.set_title("Matched-seed performance tradeoff\nLower left favors the policy")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def _resource_plot(report: dict[str, Any], output: Path) -> None:
    rows = report["paired_metrics"]
    seeds = np.asarray([row["seed"] for row in rows], dtype=int)
    airtime = np.asarray([row["sender_airtime_ratio"] for row in rows], dtype=float)
    background = 100.0 * np.asarray(
        [row["background_throughput_loss"] for row in rows], dtype=float
    )
    comparison = report["comparison_against_str"]
    figure, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    axes[0].scatter(seeds, airtime, color=POLICY_COLOR, s=28)
    axes[0].axhline(1.20, color=UNFAVORABLE_COLOR, linestyle="--", label="Upper bound")
    ratio = comparison["sender_airtime_ratio"]
    axes[0].axhspan(ratio["ci95_low"], ratio["ci95_high"], color=POLICY_COLOR, alpha=0.16)
    axes[0].axhline(ratio["estimate"], color=POLICY_COLOR, linewidth=2)
    axes[0].set_ylabel("Sender-airtime ratio")
    axes[0].legend(fontsize="small")
    axes[1].scatter(seeds, background, color=POLICY_COLOR, s=28)
    axes[1].axhline(1.0, color=UNFAVORABLE_COLOR, linestyle="--", label="Upper bound")
    loss = comparison["background_throughput_loss"]
    axes[1].axhspan(
        100.0 * loss["ci95_low"],
        100.0 * loss["ci95_high"],
        color=POLICY_COLOR,
        alpha=0.16,
    )
    axes[1].axhline(100.0 * loss["estimate"], color=POLICY_COLOR, linewidth=2)
    axes[1].set_ylabel("Background loss (%)")
    axes[1].set_xlabel("Matched seed")
    axes[1].legend(fontsize="small")
    for axis in axes:
        axis.grid(alpha=0.25)
    figure.suptitle("Resource noninferiority gates")
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def _policy_diagnostic_plot(diagnostics: dict[str, Any], output: Path) -> None:
    terminal = diagnostics["terminal_status"]
    active = [status for status in DISTRIBUTIONAL_SHADOW_T2_STATUSES if terminal[status]["frames"]]
    labels = [STATUS_LABELS[status] for status in active]
    frame_counts = [terminal[status]["frames"] for status in active]
    miss_rates = [100.0 * terminal[status]["primary_miss_rate"] for status in active]
    final_misses = [terminal[status]["final_union_misses"] for status in active]
    positions = np.arange(len(active))
    figure, axes = plt.subplots(1, 3, figsize=(16, 6))
    axes[0].barh(positions, frame_counts, color=BASELINE_COLOR)
    axes[0].set_xscale("log")
    axes[0].set_xlabel("Frames (log scale)")
    axes[1].barh(positions, miss_rates, color=POLICY_COLOR)
    axes[1].set_xlabel("Primary-copy miss rate (%)")
    axes[2].barh(positions, final_misses, color=UNFAVORABLE_COLOR)
    axes[2].set_xlabel("Final union misses")
    for axis in axes:
        axis.set_yticks(positions, labels)
        axis.invert_yaxis()
        axis.grid(axis="x", alpha=0.25)
    figure.suptitle("Distributional policy terminal decisions and realized risk")
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def _allocator_plot(diagnostics: dict[str, Any], output: Path) -> None:
    allocation = diagnostics["allocation"]
    actions = np.asarray(allocation["actions_by_time_bin"], dtype=float)
    reservations = np.asarray(
        allocation["canonical_reservation_us_by_time_bin"], dtype=float
    ) / 48_000.0
    time_labels = [f"{5 * index}-{5 * (index + 1)}" for index in range(12)]
    regimes = np.asarray(allocation["actions_by_congestion_regime"], dtype=float)
    outcomes = diagnostics["action_outcomes"]
    outcome_keys = [key for key in ACTION_LABELS if key in outcomes]
    figure, axes = plt.subplots(1, 3, figsize=(17, 5.5))
    axes[0].bar(np.arange(12), actions, color=POLICY_COLOR)
    axes[0].set_xticks(np.arange(12), time_labels, rotation=45, ha="right")
    axes[0].set_xlabel("Decision time (s)")
    axes[0].set_ylabel("Actions across 48 runs")
    axes[1].bar(np.arange(3), regimes, color=["#72b7b2", "#f2cf5b", "#e45756"])
    axes[1].set_xticks(np.arange(3), ["Low", "Middle", "High"])
    axes[1].set_xlabel("Causal congestion regime")
    axes[1].set_ylabel("Actions across 48 runs")
    axes[2].barh(
        np.arange(len(outcome_keys)),
        [outcomes[key] for key in outcome_keys],
        color=POLICY_COLOR,
    )
    axes[2].set_yticks(
        np.arange(len(outcome_keys)), [ACTION_LABELS[key] for key in outcome_keys]
    )
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Actions")
    for axis in axes:
        axis.grid(alpha=0.25)
    figure.suptitle(
        "Allocator chronology, congestion allocation, and factual action outcomes\n"
        f"Mean canonical reservation per run by time bin: {reservations.sum():.1f} ms total"
    )
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def generate(
    report_path: Path, diagnostics_path: Path, output_directory: Path
) -> dict[str, Any]:
    report, diagnostics = _inputs(report_path, diagnostics_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    figures = {
        "paired_metric_deltas.png": _paired_delta_plot,
        "paired_performance_tradeoff.png": _tradeoff_plot,
        "resource_gates.png": _resource_plot,
    }
    for name, function in figures.items():
        function(report, output_directory / name)
    _policy_diagnostic_plot(
        diagnostics, output_directory / "policy_terminal_diagnostics.png"
    )
    _allocator_plot(diagnostics, output_directory / "allocator_diagnostics.png")
    output_paths = [
        output_directory / name
        for name in (
            *figures,
            "policy_terminal_diagnostics.png",
            "allocator_diagnostics.png",
        )
    ]
    manifest = {
        "schema_version": 1,
        "analysis": "distributional_shadow_t2_str_engineering_plots_v1",
        "source_report": {
            "path": str(report_path.resolve()),
            "sha256": _sha256_file(report_path),
        },
        "source_diagnostics": {
            "path": str(diagnostics_path.resolve()),
            "sha256": _sha256_file(diagnostics_path),
        },
        "figures": [
            {
                "path": path.name,
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in output_paths
        ],
    }
    manifest_path = output_directory / "figure_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        manifest = generate(
            arguments.report.resolve(),
            arguments.diagnostics.resolve(),
            arguments.output_directory.resolve(),
        )
    except QualificationError as error:
        parser.error(str(error))
    print(f"WROTE {arguments.output_directory} figures={len(manifest['figures'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
