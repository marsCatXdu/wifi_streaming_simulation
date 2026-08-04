#!/usr/bin/env python3
"""Plot paired temporal-T2 engineering qualification outcomes against STR MLO."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze_paired_value_t2_str_qualification import (
    ARM_IDENTITIES,
    EXPECTED_PAIR_COUNT,
    PROFILES,
    V1_PROFILE,
    QualificationError,
    QualificationProfile,
    _background_metrics,
    _canonical_json,
    _finite,
    _flag,
    _frame_metrics,
    _integer,
    _read_csv,
    _read_json,
    _resolve_aggregate,
    _run_directory,
    _sender_airtime_us,
    _validate_manifest,
)
from validate_outputs import ValidationError, validate_run


DEFAULT_REPORT_NAME = "paired_value_t2_str_qualification.json"
DEFAULT_PLOT_DIRECTORY = "paired_value_t2_str_qualification"
FAVORABLE_COLOR = "#00796b"
UNFAVORABLE_COLOR = "#c44536"
POLICY_COLOR = "#4c78a8"
BASELINE_COLOR = "#6f6f6f"

DECISION_STATUS_ORDER = (
    "not_actionable",
    "history_warmup",
    "outside_decision_window",
    "frame_type_restricted",
    "descriptor_unavailable",
    "below_score_threshold",
    "airtime_guard_rejected",
    "launch_rejected",
    "action",
)
DECISION_STATUS_LABELS = {
    "not_actionable": "Not actionable",
    "history_warmup": "History warmup",
    "outside_decision_window": "Outside window",
    "frame_type_restricted": "Frame type restricted",
    "descriptor_unavailable": "Descriptor unavailable",
    "below_score_threshold": "Below score threshold",
    "airtime_guard_rejected": "Airtime guard rejected",
    "launch_rejected": "Launch rejected",
    "action": "Action",
}

ACTION_OUTCOME_ORDER = (
    "Primary on time, accelerated",
    "Primary on time, no benefit",
    "Primary miss rescued",
    "Primary miss late/incomplete",
)

DECISION_GATE_FLAGS = {
    "outside_decision_window": (False,) * 9,
    "history_warmup": (False,) * 9,
    "frame_type_restricted": (False,) * 9,
    "not_actionable": (False,) * 9,
    "descriptor_unavailable": (
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    ),
    "below_score_threshold": (
        True,
        True,
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    ),
    "airtime_guard_rejected": (
        True,
        True,
        True,
        True,
        True,
        True,
        False,
        False,
        False,
    ),
    "launch_rejected": (
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
    ),
    "action": (True,) * 9,
}


@dataclass(frozen=True)
class _TestOnlyContract:
    """Explicit test-only escape hatch for small synthetic campaign fixtures."""

    expected_pairs: tuple[tuple[int, int], ...]
    skip_strict_run_validation: bool = True

    def __post_init__(self) -> None:
        if (
            not self.expected_pairs
            or tuple(sorted(set(self.expected_pairs))) != self.expected_pairs
        ):
            raise ValueError("test-only expected pairs must be nonempty, unique, and ordered")


PAIR_COLUMNS = (
    "seed",
    "run",
    "policy_run_id",
    "str_run_id",
    "policy_deadline_miss_rate",
    "str_deadline_miss_rate",
    "miss_delta_percentage_points",
    "policy_completed_p99_us",
    "str_completed_p99_us",
    "completed_p99_delta_us",
    "policy_sender_airtime_us",
    "str_sender_airtime_us",
    "sender_airtime_ratio",
    "policy_background_throughput_mbps",
    "str_background_throughput_mbps",
    "background_throughput_loss",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise QualificationError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _report(
    path: Path,
    profile: QualificationProfile = V1_PROFILE,
) -> dict[str, Any]:
    report = _read_json(path.resolve())
    if (
        report.get("schema_version") != 1
        or report.get("analysis") != profile.analysis_id
        or (
            profile != V1_PROFILE
            and report.get("qualification_profile") != profile.key
        )
    ):
        raise QualificationError(f"{path}: not a schema-v1 paired T2/STR report")
    checks = report.get("campaign_checks")
    required_checks = {
        "all_metrics_reconstructed_from_raw_artifacts",
        "all_runs_strictly_validated",
        "exact_48_paired_units",
        "exact_two_declared_arms",
    }
    if not isinstance(checks, dict) or any(
        checks.get(name) is not True for name in required_checks
    ):
        raise QualificationError(f"{path}: report does not certify its raw campaign evidence")
    return report


def _validate_test_manifest(
    aggregate: dict[str, Any], manifest_path: Path
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Bind a deliberately small synthetic fixture without weakening production."""
    manifest = _read_json(manifest_path)
    manifest_runs = manifest.get("runs")
    aggregate_runs = aggregate.get("runs")
    if not isinstance(manifest_runs, list) or not isinstance(aggregate_runs, list):
        raise QualificationError("test manifest and aggregate require run lists")
    manifest_by_id: dict[str, dict[str, Any]] = {}
    for item in manifest_runs:
        if not isinstance(item, dict):
            raise QualificationError("test manifest run entry is not an object")
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in manifest_by_id:
            raise QualificationError("test manifest has a duplicate or invalid run ID")
        if item.get("status") != "complete" or item.get("directory") != run_id:
            raise QualificationError(f"test manifest run {run_id} is not canonical/complete")
        manifest_by_id[run_id] = item
    aggregate_by_id: dict[str, dict[str, Any]] = {}
    for item in aggregate_runs:
        if not isinstance(item, dict):
            raise QualificationError("test aggregate run entry is not an object")
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in aggregate_by_id:
            raise QualificationError("test aggregate has a duplicate or invalid run ID")
        aggregate_by_id[run_id] = item
    if set(manifest_by_id) != set(aggregate_by_id):
        raise QualificationError("test manifest and aggregate run identities differ")
    for run_id, declared in manifest_by_id.items():
        observed = aggregate_by_id[run_id]
        if (
            declared.get("seed") != observed.get("seed")
            or declared.get("run") != observed.get("run")
            or declared.get("topology") != observed.get("topology")
            or declared.get("policy") != observed.get("policy")
            or _canonical_json(declared.get("config"))
            != _canonical_json(observed.get("config"))
        ):
            raise QualificationError(f"test manifest run {run_id} identity mismatch")
    project_commit = manifest.get("project_commit")
    ns3_commit = manifest.get("ns3_upstream_commit")
    if not isinstance(project_commit, str) or not isinstance(ns3_commit, str):
        raise QualificationError("test manifest omits commit identities")
    return {
        "path": str(manifest_path.resolve()),
        "sha256": _sha256_file(manifest_path),
        "project_commit": project_commit,
        "ns3_upstream_commit": ns3_commit,
    }, aggregate_by_id


