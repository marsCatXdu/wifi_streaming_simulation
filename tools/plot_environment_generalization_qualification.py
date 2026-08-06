#!/usr/bin/env python3
"""Plot the frozen held-out environment closed-loop qualification."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from analyze_environment_generalization_qualification import (  # noqa: E402
    ANALYSIS_ID,
    ARM_IDS,
    ARM_LABELS,
    EXPECTED_PLOTS,
    QualificationAnalysisError,
    _git_identity,
    _sha256,
    load_analysis_contract,
)
from validate_outputs import ValidationError, validate_run  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PLOT_MANIFEST_NAME = "plot_artifact_manifest.json"
COLORS = {
    "str_mlo_nmaxinflights_1": "#4C78A8",
    "score_aware_t2_v2": "#F58518",
    "distributional_shadow_t2": "#54A24B",
}
FAMILY_COLORS = {
    "radio_propagation": "#4C78A8",
    "obss_intensity": "#F58518",
    "obss_geometry_mac": "#E45756",
    "video_workload": "#72B7B2",
    "legacy_coexistence": "#54A24B",
    "compound_shift": "#B279A2",
}
STR_COMPARISONS = {
    "score_aware_t2_v2": "score_aware_t2_v2_minus_str_mlo_nmaxinflights_1",
    "distributional_shadow_t2": (
        "distributional_shadow_t2_minus_str_mlo_nmaxinflights_1"
    ),
}
DISTRIBUTIONAL_V2_COMPARISON = (
    "distributional_shadow_t2_minus_score_aware_t2_v2"
)


class QualificationPlotError(QualificationAnalysisError):
    """Raised when plot inputs or historical evidence differ."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationPlotError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationPlotError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="ascii") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as error:
        raise QualificationPlotError(f"cannot read {path}: {error}") from error
    _require(bool(rows), f"{path}: table is empty")
    return rows


def load_analysis_artifacts(
    analysis_directory: Path,
    contract: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, Any],
]:
    """Load and rehash the complete analysis boundary."""

    root = analysis_directory.resolve()
    _require(root.is_dir(), f"analysis directory is absent: {root}")
    names = contract["outputs"]
    manifest_path = root / names["analysis_artifact_manifest_json"]
    manifest = _read_json(manifest_path)
    _require(
        manifest.get("schema_version") == 1
        and manifest.get("analysis") == ANALYSIS_ID,
        "analysis artifact manifest identity differs",
    )
    declared = manifest.get("artifacts")
    _require(isinstance(declared, dict) and len(declared) == 6,
             "analysis artifact set differs")
    required_names = {
        names["report_json"],
        names["report_markdown"],
        names["run_metrics_csv"],
        names["paired_metrics_csv"],
        names["scenario_metrics_csv"],
        names["family_metrics_csv"],
    }
    _require(set(declared) == required_names, "analysis artifact names differ")
    for name, identity in declared.items():
        path = root / name
        _require(
            path.is_file()
            and path.stat().st_size == identity.get("bytes")
            and _sha256(path) == identity.get("sha256"),
            f"analysis artifact differs: {name}",
        )
    report = _read_json(root / names["report_json"])
    _require(
        report.get("analysis") == ANALYSIS_ID
        and report.get("source_closure", {}).get("analysis_contract", {}).get(
            "sha256"
        )
        == _sha256(
            ROOT
            / report["source_closure"]["analysis_contract"]["path"]
        ),
        "qualification report source closure differs",
    )
    run_rows = _read_csv(root / names["run_metrics_csv"])
    family_rows = _read_csv(root / names["family_metrics_csv"])
    scenario_rows = _read_csv(root / names["scenario_metrics_csv"])
    counts = manifest.get("counts", {})
    _require(
        len(run_rows) == counts.get("strictly_validated_runs") == 576
        and len(family_rows) == counts.get("family_comparison_rows") == 18
        and len(scenario_rows) == counts.get("scenario_comparison_rows") == 144,
        "analysis table cardinality differs",
    )
    return report, run_rows, family_rows, scenario_rows, {
        "path": str(manifest_path),
        "bytes": manifest_path.stat().st_size,
        "sha256": _sha256(manifest_path),
        "manifest": manifest,
    }


def _float(row: dict[str, str], field: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, ValueError) as error:
        raise QualificationPlotError(f"invalid numeric field {field}") from error
    _require(math.isfinite(value), f"non-finite numeric field {field}")
    return value


