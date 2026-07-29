#!/usr/bin/env python3
"""Plot all observed calibrated-risk predictions by decision stage."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from plot_predicted_risk_duplication import discover_runs

STAGES = ("T0", "T1", "T2", "T4")
LINE_STYLES = {"T0": "-", "T1": "--", "T2": "-.", "T4": ":"}


def load_stage_risks(result_root: Path) -> dict[str, Any]:
    """Load every logged model probability, including non-actions."""
    values: dict[str, list[float]] = {stage: [] for stage in STAGES}
    thresholds: set[float] = set()
    selective_runs = 0
    decision_counts: dict[str, int] = {}
    for run_dir in discover_runs(result_root):
        config = json.loads(
            (run_dir / "resolved_config.json").read_text(encoding="utf-8")
        )
        if config.get("policy") != "selective_duplication":
            continue
        selective_runs += 1
        thresholds.add(
            float(config["selectiveDuplication"]["probability_threshold"])
        )
        with (run_dir / "selective_duplication_decisions.csv").open(
            newline="", encoding="utf-8"
        ) as source:
            for row in csv.DictReader(source):
                stage = row["sample_stage"]
                if stage not in values:
                    raise ValueError(f"{run_dir}: unknown decision stage {stage}")
                probability = float(row["calibrated_probability"])
                if not np.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError(f"{run_dir}: probability outside [0, 1]")
                values[stage].append(probability)
                decision = row["decision"]
                decision_counts[decision] = decision_counts.get(decision, 0) + 1
    if selective_runs == 0:
        raise ValueError(f"{result_root}: no selective-duplication runs")
    if len(thresholds) != 1:
        raise ValueError(f"{result_root}: selective runs use mixed thresholds")
    arrays = {
        stage: np.asarray(stage_values, dtype=float)
        for stage, stage_values in values.items()
    }
    if any(array.size == 0 for array in arrays.values()):
        raise ValueError(f"{result_root}: one or more stages have no predictions")
    return {
        "values": arrays,
        "threshold": next(iter(thresholds)),
        "selective_runs": selective_runs,
        "decision_counts": decision_counts,
    }


def statistics(values: np.ndarray) -> dict[str, float | int]:
    """Return compact descriptive statistics for one distribution."""
    return {
        "count": int(values.size),
        "minimum": float(np.min(values)),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p95": float(np.quantile(values, 0.95)),
        "maximum": float(np.max(values)),
    }


def plot(result_roots: list[Path], labels: list[str], output_dir: Path) -> None:
    """Write overwrite-safe unconditional risk plots and summary."""
    if len(result_roots) != len(labels):
        raise ValueError("the number of labels must match the number of result roots")
    output_dir.mkdir(parents=True, exist_ok=False)
    loaded = [
        (label, root.resolve(), load_stage_risks(root.resolve()))
        for label, root in zip(labels, result_roots)
    ]
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    color_by_label = {
        label: colors[index % len(colors)]
        for index, (label, _, _) in enumerate(loaded)
    }

    plt.figure(figsize=(10, 6))
    for label, _, data in loaded:
        for stage in STAGES:
            values = np.sort(data["values"][stage])
            cumulative = np.arange(1, len(values) + 1, dtype=float) / len(values)
            plt.step(
                values,
                cumulative,
                where="post",
                color=color_by_label[label],
                linestyle=LINE_STYLES[stage],
                label=f"{label} / {stage} (n={len(values)})",
            )
    for threshold in sorted({data["threshold"] for _, _, data in loaded}):
        plt.axvline(
            threshold,
            color="black",
            linestyle=(0, (3, 2)),
            alpha=0.6,
            label=f"current threshold={threshold:g}",
        )
    plt.xlabel("Calibrated predicted deadline-miss risk")
    plt.ylabel("Empirical CDF")
    plt.xlim(0, 1)
    plt.ylim(0, 1)
    plt.grid(alpha=0.25)
    plt.legend(fontsize="x-small", ncol=2)
    plt.title("All observed predicted risks by decision stage")
    plt.tight_layout()
    plt.savefig(output_dir / "predicted_risk_unconditional_cdf.png", dpi=200)
    plt.close()

    bins = np.linspace(0, 1, 51)
    centers = (bins[:-1] + bins[1:]) / 2
    plt.figure(figsize=(10, 6))
    for label, _, data in loaded:
        for stage in STAGES:
            density = np.histogram(data["values"][stage], bins=bins, density=True)[0]
            plt.step(
                centers,
                density,
                where="mid",
                color=color_by_label[label],
                linestyle=LINE_STYLES[stage],
                label=f"{label} / {stage}",
            )
    for threshold in sorted({data["threshold"] for _, _, data in loaded}):
        plt.axvline(
            threshold,
            color="black",
            linestyle=(0, (3, 2)),
            alpha=0.6,
            label=f"current threshold={threshold:g}",
        )
    plt.xlabel("Calibrated predicted deadline-miss risk")
    plt.ylabel("Probability density")
    plt.xlim(0, 1)
    plt.grid(alpha=0.25)
    plt.legend(fontsize="x-small", ncol=2)
    plt.title("All observed predicted-risk densities by decision stage")
    plt.tight_layout()
    plt.savefig(output_dir / "predicted_risk_unconditional_pdf.png", dpi=200)
    plt.close()

    summary = {
        "schema_version": 1,
        "definition": (
            "Every calibrated_probability logged at T0, T1, T2, or T4, "
            "without conditioning on action, actionability, threshold crossing, "
            "or token availability."
        ),
        "counterfactual_warning": (
            "Scores after an earlier launched duplication are observed under the "
            "closed-loop action and are not no-action counterfactual predictions."
        ),
        "experiments": [
            {
                "label": label,
                "result_root": str(root),
                "threshold": data["threshold"],
                "selective_runs": data["selective_runs"],
                "decision_counts": data["decision_counts"],
                "stages": {
                    stage: statistics(data["values"][stage]) for stage in STAGES
                },
            }
            for label, root, data in loaded
        ],
    }
    (output_dir / "predicted_risk_unconditional_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_roots", nargs="+", type=Path)
    parser.add_argument("--labels", nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    plot(args.result_roots, args.labels, args.output_dir)
    print(f"WROTE {args.output_dir} plots=2")


if __name__ == "__main__":
    main()
