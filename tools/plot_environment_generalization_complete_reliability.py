#!/usr/bin/env python3
"""Plot complete reliability evidence with an explicit P99 support warning."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

import analyze_environment_generalization_complete_reliability as complete
import analyze_environment_generalization_qualification as formal


ROOT = Path(__file__).resolve().parents[1]
PLOT_ID = "environment-generalization-complete-reliability-plots-v1"
EXPECTED_PLOTS = (
    "aggregate_reliability_resources",
    "family_deadline_miss_rate",
    "family_resource_effects",
    "scenario_deadline_miss_rate",
    "scenario_deadline_miss_delta",
    "completion_latency_cdf",
    "completion_latency_pdf",
    "deadline_outcome_composition",
    "deadline_miss_burst_cdf",
    "compound_p23_exact",
)
COLORS = {
    "str_mlo_nmaxinflights_1": "#4c78a8",
    "score_aware_t2_v2": "#f58518",
    "distributional_shadow_t2": "#54a24b",
}
SHORT_LABELS = {
    "str_mlo_nmaxinflights_1": "STR MLO",
    "score_aware_t2_v2": "V2",
    "distributional_shadow_t2": "Distributional",
}
WATERMARK = (
    "COMPLETE 576-RUN RELIABILITY ANALYSIS - FROZEN P99 NOT ASSESSABLE"
)


class CompleteReliabilityPlotError(RuntimeError):
    """Raised when complete reliability plot inputs are inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CompleteReliabilityPlotError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompleteReliabilityPlotError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="ascii") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as error:
        raise CompleteReliabilityPlotError(f"cannot read {path}: {error}") from error
    _require(bool(rows), f"{path}: table is empty")
    return rows


def load_analysis(
    analysis: Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, Any],
]:
    manifest_path = analysis / "analysis_artifact_manifest.json"
    manifest = _read_json(manifest_path)
    _require(
        manifest.get("analysis") == complete.ANALYSIS_ID
        and manifest.get("counts", {}).get("strictly_validated_runs") == 576,
        "analysis artifact identity differs",
    )
    for name, identity in manifest.get("artifacts", {}).items():
        path = analysis / name
        _require(
            path.is_file()
            and path.stat().st_size == identity.get("bytes")
            and _sha256(path) == identity.get("sha256"),
            f"analysis artifact differs: {name}",
        )
    report = _read_json(analysis / "complete_reliability_report.json")
    _require(
        report.get("analysis") == complete.ANALYSIS_ID
        and report.get("formal_qualification_status", {}).get("status")
        == "not_assessable",
        "complete reliability report identity differs",
    )
    return (
        report,
        _read_csv(analysis / "run_metrics.csv"),
        _read_csv(analysis / "family_metrics.csv"),
        _read_csv(analysis / "scenario_metrics.csv"),
        {
            "path": str(manifest_path),
            "bytes": manifest_path.stat().st_size,
            "sha256": _sha256(manifest_path),
        },
    )


def _flag(value: str, description: str) -> bool:
    lowered = value.strip().lower()
    _require(lowered in {"0", "1", "false", "true"},
             f"invalid flag for {description}: {value}")
    return lowered in {"1", "true"}


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