def _resolve_run_directory(row: dict[str, str], run_root: Path | None) -> Path:
    run_id = row.get("run_id", "")
    _require(bool(run_id), "run-metrics row has no run ID")
    candidates: list[Path] = []
    if run_root is not None:
        candidates.append(run_root.resolve() / run_id)
    serialized = row.get("run_dir")
    if serialized:
        candidates.append(Path(serialized))
    matches: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate.resolve().name == run_id:
            resolved = candidate.resolve()
            if resolved not in matches:
                matches.append(resolved)
    _require(len(matches) == 1, f"cannot resolve one raw run directory for {run_id}")
    return matches[0]


def _flag(value: str, label: str) -> bool:
    _require(value in {"0", "1"}, f"invalid {label} flag")
    return value == "1"


def _burst_lengths(flags: Iterable[bool]) -> list[int]:
    result: list[int] = []
    current = 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            result.append(current)
            current = 0
    if current:
        result.append(current)
    return result


def _historical_job(job: dict[str, str]) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=job["run_id"],
            expected_project_commit=job["project_commit"],
            expected_ns3_commit=job["ns3_upstream_commit"],
        )
    except ValidationError as error:
        raise QualificationPlotError(
            f"{run_dir}: historical-plot validation failed: {error}"
        ) from error
    _require(validation.get("valid") is True, f"{run_dir}: strict validation failed")
    try:
        with (run_dir / "frames.csv").open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as error:
        raise QualificationPlotError(f"cannot read {run_dir}/frames.csv: {error}") from error
    _require(bool(rows), f"{run_dir}/frames.csv: no frames")
    ordered = sorted(rows, key=lambda row: int(row["frame_id"]))
    latencies: list[float] = []
    misses: list[bool] = []
    for row in ordered:
        miss = _flag(row["deadline_miss"], "deadline_miss")
        incomplete = _flag(row["incomplete"], "incomplete")
        misses.append(miss)
        if not incomplete:
            latency = float(row["union_latency_us"])
            _require(math.isfinite(latency) and latency >= 0,
                     f"{run_dir}: invalid completion latency")
            latencies.append(latency)
    return {
        "arm_id": job["arm_id"],
        "run_id": job["run_id"],
        "latencies_us": latencies,
        "bursts": _burst_lengths(misses),
        "generated": len(ordered),
        "completed": len(latencies),
        "misses": sum(misses),
    }


def collect_historical_data(
    run_rows: Sequence[dict[str, str]],
    report: dict[str, Any],
    *,
    run_root: Path | None,
    workers: int,
) -> dict[str, dict[str, Any]]:
    """Freshly validate raw runs and collect descriptive frame distributions."""

    _require(workers > 0, "historical validation worker count must be positive")
    source = report["source_closure"]
    project_commit = source["campaign_manifest"]["project_commit"]
    ns3_commit = source["campaign_manifest"]["ns3_upstream_commit"]
    jobs = [
        {
            "run_id": row["run_id"],
            "run_dir": str(_resolve_run_directory(row, run_root)),
            "arm_id": row["arm_id"],
            "project_commit": project_commit,
            "ns3_upstream_commit": ns3_commit,
        }
        for row in run_rows
    ]
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_historical_job, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                if isinstance(error, QualificationPlotError):
                    raise error
                raise QualificationPlotError(
                    f"historical worker failed for {job['run_id']}: {error}"
                ) from error
    _require(len(results) == 576, "historical validation result count differs")
    grouped = {
        arm: {
            "latencies_us": [],
            "bursts": [],
            "generated": 0,
            "completed": 0,
            "misses": 0,
            "run_count": 0,
        }
        for arm in ARM_IDS
    }
    for result in results:
        arm = result["arm_id"]
        _require(arm in grouped, "historical result has an unknown arm")
        grouped[arm]["latencies_us"].extend(result["latencies_us"])
        grouped[arm]["bursts"].extend(result["bursts"])
        for field in ("generated", "completed", "misses"):
            grouped[arm][field] += result[field]
        grouped[arm]["run_count"] += 1
    for arm in ARM_IDS:
        _require(grouped[arm]["run_count"] == 192, f"historical arm count differs: {arm}")
    return grouped


def _save_figure(figure: plt.Figure, directory: Path, name: str) -> list[Path]:
    paths = [directory / f"{name}.png", directory / f"{name}.pdf"]
    figure.savefig(paths[0], dpi=180, bbox_inches="tight")
    figure.savefig(paths[1], bbox_inches="tight")
    plt.close(figure)
    return paths


