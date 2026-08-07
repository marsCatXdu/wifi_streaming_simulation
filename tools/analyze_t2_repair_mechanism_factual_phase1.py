#!/usr/bin/env python3
"""Analyze the five valid factual arms before diagnosing oracle pair drift."""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

import analyze_t2_repair_mechanism as mechanism
import analyze_t2_repair_mechanism_prefix as prefix


ANALYSIS_ID = "t2-repair-mechanism-factual-phase1-v1"
ARM_ORDER = tuple(
    arm
    for arm in mechanism.ARM_ORDER
    if arm != "oracle_eventual_missing_repair_t2"
)


def _load_jobs(
    shard_roots: Sequence[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mechanism._require(len(shard_roots) == 2, "exactly two shard roots are required")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    shards: set[tuple[int, int]] = set()
    commits: set[str] = set()
    sources = []
    excluded_oracle_runs = 0
    for root in shard_roots:
        manifest_path = root / "experiment_manifest.json"
        recovery_path = root / "attempt_recovery.json"
        manifest = mechanism._read_json(manifest_path)
        recovery = mechanism._read_json(recovery_path)
        shard = manifest.get("shard", {})
        shard_index = int(shard.get("index", -1))
        shards.add((shard_index, int(shard.get("count", -1))))
        mechanism._require(
            manifest.get("schema_version") == 2
            and manifest.get("mechanism_contract", {}).get("id")
            == "t2_repair_mechanism_v1"
            and len(manifest.get("runs", [])) == 60,
            f"{root}: incomplete or incompatible campaign manifest",
        )
        expected_recovery = mechanism.RECOVERY_COUNTS[shard_index]
        mechanism._require(
            recovery.get("recovered_count") == expected_recovery
            and recovery.get("all_recovered_attempts_strictly_validated") is True
            and all(
                row.get("state") == "promoted"
                for row in recovery.get("recovered", [])
            ),
            f"{root}: FEC recovery is not closed",
        )
        commits.add(manifest["project_commit"])
        for item in manifest["runs"]:
            if item.get("arm_id") == "oracle_eventual_missing_repair_t2":
                excluded_oracle_runs += 1
                continue
            mechanism._require(
                item.get("arm_id") in ARM_ORDER,
                f"{root}: unknown factual arm {item.get('arm_id')}",
            )
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
        sources.append(
            {
                "root": str(root),
                "manifest": {
                    "path": str(manifest_path),
                    "bytes": manifest_path.stat().st_size,
                    "sha256": mechanism._sha256(manifest_path),
                },
                "attempt_recovery": {
                    "path": str(recovery_path),
                    "bytes": recovery_path.stat().st_size,
                    "sha256": mechanism._sha256(recovery_path),
                    "recovered_count": expected_recovery,
                },
                "oracle_pair_validation_present": (
                    root / "oracle_pair_validation.json"
                ).exists(),
            }
        )
    mechanism._require(shards == {(0, 2), (1, 2)}, "shard identities differ")
    mechanism._require(len(commits) == 1, "simulation commits differ")
    mechanism._require(
        len(jobs) == 100 and excluded_oracle_runs == 20,
        "expected 100 factual runs and 20 excluded oracle runs",
    )
    units: dict[tuple[str, int, int], set[str]] = {}
    for job in jobs:
        key = (job["scenario"]["scenario_id"], job["seed"], job["run"])
        units.setdefault(key, set()).add(job["arm_id"])
    mechanism._require(
        len(units) == 20
        and all(arms == set(ARM_ORDER) for arms in units.values()),
        "factual phase-1 grid is incomplete",
    )
    return jobs, {
        "simulation_project_commit": next(iter(commits)),
        "shards": sources,
        "included_run_count": len(jobs),
        "paired_unit_count": len(units),
        "excluded_oracle_run_count": excluded_oracle_runs,
        "exclusion_reason": (
            "oracle primary packet outcomes failed the frozen same-seed pair closure; "
            "oracle outcomes are not used in this factual-arm report"
        ),
    }


def _render_action_plot(
    observations: Sequence[dict[str, Any]], output: Path
) -> list[Path]:
    _, plt = mechanism._plot_modules()
    figure, axis = plt.subplots(figsize=(8.2, 5.2))
    for arm in (
        "full_copy_t0",
        "full_copy_t2",
        "ideal_systematic_fec_12p5_t2",
    ):
        values = [
            value / 1000
            for row in observations
            if row["arm_id"] == arm
            for value in row["action_censored_latencies_us"]
        ]
        xx, yy = mechanism._ecdf(values)
        axis.step(
            xx,
            yy,
            where="post",
            label=mechanism.ARM_LABELS[arm],
            color=mechanism.COLORS[arm],
        )
    axis.set_xlabel("Action-to-copy/repair completion, deadline-censored (ms)")
    axis.set_ylabel("CDF over launched actions")
    axis.set_ylim(0, 1.002)
    axis.set_title("Valid factual phase 1: secondary action completion")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.25)
    paths = mechanism._finish(figure, output, "action_completion_cdf")
    plt.close(figure)
    return paths


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    mechanism._require(bool(rows), f"refusing to write empty table {path.name}")
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# T2 mechanism campaign: valid factual phase 1",
        "",
        "**Status: protected diagnostic before oracle-pair diagnosis.**",
        "",
        "This report uses all 100 individually strict-valid factual runs: STR,",
        "5 GHz only, full-copy T0, full-copy T2, and ideal 12.5% FEC T2.",
        "All 20 oracle runs are excluded because their same-seed primary packet",
        "outcomes did not satisfy the frozen pair-closure requirement.",
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
        report["source_closure"]["exclusion_reason"] + ".",
        "This report preserves valid FEC and baseline evidence, but it does not",
        "answer the decisive privileged-oracle equal-airtime question. No oracle",
        "repair claim or next-action decision may be made from this artifact.",
        "",
    ]
    return "\n".join(lines)


def analyze(shard_roots: Sequence[Path], output: Path, workers: int) -> dict[str, Any]:
    identity = mechanism._git_identity()
    jobs, source = _load_jobs(shard_roots)
    mechanism._descends_from(
        source["simulation_project_commit"], identity["project_commit"]
    )
    observations = mechanism._collect(jobs, workers)
    grid = prefix._paired_grid(observations, ARM_ORDER)
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
        "status": "valid_factual_phase1_oracle_excluded_before_diagnosis",
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
        "bootstrap": prefix._bootstrap(grid, ARM_ORDER),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    mechanism._require(not output.exists(), f"output already exists: {output}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (temporary / "factual_phase1_report.json").write_text(
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
        prefix._render_plots(
            observations,
            grid,
            plot_dir,
            ARM_ORDER,
            "Valid factual phase 1",
        )
        _render_action_plot(observations, plot_dir)
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
                "excluded_oracle_runs": 20,
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
        f"FACTUAL_VALID runs={report['source_closure']['included_run_count']} "
        f"paired_units={report['source_closure']['paired_unit_count']} "
        f"oracle_excluded={report['source_closure']['excluded_oracle_run_count']}"
    )
    print(f"REPORT {args.output.resolve() / 'REPORT.md'}")


if __name__ == "__main__":
    main()
