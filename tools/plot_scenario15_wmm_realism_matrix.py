#!/usr/bin/env python3
"""Plot the archived scenario-15 WMM realism matrix."""

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

from analyze_scenario15_wmm_realism_matrix import (
    ANALYSIS_ID,
    ARM_IDENTITIES,
    ARM_LABELS,
    PROFILE_SPECS,
    AnalysisError,
)


COLORS = {
    "str_mlo": "#6f6f6f",
    "score_aware_t2_v2": "#4c78a8",
    "distributional_shadow_t2": "#00897b",
}
PROFILE_SHORT_LABELS = {
    "be_be": "Target BE\ncompetitors BE",
    "af41_vi_be": "Target VI\ncompetitors BE",
    "af41_vi_one_vi_per_channel": "Target VI\none VI/channel",
    "af41_vi_all_vi": "Target VI\nall competitors VI",
}
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
        or report.get("campaign_checks", {}).get("strictly_validated_run_count") != 120
        or plot_data.get("schema_version") != 1
        or plot_data.get("analysis") != "scenario15_wmm_realism_plot_data_v1"
    ):
        raise AnalysisError("plot inputs do not form the formal WMM realism report")
    expected = {
        f"{profile}:{arm}" for profile in PROFILE_SPECS for arm in ARM_IDENTITIES
    }
    series = plot_data.get("series")
    if not isinstance(series, dict) or set(series) != expected:
        raise AnalysisError("plot data does not contain the exact twelve treatment cells")
    for key, item in series.items():
        profile, arm = key.split(":", 1)
        if item.get("profile") != profile or item.get("arm") != arm:
            raise AnalysisError(f"plot series identity differs for {key}")
        expected_completed = report["treatments"][profile][arm]["completed_frame_count"]
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
    for suffix in ("png", "pdf"):
        path = output_directory / f"{stem}.{suffix}"
        figure.savefig(path, dpi=180 if suffix == "png" else None)
        files[path.name] = {
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    plt.close(figure)


def _curve_figure(
    series: dict[str, Any], field: str, title: str, y_min: float
) -> plt.Figure:
    figure, axes = plt.subplots(2, 2, figsize=(13, 9.5), sharex=True, sharey=True)
    x_max = DEADLINE_MS * 1.08
    for axis, profile in zip(axes.flat, PROFILE_SPECS):
        for arm in ARM_IDENTITIES:
            curve = series[f"{profile}:{arm}"][field]
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
        axis.set_title(PROFILE_SHORT_LABELS[profile])
        axis.set_xlabel("Latency (ms)")
        axis.set_ylabel("CDF")
        axis.set_ylim(y_min, 1.002)
        _finish_axis(axis)
    axes.flat[0].set_xlim(0, min(max(x_max, 40.0), 100.0))
    handles, labels = axes.flat[-1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=4, fontsize="small")
    figure.suptitle(title + "\nMedian per-run curve with 10th-90th percentile run band")
    figure.tight_layout(rect=(0, 0.07, 1, 0.94))
    return figure


def _pdf_figure(series: dict[str, Any], field: str, title: str) -> plt.Figure:
    figure, axes = plt.subplots(2, 2, figsize=(13, 9.5), sharex=True, sharey=True)
    x_max = 40.0
    for axis, profile in zip(axes.flat, PROFILE_SPECS):
        for arm in ARM_IDENTITIES:
            density = series[f"{profile}:{arm}"][field]
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
        axis.set_title(PROFILE_SHORT_LABELS[profile])
        axis.set_xlabel("Latency (ms)")
        axis.set_ylabel("Probability density (1/ms)")
        _finish_axis(axis)
    axes.flat[0].set_xlim(0, min(x_max, 100.0))
    handles, labels = axes.flat[-1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=4, fontsize="small")
    figure.suptitle(title + "\nMedian per-run density with 10th-90th percentile run band")
    figure.tight_layout(rect=(0, 0.07, 1, 0.94))
    return figure


def _headline_bar_figure(
    report: dict[str, Any], metric: str, scale: float, ylabel: str, title: str
) -> plt.Figure:
    figure, axis = plt.subplots(figsize=(12, 6.5))
    positions = np.arange(len(PROFILE_SPECS), dtype=float)
    width = 0.24
    offsets = np.linspace(-width, width, len(ARM_IDENTITIES))
    for (arm, offset) in zip(ARM_IDENTITIES, offsets):
        values = []
        lower = []
        upper = []
        for profile in PROFILE_SPECS:
            interval = report["treatments"][profile][arm][metric]
            value = scale * interval["estimate"]
            values.append(value)
            lower.append(value - scale * interval["ci95_low"])
            upper.append(scale * interval["ci95_high"] - value)
        bars = axis.bar(
            positions + offset,
            values,
            width,
            yerr=np.asarray([lower, upper]),
            capsize=3,
            label=ARM_LABELS[arm],
            color=COLORS[arm],
            edgecolor="black",
            linewidth=0.45,
        )
        for bar, value in zip(bars, values):
            axis.annotate(
                f"{value:.3f}",
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize="xx-small",
                rotation=30,
            )
    axis.set_xticks(positions, [PROFILE_SHORT_LABELS[p] for p in PROFILE_SPECS])
    axis.set_ylabel(ylabel)
    axis.set_title(title + "\nCell means and 95% whole-run bootstrap intervals")
    handles, labels = axis.get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=3, fontsize="small")
    figure.tight_layout(rect=(0, 0.08, 1, 1))
    _finish_axis(axis)
    return figure


