"""Deterministic Increment-3 artifact writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str] | None = None) -> None:
    materialized = list(rows)
    if columns is None:
        columns = sorted({key for row in materialized for key in row})
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow(
                {
                    key: (
                        ""
                        if value is None
                        else json.dumps(value, sort_keys=True)
                        if isinstance(value, (dict, list, tuple))
                        else value
                    )
                    for key, value in row.items()
                }
            )


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def plot_stage_recall(path: Path, metric_rows: list[dict[str, Any]], budget: float) -> None:
    rows = [
        row
        for row in metric_rows
        if row.get("budget") == budget
        and row.get("partition") == "in_distribution_test"
        and row.get("recall") is not None
        and row.get("pipeline_type") == "model"
        and row.get("analysis") == "per_link"
    ]
    fig, axis = plt.subplots(figsize=(9, 5))
    for link in sorted({row["link"] for row in rows}):
        selected = [row for row in rows if row["link"] == link and row["feature_set"] == "F0+F1-ideal+F2"]
        selected.sort(key=lambda row: row["sample_offset_us"])
        axis.plot(
            [row["stage"] for row in selected],
            [row["recall"] for row in selected],
            marker="o",
            label=f"link {link}",
        )
    axis.set_ylabel(f"Miss recall @ Top {100 * budget:g}%")
    axis.set_xlabel("Stage")
    axis.set_ylim(0, 1)
    axis.grid(True, alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_calibration_curves(path: Path, rows: list[dict[str, Any]]) -> None:
    selected = [
        row
        for row in rows
        if row.get("partition") == "in_distribution_test"
        and row.get("stage") == "T1"
        and row.get("feature_set") == "F0+F1-ideal+F2"
        and row.get("analysis") == "per_link"
    ]
    fig, axis = plt.subplots(figsize=(6, 6))
    axis.plot([0, 1], [0, 1], linestyle="--", color="black", label="ideal")
    for link in sorted({row["link"] for row in selected}):
        link_rows = sorted(
            (row for row in selected if row["link"] == link),
            key=lambda row: int(row["bin"]),
        )
        axis.plot(
            [float(row["mean_probability"]) for row in link_rows],
            [float(row["observed_miss_rate"]) for row in link_rows],
            marker="o",
            label=f"link {link}",
        )
    axis.set_xlabel("Mean calibrated probability")
    axis.set_ylabel("Observed miss rate")
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.grid(True, alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_feature_importance(path: Path, rows: list[dict[str, Any]]) -> None:
    selected = [
        row
        for row in rows
        if row.get("stage") == "T1" and int(row.get("link", -1)) == 0
    ]
    selected.sort(key=lambda row: float(row["permutation_importance_mean"]), reverse=True)
    selected = selected[:15][::-1]
    fig, axis = plt.subplots(figsize=(9, 6))
    axis.barh(
        [row["feature"] for row in selected],
        [float(row["permutation_importance_mean"]) for row in selected],
    )
    axis.set_xlabel("Selection-safe permutation importance (average precision)")
    axis.set_title("T1 link 0")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_report(
    path: Path,
    summary: dict[str, Any],
    selected: list[dict[str, Any]],
    insufficiencies: list[str],
    decision: dict[str, Any] | None = None,
) -> None:
    lines = [
        "# Offline latency-risk prediction evaluation",
        "",
        f"Analysis schema version: {summary['analysis_schema_version']}",
        f"Dataset: `{summary['dataset']}`",
        f"Rows scanned: {summary['rows_scanned']:,}",
        f"Peak RSS: {summary.get('peak_rss_mib', 'unavailable')} MiB",
        f"Runtime: {summary.get('runtime_seconds', 0):.1f} seconds",
        "",
        "## Frozen selections",
        "",
        "| Link | Stage | Feature set | Model |",
        "|---:|---|---|---|",
    ]
    for row in selected:
        lines.append(
            f"| {row['link']} | {row['stage']} | {row['feature_set']} | {row['model']} |"
        )
    if decision is not None:
        lines += [
            "",
            "## Qualified recommendation",
            "",
            f"- Prediction recommendation: `{decision['prediction_recommendation']}`.",
            f"- Modified-driver support: `{decision['modified_driver_supported']}`.",
            "- Per-link outcomes remain separate in `go_no_go.json`.",
        ]
    lines += ["", "## Evidence limitations", ""]
    lines.extend(f"- {item}" for item in insufficiencies)
    lines += [
        "",
        "The ranking-budget cutoff uses the complete evaluated population and is an",
        "upper bound on a fixed online threshold. The packet-count heuristic is an",
        "additive baseline, not an airtime or completion-time estimator under A-MPDU.",
        "No result authorizes an adaptive simulation action.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
