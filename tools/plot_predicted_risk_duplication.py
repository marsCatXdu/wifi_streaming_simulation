#!/usr/bin/env python3
"""Plot calibrated-risk PDF and CDF for launched selective duplications."""

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


def discover_runs(result_root: Path) -> list[Path]:
    """Discover complete run directories without modifying result data."""
    manifest_path = result_root / "experiment_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        runs = manifest.get("runs")
        if not isinstance(runs, list):
            raise ValueError(f"{manifest_path}: runs must be a list")
        result = []
        for item in runs:
            if not isinstance(item, dict) or item.get("status") != "complete":
                raise ValueError(f"{manifest_path}: all run entries must be complete")
            directory = item.get("directory")
            if not isinstance(directory, str) or not directory:
                raise ValueError(f"{manifest_path}: run directory is missing")
            result.append(result_root / directory)
        return result
    return sorted(path.parent for path in result_root.glob("*/summary.json"))


def load_action_risks(result_root: Path) -> dict[str, Any]:
    """Load one calibrated probability per launched secondary copy."""
    probabilities: list[float] = []
    thresholds: set[float] = set()
    selective_runs = 0
    generated_frames = 0
    suppressions = 0
    for run_dir in discover_runs(result_root):
        config_path = run_dir / "resolved_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("policy") != "selective_duplication":
            continue
        selective_runs += 1
        selective = config.get("selectiveDuplication", {})
        threshold = float(selective["probability_threshold"])
        thresholds.add(threshold)
        decisions_path = run_dir / "selective_duplication_decisions.csv"
        with decisions_path.open(newline="", encoding="utf-8") as source:
            decisions = list(csv.DictReader(source))
        frame_ids = {row["frame_id"] for row in decisions}
        generated_frames += len(frame_ids)
        for row in decisions:
            decision = row["decision"]
            if decision == "action":
                probability = float(row["calibrated_probability"])
                if not 0 <= probability <= 1:
                    raise ValueError(
                        f"{decisions_path}: probability outside [0, 1]"
                    )
                probabilities.append(probability)
            elif decision == "budget_suppressed":
                suppressions += 1
    if selective_runs == 0:
        raise ValueError(f"{result_root}: no selective-duplication runs")
    if len(thresholds) != 1:
        raise ValueError(f"{result_root}: selective runs use mixed thresholds")
    if not probabilities:
        raise ValueError(f"{result_root}: no launched selective duplications")
    values = np.asarray(probabilities, dtype=float)
    return {
        "values": values,
        "threshold": next(iter(thresholds)),
        "selective_runs": selective_runs,
        "generated_frames": generated_frames,
        "action_count": len(probabilities),
        "action_rate": len(probabilities) / generated_frames,
        "budget_suppressions": suppressions,
        "minimum": float(np.min(values)),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "p95": float(np.quantile(values, 0.95)),
        "maximum": float(np.max(values)),
    }


def plot(
    result_roots: list[Path],
    labels: list[str],
    output_dir: Path,
) -> None:
    """Write comparison plots and a machine-readable summary."""
    if len(result_roots) != len(labels):
        raise ValueError("the number of labels must match the number of result roots")
    output_dir.mkdir(parents=True, exist_ok=False)
    loaded = [
        (label, root.resolve(), load_action_risks(root.resolve()))
        for label, root in zip(labels, result_roots)
    ]

    plt.figure(figsize=(8, 5))
    for label, _, data in loaded:
        values = np.sort(data["values"])
        probabilities = np.arange(1, len(values) + 1, dtype=float) / len(values)
        plt.step(values, probabilities, where="post", label=f"{label} (n={len(values)})")
    for threshold in sorted({data["threshold"] for _, _, data in loaded}):
        plt.axvline(
            threshold,
            color="black",
            linestyle="--",
            alpha=0.6,
            label=f"threshold={threshold:g}",
        )
    plt.xlabel("Calibrated predicted deadline-miss risk")
    plt.ylabel("Empirical CDF")
    plt.xlim(0, 1)
    plt.ylim(0, 1)
    plt.grid(alpha=0.25)
    plt.legend(fontsize="small")
    plt.title("Predicted risk at launched duplication decisions")
    plt.tight_layout()
    plt.savefig(output_dir / "predicted_risk_duplication_cdf.png", dpi=200)
    plt.close()

    bins = np.linspace(0, 1, 51)
    centers = (bins[:-1] + bins[1:]) / 2
    plt.figure(figsize=(8, 5))
    for label, _, data in loaded:
        density = np.histogram(data["values"], bins=bins, density=True)[0]
        plt.step(centers, density, where="mid", label=f"{label} (n={data['action_count']})")
    for threshold in sorted({data["threshold"] for _, _, data in loaded}):
        plt.axvline(
            threshold,
            color="black",
            linestyle="--",
            alpha=0.6,
            label=f"threshold={threshold:g}",
        )
    plt.xlabel("Calibrated predicted deadline-miss risk")
    plt.ylabel("Probability density")
    plt.xlim(0, 1)
    plt.grid(alpha=0.25)
    plt.legend(fontsize="small")
    plt.title("Predicted risk density at launched duplication decisions")
    plt.tight_layout()
    plt.savefig(output_dir / "predicted_risk_duplication_pdf.png", dpi=200)
    plt.close()

    summary = {
        "schema_version": 1,
        "definition": (
            "One calibrated_probability value from each decision row whose "
            "decision is action; each value represents a successfully launched "
            "secondary frame copy."
        ),
        "experiments": [
            {
                "label": label,
                "result_root": str(root),
                **{key: value for key, value in data.items() if key != "values"},
            }
            for label, root, data in loaded
        ],
    }
    (output_dir / "predicted_risk_duplication_summary.json").write_text(
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
