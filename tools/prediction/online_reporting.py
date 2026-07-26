"""Plots and concise reporting for causal online-risk replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _primary_rows(
    rows: list[dict[str, Any]],
    split_role: str,
    budget_kind: str,
    scenario_name: str = "__all_selected__",
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["scenario_name"] == scenario_name
        and row["split_role"] == split_role
        and row["pipeline_id"] == "commodity_polling_1ms"
        and row["decision_policy"] == "sequential"
        and row["budget_kind"] == budget_kind
    ]


def plot_recall_heatmap(
    path: Path,
    rows: list[dict[str, Any]],
    split_role: str,
    budget_kind: str,
    metric: str = "recall",
    scenario_name: str = "__all_selected__",
) -> None:
    """Plot one detection metric for every fixed threshold and causal budget."""
    selected = _primary_rows(rows, split_role, budget_kind, scenario_name)
    thresholds = sorted({float(row["probability_threshold"]) for row in selected})
    budgets = sorted({float(row["budget"]) for row in selected})
    if not thresholds or not budgets:
        return
    lookup = {
        (float(row["probability_threshold"]), float(row["budget"])): row[metric]
        for row in selected
    }
    matrix = np.asarray(
        [
            [
                np.nan
                if lookup.get((threshold, budget)) is None
                else lookup[(threshold, budget)]
                for threshold in thresholds
            ]
            for budget in budgets
        ],
        dtype=float,
    )
    fig, axis = plt.subplots(figsize=(9, 6))
    image = axis.imshow(matrix, vmin=0, vmax=1, aspect="auto", origin="lower")
    axis.set_xticks(range(len(thresholds)), [f"{item:g}" for item in thresholds])
    axis.set_yticks(range(len(budgets)), [f"{100 * item:g}%" for item in budgets])
    axis.set_xlabel("Calibrated miss-probability threshold")
    axis.set_ylabel(f"{budget_kind.capitalize()} action budget")
    axis.set_title(
        f"5 GHz online {metric.replace('_', ' ')}: "
        f"{split_role.replace('_', ' ')} / {scenario_name.replace('_', ' ')}"
    )
    for row, budget in enumerate(budgets):
        for column, threshold in enumerate(thresholds):
            value = lookup.get((threshold, budget))
            if value is not None:
                axis.text(column, row, f"{value:.2f}", ha="center", va="center")
    fig.colorbar(image, ax=axis, label=metric.replace("_", " ").title())
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_recall_resource_tradeoff(
    path: Path,
    rows: list[dict[str, Any]],
    split_role: str,
    budget_kind: str,
    scenario_name: str = "__all_selected__",
) -> None:
    """Plot realized action use against detected misses."""
    selected = _primary_rows(rows, split_role, budget_kind, scenario_name)
    if not selected:
        return
    resource_field = (
        "realized_action_rate"
        if budget_kind == "frames"
        else "realized_byte_overhead"
    )
    fig, axis = plt.subplots(figsize=(8, 6))
    for budget in sorted({float(row["budget"]) for row in selected}):
        line = sorted(
            (row for row in selected if float(row["budget"]) == budget),
            key=lambda row: float(row["probability_threshold"]),
        )
        axis.plot(
            [row[resource_field] for row in line],
            [row["recall"] for row in line],
            marker="o",
            label=f"{100 * budget:g}% budget",
        )
    axis.set_xlabel(
        "Realized fraction of frames receiving action"
        if budget_kind == "frames"
        else "Realized duplicated bytes / source bytes"
    )
    axis.set_ylabel("Deadline-miss recall")
    axis.set_xlim(left=0)
    axis.set_ylim(0, 1)
    axis.grid(True, alpha=0.3)
    axis.legend(fontsize="small", ncol=2)
    axis.set_title(
        f"5 GHz online warning trade-off: {split_role.replace('_', ' ')} / "
        f"{scenario_name.replace('_', ' ')}"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_miss_outcomes(
    path: Path,
    rows: list[dict[str, Any]],
    split_role: str,
    budget_kind: str,
    scenario_name: str = "__all_selected__",
) -> None:
    """Separate detected, budget-suppressed, and low-score misses."""
    selected = [
        row
        for row in _primary_rows(rows, split_role, budget_kind, scenario_name)
        if float(row["probability_threshold"]) == 0.2
    ]
    selected.sort(key=lambda row: float(row["budget"]))
    if not selected:
        return
    budgets = [100 * float(row["budget"]) for row in selected]
    misses = np.asarray([row["eligible_misses"] for row in selected], dtype=float)
    components = {
        "warned": np.asarray(
            [row["true_positive_actions"] for row in selected], dtype=float
        ),
        "budget suppressed": np.asarray(
            [row["budget_suppressed_misses"] for row in selected], dtype=float
        ),
        "below threshold": np.asarray(
            [row["threshold_negative_misses"] for row in selected], dtype=float
        ),
    }
    fig, axis = plt.subplots(figsize=(8, 5))
    bottom = np.zeros(len(selected))
    for label, values in components.items():
        fractions = np.divide(values, misses, out=np.zeros_like(values), where=misses > 0)
        axis.bar([f"{item:g}%" for item in budgets], fractions, bottom=bottom, label=label)
        bottom += fractions
    axis.set_xlabel(f"{budget_kind.capitalize()} action budget")
    axis.set_ylabel("Fraction of recorded deadline misses")
    axis.set_ylim(0, 1)
    axis.legend()
    axis.set_title(
        f"5 GHz miss disposition at risk threshold 0.2: "
        f"{split_role.replace('_', ' ')} / {scenario_name.replace('_', ' ')}"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_warning_lead_cdf(path: Path, audit_rows: list[dict[str, Any]]) -> None:
    """Plot warning lead time for representative successful actions."""
    fig, axis = plt.subplots(figsize=(8, 5))
    plotted = False
    for role in sorted({row["split_role"] for row in audit_rows}):
        values = sorted(
            float(row["warning_lead_time_us"]) / 1000
            for row in audit_rows
            if row["split_role"] == role
            and row["pipeline_id"] == "commodity_polling_1ms"
            and row["decision_policy"] == "sequential"
            and row["budget_kind"] == "frames"
            and float(row["budget"]) == 0.1
            and float(row["probability_threshold"]) == 0.2
            and row["decision"] == "action"
        )
        if values:
            plotted = True
            axis.step(
                values,
                np.arange(1, len(values) + 1) / len(values),
                where="post",
                label=role.replace("_", " "),
            )
    axis.set_xlabel("Warning lead time (ms)")
    axis.set_ylabel("CDF")
    axis.set_xlim(left=0)
    axis.set_ylim(0, 1)
    axis.grid(True, alpha=0.3)
    if plotted:
        axis.legend()
    axis.set_title("5 GHz online warning lead time")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_replay_report(
    path: Path,
    rows: list[dict[str, Any]],
    run_count: int,
    upper_bounds: list[dict[str, Any]],
) -> None:
    """Write a compact report without claiming hypothetical actions succeed."""
    lines = [
        "# Causal 5 GHz latency-risk replay",
        "",
        f"Individual runs replayed: {run_count}",
        "",
        "Frames were processed in generation order. Each model used only telemetry",
        "available at its decision stage, a fixed calibrated probability threshold,",
        "and causal frame or byte credits. Recorded outcomes were never changed.",
        "",
        "## Representative operating point",
        "",
        "| Split | Scenario | Budget type | Recall | Precision | Action rate | Byte overhead |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    representative = [
        row
        for row in rows
        if row["scenario_name"] == "__all_selected__"
        or row["split_role"] == "out_of_distribution_test"
        and row["scenario_name"] != "__all_selected__"
    ]
    representative = [
        row
        for row in representative
        if row["pipeline_id"] == "commodity_polling_1ms"
        and row["decision_policy"] == "sequential"
        and float(row["probability_threshold"]) == 0.2
        and float(row["budget"]) == 0.1
    ]
    for row in sorted(
        representative,
        key=lambda item: (
            item["split_role"],
            item["scenario_name"],
            item["budget_kind"],
        ),
    ):
        scenario_label = (
            "all selected"
            if row["scenario_name"] == "__all_selected__"
            else row["scenario_name"].replace("_", " ")
        )
        values = [
            row["split_role"].replace("_", " "),
            scenario_label,
            row["budget_kind"],
            row["recall"],
            row["precision"],
            row["realized_action_rate"],
            row["realized_byte_overhead"],
        ]
        lines.append(
            f"| {values[0]} | {values[1]} | {values[2]} | "
            + " | ".join("" if value is None else f"{float(value):.4f}" for value in values[3:])
            + " |"
        )
    lines += [
        "",
        "This table uses the predeclared probability threshold 0.2 and budget 10%.",
        "The heatmaps report every threshold and budget combination; no test-set",
        "operating point is promoted as a newly tuned deployment threshold.",
        "",
        "## Offline ranking upper bound",
        "",
        "| Split | Stage | Global Top-10% recall |",
        "|---|---|---:|",
    ]
    for row in upper_bounds:
        if (
            row["scenario_name"] == "__all_selected__"
            and float(row["budget"]) == 0.1
            and row["stage"] in {"T0", "T1"}
        ):
            recall = row["recall"]
            lines.append(
                f"| {row['split_role'].replace('_', ' ')} | {row['stage']} | "
                f"{'' if recall is None else f'{float(recall):.4f}'} |"
            )
    lines += [
        "",
        "## Interpretation boundary",
        "",
        "Recall is the fraction of recorded deadline misses that received an early",
        "warning and budget permission. It is not the fraction of misses that would",
        "be prevented. Measuring prevention requires a later closed-loop simulation.",
        "",
        "Global Top-K values from the offline evaluation remain an optimistic upper",
        "bound because they rank a complete future population. This replay does not.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
