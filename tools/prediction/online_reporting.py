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
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["scenario_name"] == "__all_selected__"
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
) -> None:
    """Plot recall for every fixed threshold and causal budget."""
    selected = _primary_rows(rows, split_role, budget_kind)
    thresholds = sorted({float(row["probability_threshold"]) for row in selected})
    budgets = sorted({float(row["budget"]) for row in selected})
    if not thresholds or not budgets:
        return
    lookup = {
        (float(row["probability_threshold"]), float(row["budget"])): row["recall"]
        for row in selected
    }
    matrix = np.asarray(
        [[lookup.get((threshold, budget), np.nan) for threshold in thresholds] for budget in budgets]
    )
    fig, axis = plt.subplots(figsize=(9, 6))
    image = axis.imshow(matrix, vmin=0, vmax=1, aspect="auto", origin="lower")
    axis.set_xticks(range(len(thresholds)), [f"{item:g}" for item in thresholds])
    axis.set_yticks(range(len(budgets)), [f"{100 * item:g}%" for item in budgets])
    axis.set_xlabel("Calibrated miss-probability threshold")
    axis.set_ylabel(f"{budget_kind.capitalize()} action budget")
    axis.set_title(
        f"5 GHz online miss recall: {split_role.replace('_', ' ')}"
    )
    for row, budget in enumerate(budgets):
        for column, threshold in enumerate(thresholds):
            value = lookup.get((threshold, budget))
            if value is not None:
                axis.text(column, row, f"{value:.2f}", ha="center", va="center")
    fig.colorbar(image, ax=axis, label="Deadline-miss recall")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_recall_resource_tradeoff(
    path: Path,
    rows: list[dict[str, Any]],
    split_role: str,
) -> None:
    """Plot realized action use against detected misses."""
    selected = _primary_rows(rows, split_role, "frames")
    fig, axis = plt.subplots(figsize=(8, 6))
    for budget in sorted({float(row["budget"]) for row in selected}):
        line = sorted(
            (row for row in selected if float(row["budget"]) == budget),
            key=lambda row: float(row["probability_threshold"]),
        )
        axis.plot(
            [row["realized_action_rate"] for row in line],
            [row["recall"] for row in line],
            marker="o",
            label=f"{100 * budget:g}% budget",
        )
    axis.set_xlabel("Realized fraction of frames receiving action")
    axis.set_ylabel("Deadline-miss recall")
    axis.set_xlim(left=0)
    axis.set_ylim(0, 1)
    axis.grid(True, alpha=0.3)
    axis.legend(fontsize="small", ncol=2)
    axis.set_title(f"5 GHz online warning trade-off: {split_role.replace('_', ' ')}")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_warning_lead_cdf(path: Path, audit_rows: list[dict[str, Any]]) -> None:
    """Plot warning lead time for representative successful actions."""
    fig, axis = plt.subplots(figsize=(8, 5))
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
    axis.legend()
    axis.set_title("5 GHz online warning lead time")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_replay_report(
    path: Path,
    rows: list[dict[str, Any]],
    run_count: int,
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
        "| Split | Budget type | Recall | Precision | Action rate | Byte overhead |",
        "|---|---|---:|---:|---:|---:|",
    ]
    representative = [
        row
        for row in rows
        if row["scenario_name"] == "__all_selected__"
        and row["pipeline_id"] == "commodity_polling_1ms"
        and row["decision_policy"] == "sequential"
        and float(row["probability_threshold"]) == 0.2
        and float(row["budget"]) == 0.1
    ]
    for row in sorted(representative, key=lambda item: (item["split_role"], item["budget_kind"])):
        values = [
            row["split_role"].replace("_", " "),
            row["budget_kind"],
            row["recall"],
            row["precision"],
            row["realized_action_rate"],
            row["realized_byte_overhead"],
        ]
        lines.append(
            f"| {values[0]} | {values[1]} | "
            + " | ".join("" if value is None else f"{float(value):.4f}" for value in values[2:])
            + " |"
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