def _strictly_validate_runs(
    runs: Sequence[dict[str, Any]],
    aggregate_path: Path,
    manifest_identity: dict[str, Any],
) -> None:
    """Revalidate raw run bytes after the qualification report was written."""

    def validate_one(run: dict[str, Any]) -> None:
        run_dir = _run_directory(run, aggregate_path)
        try:
            result = validate_run(
                run_dir,
                expected_run_id=run["run_id"],
                expected_project_commit=manifest_identity["project_commit"],
                expected_ns3_commit=manifest_identity["ns3_upstream_commit"],
            )
        except ValidationError as error:
            raise QualificationError(
                f"{run_dir}: strict plot-input validation failed: {error}"
            ) from error
        if result.get("valid") is not True or result.get("run_id") != run["run_id"]:
            raise QualificationError(f"{run_dir}: strict validator returned invalid identity")

    workers = min(8, len(runs))
    if workers == 0:
        raise QualificationError("plotting requires at least one campaign run")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(validate_one, runs))


def _bind_report_to_aggregate(
    aggregate: dict[str, Any],
    aggregate_path: Path,
    report: dict[str, Any],
    test_contract: _TestOnlyContract | None,
    profile: QualificationProfile = V1_PROFILE,
) -> list[dict[str, Any]]:
    manifests = report["campaign_checks"].get("manifests")
    if not isinstance(manifests, list) or len(manifests) != 1:
        raise QualificationError("plotting requires one manifest-bound qualification report")
    manifest_path = aggregate_path.parent / "experiment_manifest.json"
    if _sha256_file(manifest_path) != manifests[0].get("sha256"):
        raise QualificationError("analysis report is not bound to this campaign manifest")
    if test_contract is None:
        manifest_identity, runs_by_id = _validate_manifest(
            aggregate_path, aggregate, profile
        )
    else:
        manifest_identity, runs_by_id = _validate_test_manifest(aggregate, manifest_path)
    if manifest_identity.get("sha256") != manifests[0].get("sha256"):
        raise QualificationError("validated manifest identity differs from analysis report")
    runs = list(runs_by_id.values())
    for run in runs:
        run_dir = _run_directory(run, aggregate_path)
        config = run.get("config")
        if not isinstance(config, dict):
            raise QualificationError(f"{aggregate_path}: run {run['run_id']} has no config")
        resolved = _read_json(run_dir / "resolved_config.json")
        if _canonical_json(config) != _canonical_json(resolved):
            raise QualificationError(f"{run_dir}: aggregate and resolved config differ")
    if test_contract is None or not test_contract.skip_strict_run_validation:
        _strictly_validate_runs(runs, aggregate_path, manifest_identity)
    return runs


def _expected_pairs(
    report: dict[str, Any],
    test_contract: _TestOnlyContract | None,
    profile: QualificationProfile = V1_PROFILE,
) -> list[tuple[int, int]]:
    units = report.get("paired_units")
    if not isinstance(units, list) or len(units) != report.get("paired_unit_count"):
        raise QualificationError("analysis report has invalid paired units")
    pairs: list[tuple[int, int]] = []
    for unit in units:
        if not isinstance(unit, dict):
            raise QualificationError("analysis report has a non-object paired unit")
        try:
            pair = int(unit["seed"]), int(unit["run"])
        except (KeyError, TypeError, ValueError) as error:
            raise QualificationError("analysis report has an invalid paired unit") from error
        pairs.append(pair)
    if len(pairs) != len(set(pairs)) or pairs != sorted(pairs):
        raise QualificationError("analysis report paired units are not unique and ordered")
    expected = (
        list(profile.expected_seed_run_units)
        if test_contract is None
        else list(test_contract.expected_pairs)
    )
    if pairs != expected:
        description = (
            f"the frozen {EXPECTED_PAIR_COUNT}-unit contract"
            if test_contract is None
            else "the explicit test-only contract"
        )
        raise QualificationError(f"analysis report paired units differ from {description}")
    return pairs


