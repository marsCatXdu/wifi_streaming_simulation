#!/usr/bin/env python3
"""Create paired UL OFDMA off/on comparison figures."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from plot_results import _approach_label, _deadline_title, _latencies
from summarize_runs import confidence


def _base_key(item: dict) -> tuple[str, str, int]:
    wifi = item.get("config", {}).get("wifi", {})
    return item["topology"], item["policy"], int(wifi.get("sta_max_inflights", 1))


def _ofdma_enabled(item: dict) -> bool:
    return bool(item.get("config", {}).get("wifi", {}).get("ul_ofdma_enabled", False))


def _base_label(item: dict) -> str:
    clone = {
        "topology": item["topology"],
        "policy": item["policy"],
        "config": {"wifi": {
            "sta_max_inflights": item.get("config", {}).get("wifi", {}).get(
                "sta_max_inflights", 1
            ),
        }},
    }
    return _approach_label(clone)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _band(samples: list[np.ndarray], probabilities: np.ndarray) -> tuple[np.ndarray, ...]:
    quantiles = np.vstack([
        np.quantile(values, probabilities, method="linear") for values in samples
    ])
    return (
        np.median(quantiles, axis=0),
        np.quantile(quantiles, 0.10, axis=0),
        np.quantile(quantiles, 0.90, axis=0),
    )


def _errorbar(ax: plt.Axes,
              positions: np.ndarray,
              summaries: list[dict],
              label: str,
              offset: float) -> None:
    means = np.asarray([item["mean"] for item in summaries], dtype=float)
    lower = np.asarray([
        item["mean"] - item["ci95_low"] if item["ci95_low"] is not None else 0
        for item in summaries
    ])
    upper = np.asarray([
        item["ci95_high"] - item["mean"] if item["ci95_high"] is not None else 0
        for item in summaries
    ])
    ax.errorbar(positions + offset, means, yerr=np.vstack([lower, upper]),
                fmt="o", capsize=4, label=label)


def plot_ofdma_comparison(aggregate: dict, output_dir: Path) -> None:
    runs = aggregate.get("runs", [])
    output_dir.mkdir(parents=True, exist_ok=True)
    approaches = sorted({_base_key(run) for run in runs})
    representatives = {
        key: next(run for run in runs if _base_key(run) == key) for key in approaches
    }
    labels = [_base_label(representatives[key]) for key in approaches]

    distributions = output_dir / "latency_distributions"
    distributions.mkdir(exist_ok=True)
    probabilities = np.linspace(0.0, 1.0, 201)
    for key in approaches:
        members = [run for run in runs if _base_key(run) == key]
        samples = {
            state: [_latencies(run) for run in members if _ofdma_enabled(run) == state]
            for state in (False, True)
        }
        if not all(samples.values()):
            continue
        figure, axes = plt.subplots(1, 2, figsize=(11, 4.5))
        for state, color in ((False, "tab:blue"), (True, "tab:orange")):
            usable = [values for values in samples[state] if values.size]
            center, lower, upper = _band(usable, probabilities)
            label = f"UL OFDMA {'on' if state else 'off'}"
            axes[0].plot(center / 1000, probabilities, color=color, label=label)
            axes[0].fill_betweenx(probabilities, lower / 1000, upper / 1000,
                                  color=color, alpha=0.2)
        all_values = [value for state in samples.values() for values in state for value in values]
        bins = np.linspace(min(all_values), max(all_values), 81)
        centers = (bins[:-1] + bins[1:]) / 2000
        for state, color in ((False, "tab:blue"), (True, "tab:orange")):
            densities = np.vstack([
                np.histogram(values, bins=bins, density=True)[0] for values in samples[state]
            ])
            center = np.median(densities, axis=0)
            lower = np.quantile(densities, 0.10, axis=0)
            upper = np.quantile(densities, 0.90, axis=0)
            label = f"UL OFDMA {'on' if state else 'off'}"
            axes[1].plot(centers, center, color=color, label=label)
            axes[1].fill_between(centers, lower, upper, color=color, alpha=0.2)
        axes[0].set(xlabel="Frame Completion Latency (ms)", ylabel="CDF")
        axes[1].set(xlabel="Frame Completion Latency (ms)", ylabel="Probability density")
        for axis in axes:
            axis.grid(alpha=0.25)
            axis.legend()
        figure.suptitle(_base_label(representatives[key]))
        figure.tight_layout()
        figure.savefig(distributions / f"{_slug(_base_label(representatives[key]))}.png",
                       dpi=160)
        plt.close(figure)

    positions = np.arange(len(approaches), dtype=float)
    figure, axes = plt.subplots(1, 2, figsize=(13, 5))
    for state, offset in ((False, -0.12), (True, 0.12)):
        state_runs = [
            [run for run in runs if _base_key(run) == key and _ofdma_enabled(run) == state]
            for key in approaches
        ]
        p99 = [confidence([run["latency_p99_us"] / 1000
                           for run in members if run["latency_p99_us"] is not None])
               for members in state_runs]
        misses = [confidence([run["deadline_miss_ratio"] * 100 for run in members])
                  for members in state_runs]
        label = f"UL OFDMA {'on' if state else 'off'}"
        _errorbar(axes[0], positions, p99, label, offset)
        _errorbar(axes[1], positions, misses, label, offset)
    axes[0].set_ylabel("Run P99 latency mean (ms)")
    axes[1].set_ylabel("Deadline-miss ratio mean (%)")
    for axis in axes:
        axis.set_xticks(positions, labels, rotation=20, ha="right")
        axis.grid(alpha=0.25)
        axis.legend()
    figure.suptitle(_deadline_title(
        "UL OFDMA off/on comparison with 95% confidence intervals", runs
    ))
    figure.tight_layout()
    figure.savefig(output_dir / "ofdma_group_comparison.png", dpi=160)
    plt.close(figure)

    p99_deltas = []
    miss_deltas = []
    for key in approaches:
        members = [run for run in runs if _base_key(run) == key]
        off = {run["seed"]: run for run in members if not _ofdma_enabled(run)}
        on = {run["seed"]: run for run in members if _ofdma_enabled(run)}
        common = sorted(off.keys() & on.keys())
        p99_deltas.append(confidence([
            (on[seed]["latency_p99_us"] - off[seed]["latency_p99_us"]) / 1000
            for seed in common
            if on[seed]["latency_p99_us"] is not None
            and off[seed]["latency_p99_us"] is not None
        ]))
        miss_deltas.append(confidence([
            (on[seed]["deadline_miss_ratio"] - off[seed]["deadline_miss_ratio"]) * 100
            for seed in common
        ]))
    figure, axes = plt.subplots(1, 2, figsize=(13, 5))
    _errorbar(axes[0], positions, p99_deltas, "OFDMA on minus off", 0)
    _errorbar(axes[1], positions, miss_deltas, "OFDMA on minus off", 0)
    axes[0].set_ylabel("Paired P99 latency change (ms)")
    axes[1].set_ylabel("Paired deadline-miss change (percentage points)")
    for axis in axes:
        axis.axhline(0, color="black", linewidth=1)
        axis.set_xticks(positions, labels, rotation=20, ha="right")
        axis.grid(alpha=0.25)
    figure.suptitle(_deadline_title(
        "Paired UL OFDMA effect with 95% confidence intervals", runs
    ))
    figure.tight_layout()
    figure.savefig(output_dir / "ofdma_paired_differences.png", dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aggregate_json", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("ofdma_comparison"))
    args = parser.parse_args()
    aggregate = json.loads(args.aggregate_json.read_text(encoding="utf-8"))
    plot_ofdma_comparison(aggregate, args.output_dir)
    print(f"WROTE {args.output_dir}")


if __name__ == "__main__":
    main()
