#!/usr/bin/env python3
"""Analyze complete-campaign reliability when the frozen P99 is unsupported."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import analyze_environment_generalization_qualification as formal
from analyze_paired_value_t2_str_qualification import (
    BUILD_IDENTITY_FIELDS,
    MINIMUM_COMPLETED_FRAMES,
    _background_metrics,
    _finite,
    _flag,
    _integer,
    _read_csv,
    _sender_airtime_us,
    _type7_quantile,
)
from validate_outputs import ValidationError, validate_run


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_ID = "environment-generalization-complete-reliability-v1"
WARNING = (
    "COMPLETE POST-OUTCOME RELIABILITY ANALYSIS: all 576 frozen runs are "
    "included, but the preregistered completed-frame P99 estimand is not "
    "assessable because some valid runs have fewer than 100 completions."
)
METRICS = (
    "all_generated_deadline_miss_rate",
    "sender_airtime_us",
    "background_throughput_mbps",
)
COMPARISONS = (
    ("score_aware_t2_v2", "str_mlo_nmaxinflights_1"),
    ("distributional_shadow_t2", "str_mlo_nmaxinflights_1"),
    ("distributional_shadow_t2", "score_aware_t2_v2"),
)


class CompleteReliabilityError(RuntimeError):
    """Raised when complete reliability evidence is inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CompleteReliabilityError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _actual_git_identity() -> dict[str, Any]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise CompleteReliabilityError(f"cannot inspect analyzer Git identity: {error}") from error
    _require(len(head) == 40, "analyzer Git HEAD is invalid")
    _require(not status.strip(), "analyzer checkout is not clean")
    return {"project_commit": head, "worktree_clean": True}


