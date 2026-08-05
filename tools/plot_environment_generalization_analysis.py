#!/usr/bin/env python3
"""Plot checksum-closed environment-generalization analysis artifacts."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

import analyze_environment_generalization_lofo as lofo_analysis  # noqa: E402
import analyze_environment_generalization_policy as policy_analysis  # noqa: E402
import environment_generalization_policy as policy  # noqa: E402


PLOT_SCHEMA_VERSION = 1
OUTPUT_FRONTIER = "environment_policy_value_resource_frontier.png"
OUTPUT_FAMILIES = "environment_held_out_family_deadline_miss.png"
OUTPUT_LOFO = "environment_lofo_ood_diagnostics.png"
OUTPUT_ALLOCATION = "environment_oracle_allocation_scores.png"
OUTPUT_REGRET = "environment_fraction_of_oracle_gain.png"
OUTPUT_MANIFEST = "artifact_manifest.json"
PLOT_FILES = (
    OUTPUT_FRONTIER,
    OUTPUT_FAMILIES,
    OUTPUT_LOFO,
    OUTPUT_ALLOCATION,
    OUTPUT_REGRET,
)
LABELS = {
    "no_secondary_copy": "No copy",
    "uniform_random_t2_same_canonical_budget": "Uniform random",
    "myopic_deadline_risk_same_canonical_budget": "Myopic risk",
    "cross_fitted_scenario_resource_oracle_v1": "Resource oracle",
}
COLORS = {
    "no_secondary_copy": "#6b7280",
    "uniform_random_t2_same_canonical_budget": "#60a5fa",
    "myopic_deadline_risk_same_canonical_budget": "#f59e0b",
    "cross_fitted_scenario_resource_oracle_v1": "#10b981",
}


class PlotError(RuntimeError):
    """Raised when source analysis or plot output closure differs."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise PlotError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlotError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise PlotError(f"{path}: expected a JSON object")
    return value


def _validate_manifest(
    directory: Path,
    expected_files: set[str],
) -> dict[str, Any]:
    manifest = _read_json(directory / OUTPUT_MANIFEST)
    if (
        set(manifest)
        != {"manifest_schema_version", "hash_algorithm", "artifacts_sha256"}
        or manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
        or set(manifest.get("artifacts_sha256", {})) != expected_files
    ):
        raise PlotError(f"{directory}: artifact manifest schema differs")
    for name, expected_hash in manifest["artifacts_sha256"].items():
        if (
            not isinstance(expected_hash, str)
            or _sha256(directory / name) != expected_hash
        ):
            raise PlotError(f"{directory}: artifact hash differs: {name}")
    return manifest