def _error(interval: dict[str, Any], scale: float = 1.0) -> tuple[float, float]:
    estimate = float(interval["estimate"])
    return (
        scale * (estimate - float(interval["ci95_low"])),
        scale * (float(interval["ci95_high"]) - estimate),
    )


def _aggregate_policy_metrics(report: dict[str, Any], output: Path) -> list[Path]:
    figure, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
    positions = np.arange(len(ARM_IDS))
    labels = [ARM_LABELS[arm] for arm in ARM_IDS]
    for axis, metric, scale, ylabel in (
        (axes[0], "all_generated_deadline_miss_rate", 100.0, "Deadline misses (%)"),
        (
            axes[1],
            "completed_frame_hf7_p99_us",
            0.001,
            "Completed-frame P99 (ms)",
        ),
    ):
        intervals = [report["treatments"][arm]["metrics"][metric] for arm in ARM_IDS]
        values = [scale * item["estimate"] for item in intervals]
        errors = np.array([_error(item, scale) for item in intervals]).T
        axis.bar(
            positions,
            values,
            color=[COLORS[arm] for arm in ARM_IDS],
            yerr=errors,
            capsize=4,
        )
        axis.set_xticks(positions, labels, rotation=15, ha="right")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_title("All-generated reliability")
    axes[1].set_title("Survivor-conditioned latency")
    figure.suptitle("Held-out closed-loop aggregate (95% hierarchical bootstrap)")
    return _save_figure(figure, output, "aggregate_policy_metrics")


def _family_rows_by_comparison(
    rows: Sequence[dict[str, str]], comparison: str
) -> list[dict[str, str]]:
    selected = [row for row in rows if row["comparison_id"] == comparison]
    _require(len(selected) == 6, f"family row count differs for {comparison}")
    return selected


def _family_delta_plot(
    family_rows: Sequence[dict[str, str]],
    output: Path,
    *,
    metric: str,
    scale: float,
    ylabel: str,
    name: str,
) -> list[Path]:
    by_arm = {
        arm: _family_rows_by_comparison(family_rows, comparison)
        for arm, comparison in STR_COMPARISONS.items()
    }
    families = [row["family_id"] for row in by_arm["score_aware_t2_v2"]]
    _require(
        all([row["family_id"] for row in by_arm[arm]] == families for arm in by_arm),
        "family plot order differs across comparisons",
    )
    figure, axis = plt.subplots(figsize=(11, 4.8))
    positions = np.arange(len(families))
    width = 0.36
    for offset, arm in ((-width / 2, "score_aware_t2_v2"),
                        (width / 2, "distributional_shadow_t2")):
        rows = by_arm[arm]
        values = [scale * _float(row, metric) for row in rows]
        lower = [
            scale * (_float(row, metric) - _float(row, f"{metric}__ci95_low"))
            for row in rows
        ]
        upper = [
            scale * (_float(row, f"{metric}__ci95_high") - _float(row, metric))
            for row in rows
        ]
        axis.bar(
            positions + offset,
            values,
            width,
            label=ARM_LABELS[arm],
            color=COLORS[arm],
            yerr=np.array([lower, upper]),
            capsize=3,
        )
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xticks(positions, [family.replace("_", " ") for family in families],
                    rotation=20, ha="right")
    axis.set_ylabel(ylabel)
    axis.set_title("Candidate minus STR by held-out family (95% interval)")
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    return _save_figure(figure, output, name)


def _family_resource_effects(
    family_rows: Sequence[dict[str, str]], output: Path
) -> list[Path]:
    by_arm = {
        arm: _family_rows_by_comparison(family_rows, comparison)
        for arm, comparison in STR_COMPARISONS.items()
    }
    families = [row["family_id"] for row in by_arm["score_aware_t2_v2"]]
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharex=True)
    positions = np.arange(len(families))
    for axis, metric, scale, threshold, ylabel in (
        (axes[0], "sender_airtime_ratio", 1.0, 1.25, "Sender airtime / STR"),
        (
            axes[1],
            "background_throughput_loss",
            100.0,
            2.0,
            "Background-throughput loss (%)",
        ),
    ):
        for arm, marker in (("score_aware_t2_v2", "o"),
                            ("distributional_shadow_t2", "s")):
            rows = by_arm[arm]
            axis.plot(
                positions,
                [scale * _float(row, metric) for row in rows],
                marker=marker,
                color=COLORS[arm],
                label=ARM_LABELS[arm],
            )
        axis.axhline(threshold, color="#D62728", linestyle="--", linewidth=1,
                     label="Family limit")
        axis.set_xticks(
            positions,
            [family.replace("_", " ") for family in families],
            rotation=25,
            ha="right",
        )
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", alpha=0.25)
    axes[0].legend(fontsize=8)
    figure.suptitle("Held-out-family resource safety")
    return _save_figure(figure, output, "family_resource_effects")


