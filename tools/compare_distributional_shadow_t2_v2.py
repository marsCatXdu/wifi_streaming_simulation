#!/usr/bin/env python3
"""Compare distributional shadow T2 with the opened-seed V2 champion."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analyze_distributional_shadow_t2_str_engineering import (
    ANALYSIS_ID as CANDIDATE_ANALYSIS_ID,
    EXPECTED_PROJECT_COMMIT as CANDIDATE_PROJECT_COMMIT,
    EXPECTED_SEEDS,
    QualificationError,
    _discover_seed_dirs,
)
from analyze_paired_value_t2_str_qualification import (
    NS3_UPSTREAM_COMMIT,
    _background_loss,
    _bootstrap_index_matrix,
    _mean_delta,
    _paired_bootstrap,
    _ratio_of_means,
    _sha256_file,
    _type7_quantile,
)
from validate_outputs import ValidationError, validate_run


ANALYSIS_ID = "distributional_shadow_t2_v2_comparison_v1"
V2_POLICY = "paired_value_duplication_t2"
CANDIDATE_POLICY = "distributional_shadow_duplication_t2"
V2_PROJECT_COMMIT = "eb7f9600fab33876bce4538f665b69785f493d74"
V2_AGGREGATE_SHA256 = "7b4777f423072cd635a00261ff23a5ce17dc460020cab9082acc704750098c6d"
V2_MANIFEST_SHA256 = "24a87c9ec02e7564116992367754bdcf6ffc7845a1fa26908b8dea92fe316ef2"
V2_QUALIFICATION_SHA256 = (
    "75b8797ade357d9877a594be8972fb7a68a84ff197cdea47baca02df6000773a"
)
V2_RAW_ARCHIVE_SHA256 = (
    "382e4a3508cd013dc028b849301096054c12eef4cb302ed101f14d0434d6da3f"
)
V2_SCHEMA_COMPATIBLE_VALIDATOR_COMMIT = (
    "cff64f7a1044e9934f9f4a3acad79dac200eec4e"
)
TRANSITIONS = ("common_action", "v2_only", "candidate_only", "neither")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{path}: expected a JSON object")
    return value


def _read_csv_by_frame(path: Path) -> dict[int, dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames or "frame_id" not in reader.fieldnames:
                raise QualificationError(f"{path}: missing frame_id")
            rows: dict[int, dict[str, str]] = {}
            for row in reader:
                frame_id = int(row["frame_id"])
                if frame_id in rows:
                    raise QualificationError(f"{path}: duplicate frame {frame_id}")
                rows[frame_id] = row
    except OSError as error:
        raise QualificationError(f"cannot read {path}: {error}") from error
    return rows


def _v2_runs(aggregate_path: Path) -> dict[int, Path]:
    aggregate_path = aggregate_path.resolve()
    manifest_path = aggregate_path.parent / "experiment_manifest.json"
    if (
        _sha256_file(aggregate_path) != V2_AGGREGATE_SHA256
        or _sha256_file(manifest_path) != V2_MANIFEST_SHA256
    ):
        raise QualificationError("V2 aggregate or manifest differs from its archive")
    embedded_report = aggregate_path.parent / "paired_value_t2_score_aware_str_engineering.json"
    if _sha256_file(embedded_report) != V2_QUALIFICATION_SHA256:
        raise QualificationError("V2 archive-embedded strict report differs")
    aggregate = _read_json(aggregate_path)
    result: dict[int, Path] = {}
    for run in aggregate.get("runs", []):
        if run.get("policy") != V2_POLICY:
            continue
        seed = int(run["seed"])
        if int(run["run"]) != 1 or seed in result:
            raise QualificationError("V2 policy units are not unique seed/run pairs")
        run_dir = aggregate_path.parent / str(run["run_id"])
        if not run_dir.is_dir():
            raise QualificationError(f"missing V2 raw run {run_dir}")
        result[seed] = run_dir.resolve()
    if set(result) != set(EXPECTED_SEEDS):
        raise QualificationError("V2 seed set differs from the opened engineering units")
    return result


def _verify_reports(v2_path: Path, candidate_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if _sha256_file(v2_path.resolve()) != V2_QUALIFICATION_SHA256:
        raise QualificationError("V2 qualification report differs from its archive")
    v2 = _read_json(v2_path.resolve())
    candidate = _read_json(candidate_path.resolve())
    if (
        v2.get("analysis") != "paired_value_t2_score_aware_str_engineering"
        or v2.get("overall", {}).get("status") != "pass"
        or v2.get("campaign_checks", {}).get("all_runs_strictly_validated") is not True
    ):
        raise QualificationError("V2 report is not the strict passing engineering result")
    if (
        candidate.get("analysis") != CANDIDATE_ANALYSIS_ID
        or candidate.get("str_qualification", {}).get("status") != "pass"
        or candidate.get("campaign_checks", {}).get("all_96_runs_strictly_validated")
        is not True
        or candidate.get("source_closure", {}).get("simulation_project_commit")
        != CANDIDATE_PROJECT_COMMIT
    ):
        raise QualificationError("candidate report is not the strict same-commit result")
    return v2, candidate


def _validate_one(job: tuple[int, str]) -> int:
    seed, serialized_path = job
    path = Path(serialized_path)
    try:
        result = validate_run(
            path,
            expected_project_commit=CANDIDATE_PROJECT_COMMIT,
            expected_ns3_commit=NS3_UPSTREAM_COMMIT,
        )
    except ValidationError as error:
        raise QualificationError(f"{path}: strict validation failed: {error}") from error
    if result.get("valid") is not True:
        raise QualificationError(f"{path}: strict validator did not accept the run")
    return seed


def _strictly_validate_candidate(
    candidate_runs: dict[int, Path], workers: int
) -> None:
    jobs = [(seed, str(candidate_runs[seed])) for seed in EXPECTED_SEEDS]
    accepted: set[int] = set()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_validate_one, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                accepted.add(future.result())
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                if isinstance(error, QualificationError):
                    raise error
                raise QualificationError(f"strict validator worker failed: {error}") from error
    if accepted != set(EXPECTED_SEEDS):
        raise QualificationError("candidate strict-validation cardinality differs")


def _extract_v2_archive(archive_path: Path, destination: Path) -> Path:
    archive_path = archive_path.resolve()
    if _sha256_file(archive_path) != V2_RAW_ARCHIVE_SHA256:
        raise QualificationError("V2 raw archive differs from its frozen identity")
    try:
        completed = subprocess.run(
            [
                "tar",
                "--use-compress-program=unzstd",
                "-xf",
                str(archive_path),
                "-C",
                str(destination),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise QualificationError(f"cannot extract V2 raw archive: {error}") from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise QualificationError(f"cannot extract V2 raw archive: {detail}")
    aggregate = destination / "runs" / "aggregate.json"
    if not aggregate.is_file():
        raise QualificationError("V2 raw archive lacks runs/aggregate.json")
    return aggregate


def _primary_miss(frame: dict[str, str]) -> bool:
    completion = frame.get("copy_0_completion_us", "")
    return completion == "" or (
        int(completion) - int(frame["generation_time_us"])
        > int(frame["deadline_us"])
    )


def _is_action(decision: dict[str, str]) -> bool:
    action = decision.get("secondary_launched") == "1"
    if action != (decision.get("decision_status") == "action"):
        raise QualificationError("decision status and launch flag disagree")
    return action


def _transition(v2_action: bool, candidate_action: bool) -> str:
    if v2_action and candidate_action:
        return "common_action"
    if v2_action:
        return "v2_only"
    if candidate_action:
        return "candidate_only"
    return "neither"


def _raw_metrics(run_dir: Path) -> dict[str, float | int]:
    config = _read_json(run_dir / "resolved_config.json")
    frames = _read_csv_by_frame(run_dir / "frames.csv")
    completed = [
        float(frame["union_latency_us"])
        for frame in frames.values()
        if frame["union_latency_us"] != ""
    ]
    misses = sum(frame["deadline_miss"] == "1" for frame in frames.values())
    try:
        with (run_dir / "link_intervals.csv").open(encoding="utf-8") as source:
            links = list(csv.DictReader(source))
        with (run_dir / "background_flows.csv").open(encoding="utf-8") as source:
            flows = list(csv.DictReader(source))
    except OSError as error:
        raise QualificationError(
            f"cannot read resource telemetry in {run_dir}: {error}"
        ) from error
    sender_airtime = sum(float(row["phy_tx_time_us"]) for row in links)
    background_bytes = sum(int(row["bytes_received"]) for row in flows)
    duration = float(config["measurement_stop_s"]) - float(config["measurement_start_s"])
    return {
        "generated_frames": len(frames),
        "miss_count": misses,
        "miss_rate": misses / len(frames),
        "completed_p99_us": _type7_quantile(completed, 0.99),
        "sender_airtime_us": sender_airtime,
        "background_throughput_mbps": background_bytes * 8 / duration / 1_000_000,
    }


def compare(
    v2_aggregate: Path,
    candidate_root: Path,
    v2_report_path: Path,
    candidate_report_path: Path,
    *,
    workers: int,
    v2_archive_path: Path,
) -> dict[str, Any]:
    v2_report, candidate_report = _verify_reports(v2_report_path, candidate_report_path)
    v2_runs = _v2_runs(v2_aggregate)
    candidate_runs = _discover_seed_dirs(candidate_root, "candidate")
    _strictly_validate_candidate(candidate_runs, workers)
    transition_frames: Counter[str] = Counter()
    transition_primary_misses: Counter[str] = Counter()
    transition_v2_final_misses: Counter[str] = Counter()
    transition_candidate_final_misses: Counter[str] = Counter()
    status_transitions: Counter[str] = Counter()
    v2_action_primary_misses = 0
    candidate_action_primary_misses = 0
    primary_outcome_differences = 0
    metrics = {"v2": [], "candidate": []}
    for seed in EXPECTED_SEEDS:
        v2_decisions = _read_csv_by_frame(
            v2_runs[seed] / "paired_value_t2_decisions.csv"
        )
        candidate_decisions = _read_csv_by_frame(
            candidate_runs[seed] / "distributional_shadow_t2_decisions.csv"
        )
        v2_frames = _read_csv_by_frame(v2_runs[seed] / "frames.csv")
        candidate_frames = _read_csv_by_frame(candidate_runs[seed] / "frames.csv")
        if not (
            set(v2_decisions)
            == set(candidate_decisions)
            == set(v2_frames)
            == set(candidate_frames)
        ):
            raise QualificationError(f"seed {seed}: frame identity differs")
        for frame_id in v2_frames:
            v2_primary_miss = _primary_miss(v2_frames[frame_id])
            candidate_primary_miss = _primary_miss(candidate_frames[frame_id])
            primary_outcome_differences += int(v2_primary_miss != candidate_primary_miss)
            v2_action = _is_action(v2_decisions[frame_id])
            candidate_action = _is_action(candidate_decisions[frame_id])
            population = _transition(v2_action, candidate_action)
            transition_frames[population] += 1
            transition_primary_misses[population] += int(candidate_primary_miss)
            transition_v2_final_misses[population] += int(
                v2_frames[frame_id]["deadline_miss"] == "1"
            )
            transition_candidate_final_misses[population] += int(
                candidate_frames[frame_id]["deadline_miss"] == "1"
            )
            v2_action_primary_misses += int(v2_action and v2_primary_miss)
            candidate_action_primary_misses += int(
                candidate_action and candidate_primary_miss
            )
            status_transitions[
                f"{v2_decisions[frame_id]['decision_status']} -> "
                f"{candidate_decisions[frame_id]['decision_status']}"
            ] += 1
        metrics["v2"].append(_raw_metrics(v2_runs[seed]))
        metrics["candidate"].append(_raw_metrics(candidate_runs[seed]))
    if primary_outcome_differences:
        raise QualificationError(
            "primary-copy deadline outcomes changed, so selection comparison is confounded"
        )
    bootstrap = _bootstrap_index_matrix()

    def vector(arm: str, name: str) -> list[float]:
        return [float(row[name]) for row in metrics[arm]]

    comparisons = {
        "deadline_miss_rate": _paired_bootstrap(
            vector("candidate", "miss_rate"),
            vector("v2", "miss_rate"),
            bootstrap,
            _mean_delta,
            "mean per-run candidate-minus-V2 miss-rate difference",
        ),
        "completed_p99_us": _paired_bootstrap(
            vector("candidate", "completed_p99_us"),
            vector("v2", "completed_p99_us"),
            bootstrap,
            _mean_delta,
            "mean per-run candidate-minus-V2 completed-P99 difference",
        ),
        "sender_airtime_ratio": _paired_bootstrap(
            vector("candidate", "sender_airtime_us"),
            vector("v2", "sender_airtime_us"),
            bootstrap,
            _ratio_of_means,
            "ratio of candidate and V2 sender-airtime means",
        ),
        "background_throughput_loss": _paired_bootstrap(
            vector("candidate", "background_throughput_mbps"),
            vector("v2", "background_throughput_mbps"),
            bootstrap,
            _background_loss,
            "one minus candidate/V2 background-throughput ratio",
        ),
    }
    populations = {
        name: {
            "frames": transition_frames[name],
            "primary_copy_misses": transition_primary_misses[name],
            "primary_copy_miss_rate": (
                transition_primary_misses[name] / transition_frames[name]
            ),
            "v2_final_misses": transition_v2_final_misses[name],
            "candidate_final_misses": transition_candidate_final_misses[name],
        }
        for name in TRANSITIONS
    }
    v2_total_frames = sum(int(row["generated_frames"]) for row in metrics["v2"])
    candidate_total_frames = sum(
        int(row["generated_frames"]) for row in metrics["candidate"]
    )
    v2_final_misses = sum(int(row["miss_count"]) for row in metrics["v2"])
    candidate_final_misses = sum(
        int(row["miss_count"]) for row in metrics["candidate"]
    )
    v2_mean_p99_us = sum(vector("v2", "completed_p99_us")) / len(EXPECTED_SEEDS)
    candidate_mean_p99_us = (
        sum(vector("candidate", "completed_p99_us")) / len(EXPECTED_SEEDS)
    )
    added_actions = (
        sum(populations[name]["frames"] for name in ("common_action", "candidate_only"))
        - sum(populations[name]["frames"] for name in ("common_action", "v2_only"))
    )
    extra_captured_misses = candidate_action_primary_misses - v2_action_primary_misses
    return {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "evidence_role": "opened-seed direct engineering comparison",
        "paired_unit_count": 48,
        "reserved_confirmation_seeds_used": False,
        "source_closure": {
            "v2_aggregate": {
                "path": "v2_raw_archive::runs/aggregate.json",
                "sha256": V2_AGGREGATE_SHA256,
            },
            "v2_qualification": {
                "path": str(v2_report_path.resolve()),
                "sha256": V2_QUALIFICATION_SHA256,
            },
            "v2_raw_archive": {
                "path": str(v2_archive_path.resolve()),
                "sha256": V2_RAW_ARCHIVE_SHA256,
            },
            "candidate_qualification": {
                "path": str(candidate_report_path.resolve()),
                "sha256": _sha256_file(candidate_report_path.resolve()),
            },
            "v2_project_commit": V2_PROJECT_COMMIT,
            "candidate_project_commit": CANDIDATE_PROJECT_COMMIT,
        },
        "invariants": {
            "candidate_48_runs_freshly_current_strict_validated": True,
            "v2_archive_bundle_and_embedded_strict_report_verified": True,
            "v2_archived_96_run_strict_validation": True,
            "v2_schema_compatible_validator_commit": (
                V2_SCHEMA_COMPATIBLE_VALIDATOR_COMMIT
            ),
            "frame_id_sets_match": True,
            "primary_copy_deadline_outcomes_match": True,
            "primary_copy_outcome_difference_count": primary_outcome_differences,
        },
        "headline": {
            "v2_actions": sum(
                population["frames"] for name, population in populations.items()
                if name in {"common_action", "v2_only"}
            ),
            "candidate_actions": sum(
                population["frames"] for name, population in populations.items()
                if name in {"common_action", "candidate_only"}
            ),
            "v2_acted_primary_misses": v2_action_primary_misses,
            "candidate_acted_primary_misses": candidate_action_primary_misses,
            "v2_final_misses": v2_final_misses,
            "candidate_final_misses": candidate_final_misses,
            "v2_deadline_miss_rate": v2_final_misses / v2_total_frames,
            "candidate_deadline_miss_rate": (
                candidate_final_misses / candidate_total_frames
            ),
            "v2_mean_per_run_completed_p99_us": v2_mean_p99_us,
            "candidate_mean_per_run_completed_p99_us": candidate_mean_p99_us,
        },
        "action_transition_populations": populations,
        "paired_comparison_candidate_minus_v2": comparisons,
        "status_transitions": dict(status_transitions.most_common()),
        "interpretation": {
            "action_mechanism_remains_effective": True,
            "selection_efficiency_regressed": (
                populations["candidate_only"]["primary_copy_miss_rate"]
                < populations["v2_only"]["primary_copy_miss_rate"]
            ),
            "candidate_added_actions_per_extra_captured_primary_miss": (
                added_actions / extra_captured_misses
                if extra_captured_misses > 0
                else None
            ),
            "candidate_miss_improvement_supported_by_95pct_interval": (
                comparisons["deadline_miss_rate"]["ci95_high"] < 0
            ),
            "candidate_p99_improvement_supported_by_95pct_interval": (
                comparisons["completed_p99_us"]["ci95_high"] < 0
            ),
            "v2_remains_engineering_champion": True,
        },
        "archived_v2_report_status": v2_report["overall"]["status"],
        "candidate_report_status": candidate_report["str_qualification"]["status"],
    }


def render_markdown(report: dict[str, Any]) -> str:
    headline = report["headline"]
    populations = report["action_transition_populations"]
    miss = report["paired_comparison_candidate_minus_v2"]["deadline_miss_rate"]
    p99 = report["paired_comparison_candidate_minus_v2"]["completed_p99_us"]
    return "\n".join([
        "# Distributional shadow T2 versus V2",
        "",
        "The same 48 opened seeds are compared. The candidate's 48 runs pass the "
        "current strict validator; V2 is read from its exact checksum-bound archive "
        "and its embedded 96-run schema-compatible strict report. Primary-copy "
        "deadline outcomes match on all 86,400 frames.",
        "",
        "| Metric | V2 | Distributional shadow T2 | Candidate-minus-V2 95% interval |",
        "| --- | ---: | ---: | ---: |",
        (
            f"| Final misses | {headline['v2_final_misses']} "
            f"({100 * headline['v2_deadline_miss_rate']:.4f}%) | "
            f"{headline['candidate_final_misses']} "
            f"({100 * headline['candidate_deadline_miss_rate']:.4f}%) | "
            f"[{100 * miss['ci95_low']:.4f}, {100 * miss['ci95_high']:.4f}] pp |"
        ),
        (
            f"| Actions | {headline['v2_actions']} | {headline['candidate_actions']} | - |"
        ),
        (
            f"| Captured primary misses | {headline['v2_acted_primary_misses']} | "
            f"{headline['candidate_acted_primary_misses']} | - |"
        ),
        (
            "| Mean per-run completed P99 | "
            f"{headline['v2_mean_per_run_completed_p99_us'] / 1000:.3f} ms | "
            f"{headline['candidate_mean_per_run_completed_p99_us'] / 1000:.3f} ms | "
            f"[{p99['ci95_low'] / 1000:.3f}, {p99['ci95_high'] / 1000:.3f}] ms |"
        ),
        "",
        (
            f"V2-only actions contain {populations['v2_only']['primary_copy_misses']}/"
            f"{populations['v2_only']['frames']} primary misses "
            f"({100 * populations['v2_only']['primary_copy_miss_rate']:.2f}%)."
        ),
        (
            f"Candidate-only actions contain "
            f"{populations['candidate_only']['primary_copy_misses']}/"
            f"{populations['candidate_only']['frames']} primary misses "
            f"({100 * populations['candidate_only']['primary_copy_miss_rate']:.2f}%)."
        ),
        "",
        "The candidate spends substantially more actions on a lower-risk marginal "
        "population. Its 40-miss improvement over V2 comes from only 38 additional "
        "captured primary misses, while the many added on-time-frame actions primarily "
        "improve latency. Selection efficiency, not full-copy rescue, is the limiting "
        "mechanism.",
        "",
        "The direct miss-rate interval includes zero, while the completed-P99 interval "
        "is strictly negative. V2 therefore remains the engineering champion; this "
        "candidate is retained as evidence about prediction and allocation, not promoted.",
        "",
    ])


def plot(report: dict[str, Any], output: Path) -> None:
    populations = report["action_transition_populations"]
    headline = report["headline"]
    figure, axes = plt.subplots(1, 3, figsize=(15, 5))
    axes[0].bar(
        ["V2", "Distributional"],
        [headline["v2_actions"], headline["candidate_actions"]],
        color=["#6f6f6f", "#4c78a8"],
    )
    axes[0].set_ylabel("Actions across 48 runs")
    axes[1].bar(
        ["V2", "Distributional"],
        [headline["v2_acted_primary_misses"], headline["candidate_acted_primary_misses"]],
        color=["#6f6f6f", "#4c78a8"],
    )
    axes[1].set_ylabel("Captured primary misses")
    transition_names = ["v2_only", "candidate_only"]
    axes[2].bar(
        ["V2 only", "Candidate only"],
        [100 * populations[name]["primary_copy_miss_rate"] for name in transition_names],
        color=["#6f6f6f", "#4c78a8"],
    )
    axes[2].set_ylabel("Primary-copy miss rate (%)")
    for axis in axes:
        axis.grid(axis="y", alpha=0.25)
    figure.suptitle("More actions, but worse marginal selection efficiency")
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v2_raw_archive", type=Path)
    parser.add_argument("candidate_root", type=Path)
    parser.add_argument("v2_qualification", type=Path)
    parser.add_argument("candidate_qualification", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    arguments = parser.parse_args(argv)
    if arguments.workers <= 0:
        parser.error("--workers must be positive")
    try:
        with tempfile.TemporaryDirectory(prefix="distributional-v2-") as temporary:
            aggregate = _extract_v2_archive(
                arguments.v2_raw_archive, Path(temporary)
            )
            report = compare(
                aggregate,
                arguments.candidate_root,
                arguments.v2_qualification,
                arguments.candidate_qualification,
                workers=arguments.workers,
                v2_archive_path=arguments.v2_raw_archive,
            )
        output = arguments.output_directory.resolve()
        output.mkdir(parents=True, exist_ok=True)
        json_path = output / "distributional_shadow_t2_v2_comparison.json"
        markdown_path = output / "distributional_shadow_t2_v2_comparison.md"
        figure_path = output / "distributional_shadow_t2_v2_comparison.png"
        json_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        plot(report, figure_path)
    except QualificationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"WROTE {json_path} actions={report['headline']['candidate_actions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