def _policy_vs_str_figure(report: dict[str, Any]) -> plt.Figure:
    figure, axes = plt.subplots(1, 3, figsize=(16, 8), sharey=True)
    comparisons = (
        ("score_aware_t2_v2_minus_str_mlo", "V2 minus STR", "#4c78a8"),
        (
            "distributional_shadow_t2_minus_str_mlo",
            "Distributional minus STR",
            "#00897b",
        ),
    )
    rows = [
        (profile, name, label, color)
        for profile in PROFILE_SPECS
        for name, label, color in comparisons
    ]
    positions = np.arange(len(rows))
    labels = [
        f"{label}\n{PROFILE_SHORT_LABELS[profile].replace(chr(10), ' / ')}"
        for profile, _, label, _ in rows
    ]
    specifications = (
        ("deadline_miss_rate_delta", 100.0, 0.0, "Miss-rate delta (percentage points)"),
        ("completed_frame_p99_delta_us", 0.001, 0.0, "P99 delta (ms)"),
        ("sender_airtime_ratio", 100.0, 100.0, "Sender-airtime difference (%)"),
    )
    for axis, (metric, scale, origin, xlabel) in zip(axes, specifications):
        intervals = [
            report["within_profile_comparisons"][profile][name][metric]
            for profile, name, _, _ in rows
        ]
        values = np.asarray([scale * item["estimate"] - origin for item in intervals])
        lower = values - np.asarray(
            [scale * item["ci95_low"] - origin for item in intervals]
        )
        upper = np.asarray(
            [scale * item["ci95_high"] - origin for item in intervals]
        ) - values
        axis.errorbar(
            values,
            positions,
            xerr=np.asarray([lower, upper]),
            fmt="none",
            ecolor="#333333",
            capsize=4,
        )
        axis.scatter(values, positions, color=[row[3] for row in rows], zorder=3)
        axis.axvline(0, color="#c44536", linestyle=":")
        axis.set_xlabel(xlabel)
        axis.set_yticks(positions, labels)
        axis.invert_yaxis()
        _finish_axis(axis)
    figure.suptitle(
        "Selective duplication compared with STR MLO\n"
        "Negative latency deltas favor selective duplication"
    )
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    return figure


def _competitor_effect_figure(report: dict[str, Any]) -> plt.Figure:
    figure, axes = plt.subplots(1, 3, figsize=(16, 7), sharey=True)
    profiles = (
        "af41_vi_one_vi_per_channel",
        "af41_vi_all_vi",
    )
    rows = [(profile, arm) for profile in profiles for arm in ARM_IDENTITIES]
    positions = np.arange(len(rows))
    labels = [
        f"{ARM_LABELS[arm]}\n{PROFILE_SHORT_LABELS[profile].split(chr(10), 1)[1]}"
        for profile, arm in rows
    ]
    specifications = (
        ("deadline_miss_rate_delta", 100.0, 0.0, "Miss-rate delta (percentage points)"),
        ("completed_frame_p99_delta_us", 0.001, 0.0, "P99 delta (ms)"),
        ("sender_airtime_ratio", 100.0, 100.0, "Sender-airtime difference (%)"),
    )
    for axis, (metric, scale, origin, xlabel) in zip(axes, specifications):
        intervals = [
            report["competitor_effects_with_target_vi"][profile][arm][metric]
            for profile, arm in rows
        ]
        values = np.asarray([scale * item["estimate"] - origin for item in intervals])
        lower = values - np.asarray(
            [scale * item["ci95_low"] - origin for item in intervals]
        )
        upper = np.asarray(
            [scale * item["ci95_high"] - origin for item in intervals]
        ) - values
        axis.errorbar(
            values,
            positions,
            xerr=np.asarray([lower, upper]),
            fmt="none",
            ecolor="#333333",
            capsize=4,
        )
        axis.scatter(values, positions, color=[COLORS[arm] for _, arm in rows], zorder=3)
        axis.axvline(0, color="#c44536", linestyle=":")
        axis.set_xlabel(xlabel)
        axis.set_yticks(positions, labels)
        axis.invert_yaxis()
        _finish_axis(axis)
    figure.suptitle(
        "Effect of VI competitors with target fixed at AF41/VI\n"
        "Each row is relative to target VI / all competitors BE"
    )
    figure.tight_layout(rect=(0, 0, 1, 0.93))
    return figure