def _scenario_scatter(
    scenario_rows: Sequence[dict[str, str]], output: Path
) -> list[Path]:
    figure, axis = plt.subplots(figsize=(9.5, 6.4))
    markers = {"score_aware_t2_v2": "o", "distributional_shadow_t2": "s"}
    for arm, comparison in STR_COMPARISONS.items():
        rows = [row for row in scenario_rows if row["comparison_id"] == comparison]
        _require(len(rows) == 48, f"scenario row count differs for {comparison}")
        for family, color in FAMILY_COLORS.items():
            selected = [row for row in rows if row["family_id"] == family]
            axis.scatter(
                [100 * _float(row, "deadline_miss_delta") for row in selected],
                [0.001 * _float(row, "completed_p99_delta_us") for row in selected],
                marker=markers[arm],
                facecolors="none" if arm == "distributional_shadow_t2" else color,
                edgecolors=color,
                alpha=0.85,
            )
    axis.axvline(0, color="black", linewidth=1)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xlabel("Deadline-miss delta versus STR (percentage points)")
    axis.set_ylabel("Completed-P99 delta versus STR (ms)")
    axis.set_title("Scenario-level closed-loop effects")
    axis.grid(alpha=0.2)
    family_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            color=color,
            label=family.replace("_", " "),
        )
        for family, color in FAMILY_COLORS.items()
    ]
    arm_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor="black",
            markeredgecolor="black",
            label=ARM_LABELS["score_aware_t2_v2"],
        ),
        Line2D(
            [0],
            [0],
            marker="s",
            linestyle="none",
            markerfacecolor="none",
            markeredgecolor="black",
            label=ARM_LABELS["distributional_shadow_t2"],
        ),
    ]
    axis.legend(
        handles=[*family_handles, *arm_handles],
        fontsize=8,
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
    )
    return _save_figure(figure, output, "scenario_miss_p99_delta")


def _scenario_distributional_v2(
    scenario_rows: Sequence[dict[str, str]], output: Path
) -> list[Path]:
    rows = [
        row
        for row in scenario_rows
        if row["comparison_id"] == DISTRIBUTIONAL_V2_COMPARISON
    ]
    _require(len(rows) == 48, "distributional-versus-V2 scenario row count differs")
    figure, axis = plt.subplots(figsize=(7.8, 6.0))
    for family, color in FAMILY_COLORS.items():
        selected = [row for row in rows if row["family_id"] == family]
        axis.scatter(
            [100 * _float(row, "deadline_miss_delta") for row in selected],
            [0.001 * _float(row, "completed_p99_delta_us") for row in selected],
            color=color,
            label=family.replace("_", " "),
            alpha=0.85,
        )
    axis.axvline(0, color="black", linewidth=1)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xlabel("Distributional minus V2 miss rate (percentage points)")
    axis.set_ylabel("Distributional minus V2 completed P99 (ms)")
    axis.set_title("Scenario-level policy comparison")
    axis.grid(alpha=0.2)
    axis.legend(fontsize=8)
    return _save_figure(figure, output, "scenario_distributional_vs_v2")