def load_inputs(
    lofo_dir: Path | str,
    policy_dir: Path | str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, np.ndarray], dict[str, str]]:
    """Load exact LOFO, policy, and row-action artifacts."""

    lofo_path = Path(lofo_dir).resolve()
    policy_path = Path(policy_dir).resolve()
    lofo_manifest = _validate_manifest(
        lofo_path,
        {lofo_analysis.OUTPUT_PREDICTIONS, lofo_analysis.OUTPUT_METRICS},
    )
    policy_manifest = _validate_manifest(
        policy_path,
        {
            policy_analysis.OUTPUT_METRICS,
            policy_analysis.OUTPUT_REPORT,
            policy_analysis.OUTPUT_FAMILY_CSV,
            policy_analysis.OUTPUT_ACTIONS,
        },
    )
    lofo_metrics = _read_json(lofo_path / lofo_analysis.OUTPUT_METRICS)
    policy_metrics = _read_json(policy_path / policy_analysis.OUTPUT_METRICS)
    if (
        lofo_metrics.get("analysis_id") != "environment-generalization-lofo-v1"
        or policy_metrics.get("analysis_id")
        != "environment-generalization-policy-replay-v1"
        or policy_metrics.get("population", {}).get("row_count")
        != lofo_metrics.get("dataset", {}).get("row_count")
        or policy_metrics.get("provenance", {}).get("prediction_manifest_sha256")
        != _sha256(lofo_path / lofo_analysis.OUTPUT_MANIFEST)
    ):
        raise PlotError("LOFO and policy analysis identities differ")
    required_action_fields = {
        "deadline_rescue_probability",
        "tail18_acceleration_probability",
        "primary_deadline_risk",
        "canonical_reservation_us",
        "ood_fallback",
        *{
            f"action_probability_{policy_id}"
            for policy_id in policy_analysis.POLICY_ORDER
        },
    }
    columns = {name: [] for name in required_action_fields}
    action_path = policy_path / policy_analysis.OUTPUT_ACTIONS
    row_count = 0
    try:
        with gzip.open(action_path, mode="rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or not required_action_fields <= set(
                reader.fieldnames
            ):
                raise PlotError("policy action CSV schema differs")
            row_count = 0
            for row_count, row in enumerate(reader, start=1):
                try:
                    for name in required_action_fields:
                        columns[name].append(float(row[name]))
                except (KeyError, ValueError) as error:
                    raise PlotError(
                        f"policy action line {row_count + 1}: value differs"
                    ) from error
    except OSError as error:
        raise PlotError(f"cannot read {action_path}: {error}") from error
    if row_count != policy_metrics["population"]["row_count"]:
        raise PlotError("policy action row count differs")
    arrays = {name: np.asarray(values, dtype=float) for name, values in columns.items()}
    if any(not np.all(np.isfinite(values)) for values in arrays.values()):
        raise PlotError("policy action values are non-finite")
    source_hashes = {
        "lofo_manifest_sha256": _sha256(lofo_path / OUTPUT_MANIFEST),
        "policy_manifest_sha256": _sha256(policy_path / OUTPUT_MANIFEST),
        "lofo_metrics_sha256": lofo_manifest["artifacts_sha256"][
            lofo_analysis.OUTPUT_METRICS
        ],
        "policy_metrics_sha256": policy_manifest["artifacts_sha256"][
            policy_analysis.OUTPUT_METRICS
        ],
    }
    return lofo_metrics, policy_metrics, arrays, source_hashes


def _save(figure: plt.Figure, path: Path) -> None:
    figure.savefig(
        path,
        dpi=180,
        bbox_inches="tight",
        metadata={"Software": "wifi_streaming_simulation"},
    )
    plt.close(figure)


def plot_frontier(path: Path, result: dict[str, Any]) -> None:
    """Plot eligible-frame miss value against exact canonical reservation."""

    figure, axis = plt.subplots(figsize=(8.4, 5.4), constrained_layout=True)
    for policy_id in policy_analysis.POLICY_ORDER:
        row = result["policies"][policy_id]
        miss = row["policy_value"]["deadline_miss"]
        resource = row["resource"]
        x = resource["hierarchical_mean_canonical_reservation_us_per_run"] / 1000
        y = 100 * miss["estimate"]
        error = np.asarray(
            [
                [100 * max(miss["estimate"] - miss["ci_lower"], 0.0)],
                [100 * max(miss["ci_upper"] - miss["estimate"], 0.0)],
            ],
            dtype=float,
        )
        axis.errorbar(
            x,
            y,
            yerr=error,
            fmt="o",
            markersize=8,
            capsize=4,
            color=COLORS[policy_id],
            label=LABELS[policy_id],
        )
        axis.annotate(
            LABELS[policy_id],
            (x, y),
            xytext=(6, 7),
            textcoords="offset points",
            fontsize=8,
        )
    budget_ms = result["resource"]["budget_us_per_60s_run"] / 1000
    axis.axvline(budget_ms, color="#9ca3af", linestyle="--", linewidth=1)
    axis.set_xlabel("Mean canonical secondary reservation per run (ms)")
    axis.set_ylabel("Eligible-frame deadline-miss estimate (%)")
    axis.set_title("Cross-family policy value and resource frontier")
    axis.grid(True, alpha=0.25)
    axis.legend(frameon=False, loc="best")
    _save(figure, path)


