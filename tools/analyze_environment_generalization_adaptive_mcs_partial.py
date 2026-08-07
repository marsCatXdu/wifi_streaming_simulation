#!/usr/bin/env python3
"""Archive a strictly validated incomplete adaptive-MCS campaign snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

import analyze_environment_generalization_adaptive_mcs_v1 as final
import analyze_environment_generalization_complete_reliability as complete
import analyze_environment_generalization_qualification as formal
import run_experiments


ROOT = Path(__file__).resolve().parents[1]
SIMULATION_COMMIT = final.SIMULATION_COMMIT
ANALYSIS_ID = "environment-generalization-adaptive-mcs-partial-snapshot-v1"


class PartialAdaptiveMcsError(RuntimeError):
    """Raised when a partial snapshot is not internally closed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PartialAdaptiveMcsError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PartialAdaptiveMcsError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: expected JSON object")
    return value


def _git_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout
    _require(len(head) == 40 and not status.strip(), "analyzer checkout is not clean")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", SIMULATION_COMMIT, head], cwd=ROOT
    )
    _require(ancestor.returncode == 0, "analyzer does not descend from simulation")
    return {"project_commit": head, "worktree_clean": True}


def partial_jobs(
    shard_roots: Sequence[Path],
) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, tuple[str, ...]], list[dict[str, Any]]]:
    """Bind every manifest-promoted run without requiring complete shards."""

    _require(len(shard_roots) == 2, "exactly two shard roots are required")
    document = run_experiments.load_yaml(final.CONFIG_PATH)
    runtime = run_experiments.validate_runtime_contract(document)
    _require(runtime is not None, "runtime contract is absent")
    specs = run_experiments.expand_config(document)
    expected: dict[str, dict[str, Any]] = {}
    arm_map = final._arm_map()
    for spec in specs:
        run_id = run_experiments.derive_run_id(
            spec["config"], spec["seed"], spec["run"],
            run_experiments.NS3_UPSTREAM_COMMIT, SIMULATION_COMMIT, runtime,
            spec["scenario"],
        )
        arm = arm_map.get((spec["config"]["topology"], spec["config"]["policy"]))
        _require(arm is not None and run_id not in expected, "matrix identity differs")
        expected[run_id] = {**spec, "run_id": run_id, "arm_id": arm}

    jobs = []
    manifests = []
    seen: set[str] = set()
    matrix_sha = run_experiments.matrix_sha256(document)
    for index, root_input in enumerate(shard_roots):
        root = root_input.resolve()
        manifest_path = root / "experiment_manifest.json"
        manifest = _read_json(manifest_path)
        shard = manifest.get("shard")
        _require(
            manifest.get("schema_version") == 2
            and manifest.get("experiment")
            == "environment-generalization-adaptive-mcs-qualification-v1"
            and manifest.get("matrix_sha256") == matrix_sha
            and manifest.get("project_commit") == SIMULATION_COMMIT
            and manifest.get("ns3_upstream_commit")
            == run_experiments.NS3_UPSTREAM_COMMIT
            and manifest.get("runtime_contract_id") == runtime["runtime_contract_id"]
            and manifest.get("runtime_contract_sha256")
            == runtime["runtime_contract_sha256"]
            and _canonical(manifest.get("source_artifacts"))
            == _canonical(runtime["source_artifacts"])
            and isinstance(shard, dict)
            and shard.get("schema_version") == 1
            and shard.get("selection") == "paired_unit_round_robin_v1"
            and shard.get("index") == index
            and shard.get("count") == 2
            and shard.get("full_matrix_run_count") == 576
            and shard.get("selected_run_count") == 288,
            f"shard {index} identity differs",
        )
        rows = manifest.get("runs")
        _require(isinstance(rows, list) and 0 < len(rows) <= 288,
                 f"shard {index} promoted-run count differs")
        for row in rows:
            run_id = row.get("run_id") if isinstance(row, dict) else None
            _require(
                isinstance(run_id, str) and run_id in expected and run_id not in seen
                and row.get("status") == "complete"
                and row.get("directory") == run_id,
                f"shard {index} contains a noncanonical promoted run",
            )
            spec = expected[run_id]
            _require(
                row.get("seed") == spec["seed"]
                and row.get("run") == spec["run"]
                and _canonical(row.get("config")) == _canonical(spec["config"])
                and _canonical(row.get("scenario")) == _canonical(spec["scenario"]),
                f"promoted run differs from matrix: {run_id}",
            )
            run_dir = root / run_id
            _require(run_dir.is_dir(), f"promoted run directory is absent: {run_id}")
            final._validate_adaptive_mcs_resolved(
                _read_json(run_dir / "resolved_config.json"), run_id
            )
            scenario = spec["scenario"]
            jobs.append({
                "run_id": run_id,
                "run_dir": str(run_dir),
                "project_commit": SIMULATION_COMMIT,
                "ns3_upstream_commit": run_experiments.NS3_UPSTREAM_COMMIT,
                "arm_id": spec["arm_id"],
                "family_id": scenario["family_id"],
                "scenario_id": scenario["scenario_id"],
                "parameter_sample": scenario["parameter_sample"],
                "seed": spec["seed"],
                "run": spec["run"],
                "expected_config": spec["config"],
                "required_secondary_airtime_event_schema_version": (
                    None if spec["arm_id"] == final.ARMS[0] else 2
                ),
            })
            seen.add(run_id)
        manifests.append({
            "index": index,
            "path": str(manifest_path),
            "sha256": _sha256(manifest_path),
            "promoted_run_count": len(rows),
        })
    families, scenarios = formal._family_scenario_order(specs)
    return jobs, families, scenarios, manifests