def _empirical_cdf(values: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    ordered = np.sort(np.asarray(values, dtype=float))
    _require(ordered.size > 0, "cannot plot an empty empirical distribution")
    probabilities = np.arange(1, ordered.size + 1, dtype=float) / ordered.size
    return ordered, probabilities


def _completion_cdf(historical: dict[str, dict[str, Any]], output: Path) -> list[Path]:
    figure, axis = plt.subplots(figsize=(8.5, 5.0))
    for arm in ARM_IDS:
        values, probabilities = _empirical_cdf(historical[arm]["latencies_us"])
        axis.plot(values / 1000.0, probabilities, color=COLORS[arm],
                  label=ARM_LABELS[arm])
    axis.axvline(33.333, color="black", linestyle="--", linewidth=1,
                 label="33.333 ms deadline")
    axis.set_xlim(left=0)
    axis.set_ylim(0, 1.002)
    axis.set_xlabel("Completed-frame latency (ms)")
    axis.set_ylabel("Empirical CDF")
    axis.set_title("Pooled completed-frame latency (descriptive)")
    axis.grid(alpha=0.25)
    axis.legend()
    return _save_figure(figure, output, "completion_latency_cdf")


def _completion_pdf(historical: dict[str, dict[str, Any]], output: Path) -> list[Path]:
    all_values = np.concatenate(
        [np.asarray(historical[arm]["latencies_us"], dtype=float) / 1000 for arm in ARM_IDS]
    )
    maximum = max(33.333, float(np.quantile(all_values, 0.995)))
    bins = np.linspace(0, maximum, 161)
    figure, axis = plt.subplots(figsize=(8.5, 5.0))
    for arm in ARM_IDS:
        values = np.asarray(historical[arm]["latencies_us"], dtype=float) / 1000
        axis.hist(
            values,
            bins=bins,
            density=True,
            histtype="step",
            linewidth=1.6,
            color=COLORS[arm],
            label=ARM_LABELS[arm],
        )
    axis.axvline(33.333, color="black", linestyle="--", linewidth=1)
    axis.set_xlabel("Completed-frame latency (ms)")
    axis.set_ylabel("Density")
    axis.set_title("Pooled completed-frame latency PDF (through 99.5th percentile)")
    axis.grid(alpha=0.25)
    axis.legend()
    return _save_figure(figure, output, "completion_latency_pdf")


def _deadline_and_completion(
    historical: dict[str, dict[str, Any]], output: Path
) -> list[Path]:
    positions = np.arange(len(ARM_IDS))
    width = 0.36
    miss = [
        100 * historical[arm]["misses"] / historical[arm]["generated"] for arm in ARM_IDS
    ]
    completion = [
        100 * historical[arm]["completed"] / historical[arm]["generated"]
        for arm in ARM_IDS
    ]
    figure, axis = plt.subplots(figsize=(8.5, 4.8))
    axis.bar(positions - width / 2, miss, width, label="Deadline miss")
    axis.bar(positions + width / 2, completion, width, label="Completed")
    axis.set_xticks(positions, [ARM_LABELS[arm] for arm in ARM_IDS], rotation=15,
                    ha="right")
    axis.set_ylabel("All generated frames (%)")
    axis.set_title("Pooled deadline misses and completion (descriptive)")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    return _save_figure(figure, output, "deadline_miss_and_completion")


def _burst_cdf(historical: dict[str, dict[str, Any]], output: Path) -> list[Path]:
    figure, axis = plt.subplots(figsize=(8.5, 5.0))
    for arm in ARM_IDS:
        bursts = historical[arm]["bursts"]
        if not bursts:
            axis.plot([], [], color=COLORS[arm], label=f"{ARM_LABELS[arm]} (no misses)")
            continue
        values, probabilities = _empirical_cdf(bursts)
        axis.step(values, probabilities, where="post", color=COLORS[arm],
                  label=ARM_LABELS[arm])
    axis.set_xscale("log", base=2)
    axis.set_ylim(0, 1.002)
    axis.set_xlabel("Consecutive deadline misses (frames, log2 scale)")
    axis.set_ylabel("Empirical CDF of miss bursts")
    axis.set_title("Pooled per-run deadline-miss burst lengths (descriptive)")
    axis.grid(alpha=0.25)
    axis.legend()
    return _save_figure(figure, output, "deadline_miss_burst_cdf")


def _resource_summary(report: dict[str, Any], output: Path) -> list[Path]:
    candidates = ("score_aware_t2_v2", "distributional_shadow_t2")
    positions = np.arange(len(candidates))
    figure, axes = plt.subplots(1, 2, figsize=(9.5, 4.2))
    airtime = [
        report["comparisons"][STR_COMPARISONS[arm]]["aggregate"][
            "sender_airtime_ratio"
        ]
        for arm in candidates
    ]
    background_loss = [
        report["comparisons"][STR_COMPARISONS[arm]]["aggregate"][
            "background_throughput_loss"
        ]
        for arm in candidates
    ]
    for axis, intervals, scale, threshold, ylabel in (
        (axes[0], airtime, 1.0, 1.2, "Sender airtime / STR"),
        (axes[1], background_loss, 100.0, 1.0, "Background loss (%)"),
    ):
        values = [scale * item["estimate"] for item in intervals]
        errors = np.array([_error(item, scale) for item in intervals]).T
        axis.bar(
            positions,
            values,
            yerr=errors,
            capsize=4,
            color=[COLORS[arm] for arm in candidates],
        )
        axis.axhline(threshold, color="#D62728", linestyle="--", linewidth=1,
                     label="Aggregate limit")
        axis.set_xticks(positions, [ARM_LABELS[arm] for arm in candidates],
                        rotation=15, ha="right")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", alpha=0.25)
        axis.legend(fontsize=8)
    figure.suptitle("Aggregate resource effects (95% hierarchical bootstrap)")
    return _save_figure(figure, output, "resource_summary")


def render_figures(
    report: dict[str, Any],
    family_rows: Sequence[dict[str, str]],
    scenario_rows: Sequence[dict[str, str]],
    historical: dict[str, dict[str, Any]],
    output: Path,
) -> list[Path]:
    """Render every predeclared statistical and historical figure."""

    artifacts: list[Path] = []
    artifacts.extend(_aggregate_policy_metrics(report, output))
    artifacts.extend(
        _family_delta_plot(
            family_rows,
            output,
            metric="deadline_miss_delta",
            scale=100.0,
            ylabel="Deadline-miss delta (percentage points)",
            name="family_deadline_miss_delta",
        )
    )
    artifacts.extend(
        _family_delta_plot(
            family_rows,
            output,
            metric="completed_p99_delta_us",
            scale=0.001,
            ylabel="Completed-P99 delta (ms)",
            name="family_completed_p99_delta",
        )
    )
    artifacts.extend(_family_resource_effects(family_rows, output))
    artifacts.extend(_scenario_scatter(scenario_rows, output))
    artifacts.extend(_scenario_distributional_v2(scenario_rows, output))
    artifacts.extend(_completion_cdf(historical, output))
    artifacts.extend(_completion_pdf(historical, output))
    artifacts.extend(_deadline_and_completion(historical, output))
    artifacts.extend(_burst_cdf(historical, output))
    artifacts.extend(_resource_summary(report, output))
    expected = {
        f"{name}.{suffix}" for name in EXPECTED_PLOTS for suffix in ("png", "pdf")
    }
    _require({path.name for path in artifacts} == expected,
             "rendered figure set differs from plot contract")
    return artifacts


def write_plots(
    output_directory: Path,
    report: dict[str, Any],
    family_rows: Sequence[dict[str, str]],
    scenario_rows: Sequence[dict[str, str]],
    historical: dict[str, dict[str, Any]],
    analysis_identity: dict[str, Any],
) -> Path:
    """Atomically publish all figures and their checksum manifest."""

    output = output_directory.resolve()
    _require(not output.exists(), f"plot output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        artifacts = render_figures(
            report, family_rows, scenario_rows, historical, temporary
        )
        manifest = {
            "schema_version": 1,
            "manifest_id": "environment-generalization-qualification-plots-v1",
            "analysis": ANALYSIS_ID,
            "analysis_artifact_manifest": {
                key: analysis_identity[key] for key in ("path", "bytes", "sha256")
            },
            "analysis_contract": report["source_closure"]["analysis_contract"],
            "plotter": {
                "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": _sha256(Path(__file__).resolve()),
            },
            "historical_raw_revalidation": {
                "run_count": sum(historical[arm]["run_count"] for arm in ARM_IDS),
                "all_runs_passed": True,
                "completed_frame_population_is_survivor_conditioned": True,
            },
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in artifacts
            },
            "reproduction": [
                "python3 tools/plot_environment_generalization_qualification.py "
                "<analysis-directory> --output-directory <new-output-directory> "
                "--run-root <campaign-run-root> --workers 64"
            ],
        }
        manifest_path = temporary / PLOT_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="ascii",
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_directory", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--workers", type=int, default=16)
    arguments = parser.parse_args(argv)
    if arguments.workers <= 0:
        parser.error("--workers must be positive")
    try:
        contract = load_analysis_contract()
        report, run_rows, family_rows, scenario_rows, identity = (
            load_analysis_artifacts(arguments.analysis_directory, contract)
        )
        git_identity = _git_identity()
        _require(
            report["source_closure"]["campaign_manifest"]["project_commit"]
            == git_identity["project_commit"],
            "plotter checkout differs from qualification campaign commit",
        )
        historical = collect_historical_data(
            run_rows,
            report,
            run_root=arguments.run_root,
            workers=arguments.workers,
        )
        output = write_plots(
            arguments.output_directory,
            report,
            family_rows,
            scenario_rows,
            historical,
            identity,
        )
    except QualificationAnalysisError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"WROTE {output} figures={len(EXPECTED_PLOTS)} formats=png,pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
