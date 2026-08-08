#!/usr/bin/env python3
"""Plot the paired scenario-15 WMM comparison from archived analysis data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze_scenario15_wmm_comparison import (
    ANALYSIS_ID,
    ARM_IDENTITIES,
    ARM_LABELS,
    AnalysisError,
    WMM_PROFILES,
)


COLORS = {
    "str_mlo": "#6f6f6f",
    "score_aware_t2_v2": "#4c78a8",
    "distributional_shadow_t2": "#00897b",
}
MODE_STYLES = {"off": "--", "on": "-"}
MODE_LABELS = {"off": "WMM off: CS0 / AC_BE", "on": "WMM on: CS5 / AC_VI"}
DEADLINE_MS = 33.333


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise AnalysisError(f"{path}: expected a JSON object")
    return value


def _load_inputs(
    report_path: Path, plot_data_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = _read_json(report_path)
    plot_data = _read_json(plot_data_path)
    if (
        report.get("schema_version") != 1
        or report.get("analysis") != ANALYSIS_ID
        or report.get("campaign_checks", {}).get("strictly_validated_run_count") != 288
        or plot_data.get("schema_version") != 1
        or plot_data.get("analysis") != "scenario15_wmm_plot_data_v1"
    ):
        raise AnalysisError("plot inputs do not form the formal scenario-15 WMM report")
    expected = {
        f"{mode}:{arm}" for mode in WMM_PROFILES for arm in ARM_IDENTITIES
    }
    series = plot_data.get("series")
    if not isinstance(series, dict) or set(series) != expected:
        raise AnalysisError("plot data does not contain the exact six treatment cells")
    for key, item in series.items():
        mode, arm = key.split(":", 1)
        if item.get("wmm_mode") != mode or item.get("arm") != arm:
            raise AnalysisError(f"plot series identity differs for {key}")
        expected_completed = report["treatments"][mode][arm]["completed_frame_count"]
        if item.get("completed_frame_count") != expected_completed:
            raise AnalysisError(f"plot series frame count differs for {key}")
    return report, plot_data


def _finish_axis(axis: plt.Axes) -> None:
    axis.grid(alpha=0.22)


def _save(
    figure: plt.Figure,
    output_directory: Path,
    stem: str,
    files: dict[str, dict[str, Any]],
) -> None:
    figure.tight_layout()
    for suffix in ("png", "pdf"):
        path = output_directory / f"{stem}.{suffix}"
        figure.savefig(path, dpi=180 if suffix == "png" else None)
        files[path.name] = {
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    plt.close(figure)


def _curve_figure(
    series: dict[str, Any],
    field: str,
    title: str,
    y_min: float,
) -> plt.Figure:
    figure, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharex=True, sharey=True)
    x_max = DEADLINE_MS * 1.08
    for axis, mode in zip(axes, WMM_PROFILES):
        for arm in ARM_IDENTITIES:
            curve = series[f"{mode}:{arm}"][field]
            probability = np.asarray(curve["probabilities"], dtype=float)
            center = np.asarray(curve["run_hf7_median_latency_us"], dtype=float) / 1000
            lower = np.asarray(curve["run_hf7_p10_latency_us"], dtype=float) / 1000
            upper = np.asarray(curve["run_hf7_p90_latency_us"], dtype=float) / 1000
            axis.plot(center, probability, color=COLORS[arm], label=ARM_LABELS[arm])
            axis.fill_betweenx(probability, lower, upper, color=COLORS[arm], alpha=0.12)
            tail = center[probability <= 0.9999]
            if tail.size:
                x_max = max(x_max, float(tail[-1]) * 1.05)
        axis.axvline(DEADLINE_MS, color="#c44536", linestyle=":", label="Deadline")
        axis.set_title(MODE_LABELS[mode])
        axis.set_xlabel("Latency (ms)")
        axis.set_ylim(y_min, 1.002)
        _finish_axis(axis)
    x_max = min(max(x_max, 40.0), 100.0)
    axes[0].set_xlim(0, x_max)
    axes[0].set_ylabel("CDF")
    handles, labels = axes[1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=4, fontsize="small")
    figure.suptitle(title + "\nMedian per-run curve with 10th-90th percentile run band")
    figure.subplots_adjust(bottom=0.17)
    return figure


def _pdf_figure(series: dict[str, Any], field: str, title: str) -> plt.Figure:
    figure, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharex=True, sharey=True)
    x_max = 40.0
    for axis, mode in zip(axes, WMM_PROFILES):
        for arm in ARM_IDENTITIES:
            density = series[f"{mode}:{arm}"][field]
            width_us = float(density["bin_width_us"])
            centers_ms = (
                np.asarray(density["bin_left_us"], dtype=float) + width_us / 2
            ) / 1000
            center = np.asarray(density["run_density_median_per_ms"], dtype=float)
            lower = np.asarray(density["run_density_p10_per_ms"], dtype=float)
            upper = np.asarray(density["run_density_p90_per_ms"], dtype=float)
            axis.plot(centers_ms, center, color=COLORS[arm], label=ARM_LABELS[arm])
            axis.fill_between(centers_ms, lower, upper, color=COLORS[arm], alpha=0.12)
            nonzero = np.flatnonzero(np.asarray(density["pooled_counts"], dtype=int))
            if nonzero.size:
                x_max = max(x_max, float(centers_ms[nonzero[-1]]) * 1.02)
        axis.axvline(DEADLINE_MS, color="#c44536", linestyle=":", label="Deadline")
        axis.set_title(MODE_LABELS[mode])
        axis.set_xlabel("Latency (ms)")
        _finish_axis(axis)
    axes[0].set_xlim(0, min(x_max, 100.0))
    axes[0].set_ylabel("Probability density (1/ms)")
    handles, labels = axes[1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=4, fontsize="small")
    figure.suptitle(title + "\nMedian per-run density with 10th-90th percentile run band")
    figure.subplots_adjust(bottom=0.17)
    return figure


def _headline_bar_figure(
    report: dict[str, Any],
    metric: str,
    scale: float,
    ylabel: str,
    title: str,
) -> plt.Figure:
    figure, axis = plt.subplots(figsize=(9, 5.6))
    positions = np.arange(len(ARM_IDENTITIES), dtype=float)
    width = 0.34
    for mode, offset, hatch in (("off", -width / 2, "//"), ("on", width / 2, "")):
        values = []
        lower = []
        upper = []
        for arm in ARM_IDENTITIES:
            interval = report["treatments"][mode][arm][metric]
            value = scale * interval["estimate"]
            values.append(value)
            lower.append(value - scale * interval["ci95_low"])
            upper.append(scale * interval["ci95_high"] - value)
        bars = axis.bar(
            positions + offset,
            values,
            width,
            yerr=np.asarray([lower, upper]),
            capsize=4,
            label=MODE_LABELS[mode],
            color=[COLORS[arm] for arm in ARM_IDENTITIES],
            alpha=0.58 if mode == "off" else 0.96,
            hatch=hatch,
            edgecolor="black",
            linewidth=0.5,
        )
        for bar, value in zip(bars, values):
            axis.annotate(
                f"{value:.3f}",
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize="x-small",
            )
    axis.set_xticks(positions, [ARM_LABELS[arm] for arm in ARM_IDENTITIES])
    axis.set_ylabel(ylabel)
    axis.set_title(title + "\nCell means and 95% whole-run bootstrap intervals")
    axis.legend(fontsize="small")
    _finish_axis(axis)
    return figure


def _paired_effects_figure(report: dict[str, Any]) -> plt.Figure:
    figure, axes = plt.subplots(1, 3, figsize=(15, 5.5), sharey=True)
    arms = list(ARM_IDENTITIES)
    positions = np.arange(len(arms))
    specifications: tuple[tuple[str, float, float, str], ...] = (
        ("deadline_miss_rate_delta", 100.0, 0.0, "Miss-rate delta (percentage points)"),
        ("completed_frame_p99_delta_us", 0.001, 0.0, "P99 delta (ms)"),
        ("sender_airtime_ratio", 100.0, 100.0, "Sender-airtime change (%)"),
    )
    for axis, (metric, scale, origin, xlabel) in zip(axes, specifications):
        intervals = [report["wmm_effects"][arm][metric] for arm in arms]
        values = np.asarray([scale * item["estimate"] - origin for item in intervals])
        lower = values - np.asarray([scale * item["ci95_low"] - origin for item in intervals])
        upper = np.asarray([scale * item["ci95_high"] - origin for item in intervals]) - values
        axis.errorbar(
            values,
            positions,
            xerr=np.asarray([lower, upper]),
            fmt="o",
            color="#222222",
            capsize=4,
        )
        axis.axvline(0, color="#c44536", linestyle=":")
        axis.set_xlabel(xlabel)
        axis.set_yticks(positions, [ARM_LABELS[arm] for arm in arms])
        axis.invert_yaxis()
        _finish_axis(axis)
    figure.suptitle("Paired WMM effect: on minus off\n95% paired whole-run bootstrap intervals")
    return figure


def _within_mode_figure(report: dict[str, Any]) -> plt.Figure:
    figure, axes = plt.subplots(1, 3, figsize=(15, 5.5), sharey=True)
    comparisons = (
        ("score_aware_t2_v2_minus_str_mlo", "V2 minus STR"),
        ("distributional_shadow_t2_minus_str_mlo", "Distributional minus STR"),
    )
    rows = [(mode, name, label) for mode in WMM_PROFILES for name, label in comparisons]
    positions = np.arange(len(rows))
    labels = [f"{label}\nWMM {mode}" for mode, _, label in rows]
    specifications: tuple[tuple[str, float, float, str], ...] = (
        ("deadline_miss_rate_delta", 100.0, 0.0, "Miss-rate delta (percentage points)"),
        ("completed_frame_p99_delta_us", 0.001, 0.0, "P99 delta (ms)"),
        ("sender_airtime_ratio", 100.0, 100.0, "Sender-airtime difference (%)"),
    )
    for axis, (metric, scale, origin, xlabel) in zip(axes, specifications):
        intervals = [report["within_mode_comparisons"][mode][name][metric] for mode, name, _ in rows]
        values = np.asarray([scale * item["estimate"] - origin for item in intervals])
        lower = values - np.asarray([scale * item["ci95_low"] - origin for item in intervals])
        upper = np.asarray([scale * item["ci95_high"] - origin for item in intervals]) - values
        colors = ["#4c78a8" if "v2" in name else "#00897b" for _, name, _ in rows]
        axis.errorbar(
            values,
            positions,
            xerr=np.asarray([lower, upper]),
            fmt="none",
            ecolor="#333333",
            capsize=4,
        )
        axis.scatter(values, positions, color=colors, zorder=3)
        axis.axvline(0, color="#c44536", linestyle=":")
        axis.set_xlabel(xlabel)
        axis.set_yticks(positions, labels)
        axis.invert_yaxis()
        _finish_axis(axis)
    figure.suptitle("Selective duplication compared with STR MLO\nNegative latency deltas favor selective duplication")
    return figure


def _burst_figure(series: dict[str, Any]) -> plt.Figure:
    figure, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharex=True, sharey=True)
    for axis, mode in zip(axes, WMM_PROFILES):
        for arm in ARM_IDENTITIES:
            lengths = np.asarray(
                series[f"{mode}:{arm}"]["deadline_miss_burst_cdf"]["lengths"],
                dtype=float,
            )
            if lengths.size:
                probability = np.arange(1, lengths.size + 1) / lengths.size
                axis.step(lengths, probability, where="post", color=COLORS[arm], label=ARM_LABELS[arm])
        axis.set_title(MODE_LABELS[mode])
        axis.set_xlabel("Consecutive deadline-missed frames")
        axis.set_xscale("log", base=2)
        axis.set_xlim(left=1)
        _finish_axis(axis)
    axes[0].set_ylabel("CDF across miss bursts")
    handles, labels = axes[1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=3, fontsize="small")
    figure.suptitle("Deadline-miss burst length")
    figure.subplots_adjust(bottom=0.17)
    return figure


def generate(
    report_path: Path, plot_data_path: Path, output_directory: Path
) -> dict[str, Any]:
    report_path = report_path.resolve()
    plot_data_path = plot_data_path.resolve()
    report, plot_data = _load_inputs(report_path, plot_data_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    series = plot_data["series"]
    files: dict[str, dict[str, Any]] = {}
    figures: tuple[tuple[str, Callable[[], plt.Figure]], ...] = (
        ("latency_cdf", lambda: _curve_figure(series, "completed_cdf", "Completed-frame latency CDF", 0.0)),
        ("latency_tail_cdf", lambda: _curve_figure(series, "completed_cdf", "Completed-frame tail latency CDF", 0.90)),
        ("latency_pdf", lambda: _pdf_figure(series, "completed_pdf", "Completed-frame latency PDF")),
        ("all_generated_censored_latency_cdf", lambda: _curve_figure(series, "deadline_censored_cdf", "All-generated deadline-censored latency CDF", 0.0)),
        ("all_generated_censored_latency_pdf", lambda: _pdf_figure(series, "deadline_censored_pdf", "All-generated deadline-censored latency PDF")),
        ("deadline_miss", lambda: _headline_bar_figure(report, "all_generated_deadline_miss_rate", 100.0, "Deadline miss rate (%)", "Deadline misses over all generated frames")),
        ("completed_p99", lambda: _headline_bar_figure(report, "completed_frame_p99_us", 0.001, "Mean per-run P99 (ms)", "Completed-frame P99 latency")),
        ("sender_airtime", lambda: _headline_bar_figure(report, "sender_airtime_us", 1e-6, "Mean sender PHY airtime (s/run)", "Target sender airtime")),
        ("background_throughput", lambda: _headline_bar_figure(report, "background_throughput_mbps", 1.0, "Mean background throughput (Mbps)", "Background throughput")),
        ("paired_wmm_effects", lambda: _paired_effects_figure(report)),
        ("policy_vs_str", lambda: _within_mode_figure(report)),
        ("deadline_miss_burst_cdf", lambda: _burst_figure(series)),
    )
    for stem, factory in figures:
        _save(factory(), output_directory, stem, files)
    manifest = {
        "schema_version": 1,
        "analysis": "scenario15_wmm_figures_v1",
        "generator": str(Path(__file__).resolve().relative_to(Path(__file__).resolve().parents[1])),
        "source_report": {"path": str(report_path), "sha256": _sha256_file(report_path)},
        "source_plot_data": {"path": str(plot_data_path), "sha256": _sha256_file(plot_data_path)},
        "figure_count": len(figures),
        "files": files,
    }
    manifest_path = output_directory / "figure_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("plot_data", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    manifest = generate(args.report, args.plot_data, args.output_directory.resolve())
    print(f"WROTE {args.output_directory.resolve()} figures={manifest['figure_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