def plot_families(path: Path, result: dict[str, Any]) -> None:
    """Plot held-out-family deadline-miss estimates for every policy."""

    family_order = list(
        result["policies"][policy_analysis.POLICY_ORDER[0]]["family_value"]
    )
    x = np.arange(len(family_order))
    width = 0.19
    figure, axis = plt.subplots(figsize=(11.2, 5.8), constrained_layout=True)
    for policy_index, policy_id in enumerate(policy_analysis.POLICY_ORDER):
        values = [
            100
            * result["policies"][policy_id]["family_value"][family][
                "deadline_miss"
            ]
            for family in family_order
        ]
        axis.bar(
            x + (policy_index - 1.5) * width,
            values,
            width,
            color=COLORS[policy_id],
            label=LABELS[policy_id],
        )
    axis.set_xticks(x, [name.replace("_", "\n") for name in family_order])
    axis.set_ylabel("Eligible-frame deadline-miss estimate (%)")
    axis.set_title("Leave-one-family-out policy value")
    axis.grid(True, axis="y", alpha=0.25)
    axis.legend(frameon=False, ncol=2)
    _save(figure, path)


def plot_lofo_diagnostics(path: Path, metrics: dict[str, Any]) -> None:
    """Plot family risk ranking and conservative fallback diagnostics."""

    families = metrics["diagnostics"]["family_metrics"]
    order = list(families)
    x = np.arange(len(order))
    auc = [families[name]["control_deadline_miss_auc"] for name in order]
    fallback = [100 * families[name]["ood"]["fallback_fraction"] for name in order]
    rescue = [100 * families[name]["mean_predicted_deadline_rescue"] for name in order]
    figure, axes = plt.subplots(1, 3, figsize=(13.2, 4.5), constrained_layout=True)
    auc_values = [np.nan if value is None else value for value in auc]
    axes[0].bar(x, auc_values, color="#2563eb")
    axes[0].axhline(0.5, color="#9ca3af", linestyle="--", linewidth=1)
    axes[0].set_ylabel("AUC")
    axes[0].set_title("Control miss ranking")
    axes[1].bar(x, rescue, color="#10b981")
    axes[1].set_ylabel("Predicted rescue (%)")
    axes[1].set_title("Mean predicted deadline rescue")
    axes[2].bar(x, fallback, color="#ef4444")
    axes[2].set_ylabel("Fallback rows (%)")
    axes[2].set_title("Conservative OOD fallback")
    for axis in axes:
        axis.set_xticks(x, [name.replace("_", "\n") for name in order])
        axis.tick_params(axis="x", labelsize=7)
        axis.grid(True, axis="y", alpha=0.2)
    _save(figure, path)


def _empirical_cdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ordered = np.sort(np.asarray(values, dtype=float))
    if len(ordered) == 0:
        return ordered, ordered
    return ordered, np.arange(1, len(ordered) + 1) / len(ordered)


def plot_allocation(
    path: Path,
    actions: dict[str, np.ndarray],
) -> None:
    """Plot oracle allocation over predicted rescue and canonical cost."""

    oracle = actions[
        "action_probability_cross_fitted_scenario_resource_oracle_v1"
    ] > 0.5
    rescue = actions["deadline_rescue_probability"]
    cost = actions["canonical_reservation_us"]
    figure, axes = plt.subplots(1, 2, figsize=(10.8, 4.5), constrained_layout=True)
    for selected, label, color in (
        (True, "Oracle selected", "#10b981"),
        (False, "Oracle rejected", "#6b7280"),
    ):
        x, y = _empirical_cdf(rescue[oracle == selected])
        axes[0].plot(x, y, label=label, color=color, linewidth=1.8)
    axes[0].set_xlabel("Predicted deadline-rescue probability")
    axes[0].set_ylabel("Empirical CDF")
    axes[0].set_title("Resource allocation by predicted benefit")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(frameon=False)
    axes[1].hist(
        [cost[oracle], cost[~oracle]],
        bins=30,
        weights=[
            np.full(np.sum(oracle), 1 / max(np.sum(oracle), 1)),
            np.full(np.sum(~oracle), 1 / max(np.sum(~oracle), 1)),
        ],
        label=["Oracle selected", "Oracle rejected"],
        color=["#10b981", "#9ca3af"],
        alpha=0.7,
    )
    axes[1].set_xlabel("Canonical reservation per action (us)")
    axes[1].set_ylabel("Fraction")
    axes[1].set_title("Selected and rejected action costs")
    axes[1].grid(True, axis="y", alpha=0.25)
    axes[1].legend(frameon=False)
    _save(figure, path)