def collect_historical(
    run_root: Path, run_rows: Sequence[dict[str, str]], report: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    expected_manifest = report["source_closure"]["campaign_manifest"]
    manifest_path = run_root / "experiment_manifest.json"
    _require(
        manifest_path.is_file()
        and _sha256(manifest_path) == expected_manifest["sha256"],
        "plot run root differs from the analyzed campaign manifest",
    )
    grouped = {
        arm: {
            "latencies_us": [],
            "bursts": [],
            "generated": 0,
            "completed": 0,
            "late_completed": 0,
            "incomplete": 0,
            "misses": 0,
            "runs": [],
        }
        for arm in formal.ARM_IDS
    }
    seen: set[str] = set()
    for summary in run_rows:
        run_id = summary["run_id"]
        arm = summary["arm_id"]
        _require(run_id not in seen and arm in grouped,
                 "run table has a duplicate or unknown arm")
        seen.add(run_id)
        run_dir = run_root / run_id
        rows = _read_csv(run_dir / "frames.csv")
        latencies: list[float] = []
        flags: list[bool] = []
        incomplete_count = 0
        late_completed = 0
        for row in rows:
            _require(row.get("run_id") == run_id,
                     f"{run_dir}: frame run ID differs")
            incomplete = _flag(row["incomplete"], f"{run_id}: incomplete")
            miss = _flag(row["deadline_miss"], f"{run_id}: deadline_miss")
            flags.append(miss)
            if incomplete:
                incomplete_count += 1
                _require(row["union_latency_us"] == "",
                         f"{run_dir}: incomplete frame has latency")
            else:
                latency = float(row["union_latency_us"])
                _require(math.isfinite(latency) and latency >= 0,
                         f"{run_dir}: invalid completion latency")
                latencies.append(latency)
                late_completed += miss
        _require(
            len(rows) == int(summary["generated_frame_count"])
            and len(latencies) == int(summary["completed_frame_count"])
            and incomplete_count == int(summary["incomplete_frame_count"])
            and sum(flags) == int(summary["deadline_miss_count"]),
            f"{run_dir}: historical reduction differs from analyzed metrics",
        )
        target = grouped[arm]
        target["latencies_us"].extend(latencies)
        target["bursts"].extend(_burst_lengths(flags))
        target["generated"] += len(rows)
        target["completed"] += len(latencies)
        target["late_completed"] += late_completed
        target["incomplete"] += incomplete_count
        target["misses"] += sum(flags)
        target["runs"].append(
            {
                "run_id": run_id,
                "family_id": summary["family_id"],
                "scenario_id": summary["scenario_id"],
                "seed": int(summary["seed"]),
                "miss_rate": float(summary["all_generated_deadline_miss_rate"]),
                "sender_airtime_us": float(summary["sender_airtime_us"]),
            }
        )
    _require(
        len(seen) == 576
        and all(len(grouped[arm]["runs"]) == 192 for arm in formal.ARM_IDS),
        "historical population dimensions differ",
    )
    return grouped


def _plot_modules() -> tuple[Any, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as error:
        raise CompleteReliabilityPlotError(
            "matplotlib and numpy are required to render plots"
        ) from error
    return plt, np


def _finish(figure: Any, output: Path, name: str) -> list[Path]:
    figure.text(
        0.5,
        0.004,
        WATERMARK,
        ha="center",
        va="bottom",
        fontsize=7.5,
        color="firebrick",
        weight="bold",
    )
    figure.tight_layout(rect=(0, 0.035, 1, 1))
    paths = []
    for suffix in ("png", "pdf"):
        path = output / f"{name}.{suffix}"
        figure.savefig(path, dpi=180 if suffix == "png" else None)
        paths.append(path)
    return paths


def _errors(interval: dict[str, float], scale: float) -> list[list[float]]:
    estimate = scale * interval["estimate"]
    return [
        [estimate - scale * interval["ci95_low"]],
        [scale * interval["ci95_high"] - estimate],
    ]


def _aggregate_plot(plt: Any, report: dict[str, Any], output: Path) -> list[Path]:
    figure, axes = plt.subplots(2, 2, figsize=(10, 7.2))
    arms = formal.ARM_IDS
    x = list(range(len(arms)))
    labels = [SHORT_LABELS[arm] for arm in arms]
    colors = [COLORS[arm] for arm in arms]
    miss = [report["treatments"][arm]["all_generated_deadline_miss_rate"] for arm in arms]
    axes[0, 0].bar(x, [100 * value["estimate"] for value in miss], color=colors)
    axes[0, 0].errorbar(
        x,
        [100 * value["estimate"] for value in miss],
        yerr=[
            [100 * (value["estimate"] - value["ci95_low"]) for value in miss],
            [100 * (value["ci95_high"] - value["estimate"]) for value in miss],
        ],
        fmt="none",
        ecolor="black",
        capsize=3,
    )
    axes[0, 0].set_ylabel("Deadline misses (%)")
    axes[0, 0].set_title("All generated frames")
    airtime = [report["treatments"][arm]["sender_airtime_us"] for arm in arms]
    axes[0, 1].bar(x, [value["estimate"] / 1000 for value in airtime], color=colors)
    axes[0, 1].set_ylabel("Mean sender airtime (ms/run)")
    axes[0, 1].set_title("Sender resource use")
    throughput = [
        report["treatments"][arm]["background_throughput_mbps"] for arm in arms
    ]
    axes[1, 0].bar(x, [value["estimate"] for value in throughput], color=colors)
    axes[1, 0].set_ylabel("Background throughput (Mbit/s)")
    axes[1, 0].set_title("Coexistence impact")
    support = [report["p99_support"]["arms"][arm]["eligible_run_count"] for arm in arms]
    axes[1, 1].bar(x, support, color=colors)
    axes[1, 1].axhline(192, color="black", linestyle="--", linewidth=1)
    axes[1, 1].set_ylim(0, 200)
    axes[1, 1].set_ylabel("Runs with >=100 completions")
    axes[1, 1].set_title("Frozen P99 support (insufficient)")
    for axis in axes.flat:
        axis.set_xticks(x, labels, rotation=12)
        axis.grid(axis="y", alpha=0.25)
    figure.suptitle("Complete held-out environment qualification: supported estimands")
    return _finish(figure, output, "aggregate_reliability_resources")


def _family_plots(plt: Any, report: dict[str, Any], output: Path) -> list[Path]:
    families = list(report["family_treatments"])
    x = list(range(len(families)))
    width = 0.25
    artifacts: list[Path] = []
    figure, axis = plt.subplots(figsize=(11, 5.5))
    for index, arm in enumerate(formal.ARM_IDS):
        values = [
            100
            * report["family_treatments"][family][arm][
                "all_generated_deadline_miss_rate"
            ]["estimate"]
            for family in families
        ]
        axis.bar(
            [value + (index - 1) * width for value in x],
            values,
            width,
            label=SHORT_LABELS[arm],
            color=COLORS[arm],
        )
    axis.set_xticks(x, [value.replace("_", " ") for value in families], rotation=20)
    axis.set_ylabel("Deadline misses (%)")
    axis.set_title("All-generated-frame reliability by held-out family")
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    artifacts.extend(_finish(figure, output, "family_deadline_miss_rate"))

    figure, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    baseline = "str_mlo_nmaxinflights_1"
    for candidate in ("score_aware_t2_v2", "distributional_shadow_t2"):
        miss_deltas = []
        ratios = []
        for family in families:
            base = report["family_treatments"][family][baseline]
            cand = report["family_treatments"][family][candidate]
            miss_deltas.append(
                100
                * (
                    cand["all_generated_deadline_miss_rate"]["estimate"]
                    - base["all_generated_deadline_miss_rate"]["estimate"]
                )
            )
            ratios.append(
                cand["sender_airtime_us"]["estimate"]
                / base["sender_airtime_us"]["estimate"]
            )
        axes[0].plot(x, miss_deltas, marker="o", label=SHORT_LABELS[candidate],
                     color=COLORS[candidate])
        axes[1].plot(x, ratios, marker="o", label=SHORT_LABELS[candidate],
                     color=COLORS[candidate])
    axes[0].axhline(0, color="black", linewidth=1)
    axes[0].set_ylabel("Miss-rate delta vs STR (percentage points)")
    axes[1].axhline(1.2, color="firebrick", linestyle="--", linewidth=1)
    axes[1].axhline(1.0, color="black", linewidth=1)
    axes[1].set_ylabel("Sender-airtime ratio vs STR")
    for axis in axes:
        axis.set_xticks(x, [value.replace("_", " ") for value in families], rotation=25)
        axis.grid(alpha=0.25)
        axis.legend()
    figure.suptitle("Family-level reliability and airtime effects")
    artifacts.extend(_finish(figure, output, "family_resource_effects"))
    return artifacts


def _scenario_plots(plt: Any, report: dict[str, Any], output: Path) -> list[Path]:
    points = report["scenario_points"]
    ordered = [(family, scenario) for family, values in points.items() for scenario in values]
    x = list(range(len(ordered)))
    artifacts: list[Path] = []
    figure, axis = plt.subplots(figsize=(13, 5.8))
    for arm in formal.ARM_IDS:
        values = [
            100 * points[family][scenario][arm]["all_generated_deadline_miss_rate"]
            for family, scenario in ordered
        ]
        axis.plot(x, values, marker=".", linewidth=1, label=SHORT_LABELS[arm],
                  color=COLORS[arm])
    axis.set_ylabel("Deadline misses (%)")
    axis.set_xlabel("Frozen scenario order (8 scenarios per family)")
    axis.set_title("Scenario-level all-generated-frame reliability")
    axis.grid(alpha=0.2)
    axis.legend()
    for boundary in range(8, len(ordered), 8):
        axis.axvline(boundary - 0.5, color="grey", linewidth=0.7, alpha=0.5)
    artifacts.extend(_finish(figure, output, "scenario_deadline_miss_rate"))

    figure, axes = plt.subplots(2, 1, figsize=(13, 7.2), sharex=True)
    baseline = "str_mlo_nmaxinflights_1"
    for axis, candidate in zip(
        axes,
        ("score_aware_t2_v2", "distributional_shadow_t2"),
        strict=True,
    ):
        deltas = [
            100
            * (
                points[family][scenario][candidate][
                    "all_generated_deadline_miss_rate"
                ]
                - points[family][scenario][baseline][
                    "all_generated_deadline_miss_rate"
                ]
            )
            for family, scenario in ordered
        ]
        axis.bar(x, deltas, color=COLORS[candidate])
        axis.axhline(0, color="black", linewidth=1)
        axis.set_ylabel(f"{SHORT_LABELS[candidate]} - STR (pp)")
        axis.grid(axis="y", alpha=0.2)
        for boundary in range(8, len(ordered), 8):
            axis.axvline(boundary - 0.5, color="grey", linewidth=0.7, alpha=0.5)
    axes[-1].set_xlabel("Frozen scenario order (8 scenarios per family)")
    figure.suptitle("Scenario-level deadline-miss deltas against STR MLO")
    artifacts.extend(_finish(figure, output, "scenario_deadline_miss_delta"))
    return artifacts


def _empirical_cdf(np: Any, values: Sequence[float]) -> tuple[Any, Any]:
    ordered = np.sort(np.asarray(values, dtype=float))
    return ordered, np.arange(1, len(ordered) + 1, dtype=float) / len(ordered)


def _completion_plots(
    plt: Any, np: Any, historical: dict[str, dict[str, Any]], output: Path
) -> list[Path]:
    artifacts: list[Path] = []
    all_values = [
        value
        for arm in formal.ARM_IDS
        for value in historical[arm]["latencies_us"]
    ]
    xmax = max(40_000.0, float(np.quantile(np.asarray(all_values), 0.999)))
    figure, axis = plt.subplots(figsize=(8.5, 5.4))
    for arm in formal.ARM_IDS:
        x, y = _empirical_cdf(np, historical[arm]["latencies_us"])
        axis.plot(x / 1000, y, label=SHORT_LABELS[arm], color=COLORS[arm])
    axis.axvline(33.333, color="black", linestyle="--", linewidth=1, label="Deadline")
    axis.set_xlim(0, xmax / 1000)
    axis.set_xlabel("Completed-frame latency (ms)")
    axis.set_ylabel("Empirical CDF among completed frames")
    axis.set_title("Completion latency CDF (survivor-conditioned)")
    axis.grid(alpha=0.25)
    axis.legend()
    artifacts.extend(_finish(figure, output, "completion_latency_cdf"))

    figure, axis = plt.subplots(figsize=(8.5, 5.4))
    bins = np.linspace(0, xmax / 1000, 90)
    for arm in formal.ARM_IDS:
        values = np.asarray(historical[arm]["latencies_us"], dtype=float) / 1000
        axis.hist(
            values,
            bins=bins,
            density=True,
            histtype="step",
            linewidth=1.6,
            label=SHORT_LABELS[arm],
            color=COLORS[arm],
        )
    axis.axvline(33.333, color="black", linestyle="--", linewidth=1, label="Deadline")
    axis.set_xlabel("Completed-frame latency (ms)")
    axis.set_ylabel("Density")
    axis.set_title("Completion latency PDF (survivor-conditioned)")
    axis.grid(alpha=0.25)
    axis.legend()
    artifacts.extend(_finish(figure, output, "completion_latency_pdf"))
    return artifacts


def _outcome_and_burst_plots(
    plt: Any, np: Any, historical: dict[str, dict[str, Any]], output: Path
) -> list[Path]:
    artifacts: list[Path] = []
    arms = formal.ARM_IDS
    x = np.arange(len(arms))
    on_time = []
    late = []
    incomplete = []
    for arm in arms:
        generated = historical[arm]["generated"]
        late.append(100 * historical[arm]["late_completed"] / generated)
        incomplete.append(100 * historical[arm]["incomplete"] / generated)
        on_time.append(100 - late[-1] - incomplete[-1])
    figure, axis = plt.subplots(figsize=(8.5, 5.4))
    axis.bar(x, on_time, label="On-time completion", color="#72b7b2")
    axis.bar(x, late, bottom=on_time, label="Late completion", color="#eeca3b")
    axis.bar(
        x,
        incomplete,
        bottom=np.asarray(on_time) + np.asarray(late),
        label="Incomplete",
        color="#e45756",
    )
    axis.set_xticks(x, [SHORT_LABELS[arm] for arm in arms])
    axis.set_ylabel("All generated frames (%)")
    axis.set_title("Deadline outcome composition")
    axis.legend()
    axis.grid(axis="y", alpha=0.2)
    artifacts.extend(_finish(figure, output, "deadline_outcome_composition"))

    figure, axis = plt.subplots(figsize=(8.5, 5.4))
    for arm in arms:
        values = historical[arm]["bursts"]
        x_values, y_values = _empirical_cdf(np, values)
        axis.step(x_values, y_values, where="post", label=SHORT_LABELS[arm],
                  color=COLORS[arm])
    axis.set_xscale("log")
    axis.set_xlabel("Consecutive deadline misses per burst")
    axis.set_ylabel("Empirical CDF of miss bursts")
    axis.set_title("Deadline-miss burst length")
    axis.grid(alpha=0.25)
    axis.legend()
    artifacts.extend(_finish(figure, output, "deadline_miss_burst_cdf"))
    return artifacts


def _p23_plot(plt: Any, np: Any, historical: dict[str, dict[str, Any]], output: Path) -> list[Path]:
    scenario = "compound-shift-qualification-p23"
    seeds = sorted(
        run["seed"]
        for run in historical["str_mlo_nmaxinflights_1"]["runs"]
        if run["scenario_id"] == scenario
    )
    _require(len(seeds) == 4, "p23 does not contain four frozen replicates")
    x = np.arange(len(seeds))
    width = 0.25
    figure, axis = plt.subplots(figsize=(9.5, 5.4))
    for index, arm in enumerate(formal.ARM_IDS):
        indexed = {
            run["seed"]: run["miss_rate"]
            for run in historical[arm]["runs"]
            if run["scenario_id"] == scenario
        }
        _require(set(indexed) == set(seeds), f"p23 seed pairing differs for {arm}")
        axis.bar(
            x + (index - 1) * width,
            [100 * indexed[seed] for seed in seeds],
            width,
            label=SHORT_LABELS[arm],
            color=COLORS[arm],
        )
    axis.set_xticks(x, [str(seed) for seed in seeds])
    axis.set_xlabel("Frozen replicate seed")
    axis.set_ylabel("Deadline misses (%)")
    axis.set_ylim(0, 100)
    axis.set_title("Exact repaired p23 scenario: all generated frames")
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    return _finish(figure, output, "compound_p23_exact")


def render(
    report: dict[str, Any],
    historical: dict[str, dict[str, Any]],
    output: Path,
) -> list[Path]:
    plt, np = _plot_modules()
    artifacts: list[Path] = []
    artifacts.extend(_aggregate_plot(plt, report, output))
    artifacts.extend(_family_plots(plt, report, output))
    artifacts.extend(_scenario_plots(plt, report, output))
    artifacts.extend(_completion_plots(plt, np, historical, output))
    artifacts.extend(_outcome_and_burst_plots(plt, np, historical, output))
    artifacts.extend(_p23_plot(plt, np, historical, output))
    plt.close("all")
    expected = {
        f"{name}.{suffix}" for name in EXPECTED_PLOTS for suffix in ("png", "pdf")
    }
    _require({path.name for path in artifacts} == expected,
             "rendered plot set differs")
    return artifacts


def write_plots(
    analysis: Path, campaign_input: Path, output: Path
) -> Path:
    report, run_rows, _family_rows, _scenario_rows, analysis_identity = load_analysis(
        analysis
    )
    run_root, _manifest = formal._manifest_root(campaign_input)
    historical = collect_historical(run_root, run_rows, report)
    _require(not output.exists(), f"plot output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        artifacts = render(report, historical, temporary)
        manifest = {
            "schema_version": 1,
            "manifest_id": PLOT_ID,
            "analysis_artifact_manifest": analysis_identity,
            "plotter": {
                "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": _sha256(Path(__file__).resolve()),
            },
            "warning": WATERMARK,
            "historical_raw_reduction": {
                "run_count": sum(len(historical[arm]["runs"]) for arm in formal.ARM_IDS),
                "all_runs_match_analyzed_counts": True,
                "completed_latency_population_is_survivor_conditioned": True,
            },
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in artifacts
            },
        }
        manifest_path = temporary / "plot_artifact_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="ascii",
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output.resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_directory", type=Path)
    parser.add_argument("campaign_input", type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        output = write_plots(
            arguments.analysis_directory.resolve(),
            arguments.campaign_input.resolve(),
            arguments.output_directory.resolve(),
        )
    except (CompleteReliabilityPlotError, OSError, ValueError) as error:
        parser.exit(2, f"ERROR: {error}\n")
    print(f"WROTE {output} figures={len(EXPECTED_PLOTS)} formats=png,pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