def _complete_unit_keys(observations: Sequence[dict[str, Any]]) -> tuple[set[tuple[Any, ...]], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], set[str]] = {}
    for row in observations:
        key = (row["family_id"], row["scenario_id"], row["seed"], row["run"])
        grouped.setdefault(key, set()).add(row["arm_id"])
    complete_keys = {key for key, arms in grouped.items() if arms == set(final.ARMS)}
    excluded = [
        {
            "family_id": key[0], "scenario_id": key[1], "seed": key[2],
            "run": key[3], "present_arms": sorted(arms),
            "missing_arms": sorted(set(final.ARMS) - arms),
        }
        for key, arms in sorted(grouped.items())
        if arms != set(final.ARMS)
    ]
    _require(bool(complete_keys), "snapshot contains no complete paired units")
    return complete_keys, excluded


def _adaptive_rows(observations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a nonempty strict adaptive prefix without asserting 576 runs."""

    _require(bool(observations), "adaptive observation prefix is empty")
    fields = (
        "family_id", "scenario_id", "parameter_sample", "seed", "run", "arm_id",
        "run_id", "run_dir", "generated_frame_count", "completed_frame_count",
        "incomplete_frame_count", "deadline_miss_count",
        "all_generated_deadline_miss_rate", "completed_frame_hf7_p99_supported",
        "completed_frame_hf7_p99_us", "sender_airtime_us",
        "background_throughput_mbps",
    )
    return [{"mode": "adaptive", **{key: source[key] for key in fields}}
            for source in observations]


def _summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for mode in final.MODES:
        result[mode] = {}
        for arm in final.ARMS:
            selected = [row for row in rows if row["mode"] == mode and row["arm_id"] == arm]
            generated = sum(row["generated_frame_count"] for row in selected)
            misses = sum(row["deadline_miss_count"] for row in selected)
            result[mode][arm] = {
                "run_count": len(selected),
                "generated_frame_count": generated,
                "deadline_miss_count": misses,
                "pooled_deadline_miss_rate": misses / generated,
                "mean_run_deadline_miss_rate": statistics.fmean(
                    row["all_generated_deadline_miss_rate"] for row in selected
                ),
                "mean_sender_airtime_us": statistics.fmean(
                    row["sender_airtime_us"] for row in selected
                ),
                "mean_background_throughput_mbps": statistics.fmean(
                    row["background_throughput_mbps"] for row in selected
                ),
                "mean_censored_latency_us": statistics.fmean(
                    row["all_generated_censored_mean_us"] for row in selected
                ),
            }
    return result


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _require(bool(rows), f"cannot write empty {path.name}")
    fields = list(rows[0])
    with path.open("w", newline="", encoding="ascii") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _plots(summary: dict[str, Any], history: dict[tuple[str, str], dict[str, Any]], output: Path) -> list[Path]:
    plt, np = final._plot_modules()
    paths = []
    x = np.arange(len(final.ARMS))
    width = 0.36
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    for mode_index, mode in enumerate(final.MODES):
        axes[0].bar(
            x + (mode_index - 0.5) * width,
            [100 * summary[mode][arm]["pooled_deadline_miss_rate"] for arm in final.ARMS],
            width, label=mode.capitalize(), alpha=0.55 if mode == "fixed" else 1.0,
            hatch="//" if mode == "fixed" else None,
        )
        axes[1].bar(
            x + (mode_index - 0.5) * width,
            [summary[mode][arm]["mean_sender_airtime_us"] / 1000 for arm in final.ARMS],
            width, label=mode.capitalize(), alpha=0.55 if mode == "fixed" else 1.0,
            hatch="//" if mode == "fixed" else None,
        )
    for axis, label in zip(axes, ("Pooled deadline misses (%)", "Mean sender airtime (ms/run)"), strict=True):
        axis.set_xticks(x, [final.ARM_LABELS[arm] for arm in final.ARMS])
        axis.set_ylabel(label)
        axis.grid(axis="y", alpha=0.25)
    axes[0].legend()
    figure.suptitle("Pre-recovery matched-unit snapshot (not final)")
    paths.extend(final._finish(figure, output, "partial_aggregate_fixed_vs_adaptive"))

    for field, name, title in (
        ("censored", "partial_all_generated_censored_latency_cdf", "All-generated deadline-censored latency"),
        ("completed", "partial_completion_latency_cdf", "Completed-frame latency (survivor-conditioned)"),
    ):
        figure, axes = plt.subplots(1, 3, figsize=(14, 4.6), sharey=True)
        for axis, arm in zip(axes, final.ARMS, strict=True):
            for mode in final.MODES:
                values = history[(mode, arm)][field]
                cdf_x, cdf_y = final._empirical_cdf(np, values)
                axis.plot(
                    cdf_x / 1000, cdf_y, label=mode.capitalize(),
                    linestyle="--" if mode == "fixed" else "-",
                )
            axis.set_title(final.ARM_LABELS[arm])
            axis.set_xlabel("Latency (ms)")
            axis.grid(alpha=0.25)
        axes[0].set_ylabel("Empirical CDF")
        axes[-1].legend()
        figure.suptitle(f"{title} - partial snapshot")
        paths.extend(final._finish(figure, output, name))
    plt.close("all")
    return paths


def analyze(shard_roots: Sequence[Path], output: Path, workers: int) -> Path:
    _require(workers > 0 and not output.exists(), "invalid workers or existing output")
    git = _git_identity()
    jobs, families, scenarios, manifests = partial_jobs(shard_roots)
    observations = complete.collect_observations(jobs, workers)
    unit_keys, excluded_units = _complete_unit_keys(observations)
    adaptive = [
        row for row in _adaptive_rows(observations)
        if (row["family_id"], row["scenario_id"], row["seed"], row["run"])
        in unit_keys
    ]
    fixed_rows, fixed_identity = final.load_fixed_rows()
    fixed = [
        row for row in fixed_rows
        if (row["family_id"], row["scenario_id"], row["seed"], row["run"])
        in unit_keys
    ]
    rows = fixed + adaptive
    _require(len(fixed) == len(adaptive) == len(unit_keys) * 3,
             "matched fixed/adaptive closure differs")
    history_means, history = final.collect_history(rows, workers)
    for row in rows:
        row["all_generated_censored_mean_us"] = history_means[(row["mode"], row["run_id"])]
    summary = _summary(rows)
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "status": "partial_pre_recovery_not_final",
        "source_closure": {
            "adaptive_manifests": manifests,
            "fixed_evidence": fixed_identity,
            "analyzer": {
                **git, "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": _sha256(Path(__file__).resolve()),
            },
        },
        "strictly_validated_promoted_run_count": len(observations),
        "complete_paired_unit_count": len(unit_keys),
        "matched_run_count_per_mode": len(adaptive),
        "excluded_incomplete_units": excluded_units,
        "summary": summary,
        "limits": [
            "This snapshot is retained before diagnosis or recovery of a failed run.",
            "Only units with all three adaptive arms are compared to matching fixed units.",
            "Coverage is incomplete and uneven; values are descriptive, not formal estimates.",
            "CDFs pool frames; completion latency is survivor-conditioned.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        plots_dir = temporary / "plots"
        plots_dir.mkdir()
        plot_paths = _plots(summary, history, plots_dir)
        (temporary / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="ascii"
        )
        lines = [
            "# Adaptive-MCS partial pre-recovery snapshot", "",
            f"Strictly validated promoted runs: {len(observations)}.",
            f"Complete matched three-arm units: {len(unit_keys)}.", "",
            "This is a non-final descriptive checkpoint retained before diagnosing or "
            "recovering the missing run.", "", "| MCS | Arm | Miss rate | Airtime |",
            "| --- | --- | ---: | ---: |",
        ]
        for mode in final.MODES:
            for arm in final.ARMS:
                value = summary[mode][arm]
                lines.append(
                    f"| {mode} | {final.ARM_LABELS[arm]} | "
                    f"{100 * value['pooled_deadline_miss_rate']:.4f}% | "
                    f"{value['mean_sender_airtime_us'] / 1000:.3f} ms/run |"
                )
        lines.extend(["", "See `report.json` for exclusions and source closure.", ""])
        (temporary / "README.md").write_text("\n".join(lines), encoding="ascii")
        table = [
            {key: row[key] for key in (
                "mode", "family_id", "scenario_id", "parameter_sample", "seed", "run",
                "arm_id", "run_id", "generated_frame_count", "completed_frame_count",
                "incomplete_frame_count", "deadline_miss_count",
                "all_generated_deadline_miss_rate", "all_generated_censored_mean_us",
                "sender_airtime_us", "background_throughput_mbps",
            )}
            for row in rows
        ]
        _write_csv(temporary / "run_metrics.csv", table)
        source_dir = temporary / "source_manifests"
        source_dir.mkdir()
        for identity in manifests:
            shutil.copyfile(
                identity["path"],
                source_dir / f"adaptive_shard{identity['index']}_experiment_manifest.json",
            )
        artifacts = {
            str(path.relative_to(temporary)): {
                "bytes": path.stat().st_size, "sha256": _sha256(path),
            }
            for path in sorted(temporary.rglob("*"))
            if path.is_file() and path.name != "artifact_manifest.json"
        }
        (temporary / "artifact_manifest.json").write_text(
            json.dumps({
                "schema_version": 1, "analysis": ANALYSIS_ID,
                "plot_count": len(plot_paths), "artifacts": artifacts,
            }, indent=2, sort_keys=True) + "\n", encoding="ascii",
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output.resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adaptive-shard-root", action="append", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    if len(args.adaptive_shard_root) != 2:
        parser.error("--adaptive-shard-root must be supplied exactly twice")
    try:
        output = analyze(
            [path.resolve() for path in args.adaptive_shard_root],
            args.output_directory.resolve(), args.workers,
        )
    except (
        PartialAdaptiveMcsError, final.AdaptiveMcsAnalysisError,
        complete.CompleteReliabilityError, OSError, subprocess.CalledProcessError,
    ) as error:
        parser.exit(2, f"ERROR: {error}\n")
    print(f"WROTE {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