def _raw_run_metrics(
    run: dict[str, Any], aggregate_path: Path
) -> dict[str, int | float | str]:
    run_dir = _run_directory(run, aggregate_path)
    config = run.get("config")
    if not isinstance(config, dict):
        raise QualificationError(f"{aggregate_path}: run {run['run_id']} has no config")
    if (
        config.get("run_id") != run.get("run_id")
        or config.get("seed") != run.get("seed")
        or config.get("run") != run.get("run")
        or (config.get("topology"), config.get("policy"))
        != (run.get("topology"), run.get("policy"))
    ):
        raise QualificationError(f"{run_dir}: aggregate run identity mismatch")
    return {
        "run_id": run["run_id"],
        **_frame_metrics(run_dir, config),
        "sender_airtime_us": _sender_airtime_us(run_dir),
        **_background_metrics(run_dir, config),
    }


def _assert_close(actual: float, expected: Any, description: str) -> None:
    try:
        expected_float = float(expected)
    except (TypeError, ValueError) as error:
        raise QualificationError(f"analysis report has invalid {description}") from error
    if not math.isclose(actual, expected_float, rel_tol=1e-12, abs_tol=1e-9):
        raise QualificationError(
            f"raw {description} {actual!r} differs from report {expected_float!r}"
        )


def _verify_report_summaries(
    indexes: dict[str, dict[tuple[int, int], dict[str, int | float | str]]],
    pairs: list[tuple[int, int]],
    report: dict[str, Any],
) -> None:
    fields = {
        "all_generated_deadline_miss_rate": "all_generated_deadline_miss_rate",
        "completed_frame_p99_us": "completed_frame_p99_us",
        "sender_phy_tx_airtime_us": "sender_airtime_us",
        "background_throughput_mbps": "background_throughput_mbps",
    }
    treatments = report.get("treatments")
    if not isinstance(treatments, dict):
        raise QualificationError("analysis report has no treatment summaries")
    for arm in ARM_IDENTITIES:
        treatment = treatments.get(arm)
        if not isinstance(treatment, dict):
            raise QualificationError(f"analysis report has no {arm} summary")
        for report_field, raw_field in fields.items():
            summary = treatment.get(report_field)
            if not isinstance(summary, dict):
                raise QualificationError(f"analysis report has no {arm} {report_field}")
            mean = statistics.mean(float(indexes[arm][pair][raw_field]) for pair in pairs)
            _assert_close(mean, summary.get("mean"), f"{arm} {report_field} mean")