def _burst_figure(series: dict[str, Any]) -> plt.Figure:
    figure, axes = plt.subplots(2, 2, figsize=(13, 9.5), sharex=True, sharey=True)
    for axis, profile in zip(axes.flat, PROFILE_SPECS):
        for arm in ARM_IDENTITIES:
            lengths = np.asarray(
                series[f"{profile}:{arm}"]["deadline_miss_burst_cdf"]["lengths"],
                dtype=float,
            )
            if lengths.size:
                probability = np.arange(1, lengths.size + 1) / lengths.size
                axis.step(
                    lengths,
                    probability,
                    where="post",
                    color=COLORS[arm],
                    marker="o",
                    markersize=3,
                    label=ARM_LABELS[arm],
                )
        axis.set_title(PROFILE_SHORT_LABELS[profile])
        axis.set_xlabel("Consecutive deadline-missed frames")
        axis.set_ylabel("CDF across miss bursts")
        axis.set_xscale("log", base=2)
        axis.set_xlim(left=0.8)
        _finish_axis(axis)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=3, fontsize="small")
    figure.suptitle("Deadline-miss burst length")
    figure.tight_layout(rect=(0, 0.07, 1, 0.95))
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
        (
            "latency_cdf",
            lambda: _curve_figure(series, "completed_cdf", "Completed-frame latency CDF", 0),
        ),
        (
            "latency_tail_cdf",
            lambda: _curve_figure(
                series, "completed_cdf", "Completed-frame tail latency CDF", 0.90
            ),
        ),
        (
            "latency_pdf",
            lambda: _pdf_figure(series, "completed_pdf", "Completed-frame latency PDF"),
        ),
        (
            "all_generated_censored_latency_cdf",
            lambda: _curve_figure(
                series,
                "deadline_censored_cdf",
                "All-generated deadline-censored latency CDF",
                0,
            ),
        ),
        (
            "all_generated_censored_latency_pdf",
            lambda: _pdf_figure(
                series,
                "deadline_censored_pdf",
                "All-generated deadline-censored latency PDF",
            ),
        ),
        (
            "deadline_miss",
            lambda: _headline_bar_figure(
                report,
                "all_generated_deadline_miss_rate",
                100,
                "Deadline miss rate (%)",
                "Deadline misses over all generated frames",
            ),
        ),
        (
            "completed_p99",
            lambda: _headline_bar_figure(
                report,
                "completed_frame_p99_us",
                0.001,
                "Mean per-run P99 (ms)",
                "Completed-frame P99 latency",
            ),
        ),
        (
            "deadline_censored_mean",
            lambda: _headline_bar_figure(
                report,
                "deadline_censored_mean_us",
                0.001,
                "Mean censored latency (ms)",
                "All-generated deadline-censored mean latency",
            ),
        ),
        (
            "sender_airtime",
            lambda: _headline_bar_figure(
                report,
                "sender_airtime_us",
                1e-6,
                "Mean sender PHY airtime (s/run)",
                "Target sender airtime",
            ),
        ),
        (
            "background_throughput",
            lambda: _headline_bar_figure(
                report,
                "background_throughput_mbps",
                1,
                "Mean OBSS goodput (Mbps)",
                "Competing OBSS throughput",
            ),
        ),
        ("policy_vs_str", lambda: _policy_vs_str_figure(report)),
        ("vi_competitor_effect", lambda: _competitor_effect_figure(report)),
        ("deadline_miss_burst_cdf", lambda: _burst_figure(series)),
    )
    for stem, factory in figures:
        _save(factory(), output_directory, stem, files)
    manifest = {
        "schema_version": 1,
        "analysis": "scenario15_wmm_realism_figures_v1",
        "generator": str(Path(__file__).resolve().relative_to(ROOT)),
        "source_report": {"path": str(report_path), "sha256": _sha256_file(report_path)},
        "source_plot_data": {
            "path": str(plot_data_path),
            "sha256": _sha256_file(plot_data_path),
        },
        "figure_count": len(figures),
        "files": files,
    }
    manifest_path = output_directory / "figure_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
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
