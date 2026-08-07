#!/usr/bin/env python3
"""Analyze the strictly valid pre-fix prefix of the T2 mechanism campaign."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any, Sequence

import numpy as np

import analyze_t2_repair_mechanism as mechanism


ANALYSIS_ID = "t2-repair-mechanism-valid-prefix-v1"
ARM_ORDER = mechanism.ARM_ORDER[:4]
BOOTSTRAP_REPLICATIONS = 10_000
BOOTSTRAP_SEED = 20260807


def _load_prefix(
    shard_roots: Sequence[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mechanism._require(len(shard_roots) == 2, "exactly two shard roots are required")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    commits: set[str] = set()
    shards: set[tuple[int, int]] = set()
    manifest_sources = []
    excluded_counts: dict[str, int] = {}
    for root in shard_roots:
        manifest_path = root / "experiment_manifest.json"
        manifest = mechanism._read_json(manifest_path)
        shard = manifest.get("shard", {})
        shards.add((int(shard.get("index", -1)), int(shard.get("count", -1))))
        mechanism._require(
            manifest.get("schema_version") == 2
            and manifest.get("mechanism_contract", {}).get("id")
            == "t2_repair_mechanism_v1",
            f"{root}: incompatible campaign manifest",
        )
        commits.add(manifest["project_commit"])
        for item in manifest.get("runs", []):
            arm = item.get("arm_id")
            if arm not in ARM_ORDER:
                excluded_counts[arm] = excluded_counts.get(arm, 0) + 1
                continue
            run_id = item["run_id"]
            mechanism._require(run_id not in seen, f"duplicate run ID: {run_id}")
            seen.add(run_id)
            jobs.append(
                {
                    **item,
                    "run_dir": str(root / item["directory"]),
                    "project_commit": manifest["project_commit"],
                    "ns3_upstream_commit": manifest["ns3_upstream_commit"],
                }
            )
        manifest_sources.append(
            {
                "path": str(manifest_path),
                "bytes": manifest_path.stat().st_size,
                "sha256": mechanism._sha256(manifest_path),
                "promoted_runs": len(manifest.get("runs", [])),
            }
        )
    mechanism._require(shards == {(0, 2), (1, 2)}, "shard identities differ")
    mechanism._require(len(commits) == 1, "simulation commits differ")
    grid: dict[tuple[str, int, int], set[str]] = {}
    for job in jobs:
        key = (job["scenario"]["scenario_id"], job["seed"], job["run"])
        grid.setdefault(key, set()).add(job["arm_id"])
    mechanism._require(
        len(grid) == 20 and all(arms == set(ARM_ORDER) for arms in grid.values()),
        "the four-arm promoted prefix is not a complete 20-unit paired grid",
    )
    mechanism._require(len(jobs) == 80, "expected 80 valid prefix runs")
    return jobs, {
        "simulation_project_commit": next(iter(commits)),
        "manifests": manifest_sources,
        "promoted_run_count": sum(row["promoted_runs"] for row in manifest_sources),
        "included_run_count": len(jobs),
        "included_paired_units": len(grid),
        "excluded_promoted_arms": excluded_counts,
        "known_phase_boundary": {
            "oracle_runs_launched": 0,
            "fec_runs_promoted": excluded_counts.get(
                "ideal_systematic_fec_12p5_t2", 0
            ),
            "fec_attempts_rejected_by_old_validator": 19,
        },
    }


def _paired_grid(
    observations: Sequence[dict[str, Any]],
    arm_order: Sequence[str] = ARM_ORDER,
) -> dict[tuple[str, int, int], dict[str, dict[str, Any]]]:
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]] = {}
    for row in observations:
        key = (row["scenario_id"], row["seed"], row["run"])
        target = grid.setdefault(key, {})
        mechanism._require(row["arm_id"] not in target, "duplicate paired arm")
        target[row["arm_id"]] = row
    mechanism._require(
        len(grid) == 20
        and all(set(unit) == set(arm_order) for unit in grid.values()),
        "validated factual grid is incomplete",
    )
    return grid


def _bootstrap(
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]],
    arm_order: Sequence[str] = ARM_ORDER,
) -> dict[str, Any]:
    scenarios = sorted({key[0] for key in grid})
    units = {
        scenario: sorted(key for key in grid if key[0] == scenario)
        for scenario in scenarios
    }
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = {
        arm: {"miss_rate": [], "censored_mean_us": [], "airtime_ratio": []}
        for arm in arm_order[1:]
    }
    for _ in range(BOOTSTRAP_REPLICATIONS):
        selected = [
            units[scenario][index]
            for scenario in scenarios
            for index in rng.integers(0, len(units[scenario]), len(units[scenario]))
        ]
        values: dict[str, dict[str, float]] = {}
        for arm in arm_order:
            rows = [grid[key][arm] for key in selected]
            generated = sum(row["generated_frames"] for row in rows)
            values[arm] = {
                "miss_rate": sum(row["deadline_misses"] for row in rows) / generated,
                "censored_mean_us": sum(
                    sum(row["censored_latencies_us"]) for row in rows
                )
                / generated,
                "airtime_us": sum(row["sender_airtime_us"] for row in rows),
            }
        baseline = values[arm_order[0]]
        for arm in arm_order[1:]:
            draws[arm]["miss_rate"].append(
                values[arm]["miss_rate"] - baseline["miss_rate"]
            )
            draws[arm]["censored_mean_us"].append(
                values[arm]["censored_mean_us"] - baseline["censored_mean_us"]
            )
            draws[arm]["airtime_ratio"].append(
                values[arm]["airtime_us"] / baseline["airtime_us"]
            )

    aggregate = {
        arm: mechanism._summarize(
            [row for unit in grid.values() for name, row in unit.items() if name == arm]
        )
        for arm in arm_order
    }
    baseline = aggregate[arm_order[0]]

    def interval(values: Sequence[float], estimate: float) -> dict[str, float]:
        return {
            "estimate": estimate,
            "ci95_low": mechanism._quantile(values, 0.025),
            "ci95_high": mechanism._quantile(values, 0.975),
        }

    contrasts = {}
    for arm in arm_order[1:]:
        candidate = aggregate[arm]
        contrasts[arm] = {
            "miss_rate_delta": interval(
                draws[arm]["miss_rate"],
                candidate["all_generated_deadline_miss_rate"]
                - baseline["all_generated_deadline_miss_rate"],
            ),
            "censored_mean_delta_us": interval(
                draws[arm]["censored_mean_us"],
                candidate["all_generated_censored_mean_us"]
                - baseline["all_generated_censored_mean_us"],
            ),
            "sender_airtime_ratio": interval(
                draws[arm]["airtime_ratio"],
                candidate["sender_airtime_mean_us"]
                / baseline["sender_airtime_mean_us"],
            ),
        }
    return {
        "method": "paired stratified bootstrap; four units per scenario",
        "replications": BOOTSTRAP_REPLICATIONS,
        "random_seed": BOOTSTRAP_SEED,
        "versus_str": contrasts,
    }


def _render_plots(
    observations: Sequence[dict[str, Any]],
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]],
    output: Path,
    arm_order: Sequence[str] = ARM_ORDER,
    title_prefix: str = "Valid pre-fix prefix",
) -> list[Path]:
    _, plt = mechanism._plot_modules()
    artifacts: list[Path] = []
    scenarios = list(mechanism.SCENARIO_LABELS)
    labels = [mechanism.ARM_LABELS[arm] for arm in arm_order]
    width = 0.76 / len(arm_order)

    figure, axis = plt.subplots(figsize=(11.5, 5.3))
    x = np.arange(len(scenarios))
    for index, arm in enumerate(arm_order):
        values = [
            100
            * mechanism._summarize(
                [
                    row
                    for row in observations
                    if row["scenario_id"] == scenario and row["arm_id"] == arm
                ]
            )["all_generated_deadline_miss_rate"]
            for scenario in scenarios
        ]
        axis.bar(
            x + (index - (len(arm_order) - 1) / 2) * width,
            values,
            width,
            label=mechanism.ARM_LABELS[arm],
            color=mechanism.COLORS[arm],
        )
    axis.set_xticks(x, [mechanism.SCENARIO_LABELS[item] for item in scenarios])
    axis.set_ylabel("All-generated deadline misses (%)")
    axis.set_title(f"{title_prefix}: reliability by scenario")
    axis.legend(fontsize=8, ncol=2)
    axis.grid(axis="y", alpha=0.25)
    artifacts.extend(mechanism._finish(figure, output, "deadline_miss_by_scenario"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.2, 5.3))
    for arm in arm_order:
        values = [
            value / 1000
            for row in observations
            if row["arm_id"] == arm
            for value in row["censored_latencies_us"]
        ]
        xx, yy = mechanism._ecdf(values)
        axis.step(
            xx,
            yy,
            where="post",
            label=mechanism.ARM_LABELS[arm],
            color=mechanism.COLORS[arm],
        )
    axis.set_xlim(left=0)
    axis.set_ylim(0, 1.002)
    axis.set_xlabel("Deadline-censored frame latency (ms)")
    axis.set_ylabel("CDF over all generated frames")
    axis.set_title(f"{title_prefix}: all-generated completion CDF")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.25)
    artifacts.extend(mechanism._finish(figure, output, "censored_latency_cdf"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.2, 5.3))
    bins = np.linspace(0, 33.333, 90)
    for arm in arm_order:
        values = [
            min(value / 1000, bins[-1])
            for row in observations
            if row["arm_id"] == arm
            for value in row["censored_latencies_us"]
        ]
        axis.hist(
            values,
            bins=bins,
            density=True,
            histtype="step",
            linewidth=1.5,
            label=mechanism.ARM_LABELS[arm],
            color=mechanism.COLORS[arm],
        )
    axis.set_xlabel("Deadline-censored frame latency (ms)")
    axis.set_ylabel("Density")
    axis.set_title(f"{title_prefix}: all-generated completion PDF")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.2)
    artifacts.extend(mechanism._finish(figure, output, "censored_latency_pdf"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(9.5, 5.2))
    link0 = [
        statistics.fmean(
            row["airtime_link_0_us"] for row in observations if row["arm_id"] == arm
        )
        / 1000
        for arm in arm_order
    ]
    link1 = [
        statistics.fmean(
            row["airtime_link_1_us"] for row in observations if row["arm_id"] == arm
        )
        / 1000
        for arm in arm_order
    ]
    axis.bar(labels, link1, label="5 GHz / link 1", color="#4c78a8")
    axis.bar(labels, link0, bottom=link1, label="2.4 GHz / link 0", color="#f2cf5b")
    axis.set_ylabel("Mean target-sender PHY TX airtime (ms/run)")
    axis.set_title(f"{title_prefix}: measured sender airtime")
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    artifacts.extend(mechanism._finish(figure, output, "sender_airtime_by_link"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8.2, 5.2))
    for arm in arm_order:
        values = [
            value
            for row in observations
            if row["arm_id"] == arm
            for value in row["miss_bursts"]
        ] or [0]
        xx, yy = mechanism._ecdf(values)
        axis.step(
            xx,
            yy,
            where="post",
            label=mechanism.ARM_LABELS[arm],
            color=mechanism.COLORS[arm],
        )
    axis.set_xlabel("Consecutive deadline misses (frames)")
    axis.set_ylabel("CDF over miss bursts")
    axis.set_title(f"{title_prefix}: deadline-miss bursts")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.25)
    artifacts.extend(mechanism._finish(figure, output, "deadline_miss_burst_cdf"))
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(10, 5.2))
    family_colors = dict(
        zip(scenarios, ("#4c78a8", "#f58518", "#54a24b", "#e45756", "#b279a2"))
    )
    positions = {arm: index for index, arm in enumerate(arm_order[1:])}
    for scenario in scenarios:
        for key, unit in sorted(grid.items()):
            if key[0] != scenario:
                continue
            for arm in arm_order[1:]:
                axis.scatter(
                    positions[arm],
                    100
                    * (
                        unit[arm]["deadline_miss_rate"]
                        - unit[arm_order[0]]["deadline_miss_rate"]
                    ),
                    color=family_colors[scenario],
                    s=35,
                    alpha=0.8,
                )
    axis.axhline(0, color="black", linestyle="--", linewidth=1)
    axis.set_xticks(range(len(arm_order) - 1), labels[1:])
    axis.set_ylabel("Candidate - STR misses (percentage points per unit)")
    axis.set_title(f"{title_prefix}: paired reliability effects")
    axis.grid(axis="y", alpha=0.25)
    artifacts.extend(mechanism._finish(figure, output, "paired_miss_delta"))
    plt.close(figure)

    figure, axes = plt.subplots(1, 3, figsize=(12.5, 4.7))
    for axis, field, title in (
        (axes[0], "primary_mac_queue_packets", "Primary MAC queue at T2"),
        (axes[1], "secondary_mac_queue_packets", "Secondary MAC queue at T2"),
        (axes[2], "primary_ack_deficit_packets", "Primary ACK deficit at T2"),
    ):
        arms = arm_order[1:]
        values = [
            [
                item
                for row in observations
                if row["arm_id"] == arm
                for item in row["snapshots"].get(field, [])
            ]
            for arm in arms
        ]
        axis.boxplot(
            values,
            labels=[mechanism.ARM_LABELS[arm] for arm in arms],
            showfliers=False,
        )
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=24, labelsize=7)
        axis.set_ylabel("Packets")
        axis.grid(axis="y", alpha=0.2)
    artifacts.extend(mechanism._finish(figure, output, "t2_queue_and_ack_state"))
    plt.close(figure)
    return artifacts


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# T2 mechanism campaign: valid pre-fix prefix",
        "",
        "**Status: partial diagnostic, not the decisive six-arm result.**",
        "",
        "This report uses the 80 strictly valid, balanced runs for STR, 5 GHz",
        "only, full-copy T0, and full-copy T2. The oracle phase was not launched.",
        "Nineteen FEC outputs were preserved but excluded because the old generic",
        "validator rejects coded completion; the one promoted FEC run is also",
        "excluded to keep the comparison balanced.",
        "",
        "Primary outcomes include every generated frame. Completed-frame P99 is",
        "descriptive only because it is survivor-conditioned.",
        "",
        "## Aggregate result",
        "",
        "| Arm | Misses | Miss rate | Censored mean | Sender airtime |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARM_ORDER:
        row = report["aggregate"][arm]
        lines.append(
            f"| {mechanism.ARM_LABELS[arm]} | {row['deadline_misses']:,} | "
            f"{100 * row['all_generated_deadline_miss_rate']:.4f}% | "
            f"{row['all_generated_censored_mean_us'] / 1000:.3f} ms | "
            f"{row['sender_airtime_mean_us'] / 1000:.2f} ms/run |"
        )
    lines += ["", "## Paired contrasts versus STR", ""]
    for arm, values in report["bootstrap"]["versus_str"].items():
        miss = values["miss_rate_delta"]
        airtime = values["sender_airtime_ratio"]
        lines.append(
            f"- {mechanism.ARM_LABELS[arm]}: miss delta "
            f"{100 * miss['estimate']:+.4f} pp (95% CI "
            f"{100 * miss['ci95_low']:+.4f} to {100 * miss['ci95_high']:+.4f}); "
            f"airtime ratio {airtime['estimate']:.4f} (95% CI "
            f"{airtime['ci95_low']:.4f} to {airtime['ci95_high']:.4f})."
        )
    lines += [
        "",
        "## Interpretation boundary",
        "",
        "These four arms provide an early mechanism baseline only. They cannot",
        "answer whether oracle packet-level repair beats STR at equal airtime.",
        "The preserved FEC outputs must first be revalidated with coded-aware",
        "completion accounting, then the paired oracle phase must run.",
        "",
    ]
    return "\n".join(lines)


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    mechanism._require(bool(rows), f"refusing to write empty table {path.name}")
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analyze(shard_roots: Sequence[Path], output: Path, workers: int) -> dict[str, Any]:
    identity = mechanism._git_identity()
    jobs, source = _load_prefix(shard_roots)
    mechanism._descends_from(
        source["simulation_project_commit"], identity["project_commit"]
    )
    observations = mechanism._collect(jobs, workers)
    grid = _paired_grid(observations)
    aggregate = {
        arm: mechanism._summarize([row for row in observations if row["arm_id"] == arm])
        for arm in ARM_ORDER
    }
    scenarios = {
        scenario: {
            arm: mechanism._summarize(
                [
                    row
                    for row in observations
                    if row["scenario_id"] == scenario and row["arm_id"] == arm
                ]
            )
            for arm in ARM_ORDER
        }
        for scenario in mechanism.SCENARIO_LABELS
    }
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "status": "partial_valid_prefix_before_fec_validator_fix",
        "analyzer_identity": identity,
        "source_closure": source,
        "estimands": {
            "primary": "deadline misses over every generated frame",
            "stable_latency": (
                "min(union completion latency, frame deadline) over every "
                "generated frame"
            ),
            "completed_p99": "descriptive only; survivor-conditioned",
        },
        "aggregate": aggregate,
        "scenarios": scenarios,
        "bootstrap": _bootstrap(grid),
        "interpretation_boundary": (
            "No oracle runs and no balanced FEC arm are included; this prefix cannot "
            "answer the decisive equal-airtime packet-repair question."
        ),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    mechanism._require(not output.exists(), f"output already exists: {output}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (temporary / "prefix_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "REPORT.md").write_text(
            _report_markdown(report), encoding="utf-8"
        )
        _write_csv(
            temporary / "run_metrics.csv",
            mechanism._flat_summary_rows(observations),
        )
        _write_csv(
            temporary / "aggregate_metrics.csv",
            [{"arm_id": arm, **aggregate[arm]} for arm in ARM_ORDER],
        )
        _write_csv(
            temporary / "scenario_metrics.csv",
            [
                {"scenario_id": scenario, "arm_id": arm, **scenarios[scenario][arm]}
                for scenario in mechanism.SCENARIO_LABELS
                for arm in ARM_ORDER
            ],
        )
        plot_dir = temporary / "plots"
        plot_dir.mkdir()
        _render_plots(observations, grid, plot_dir)
        artifacts = {}
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            relative = str(path.relative_to(temporary))
            artifacts[relative] = {
                "bytes": path.stat().st_size,
                "sha256": mechanism._sha256(path),
            }
        manifest = {
            "schema_version": 1,
            "analysis": ANALYSIS_ID,
            "analyzer_identity": identity,
            "source_closure": source,
            "counts": {
                "strictly_validated_runs": len(observations),
                "paired_units": len(grid),
                "png_figures": len(list(plot_dir.glob("*.png"))),
            },
            "artifacts": artifacts,
        }
        (temporary / "analysis_artifact_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
    except Exception:
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard_roots", nargs=2, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    report = analyze(
        [path.resolve() for path in args.shard_roots],
        args.output.resolve(),
        args.workers,
    )
    print(
        f"PREFIX_VALID runs={report['source_closure']['included_run_count']} "
        f"paired_units={report['source_closure']['included_paired_units']}"
    )
    print(f"REPORT {args.output.resolve() / 'REPORT.md'}")


if __name__ == "__main__":
    main()