def _paired_rows_from_runs(
    runs: Sequence[dict[str, Any]],
    aggregate_path: Path,
    pairs: list[tuple[int, int]],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    indexes: dict[str, dict[tuple[int, int], dict[str, int | float | str]]] = {
        arm: {} for arm in ARM_IDENTITIES
    }
    for run in runs:
        identity = run.get("topology"), run.get("policy")
        matching_arms = [
            arm for arm, expected in ARM_IDENTITIES.items() if identity == expected
        ]
        if len(matching_arms) != 1:
            raise QualificationError(f"undeclared plot arm {identity!r}")
        try:
            pair = int(run["seed"]), int(run["run"])
        except (KeyError, TypeError, ValueError) as error:
            raise QualificationError(f"run {run.get('run_id')} has invalid seed/run") from error
        arm = matching_arms[0]
        if pair in indexes[arm]:
            raise QualificationError(f"duplicate {arm} run for seed/run {pair}")
        indexes[arm][pair] = _raw_run_metrics(run, aggregate_path)
    expected = set(pairs)
    for arm, index in indexes.items():
        if set(index) != expected:
            raise QualificationError(f"{arm} paired units differ from the analysis report")
    _verify_report_summaries(indexes, pairs, report)

    rows: list[dict[str, Any]] = []
    for seed, run_number in pairs:
        policy = indexes["policy"][(seed, run_number)]
        baseline = indexes["str_mlo"][(seed, run_number)]
        str_airtime = float(baseline["sender_airtime_us"])
        str_background = float(baseline["background_throughput_mbps"])
        if str_airtime <= 0 or str_background <= 0:
            raise QualificationError(f"seed/run {(seed, run_number)} has zero STR resource")
        policy_miss = float(policy["all_generated_deadline_miss_rate"])
        str_miss = float(baseline["all_generated_deadline_miss_rate"])
        policy_p99 = float(policy["completed_frame_p99_us"])
        str_p99 = float(baseline["completed_frame_p99_us"])
        policy_background = float(policy["background_throughput_mbps"])
        rows.append(
            {
                "seed": seed,
                "run": run_number,
                "policy_run_id": policy["run_id"],
                "str_run_id": baseline["run_id"],
                "policy_deadline_miss_rate": policy_miss,
                "str_deadline_miss_rate": str_miss,
                "miss_delta_percentage_points": 100.0 * (policy_miss - str_miss),
                "policy_completed_p99_us": policy_p99,
                "str_completed_p99_us": str_p99,
                "completed_p99_delta_us": policy_p99 - str_p99,
                "policy_sender_airtime_us": policy["sender_airtime_us"],
                "str_sender_airtime_us": str_airtime,
                "sender_airtime_ratio": float(policy["sender_airtime_us"])
                / str_airtime,
                "policy_background_throughput_mbps": policy_background,
                "str_background_throughput_mbps": str_background,
                "background_throughput_loss": 1.0
                - policy_background / str_background,
            }
        )
    return rows


def paired_rows(
    aggregate_input: Path,
    report_path: Path,
    *,
    profile: QualificationProfile = V1_PROFILE,
    _test_contract: _TestOnlyContract | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    """Reconstruct paired plot rows and bind them to one strict report."""
    aggregate_path = _resolve_aggregate(aggregate_input)
    aggregate = _read_json(aggregate_path)
    report = _report(report_path, profile)
    pairs = _expected_pairs(report, _test_contract, profile)
    runs = _bind_report_to_aggregate(
        aggregate, aggregate_path, report, _test_contract, profile
    )
    return (
        _paired_rows_from_runs(runs, aggregate_path, pairs, report),
        report,
        aggregate_path,
    )


def _policy_admission_diagnostics_from_runs(
    aggregate_path: Path,
    report: dict[str, Any],
    runs: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize policy admission and primary-copy outcomes for diagnosis only."""
    policy_runs = [
        run
        for run in runs
        if (run.get("topology"), run.get("policy")) == ARM_IDENTITIES["policy"]
    ]
    if len(policy_runs) != report.get("paired_unit_count"):
        raise QualificationError("policy diagnostic run count differs from strict report")

    funnel_counts = {
        "All generated frames": 0,
        "Primary actionable": 0,
        "In window and history ready": 0,
        "P-frame model evaluated": 0,
        "Score threshold passed": 0,
        "Airtime guard admitted": 0,
        "Secondary launched": 0,
    }
    status_counts = {status: 0 for status in DECISION_STATUS_ORDER}
    status_primary_misses = {status: 0 for status in DECISION_STATUS_ORDER}
    action_outcomes = {outcome: 0 for outcome in ACTION_OUTCOME_ORDER}
    seen_frames: set[tuple[str, int]] = set()
    for run in policy_runs:
        run_dir = _run_directory(run, aggregate_path)
        frames = _read_csv(
            run_dir / "frames.csv",
            {
                "run_id",
                "frame_id",
                "generation_time_us",
                "deadline_us",
                "duplicated",
                "union_completion_us",
                "copy_0_completion_us",
                "deadline_miss",
                "incomplete",
            },
        )
        decisions = _read_csv(
            run_dir / "paired_value_t2_decisions.csv",
            {
                "run_id",
                "frame_id",
                "decision_status",
                "primary_copy_id",
                "secondary_copy_id",
                "frame_type",
                "primary_actionable",
                "inside_decision_window",
                "history_ready",
                "descriptor_checked",
                "descriptor_available",
                "feature_evaluated",
                "passes_score_threshold",
                "guard_admission_considered",
                "guard_admitted",
                "launch_attempted",
                "secondary_launched",
            },
        )
        frame_index: dict[int, dict[str, str]] = {}
        for frame in frames:
            if frame.get("run_id") != run["run_id"]:
                raise QualificationError(f"{run_dir}/frames.csv: run_id mismatch")
            frame_id = _integer(frame.get("frame_id"), f"{run_dir}: frame_id")
            if frame_id in frame_index:
                raise QualificationError(f"{run_dir}/frames.csv: duplicate frame_id")
            frame_index[frame_id] = frame
        if len(decisions) != len(frame_index):
            raise QualificationError(f"{run_dir}: decision/frame row count differs")

        for decision in decisions:
            if decision.get("run_id") != run["run_id"]:
                raise QualificationError(
                    f"{run_dir}/paired_value_t2_decisions.csv: run_id mismatch"
                )
            frame_id = _integer(decision.get("frame_id"), f"{run_dir}: frame_id")
            frame = frame_index.get(frame_id)
            key = run["run_id"], frame_id
            if frame is None or key in seen_frames:
                raise QualificationError(f"{run_dir}: invalid decision/frame mapping")
            seen_frames.add(key)
            if (
                decision.get("primary_copy_id") != "0"
                or decision.get("secondary_copy_id") != "1"
            ):
                raise QualificationError(f"{run_dir}: unexpected primary/secondary copy IDs")
            status = decision.get("decision_status")
            if status not in status_counts:
                raise QualificationError(f"{run_dir}: unknown decision status {status!r}")

            actionable = _flag(
                decision.get("primary_actionable"), f"{run_dir}: primary_actionable"
            )
            inside_window = _flag(
                decision.get("inside_decision_window"),
                f"{run_dir}: inside_decision_window",
            )
            history_ready = _flag(
                decision.get("history_ready"), f"{run_dir}: history_ready"
            )
            descriptor_checked = _flag(
                decision.get("descriptor_checked"), f"{run_dir}: descriptor_checked"
            )
            descriptor_available = _flag(
                decision.get("descriptor_available"),
                f"{run_dir}: descriptor_available",
            )
            feature_evaluated = _flag(
                decision.get("feature_evaluated"), f"{run_dir}: feature_evaluated"
            )
            score_value = decision.get("passes_score_threshold")
            score_recorded = score_value != ""
            score_passed = score_recorded and _flag(
                score_value, f"{run_dir}: passes_score_threshold"
            )
            guard_considered = _flag(
                decision.get("guard_admission_considered"),
                f"{run_dir}: guard_admission_considered",
            )
            guard_admitted = _flag(
                decision.get("guard_admitted"), f"{run_dir}: guard_admitted"
            )
            launch_attempted = _flag(
                decision.get("launch_attempted"), f"{run_dir}: launch_attempted"
            )
            launched = _flag(
                decision.get("secondary_launched"), f"{run_dir}: secondary_launched"
            )
            if (
                descriptor_available and not descriptor_checked
                or feature_evaluated and not descriptor_available
                or score_passed and not feature_evaluated
                or guard_considered != score_passed
                or guard_admitted and not guard_considered
                or launch_attempted != guard_admitted
                or launched and not launch_attempted
            ):
                raise QualificationError(f"{run_dir}: inconsistent admission funnel flags")

            frame_type = decision.get("frame_type")
            if not inside_window:
                expected_status = "outside_decision_window"
            elif not history_ready:
                expected_status = "history_warmup"
            elif frame_type != "P_FRAME":
                expected_status = "frame_type_restricted"
            elif not actionable:
                expected_status = "not_actionable"
            elif not descriptor_checked:
                raise QualificationError(f"{run_dir}: actionable frame skipped descriptor gate")
            elif not descriptor_available:
                expected_status = "descriptor_unavailable"
            elif not feature_evaluated:
                raise QualificationError(f"{run_dir}: available descriptor skipped model gate")
            elif not score_passed:
                expected_status = "below_score_threshold"
            elif not guard_admitted:
                expected_status = "airtime_guard_rejected"
            elif not launched:
                expected_status = "launch_rejected"
            else:
                expected_status = "action"
            observed_late_flags = (
                descriptor_checked,
                descriptor_available,
                feature_evaluated,
                score_recorded,
                score_passed,
                guard_considered,
                guard_admitted,
                launch_attempted,
                launched,
            )
            if (
                status != expected_status
                or observed_late_flags != DECISION_GATE_FLAGS[status]
            ):
                raise QualificationError(
                    f"{run_dir}: decision status and gate evidence disagree"
                )

            generation = _finite(
                frame.get("generation_time_us"),
                f"{run_dir}: generation_time_us",
                nonnegative=True,
            )
            deadline = _finite(
                frame.get("deadline_us"), f"{run_dir}: deadline_us", nonnegative=True
            )
            primary_serialized = frame.get("copy_0_completion_us", "")
            primary_completion = (
                None
                if primary_serialized == ""
                else _finite(
                    primary_serialized,
                    f"{run_dir}: copy_0_completion_us",
                    nonnegative=True,
                )
            )
            union_serialized = frame.get("union_completion_us", "")
            union_completion = (
                None
                if union_serialized == ""
                else _finite(
                    union_serialized,
                    f"{run_dir}: union_completion_us",
                    nonnegative=True,
                )
            )
            incomplete = _flag(frame.get("incomplete"), f"{run_dir}: incomplete")
            duplicated = _flag(frame.get("duplicated"), f"{run_dir}: duplicated")
            if (
                incomplete != (union_completion is None)
                or (union_completion is None and primary_completion is not None)
                or (
                    primary_completion is not None
                    and primary_completion < generation
                )
                or (union_completion is not None and union_completion < generation)
                or (
                    primary_completion is not None
                    and union_completion is not None
                    and union_completion > primary_completion
                )
                or launched != duplicated
            ):
                raise QualificationError(f"{run_dir}: inconsistent frame/action evidence")
            primary_miss = (
                primary_completion is None
                or primary_completion - generation > deadline
            )
            union_miss = _flag(
                frame.get("deadline_miss"), f"{run_dir}: deadline_miss"
            )
            computed_union_miss = (
                union_completion is None
                or union_completion - generation > deadline
            )
            if union_miss != computed_union_miss:
                raise QualificationError(f"{run_dir}: union deadline evidence disagrees")

            funnel_counts["All generated frames"] += 1
            funnel_counts["Primary actionable"] += actionable
            funnel_counts["In window and history ready"] += (
                actionable and inside_window and history_ready
            )
            funnel_counts["P-frame model evaluated"] += feature_evaluated
            funnel_counts["Score threshold passed"] += score_passed
            funnel_counts["Airtime guard admitted"] += guard_admitted
            funnel_counts["Secondary launched"] += launched
            status_counts[status] += 1
            status_primary_misses[status] += primary_miss
            if launched:
                if primary_miss and not union_miss:
                    action_outcomes["Primary miss rescued"] += 1
                elif primary_miss:
                    action_outcomes["Primary miss late/incomplete"] += 1
                elif union_completion < primary_completion:
                    action_outcomes["Primary on time, accelerated"] += 1
                else:
                    action_outcomes["Primary on time, no benefit"] += 1

    funnel = list(funnel_counts.items())
    if any(right > left for (_, left), (_, right) in zip(funnel, funnel[1:])):
        raise QualificationError("policy admission funnel is not cumulative")
    reported_total = (
        report.get("treatments", {})
        .get("policy", {})
        .get("generated_frame_count", {})
        .get("total")
    )
    if reported_total != funnel_counts["All generated frames"]:
        raise QualificationError("policy decision total differs from strict report")
    if sum(status_counts.values()) != funnel_counts["All generated frames"]:
        raise QualificationError("policy terminal statuses do not cover all frames")
    if sum(action_outcomes.values()) != funnel_counts["Secondary launched"]:
        raise QualificationError("policy action outcomes do not cover all launches")
    return {
        "evidence_role": "diagnostic_only_not_a_qualification_gate",
        "funnel": [{"label": label, "count": count} for label, count in funnel],
        "statuses": [
            {
                "status": status,
                "label": DECISION_STATUS_LABELS[status],
                "count": status_counts[status],
                "primary_miss_count": status_primary_misses[status],
                "primary_miss_rate": (
                    status_primary_misses[status] / status_counts[status]
                    if status_counts[status]
                    else 0.0
                ),
            }
            for status in DECISION_STATUS_ORDER
        ],
        "action_outcomes": [
            {"label": label, "count": count}
            for label, count in action_outcomes.items()
        ],
    }


def policy_admission_diagnostics(
    aggregate_path: Path,
    report: dict[str, Any],
    *,
    profile: QualificationProfile = V1_PROFILE,
    _test_contract: _TestOnlyContract | None = None,
) -> dict[str, Any]:
    """Return freshly validated diagnostic-only policy admission evidence."""
    aggregate_path = _resolve_aggregate(aggregate_path)
    aggregate = _read_json(aggregate_path)
    _expected_pairs(report, _test_contract, profile)
    runs = _bind_report_to_aggregate(
        aggregate, aggregate_path, report, _test_contract, profile
    )
    return _policy_admission_diagnostics_from_runs(
        aggregate_path, report, runs
    )


def _colors(values: Iterable[float]) -> list[str]:
    return [FAVORABLE_COLOR if value < 0 else UNFAVORABLE_COLOR for value in values]


def _paired_delta_plot(
    rows: list[dict[str, Any]], report: dict[str, Any], output: Path
) -> None:
    policy_label = str(
        report["treatments"]["policy"].get("label", V1_PROFILE.policy_label)
    )
    seeds = np.asarray([row["seed"] for row in rows], dtype=int)
    miss = np.asarray([row["miss_delta_percentage_points"] for row in rows])
    p99_ms = np.asarray([row["completed_p99_delta_us"] for row in rows]) / 1000.0
    comparison = report["comparison_against_str"]
    miss_summary = comparison["all_generated_deadline_miss_rate"][
        "paired_policy_minus_str"
    ]
    p99_summary = comparison["completed_frame_p99_us"]["paired_policy_minus_str"]

    figure, axes = plt.subplots(2, 1, figsize=(12.0, 8.0), sharex=True)
    series = (
        (
            axes[0],
            miss,
            100.0 * miss_summary["estimate"],
            100.0 * miss_summary["ci95_low"],
            100.0 * miss_summary["ci95_high"],
            "Deadline-miss delta (percentage points)",
        ),
        (
            axes[1],
            p99_ms,
            p99_summary["estimate"] / 1000.0,
            p99_summary["ci95_low"] / 1000.0,
            p99_summary["ci95_high"] / 1000.0,
            "Completed-frame HF7 P99 delta (ms)",
        ),
    )
    for axis, values, estimate, ci_low, ci_high, ylabel in series:
        axis.axhspan(ci_low, ci_high, color=POLICY_COLOR, alpha=0.12)
        axis.axhline(estimate, color=POLICY_COLOR, linewidth=1.8, label="paired mean")
        axis.axhline(0.0, color="black", linewidth=1.0)
        axis.bar(seeds, values, color=_colors(values), width=0.78, alpha=0.88)
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", color="#dddddd", linewidth=0.7)
        axis.legend(loc="upper right", frameon=False)
    axes[0].set_title(
        f"{policy_label} minus STR MLO by matched seed\n"
        f"Negative values favor {policy_label}; band is the global paired 95% interval"
    )
    axes[1].set_xlabel("Seed (run 1)")
    axes[1].set_xticks(seeds[::4])
    figure.tight_layout()
    figure.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(figure)


def _resource_axis(
    axis: plt.Axes,
    per_pair: np.ndarray,
    estimate: float,
    ci_low: float,
    ci_high: float,
    threshold: float,
    status: str,
    title: str,
    xlabel: str,
) -> None:
    spread = max(np.ptp(per_pair), abs(threshold - estimate), 1e-6)
    lower = min(float(np.min(per_pair)), ci_low, estimate, threshold) - 0.18 * spread
    upper = max(float(np.max(per_pair)), ci_high, estimate, threshold) + 0.18 * spread
    axis.axvspan(lower, threshold, color=FAVORABLE_COLOR, alpha=0.08)
    axis.axvline(threshold, color=UNFAVORABLE_COLOR, linestyle="--", linewidth=1.8)
    jitter = np.linspace(-0.055, 0.055, len(per_pair))
    axis.scatter(per_pair, jitter, s=16, color=BASELINE_COLOR, alpha=0.55)
    axis.errorbar(
        estimate,
        0.22,
        xerr=[[estimate - ci_low], [ci_high - estimate]],
        fmt="o",
        markersize=8,
        color=POLICY_COLOR,
        capsize=5,
        linewidth=2,
    )
    if status not in {"pass", "fail"}:
        raise QualificationError(f"invalid resource-gate status {status!r}")
    axis.text(
        estimate,
        0.31,
        f"campaign estimate ({status.upper()})",
        ha="center",
        va="bottom",
        color=POLICY_COLOR,
    )
    axis.text(
        threshold,
        -0.18,
        f"gate {threshold:g}",
        ha="right",
        va="top",
        color=UNFAVORABLE_COLOR,
    )
    axis.set_xlim(lower, upper)
    axis.set_ylim(-0.28, 0.46)
    axis.set_yticks([0.0, 0.22], ["paired runs", "campaign"])
    axis.set_xlabel(xlabel)
    axis.set_title(title)
    axis.grid(axis="x", color="#dddddd", linewidth=0.7)


def _resource_plot(
    rows: list[dict[str, Any]], report: dict[str, Any], output: Path
) -> None:
    policy_label = str(
        report["treatments"]["policy"].get("label", V1_PROFILE.policy_label)
    )
    comparison = report["comparison_against_str"]
    criteria = report["resource_target_against_str"]["criteria"]
    airtime = comparison["sender_phy_tx_airtime_ratio"]
    airtime_ci = airtime["paired_bootstrap"]
    background = comparison["background_throughput_loss"]
    background_ci = background["paired_bootstrap"]
    pair_airtime = np.asarray([row["sender_airtime_ratio"] for row in rows])
    pair_background_percent = 100.0 * np.asarray(
        [row["background_throughput_loss"] for row in rows]
    )

    figure, axes = plt.subplots(1, 2, figsize=(12.0, 4.8))
    _resource_axis(
        axes[0],
        pair_airtime,
        airtime["estimate"],
        airtime_ci["ci95_low"],
        airtime_ci["ci95_high"],
        criteria["sender_airtime_ratio"]["threshold"],
        criteria["sender_airtime_ratio"]["status"],
        "Sender PHY airtime",
        f"{policy_label} / STR ratio",
    )
    _resource_axis(
        axes[1],
        pair_background_percent,
        100.0 * background["estimate"],
        100.0 * background_ci["ci95_low"],
        100.0 * background_ci["ci95_high"],
        100.0 * criteria["background_throughput_loss"]["threshold"],
        criteria["background_throughput_loss"]["status"],
        "Background throughput",
        "Loss versus STR (%)",
    )
    figure.suptitle(
        "Resource outcomes and frozen gates\n"
        "Dots are per-pair diagnostics; campaign points and intervals come from the strict report",
        y=1.03,
    )
    figure.tight_layout()
    figure.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(figure)


def _tradeoff_plot(
    rows: list[dict[str, Any]], policy_label: str, output: Path
) -> None:
    miss = np.asarray([row["miss_delta_percentage_points"] for row in rows])
    p99_ms = np.asarray([row["completed_p99_delta_us"] for row in rows]) / 1000.0
    airtime_overhead = 100.0 * (
        np.asarray([row["sender_airtime_ratio"] for row in rows]) - 1.0
    )
    seeds = [int(row["seed"]) for row in rows]

    figure, axis = plt.subplots(figsize=(8.8, 6.8))
    axis.axvspan(float(np.min(miss)) - 0.1, 0.0, color=FAVORABLE_COLOR, alpha=0.04)
    axis.axhspan(float(np.min(p99_ms)) - 0.1, 0.0, color=FAVORABLE_COLOR, alpha=0.04)
    axis.axvline(0.0, color="black", linewidth=1.0)
    axis.axhline(0.0, color="black", linewidth=1.0)
    points = axis.scatter(
        miss,
        p99_ms,
        c=airtime_overhead,
        cmap="viridis",
        s=44,
        edgecolors="white",
        linewidths=0.45,
    )
    distance = np.hypot(
        (miss - np.mean(miss)) / max(np.std(miss), 1e-12),
        (p99_ms - np.mean(p99_ms)) / max(np.std(p99_ms), 1e-12),
    )
    for index in np.argsort(distance)[-5:]:
        axis.annotate(
            str(seeds[int(index)]),
            (miss[int(index)], p99_ms[int(index)]),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=8,
        )
    colorbar = figure.colorbar(points, ax=axis)
    colorbar.set_label("Per-pair sender-airtime overhead (%)")
    axis.set_xlabel("Deadline-miss delta (percentage points)")
    axis.set_ylabel("Completed-frame HF7 P99 delta (ms)")
    axis.set_title(
        f"Matched-seed performance tradeoff: {policy_label} minus STR MLO\n"
        "Lower-left improves both metrics; labels mark five most distant points"
    )
    axis.grid(color="#dddddd", linewidth=0.7)
    figure.tight_layout()
    figure.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(figure)


def _policy_admission_plot(
    diagnostics: dict[str, Any], policy_label: str, output: Path
) -> None:
    funnel = diagnostics["funnel"]
    statuses = diagnostics["statuses"]
    outcomes = diagnostics["action_outcomes"]
    total = funnel[0]["count"]
    action_total = sum(item["count"] for item in outcomes)
    figure, axes = plt.subplots(1, 3, figsize=(16.0, 5.8))

    funnel_labels = [item["label"] for item in funnel][::-1]
    funnel_values = np.asarray([item["count"] for item in funnel][::-1])
    axes[0].barh(
        funnel_labels,
        funnel_values,
        color=plt.cm.Blues(np.linspace(0.42, 0.86, len(funnel_values))),
    )
    for index, value in enumerate(funnel_values):
        axes[0].text(
            value,
            index,
            f" {value:,} ({100.0 * value / total:.1f}%)",
            va="center",
            fontsize=8.5,
        )
    axes[0].set_xlim(0, max(funnel_values) * 1.28)
    axes[0].set_xlabel("Cumulative frame count")
    axes[0].set_title("Admission funnel")
    axes[0].grid(axis="x", color="#dddddd", linewidth=0.7)

    status_labels = [
        f"{item['label']}\n(n={item['count']:,})" for item in statuses
    ][::-1]
    status_rates = 100.0 * np.asarray(
        [item["primary_miss_rate"] for item in statuses][::-1]
    )
    status_colors = [
        POLICY_COLOR if item["status"] == "action" else BASELINE_COLOR
        for item in statuses
    ][::-1]
    axes[1].barh(status_labels, status_rates, color=status_colors, alpha=0.9)
    for index, (rate, item) in enumerate(zip(status_rates, statuses[::-1])):
        axes[1].text(
            rate,
            index,
            f" {rate:.2f}% ({item['primary_miss_count']:,})",
            va="center",
            fontsize=8.5,
        )
    axes[1].set_xlim(0, max(status_rates) * 1.45)
    axes[1].set_xlabel("Primary-copy deadline-miss rate (%)")
    axes[1].set_title("Risk by terminal decision status")
    axes[1].grid(axis="x", color="#dddddd", linewidth=0.7)

    outcome_labels = [item["label"] for item in outcomes][::-1]
    outcome_rates = 100.0 * np.asarray(
        [item["count"] / action_total for item in outcomes][::-1]
    )
    outcome_color_by_label = {
        "Primary on time, accelerated": FAVORABLE_COLOR,
        "Primary on time, no benefit": BASELINE_COLOR,
        "Primary miss rescued": FAVORABLE_COLOR,
        "Primary miss late/incomplete": UNFAVORABLE_COLOR,
    }
    outcome_colors = [outcome_color_by_label[label] for label in outcome_labels]
    axes[2].barh(outcome_labels, outcome_rates, color=outcome_colors, alpha=0.9)
    for index, (rate, item) in enumerate(zip(outcome_rates, outcomes[::-1])):
        axes[2].text(
            rate,
            index,
            f" {rate:.2f}% ({item['count']:,})",
            va="center",
            fontsize=9,
        )
    axes[2].set_xlim(0, max(outcome_rates) * 1.27)
    axes[2].set_xlabel("Share of actions (%)")
    axes[2].set_title(f"Action outcomes (n={action_total:,})")
    axes[2].grid(axis="x", color="#dddddd", linewidth=0.7)

    figure.suptitle(
        f"{policy_label} admission and primary-copy outcomes\n"
        "Diagnostic only: these conditional populations are not qualification gates",
        y=1.04,
    )
    figure.tight_layout()
    figure.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(figure)


def _write_pair_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=PAIR_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as error:
        raise QualificationError(f"cannot write {path}: {error}") from error


def _write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as error:
        raise QualificationError(f"cannot write {path}: {error}") from error


def generate_plots(
    aggregate_input: Path,
    report_path: Path | None = None,
    output_directory: Path | None = None,
    *,
    profile: QualificationProfile = V1_PROFILE,
    _test_contract: _TestOnlyContract | None = None,
) -> list[Path]:
    """Generate raw-backed paired diagnostic plots without recalculating gates."""
    aggregate_path = _resolve_aggregate(aggregate_input)
    campaign_root = aggregate_path.parent.parent
    default_report_name = (
        DEFAULT_REPORT_NAME
        if profile == V1_PROFILE
        else f"{profile.analysis_id}.json"
    )
    default_plot_directory = (
        DEFAULT_PLOT_DIRECTORY
        if profile == V1_PROFILE
        else profile.analysis_id
    )
    report_path = report_path or campaign_root / default_report_name
    output_directory = output_directory or (
        campaign_root / "plots" / default_plot_directory
    )
    aggregate = _read_json(aggregate_path)
    report = _report(report_path, profile)
    pairs = _expected_pairs(report, _test_contract, profile)
    runs = _bind_report_to_aggregate(
        aggregate, aggregate_path, report, _test_contract, profile
    )
    rows = _paired_rows_from_runs(runs, aggregate_path, pairs, report)
    diagnostics = _policy_admission_diagnostics_from_runs(
        aggregate_path, report, runs
    )
    freshly_validated = (
        _test_contract is None or not _test_contract.skip_strict_run_validation
    )
    diagnostic_document = {
        "schema_version": 1,
        "analysis": "paired_value_t2_policy_admission_diagnostics",
        **diagnostics,
        "provenance": {
            "strict_report_path": str(report_path.resolve()),
            "strict_report_sha256": _sha256_file(report_path.resolve()),
            "manifest_sha256": report["campaign_checks"]["manifests"][0]["sha256"],
            "expected_paired_unit_count": len(pairs),
            "validated_run_count": len(runs) if freshly_validated else 0,
            "all_runs_freshly_strict_validated": freshly_validated,
        },
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs = [
        output_directory / "paired_metric_deltas.png",
        output_directory / "resource_gates.png",
        output_directory / "paired_performance_tradeoff.png",
        output_directory / "policy_admission_diagnostics.png",
        output_directory / "policy_admission_diagnostics.json",
        output_directory / "paired_metrics.csv",
    ]
    _paired_delta_plot(rows, report, outputs[0])
    _resource_plot(rows, report, outputs[1])
    policy_label = str(
        report["treatments"]["policy"].get("label", V1_PROFILE.policy_label)
    )
    _tradeoff_plot(rows, policy_label, outputs[2])
    _policy_admission_plot(diagnostics, policy_label, outputs[3])
    _write_json(outputs[4], diagnostic_document)
    _write_pair_csv(outputs[5], rows)
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "aggregate",
        type=Path,
        help="campaign directory, runs directory, or aggregate.json",
    )
    parser.add_argument(
        "--analysis",
        type=Path,
        help="strict analysis JSON (default: campaign/<profile analysis>.json)",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        help=(
            "plot directory (default: campaign/plots/<profile analysis>)"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILES),
        default=V1_PROFILE.key,
        help="frozen runtime and engineering-seed profile to plot",
    )
    arguments = parser.parse_args(argv)
    try:
        outputs = generate_plots(
            arguments.aggregate,
            report_path=arguments.analysis,
            output_directory=arguments.output_directory,
            profile=PROFILES[arguments.profile],
        )
    except QualificationError as error:
        parser.error(str(error))
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