def plot_regret(path: Path, result: dict[str, Any]) -> None:
    """Plot the fraction of identified oracle deadline gain realized."""

    order = list(policy_analysis.POLICY_ORDER)
    rows = result["contrasts_against_resource_oracle"]
    estimates = [
        rows[name]["fraction_of_oracle_deadline_gain_realized"] for name in order
    ]
    values = np.asarray(
        [np.nan if row["estimate"] is None else row["estimate"] for row in estimates]
    )
    lower = np.asarray(
        [np.nan if row["ci_lower"] is None else row["ci_lower"] for row in estimates]
    )
    upper = np.asarray(
        [np.nan if row["ci_upper"] is None else row["ci_upper"] for row in estimates]
    )
    x = np.arange(len(order))
    figure, axis = plt.subplots(figsize=(8.8, 4.8), constrained_layout=True)
    axis.bar(x, 100 * values, color=[COLORS[name] for name in order])
    identified = np.isfinite(values) & np.isfinite(lower) & np.isfinite(upper)
    axis.errorbar(
        x[identified],
        100 * values[identified],
        yerr=np.vstack(
            (
                100 * np.maximum(values[identified] - lower[identified], 0.0),
                100 * np.maximum(upper[identified] - values[identified], 0.0),
            )
        ),
        fmt="none",
        color="#111827",
        capsize=4,
    )
    axis.axhline(50, color="#dc2626", linestyle="--", linewidth=1, label="50% target")
    axis.set_xticks(x, [LABELS[name] for name in order], rotation=12, ha="right")
    axis.set_ylabel("Fraction of oracle deadline gain realized (%)")
    axis.set_title("Regret against the cross-fitted resource ceiling")
    axis.grid(True, axis="y", alpha=0.25)
    axis.legend(frameon=False)
    _save(figure, path)


def plot_all(
    lofo_dir: Path | str,
    policy_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Render and atomically publish all checksum-closed figures."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise PlotError(f"refusing to overwrite output directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
    )
    try:
        lofo_metrics, policy_metrics, actions, sources = load_inputs(
            lofo_dir, policy_dir
        )
        plot_frontier(temporary / OUTPUT_FRONTIER, policy_metrics)
        plot_families(temporary / OUTPUT_FAMILIES, policy_metrics)
        plot_lofo_diagnostics(temporary / OUTPUT_LOFO, lofo_metrics)
        plot_allocation(temporary / OUTPUT_ALLOCATION, actions)
        plot_regret(temporary / OUTPUT_REGRET, policy_metrics)
        manifest = {
            "manifest_schema_version": 1,
            "plot_schema_version": PLOT_SCHEMA_VERSION,
            "hash_algorithm": "sha256",
            "sources_sha256": sources,
            "artifacts_sha256": {
                name: _sha256(temporary / name) for name in PLOT_FILES
            },
        }
        (temporary / OUTPUT_MANIFEST).write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="plot frozen environment-generalization analysis"
    )
    parser.add_argument("--lofo-dir", required=True, type=Path)
    parser.add_argument("--policy-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    manifest = plot_all(args.lofo_dir, args.policy_dir, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "figure_count": len(manifest["artifacts_sha256"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
