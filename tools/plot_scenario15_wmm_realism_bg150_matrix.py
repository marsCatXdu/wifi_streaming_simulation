#!/usr/bin/env python3
"""Plot the 1.5x-background WMM matrix and paired load effects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import analyze_scenario15_wmm_realism_bg150_matrix as analysis
import plot_scenario15_wmm_realism_matrix as common


ROOT = Path(__file__).resolve().parents[1]


def _load_inputs(
    report_path: Path, plot_data_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = common._read_json(report_path)
    plot_data = common._read_json(plot_data_path)
    if (
        report.get("schema_version") != 1
        or report.get("analysis") != analysis.ANALYSIS_ID
        or report.get("campaign_checks", {}).get("strictly_validated_run_count") != 120
        or report.get("campaign_checks", {}).get("freshly_validated_baseline_run_count")
        != 120
        or plot_data.get("schema_version") != 1
        or plot_data.get("analysis") != analysis.PLOT_DATA_ID
    ):
        raise analysis.common.AnalysisError("plot inputs are not the formal load repeat")
    expected = {
        f"{profile}:{arm}"
        for profile in analysis.common.PROFILE_SPECS
        for arm in analysis.common.ARM_IDENTITIES
    }
    for field, treatment_field in (
        ("series", "treatments"),
        ("baseline_series", "baseline_treatments"),
    ):
        series = plot_data.get(field)
        if not isinstance(series, dict) or set(series) != expected:
            raise analysis.common.AnalysisError(f"{field} lacks twelve cells")
        for key, item in series.items():
            profile, arm = key.split(":", 1)
            expected_completed = report[treatment_field][profile][arm][
                "completed_frame_count"
            ]
            if (
                item.get("profile") != profile
                or item.get("arm") != arm
                or item.get("completed_frame_count") != expected_completed
            ):
                raise analysis.common.AnalysisError(f"{field} identity differs for {key}")
    return report, plot_data


def _load_effect_figure(report: dict[str, Any]) -> plt.Figure:
    figure, axes = plt.subplots(1, 4, figsize=(19, 8), sharey=True)
    rows = [
        (profile, arm)
        for profile in analysis.common.PROFILE_SPECS
        for arm in analysis.common.ARM_IDENTITIES
    ]
    positions = np.arange(len(rows))
    labels = [
        f"{analysis.common.ARM_LABELS[arm]}\n"
        f"{common.PROFILE_SHORT_LABELS[profile].replace(chr(10), ' / ')}"
        for profile, arm in rows
    ]
    specifications = (
        (
            "deadline_miss_rate_delta",
            lambda item: (
                item["estimate"] * 100,
                item["ci95_low"] * 100,
                item["ci95_high"] * 100,
            ),
            "Miss-rate change (percentage points)",
        ),
        (
            "completed_frame_p99_delta_us",
            lambda item: (
                item["estimate"] / 1000,
                item["ci95_low"] / 1000,
                item["ci95_high"] / 1000,
            ),
            "P99 change (ms)",
        ),
        (
            "sender_airtime_ratio",
            lambda item: (
                (item["estimate"] - 1) * 100,
                (item["ci95_low"] - 1) * 100,
                (item["ci95_high"] - 1) * 100,
            ),
            "Sender-airtime change (%)",
        ),
        (
            "background_throughput_loss",
            lambda item: (
                -item["estimate"] * 100,
                -item["ci95_high"] * 100,
                -item["ci95_low"] * 100,
            ),
            "Achieved OBSS-goodput change (%)",
        ),
    )
    for axis, (metric, transform, xlabel) in zip(axes, specifications):
        intervals = [
            report["load_effects_vs_bg100"][profile][arm][metric]
            for profile, arm in rows
        ]
        transformed = [transform(item) for item in intervals]
        values = np.asarray([item[0] for item in transformed])
        lower = values - np.asarray([item[1] for item in transformed])
        upper = np.asarray([item[2] for item in transformed]) - values
        axis.errorbar(
            values,
            positions,
            xerr=np.asarray([lower, upper]),
            fmt="none",
            ecolor="#333333",
            capsize=4,
        )
        axis.scatter(
            values,
            positions,
            color=[common.COLORS[arm] for _, arm in rows],
            zorder=3,
        )
        axis.axvline(0, color="#c44536", linestyle=":")
        axis.set_xlabel(xlabel)
        axis.set_yticks(positions, labels)
        axis.invert_yaxis()
        common._finish_axis(axis)
    figure.suptitle(
        "Effect of increasing every OBSS ON-period rate by 50%\n"
        "Each row is paired 1.5x minus 1.0x on identical seeds"
    )
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    return figure


def _load_overlay_curve_figure(
    treatment: dict[str, Any],
    baseline: dict[str, Any],
    field: str,
    title: str,
    y_min: float,
) -> plt.Figure:
    figure, axes = plt.subplots(2, 2, figsize=(13, 9.5), sharex=True, sharey=True)
    x_max = common.DEADLINE_MS * 1.08
    for axis, profile in zip(axes.flat, analysis.common.PROFILE_SPECS):
        for arm in analysis.common.ARM_IDENTITIES:
            for series, linestyle, load_label, alpha in (
                (baseline, "--", "1.0x", 0.70),
                (treatment, "-", "1.5x", 1.0),
            ):
                curve = series[f"{profile}:{arm}"][field]
                probability = np.asarray(curve["probabilities"], dtype=float)
                center = np.asarray(
                    curve["run_hf7_median_latency_us"], dtype=float
                ) / 1000
                axis.plot(
                    center,
                    probability,
                    color=common.COLORS[arm],
                    linestyle=linestyle,
                    alpha=alpha,
                    label=f"{analysis.common.ARM_LABELS[arm]} {load_label}",
                )
                tail = center[probability <= 0.9999]
                if tail.size:
                    x_max = max(x_max, float(tail[-1]) * 1.05)
        axis.axvline(common.DEADLINE_MS, color="#c44536", linestyle=":")
        axis.set_title(common.PROFILE_SHORT_LABELS[profile])
        axis.set_xlabel("Latency (ms)")
        axis.set_ylabel("CDF")
        axis.set_ylim(y_min, 1.002)
        common._finish_axis(axis)
    axes.flat[0].set_xlim(0, min(max(x_max, 40.0), 100.0))
    handles, labels = axes.flat[-1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=3, fontsize="small")
    figure.suptitle(title + "\nDashed: 1.0x offered load; solid: 1.5x offered load")
    figure.tight_layout(rect=(0, 0.09, 1, 0.94))
    return figure


def generate(
    report_path: Path, plot_data_path: Path, output_directory: Path
) -> dict[str, Any]:
    report_path = report_path.resolve()
    plot_data_path = plot_data_path.resolve()
    report, plot_data = _load_inputs(report_path, plot_data_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    series = plot_data["series"]
    baseline_series = plot_data["baseline_series"]
    files: dict[str, dict[str, Any]] = {}
    figures: tuple[tuple[str, Callable[[], plt.Figure]], ...] = (
        (
            "latency_cdf",
            lambda: common._curve_figure(
                series, "completed_cdf", "Completed-frame latency CDF at 1.5x load", 0
            ),
        ),
        (
            "latency_tail_cdf",
            lambda: common._curve_figure(
                series,
                "completed_cdf",
                "Completed-frame tail latency CDF at 1.5x load",
                0.90,
            ),
        ),
        (
            "latency_pdf",
            lambda: common._pdf_figure(
                series, "completed_pdf", "Completed-frame latency PDF at 1.5x load"
            ),
        ),
        (
            "all_generated_censored_latency_cdf",
            lambda: common._curve_figure(
                series,
                "deadline_censored_cdf",
                "All-generated deadline-censored latency CDF at 1.5x load",
                0,
            ),
        ),
        (
            "all_generated_censored_latency_pdf",
            lambda: common._pdf_figure(
                series,
                "deadline_censored_pdf",
                "All-generated deadline-censored latency PDF at 1.5x load",
            ),
        ),
        (
            "deadline_miss",
            lambda: common._headline_bar_figure(
                report,
                "all_generated_deadline_miss_rate",
                100,
                "Deadline miss rate (%)",
                "Deadline misses at 1.5x background offered load",
            ),
        ),
        (
            "completed_p99",
            lambda: common._headline_bar_figure(
                report,
                "completed_frame_p99_us",
                0.001,
                "Mean per-run P99 (ms)",
                "Completed-frame P99 at 1.5x background offered load",
            ),
        ),
        (
            "deadline_censored_mean",
            lambda: common._headline_bar_figure(
                report,
                "deadline_censored_mean_us",
                0.001,
                "Mean censored latency (ms)",
                "All-generated censored mean at 1.5x background offered load",
            ),
        ),
        (
            "sender_airtime",
            lambda: common._headline_bar_figure(
                report,
                "sender_airtime_us",
                1e-6,
                "Mean sender PHY airtime (s/run)",
                "Target sender airtime at 1.5x background offered load",
            ),
        ),
        (
            "background_throughput",
            lambda: common._headline_bar_figure(
                report,
                "background_throughput_mbps",
                1,
                "Mean OBSS goodput (Mbps)",
                "Competing OBSS goodput at 1.5x offered load",
            ),
        ),
        ("policy_vs_str", lambda: common._policy_vs_str_figure(report)),
        ("vi_competitor_effect", lambda: common._competitor_effect_figure(report)),
        ("deadline_miss_burst_cdf", lambda: common._burst_figure(series)),
        ("bg150_vs_bg100_effects", lambda: _load_effect_figure(report)),
        (
            "bg150_vs_bg100_completed_tail_cdf",
            lambda: _load_overlay_curve_figure(
                series,
                baseline_series,
                "completed_cdf",
                "Completed-frame tail CDF under background-load scaling",
                0.90,
            ),
        ),
        (
            "bg150_vs_bg100_censored_tail_cdf",
            lambda: _load_overlay_curve_figure(
                series,
                baseline_series,
                "deadline_censored_cdf",
                "All-generated deadline-censored tail CDF under load scaling",
                0.90,
            ),
        ),
    )
    for stem, factory in figures:
        common._save(factory(), output_directory, stem, files)
    manifest = {
        "schema_version": 1,
        "analysis": "scenario15_wmm_realism_bg150_figures_v1",
        "generator": str(Path(__file__).resolve().relative_to(ROOT)),
        "source_report": {
            "path": str(report_path),
            "sha256": common._sha256_file(report_path),
        },
        "source_plot_data": {
            "path": str(plot_data_path),
            "sha256": common._sha256_file(plot_data_path),
        },
        "figure_count": len(figures),
        "files": files,
    }
    (output_directory / "figure_manifest.json").write_text(
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