def _require_descendant(ancestor: str, descendant: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    _require(
        result.returncode == 0,
        "post-outcome analyzer is not descended from the simulation commit",
    )


def _frame_metrics_allow_unsupported_p99(
    run_dir: Path, config: dict[str, Any]
) -> dict[str, Any]:
    """Validate every frame while allowing the run-level P99 to be absent."""

    rows = _read_csv(
        run_dir / "frames.csv",
        {
            "run_id",
            "frame_id",
            "generation_time_us",
            "deadline_us",
            "union_latency_us",
            "deadline_miss",
            "incomplete",
        },
    )
    _require(bool(rows), f"{run_dir}/frames.csv: no generated frames")
    start_us = _finite(
        config.get("measurement_start_s"), f"{run_dir}: measurement_start_s"
    ) * 1_000_000.0
    stop_us = _finite(
        config.get("measurement_stop_s"), f"{run_dir}: measurement_stop_s"
    ) * 1_000_000.0
    _require(stop_us > start_us, f"{run_dir}: invalid measurement window")
    frame_ids: set[int] = set()
    misses = 0
    incomplete_count = 0
    completed: list[float] = []
    for row in rows:
        _require(row.get("run_id") == config.get("run_id"),
                 f"{run_dir}/frames.csv: run_id mismatch")
        frame_id = _integer(row.get("frame_id"), f"{run_dir}: frame_id")
        _require(frame_id not in frame_ids, f"{run_dir}/frames.csv: duplicate frame_id")
        frame_ids.add(frame_id)
        generation = _finite(
            row.get("generation_time_us"),
            f"{run_dir}: generation_time_us",
            nonnegative=True,
        )
        _require(
            start_us <= generation < stop_us,
            f"{run_dir}: generated frame outside measurement window",
        )
        deadline = _finite(
            row.get("deadline_us"), f"{run_dir}: deadline_us", nonnegative=True
        )
        _require(deadline > 0, f"{run_dir}: frame deadline must be positive")
        incomplete = _flag(row.get("incomplete"), f"{run_dir}: incomplete")
        if incomplete:
            _require(
                row.get("union_latency_us", "") == "",
                f"{run_dir}: incomplete frame has union latency",
            )
            incomplete_count += 1
            computed_miss = True
        else:
            latency = _finite(
                row.get("union_latency_us"),
                f"{run_dir}: union_latency_us",
                nonnegative=True,
            )
            completed.append(latency)
            computed_miss = latency > deadline
        _require(
            _flag(row.get("deadline_miss"), f"{run_dir}: deadline_miss")
            == computed_miss,
            f"{run_dir}: deadline-miss flag disagrees with raw outcome",
        )
        misses += computed_miss
    supported = len(completed) >= MINIMUM_COMPLETED_FRAMES
    return {
        "generated_frame_count": len(rows),
        "completed_frame_count": len(completed),
        "incomplete_frame_count": incomplete_count,
        "deadline_miss_count": misses,
        "all_generated_deadline_miss_rate": misses / len(rows),
        "completed_frame_hf7_p99_supported": supported,
        "completed_frame_hf7_p99_us": (
            _type7_quantile(completed, 0.99) if supported else None
        ),
    }


def _validate_observation(job: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=job["run_id"],
            expected_project_commit=job["project_commit"],
            expected_ns3_commit=job["ns3_upstream_commit"],
        )
    except ValidationError as error:
        raise CompleteReliabilityError(
            f"{run_dir}: strict output validation failed: {error}"
        ) from error
    _require(validation.get("valid") is True,
             f"{run_dir}: validator did not return valid")
    config = formal._read_json(run_dir / "resolved_config.json")
    expected = job["expected_config"]
    expected_profile = expected.get("prediction", {}).get(
        "paired_temporal_t2_frame_profile"
    )
    _require(
        config.get("run_id") == job["run_id"]
        and config.get("seed") == job["seed"]
        and config.get("run") == job["run"]
        and config.get("topology") == expected.get("topology")
        and config.get("policy") == expected.get("policy"),
        f"{run_dir}: resolved run/arm identity differs from frozen expansion",
    )
    if job["arm_id"] == "str_mlo_nmaxinflights_1":
        _require(
            expected_profile is None and "pairedTemporalT2FrameProfile" not in config,
            f"{run_dir}: STR arm unexpectedly selects a temporal-T2 profile",
        )
    else:
        meter = config.get("secondaryAirtimeMeter")
        _require(
            expected_profile == "environment_generalization_v1"
            and config.get("pairedTemporalT2FrameProfile") == expected_profile
            and config.get("environment") == "held_out_environment_generalization_v1",
            f"{run_dir}: selective arm generalization profile differs",
        )
        _require(
            isinstance(meter, dict)
            and meter.get("event_schema_version")
            == job["required_secondary_airtime_event_schema_version"],
            f"{run_dir}: secondary airtime event schema differs",
        )
    build = formal._read_json(run_dir / "build_info.json")
    build_identity: dict[str, str] = {}
    for field in BUILD_IDENTITY_FIELDS:
        value = build.get(field)
        _require(isinstance(value, str) and value,
                 f"{run_dir}: invalid build {field}")
        build_identity[field] = value
    frame = _frame_metrics_allow_unsupported_p99(run_dir, config)
    background = _background_metrics(run_dir, config)
    return {
        "family_id": job["family_id"],
        "scenario_id": job["scenario_id"],
        "parameter_sample": job["parameter_sample"],
        "seed": job["seed"],
        "run": job["run"],
        "arm_id": job["arm_id"],
        "run_id": job["run_id"],
        "run_dir": job["run_dir"],
        **frame,
        "sender_airtime_us": _sender_airtime_us(run_dir),
        "background_bytes_received": background["background_bytes_received"],
        "background_throughput_mbps": background["background_throughput_mbps"],
        "build_identity": build_identity,
        "strict_validation": validation,
    }


def collect_observations(
    jobs: Sequence[dict[str, Any]], workers: int
) -> list[dict[str, Any]]:
    """Strictly validate and reduce all complete-campaign runs."""

    _require(workers > 0, "validation worker count must be positive")
    order = {job["run_id"]: index for index, job in enumerate(jobs)}
    observations: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_validate_observation, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                observations.append(future.result())
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                if isinstance(error, CompleteReliabilityError):
                    raise error
                raise CompleteReliabilityError(
                    f"validation worker failed for {job['run_id']}: {error}"
                ) from error
    _require(len(observations) == len(jobs), "validation result count differs")
    observations.sort(key=lambda row: order[row["run_id"]])
    return observations


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    _require(bool(materialized), "cannot average an empty sample")
    _require(all(math.isfinite(value) for value in materialized),
             "analysis sample contains a non-finite value")
    return statistics.mean(materialized)


def _arm_means(
    grid: dict[str, dict[str, list[dict[str, dict[str, Any]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
) -> tuple[
    dict[str, dict[str, float]],
    dict[str, dict[str, dict[str, float]]],
    dict[str, dict[str, dict[str, dict[str, float]]]],
]:
    scenario_means: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    family_means: dict[str, dict[str, dict[str, float]]] = {}
    for family in families:
        scenario_means[family] = {}
        for scenario in scenarios[family]:
            scenario_means[family][scenario] = {
                arm: {
                    metric: _mean(
                        unit[arm][metric] for unit in grid[family][scenario]
                    )
                    for metric in METRICS
                }
                for arm in formal.ARM_IDS
            }
        family_means[family] = {
            arm: {
                metric: _mean(
                    scenario_means[family][scenario][arm][metric]
                    for scenario in scenarios[family]
                )
                for metric in METRICS
            }
            for arm in formal.ARM_IDS
        }
    aggregate = {
        arm: {
            metric: _mean(family_means[family][arm][metric] for family in families)
            for metric in METRICS
        }
        for arm in formal.ARM_IDS
    }
    return aggregate, family_means, scenario_means


def _contrast(candidate: dict[str, float], baseline: dict[str, float]) -> dict[str, float]:
    _require(baseline["sender_airtime_us"] > 0,
             "sender airtime denominator is nonpositive")
    _require(baseline["background_throughput_mbps"] > 0,
             "background throughput denominator is nonpositive")
    _require(baseline["all_generated_deadline_miss_rate"] > 0,
             "deadline miss denominator is nonpositive")
    return {
        "deadline_miss_delta": (
            candidate["all_generated_deadline_miss_rate"]
            - baseline["all_generated_deadline_miss_rate"]
        ),
        "relative_deadline_miss_reduction": (
            1
            - candidate["all_generated_deadline_miss_rate"]
            / baseline["all_generated_deadline_miss_rate"]
        ),
        "sender_airtime_ratio": (
            candidate["sender_airtime_us"] / baseline["sender_airtime_us"]
        ),
        "background_throughput_loss": (
            1
            - candidate["background_throughput_mbps"]
            / baseline["background_throughput_mbps"]
        ),
    }


def _interval(point: float, samples: Sequence[float]) -> dict[str, float]:
    return {
        "estimate": point,
        "ci95_low": _type7_quantile(samples, 0.025),
        "ci95_high": _type7_quantile(samples, 0.975),
    }


def hierarchical_bootstrap(
    grid: dict[str, dict[str, list[dict[str, dict[str, Any]]]]],
    families: Sequence[str],
    scenarios: dict[str, Sequence[str]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Bootstrap supported metrics with shared paired hierarchical draws."""

    point, family_point, scenario_point = _arm_means(grid, families, scenarios)
    replications = int(contract["bootstrap"]["replications"])
    seed = int(contract["bootstrap"]["random_seed"])
    _require(replications > 0, "bootstrap replication count must be positive")
    rng = random.Random(seed)
    arm_samples = {
        arm: {metric: [] for metric in METRICS} for arm in formal.ARM_IDS
    }
    comparison_samples = {
        f"{candidate}_minus_{baseline}": {
            metric: []
            for metric in (
                "deadline_miss_delta",
                "relative_deadline_miss_reduction",
                "sender_airtime_ratio",
                "background_throughput_loss",
            )
        }
        for candidate, baseline in COMPARISONS
    }
    family_arm_samples = {
        family: {
            arm: {metric: [] for metric in METRICS} for arm in formal.ARM_IDS
        }
        for family in families
    }
    for _ in range(replications):
        draw_family: dict[str, dict[str, float]] = {}
        for family in families:
            available = scenarios[family]
            sums = {
                arm: {metric: 0.0 for metric in METRICS}
                for arm in formal.ARM_IDS
            }
            count = 0
            for _scenario_index in range(len(available)):
                scenario = available[rng.randrange(len(available))]
                units = grid[family][scenario]
                for _replicate_index in range(len(units)):
                    unit = units[rng.randrange(len(units))]
                    for arm in formal.ARM_IDS:
                        for metric in METRICS:
                            sums[arm][metric] += unit[arm][metric]
                    count += 1
            draw_family[family] = {
                f"{arm}:{metric}": sums[arm][metric] / count
                for arm in formal.ARM_IDS
                for metric in METRICS
            }
            for arm in formal.ARM_IDS:
                for metric in METRICS:
                    family_arm_samples[family][arm][metric].append(
                        draw_family[family][f"{arm}:{metric}"]
                    )
        draw_aggregate = {
            arm: {
                metric: _mean(
                    draw_family[family][f"{arm}:{metric}"] for family in families
                )
                for metric in METRICS
            }
            for arm in formal.ARM_IDS
        }
        for arm in formal.ARM_IDS:
            for metric in METRICS:
                arm_samples[arm][metric].append(draw_aggregate[arm][metric])
        for candidate, baseline in COMPARISONS:
            comparison = _contrast(draw_aggregate[candidate], draw_aggregate[baseline])
            target = comparison_samples[f"{candidate}_minus_{baseline}"]
            for metric, value in comparison.items():
                target[metric].append(value)
    treatments = {
        arm: {
            metric: _interval(point[arm][metric], arm_samples[arm][metric])
            for metric in METRICS
        }
        for arm in formal.ARM_IDS
    }
    comparisons = {}
    for candidate, baseline in COMPARISONS:
        comparison_id = f"{candidate}_minus_{baseline}"
        point_contrast = _contrast(point[candidate], point[baseline])
        comparisons[comparison_id] = {
            metric: _interval(value, comparison_samples[comparison_id][metric])
            for metric, value in point_contrast.items()
        }
    family_intervals = {
        family: {
            arm: {
                metric: _interval(
                    family_point[family][arm][metric],
                    family_arm_samples[family][arm][metric],
                )
                for metric in METRICS
            }
            for arm in formal.ARM_IDS
        }
        for family in families
    }
    return {
        "method": "shared paired hierarchical bootstrap",
        "replications": replications,
        "random_seed": seed,
        "treatments": treatments,
        "comparisons": comparisons,
        "family_treatments": family_intervals,
        "family_points": family_point,
        "scenario_points": scenario_point,
    }


def _p99_support(
    observations: Sequence[dict[str, Any]], families: Sequence[str]
) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm in formal.ARM_IDS:
        rows = [row for row in observations if row["arm_id"] == arm]
        unsupported = [
            {
                "run_id": row["run_id"],
                "family_id": row["family_id"],
                "scenario_id": row["scenario_id"],
                "seed": row["seed"],
                "completed_frame_count": row["completed_frame_count"],
            }
            for row in rows
            if not row["completed_frame_hf7_p99_supported"]
        ]
        arms[arm] = {
            "eligible_run_count": len(rows) - len(unsupported),
            "total_run_count": len(rows),
            "unsupported_runs": unsupported,
            "eligible_run_count_by_family": {
                family: sum(
                    row["family_id"] == family
                    and row["completed_frame_hf7_p99_supported"]
                    for row in rows
                )
                for family in families
            },
        }
    unsupported_count = sum(len(value["unsupported_runs"]) for value in arms.values())
    return {
        "minimum_completed_frames_per_run": MINIMUM_COMPLETED_FRAMES,
        "unsupported_run_count": unsupported_count,
        "arms": arms,
        "frozen_completed_p99_estimand": {
            "status": "not_assessable",
            "reason": (
                f"{unsupported_count} valid runs have fewer than "
                f"{MINIMUM_COMPLETED_FRAMES} completed frames"
            ),
        },
    }


def _rows(
    observations: Sequence[dict[str, Any]], result: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    run_rows = []
    for row in observations:
        run_rows.append(
            {
                "family_id": row["family_id"],
                "scenario_id": row["scenario_id"],
                "parameter_sample": row["parameter_sample"],
                "seed": row["seed"],
                "run": row["run"],
                "arm_id": row["arm_id"],
                "run_id": row["run_id"],
                "generated_frame_count": row["generated_frame_count"],
                "completed_frame_count": row["completed_frame_count"],
                "incomplete_frame_count": row["incomplete_frame_count"],
                "deadline_miss_count": row["deadline_miss_count"],
                "all_generated_deadline_miss_rate": row[
                    "all_generated_deadline_miss_rate"
                ],
                "completed_frame_hf7_p99_supported": int(
                    row["completed_frame_hf7_p99_supported"]
                ),
                "completed_frame_hf7_p99_us": (
                    ""
                    if row["completed_frame_hf7_p99_us"] is None
                    else row["completed_frame_hf7_p99_us"]
                ),
                "sender_airtime_us": row["sender_airtime_us"],
                "background_throughput_mbps": row["background_throughput_mbps"],
            }
        )
    family_rows = []
    for family, arm_values in result["family_treatments"].items():
        for arm, metrics in arm_values.items():
            family_rows.append(
                {
                    "family_id": family,
                    "arm_id": arm,
                    **{
                        f"{metric}__{field}": interval[field]
                        for metric, interval in metrics.items()
                        for field in ("estimate", "ci95_low", "ci95_high")
                    },
                }
            )
    scenario_rows = []
    for family, scenarios in result["scenario_points"].items():
        for scenario, arm_values in scenarios.items():
            for arm, metrics in arm_values.items():
                scenario_rows.append(
                    {
                        "family_id": family,
                        "scenario_id": scenario,
                        "arm_id": arm,
                        **metrics,
                    }
                )
    return run_rows, family_rows, scenario_rows


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Complete held-out environment reliability result",
        "",
        f"**{WARNING}**",
        "",
        "## Complete-campaign results",
        "",
        "| Arm | Deadline miss rate | Sender airtime | Background throughput | P99 support |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in formal.ARM_IDS:
        metrics = report["treatments"][arm]
        support = report["p99_support"]["arms"][arm]
        lines.append(
            f"| {formal.ARM_LABELS[arm]} | "
            f"{100 * metrics['all_generated_deadline_miss_rate']['estimate']:.4f}% | "
            f"{metrics['sender_airtime_us']['estimate'] / 1000:.3f} ms | "
            f"{metrics['background_throughput_mbps']['estimate']:.3f} Mbit/s | "
            f"{support['eligible_run_count']}/{support['total_run_count']} runs |"
        )
    lines.extend(["", "## Comparisons", ""])
    for candidate, baseline in COMPARISONS:
        comparison_id = f"{candidate}_minus_{baseline}"
        values = report["comparisons"][comparison_id]
        lines.extend(
            [
                f"### {formal.ARM_LABELS[candidate]} versus {formal.ARM_LABELS[baseline]}",
                "",
                f"- Miss delta: {100 * values['deadline_miss_delta']['estimate']:.4f} "
                f"percentage points (95% interval "
                f"[{100 * values['deadline_miss_delta']['ci95_low']:.4f}, "
                f"{100 * values['deadline_miss_delta']['ci95_high']:.4f}]).",
                f"- Relative miss reduction: "
                f"{100 * values['relative_deadline_miss_reduction']['estimate']:.2f}%.",
                f"- Sender-airtime ratio: {values['sender_airtime_ratio']['estimate']:.4f} "
                f"(95% interval [{values['sender_airtime_ratio']['ci95_low']:.4f}, "
                f"{values['sender_airtime_ratio']['ci95_high']:.4f}]).",
                f"- Background-throughput loss: "
                f"{100 * values['background_throughput_loss']['estimate']:.3f}% "
                f"(95% interval "
                f"[{100 * values['background_throughput_loss']['ci95_low']:.3f}%, "
                f"{100 * values['background_throughput_loss']['ci95_high']:.3f}%]).",
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "- All 576 canonical runs passed strict validation.",
            "- Reliability and resource estimands include every frozen run.",
            "- The frozen run-level completed-frame P99 estimand is not assessable.",
            "- No P99 gate or policy-promotion decision is inferred from this report.",
            "- Completed-frame CDF/PDF plots are survivor-conditioned descriptive views.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="ascii",
    )


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _require(bool(rows), f"cannot write empty table: {path.name}")
    fields = list(rows[0])
    _require(all(list(row) == fields for row in rows),
             f"table columns differ: {path.name}")
    with path.open("w", newline="", encoding="ascii") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_outputs(
    output: Path,
    report: dict[str, Any],
    observations: Sequence[dict[str, Any]],
    result: dict[str, Any],
) -> Path:
    _require(not output.exists(), f"analysis output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        run_rows, family_rows, scenario_rows = _rows(observations, result)
        files = {
            "report_json": temporary / "complete_reliability_report.json",
            "report_markdown": temporary / "complete_reliability_report.md",
            "run_metrics": temporary / "run_metrics.csv",
            "family_metrics": temporary / "family_metrics.csv",
            "scenario_metrics": temporary / "scenario_metrics.csv",
        }
        _write_json(files["report_json"], report)
        files["report_markdown"].write_text(_markdown(report), encoding="ascii")
        _write_csv(files["run_metrics"], run_rows)
        _write_csv(files["family_metrics"], family_rows)
        _write_csv(files["scenario_metrics"], scenario_rows)
        manifest = {
            "schema_version": 1,
            "manifest_id": "environment-generalization-complete-reliability-artifacts-v1",
            "analysis": ANALYSIS_ID,
            "warning": WARNING,
            "source_campaign_manifest": report["source_closure"]["campaign_manifest"],
            "analyzer": report["source_closure"]["analyzer"],
            "counts": {
                "strictly_validated_runs": len(observations),
                "paired_units": len(observations) // len(formal.ARM_IDS),
                "families": len(result["family_treatments"]),
                "scenarios": sum(len(value) for value in result["scenario_points"].values()),
            },
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in files.values()
            },
        }
        _write_json(temporary / "analysis_artifact_manifest.json", manifest)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output.resolve()


def analyze(campaign_input: Path, output: Path, workers: int) -> Path:
    contract = formal.load_analysis_contract()
    actual_git = _actual_git_identity()
    _run_root, manifest_path = formal._manifest_root(campaign_input)
    manifest = formal._read_json(manifest_path)
    simulation_commit = manifest.get("project_commit")
    _require(isinstance(simulation_commit, str) and len(simulation_commit) == 40,
             "campaign simulation commit is invalid")
    _require_descendant(simulation_commit, actual_git["project_commit"])
    manifest_identity, jobs, families, scenarios = formal.validate_campaign_manifest(
        campaign_input,
        contract,
        {"project_commit": simulation_commit, "worktree_clean": True},
    )
    observations = collect_observations(jobs, workers)
    grid = formal.build_observation_grid(observations, families, scenarios, contract)
    result = hierarchical_bootstrap(grid, families, scenarios, contract)
    support = _p99_support(observations, families)
    generated_by_arm = {
        arm: sum(
            row["generated_frame_count"] for row in observations if row["arm_id"] == arm
        )
        for arm in formal.ARM_IDS
    }
    misses_by_arm = {
        arm: sum(
            row["deadline_miss_count"] for row in observations if row["arm_id"] == arm
        )
        for arm in formal.ARM_IDS
    }
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "evidence_role": "complete post-outcome reliability and resource analysis",
        "warning": WARNING,
        "source_closure": {
            "campaign_manifest": manifest_identity,
            "simulation_project_commit": simulation_commit,
            "analyzer": {
                **actual_git,
                "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": _sha256(Path(__file__).resolve()),
                "post_outcome": True,
            },
            "strict_validator": {
                "path": "tools/validate_outputs.py",
                "sha256": _sha256(ROOT / "tools/validate_outputs.py"),
            },
            "analysis_contract": {
                "path": str(formal.ANALYSIS_CONTRACT_PATH.relative_to(ROOT)),
                "sha256": _sha256(formal.ANALYSIS_CONTRACT_PATH),
            },
        },
        "population": copy.deepcopy(contract["population"]),
        "strict_validation": {
            "status": "pass",
            "validated_run_count": len(observations),
            "mixed_build_identity": False,
        },
        "bootstrap": {
            "method": result["method"],
            "replications": result["replications"],
            "random_seed": result["random_seed"],
            "point_weighting": contract["estimand"]["point_weighting"],
        },
        "treatments": result["treatments"],
        "comparisons": result["comparisons"],
        "generated_frame_count_by_arm": generated_by_arm,
        "deadline_miss_count_by_arm": misses_by_arm,
        "p99_support": support,
        "formal_qualification_status": {
            "status": "not_assessable",
            "reason": support["frozen_completed_p99_estimand"]["reason"],
        },
        "family_treatments": result["family_treatments"],
        "scenario_points": result["scenario_points"],
        "interpretation_limits": [
            "This analysis was specified after observing that the frozen P99 support gate fails.",
            "It does not replace the preregistered formal analysis.",
            "Completed-frame latency distributions are survivor-conditioned.",
            "Supplementary seeds are excluded from every reported estimand.",
        ],
    }
    return _write_outputs(output, report, observations, result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_input", type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--workers", default=16, type=int)
    arguments = parser.parse_args(argv)
    if arguments.workers <= 0:
        parser.error("--workers must be positive")
    try:
        output = analyze(
            arguments.campaign_input,
            arguments.output_directory.resolve(),
            arguments.workers,
        )
    except (CompleteReliabilityError, formal.QualificationAnalysisError, OSError) as error:
        parser.exit(2, f"ERROR: {error}\n")
    print(f"WROTE {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
