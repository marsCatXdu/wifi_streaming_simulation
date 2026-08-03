#!/usr/bin/env python3
"""Summarize strictly validated randomized-intervention runs.

This is deliberately a descriptive first-stage audit.  It exposes a reusable
frame-level loader and reports assignment balance, common T2 eligibility,
realized exposure, protocol compliance, outcomes, and settled secondary
airtime.  It does not estimate treatment effects or CATE models.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from validate_outputs import (
    FRAME_COLUMNS,
    RANDOMIZED_ASSIGNMENT_COLUMNS,
    RANDOMIZED_EXECUTION_COLUMNS,
    SECONDARY_AIRTIME_SETTLEMENT_COLUMNS,
    validate_run,
)


ANALYSIS_SCHEMA_VERSION = 1
ARMS = ("CONTROL", "FULL_COPY_T2", "FULL_COPY_T4")
BUILD_IDENTITY_FIELDS = (
    "ns3_version",
    "ns3_upstream_commit",
    "project_git_commit",
    "compiler",
    "build_profile",
)
RUN_IDENTITY_KEYS = {"run_id", "seed", "run"}


class AnalysisError(ValueError):
    """Raised when validated runs cannot be pooled without ambiguity."""


@dataclass(frozen=True)
class FrameObservation:
    """One joined randomized assignment, execution, outcome, and settlement."""

    run_id: str
    seed: int
    run_number: int
    frame_id: int
    frame_type: str
    frame_size_bytes: int
    packet_count: int
    arm: str
    propensity: float
    eligible_t2: bool
    eligibility_reason: str
    execution_stage: str
    attempted: bool
    launched: bool
    noncompliance: bool
    status: str
    incomplete: bool
    deadline_miss: bool
    union_latency_us: float | None
    descriptor_nominal_airtime_us: float
    descriptor_estimated_airtime_us: float
    measured_airtime_us: float | None
    released_airtime_us: float | None
    fallback_settlement: bool | None


@dataclass(frozen=True)
class RandomizedRun:
    """A validated randomized run and its analysis-ready observations."""

    run_dir: Path
    run_id: str
    seed: int
    run_number: int
    design: dict[str, Any]
    environment: dict[str, Any]
    build_identity: dict[str, str]
    meter_summary: dict[str, Any]
    observations: tuple[FrameObservation, ...]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise AnalysisError(f"{path}: expected a JSON object")
    return value


def _read_csv(
    path: Path, columns: set[str], *, exact_schema: bool = True
) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames
            if (
                fieldnames is None
                or len(fieldnames) != len(set(fieldnames))
                or (
                    set(fieldnames) != columns
                    if exact_schema
                    else not columns <= set(fieldnames)
                )
            ):
                raise AnalysisError(f"{path}: CSV schema mismatch")
            rows = list(reader)
    except OSError as error:
        raise AnalysisError(f"cannot read {path}: {error}") from error
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise AnalysisError(f"{path}: malformed CSV row")
    return rows


def _integer(row: dict[str, str], key: str, source: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise AnalysisError(f"{source}: invalid integer {key}") from error
    return value


def _number(row: dict[str, str], key: str, source: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise AnalysisError(f"{source}: invalid number {key}") from error
    if not math.isfinite(value):
        raise AnalysisError(f"{source}: non-finite number {key}")
    return value


def _flag(row: dict[str, str], key: str, source: str) -> bool:
    value = row.get(key)
    if value not in {"0", "1"}:
        raise AnalysisError(f"{source}: invalid flag {key}")
    return value == "1"


def _index_rows(
    rows: Iterable[dict[str, str]], source: str
) -> dict[int, dict[str, str]]:
    indexed: dict[int, dict[str, str]] = {}
    for row in rows:
        frame_id = _integer(row, "frame_id", source)
        if frame_id in indexed:
            raise AnalysisError(f"{source}: duplicate frame_id {frame_id}")
        indexed[frame_id] = row
    return indexed


def _canonical_environment(config: dict[str, Any]) -> dict[str, Any]:
    """Return the complete resolved configuration without run identity."""

    return {
        key: value
        for key, value in config.items()
        if key not in RUN_IDENTITY_KEYS
    }


def _randomized_design(config: dict[str, Any]) -> dict[str, Any]:
    randomized = config.get("randomizedIntervention")
    if not isinstance(randomized, dict):
        raise AnalysisError("resolved_config.json: randomizedIntervention is absent")
    return {
        "policy": config.get("policy"),
        "topology": config.get("topology"),
        "csv_schema_version": randomized.get("csv_schema_version"),
        "assignment_algorithm": randomized.get("assignment_algorithm"),
        "assignment_salt": randomized.get("assignment_salt"),
        "arm_probabilities": randomized.get("arm_probabilities"),
        "stages": randomized.get("stages"),
        "stage_offsets_us": randomized.get("stage_offsets_us"),
        "common_eligibility_rule": randomized.get("common_eligibility_rule"),
        "intervention": randomized.get("intervention"),
        "cost_estimator_id": randomized.get("cost_estimator_id"),
    }


def load_randomized_run(run_dir: Path | str) -> RandomizedRun:
    """Validate and join one randomized run directory.

    The repository's authoritative output validator runs before any values are
    admitted to the analysis.  The joins below are checked again so a report
    cannot silently omit or duplicate a frame.
    """

    run_dir = Path(run_dir).resolve()
    validate_run(run_dir)

    config = _read_json(run_dir / "resolved_config.json")
    if (
        config.get("policy") != "randomized_full_copy_exploration"
        or config.get("topology") != "dual_interface"
    ):
        raise AnalysisError(f"{run_dir}: not a randomized dual-interface run")
    run_id = config.get("run_id")
    seed = config.get("seed")
    run_number = config.get("run")
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(seed, int)
        or isinstance(seed, bool)
        or not isinstance(run_number, int)
        or isinstance(run_number, bool)
    ):
        raise AnalysisError(f"{run_dir}: invalid run identity")

    build = _read_json(run_dir / "build_info.json")
    build_identity: dict[str, str] = {}
    for key in BUILD_IDENTITY_FIELDS:
        value = build.get(key)
        if not isinstance(value, str) or not value:
            raise AnalysisError(f"{run_dir}: invalid build identity field {key}")
        build_identity[key] = value

    # frames.csv retains two legacy prediction-delay columns.  The
    # authoritative schema deliberately treats FRAME_COLUMNS as required
    # columns, while the randomized ledgers and settlements are exact schemas.
    frame_rows = _read_csv(
        run_dir / "frames.csv", FRAME_COLUMNS, exact_schema=False
    )
    assignment_rows = _read_csv(
        run_dir / "randomized_intervention_assignments.csv",
        RANDOMIZED_ASSIGNMENT_COLUMNS,
    )
    execution_rows = _read_csv(
        run_dir / "randomized_intervention_executions.csv",
        RANDOMIZED_EXECUTION_COLUMNS,
    )
    settlement_rows = _read_csv(
        run_dir / "secondary_airtime_settlements.csv",
        SECONDARY_AIRTIME_SETTLEMENT_COLUMNS,
    )
    meter_summary = _read_json(run_dir / "secondary_airtime_summary.json")

    frames = _index_rows(frame_rows, "frames.csv")
    assignments = _index_rows(
        assignment_rows, "randomized_intervention_assignments.csv"
    )
    executions = _index_rows(
        execution_rows, "randomized_intervention_executions.csv"
    )
    settlements = _index_rows(
        settlement_rows, "secondary_airtime_settlements.csv"
    )
    if not frames or set(frames) != set(assignments) or set(frames) != set(executions):
        raise AnalysisError(f"{run_dir}: randomized frame ledgers do not join exactly")

    observations: list[FrameObservation] = []
    launched_ids: set[int] = set()
    for frame_id in sorted(frames):
        frame = frames[frame_id]
        assignment = assignments[frame_id]
        execution = executions[frame_id]
        source = f"{run_dir}: frame {frame_id}"
        arm = assignment.get("assigned_arm", "")
        if arm not in ARMS or execution.get("assigned_arm") != arm:
            raise AnalysisError(f"{source}: assignment arm mismatch")
        eligible = _flag(assignment, "eligible_t2", source)
        if (
            execution.get("eligible_t2") != assignment.get("eligible_t2")
            or execution.get("eligibility_reason")
            != assignment.get("eligibility_reason")
        ):
            raise AnalysisError(f"{source}: assignment outcome changed at execution")
        propensity = _number(assignment, "propensity", source)
        if propensity <= 0 or propensity > 1:
            raise AnalysisError(f"{source}: propensity is outside (0, 1]")

        incomplete = _flag(frame, "incomplete", source)
        deadline_miss = _flag(frame, "deadline_miss", source)
        latency_text = frame.get("union_latency_us", "")
        if incomplete:
            if latency_text:
                raise AnalysisError(f"{source}: incomplete frame has latency")
            latency: float | None = None
        else:
            latency = _number(frame, "union_latency_us", source)
            if latency < 0:
                raise AnalysisError(f"{source}: negative union latency")

        launched = _flag(execution, "launched", source)
        if launched:
            launched_ids.add(frame_id)
        settlement = settlements.get(frame_id)
        if launched != (settlement is not None):
            raise AnalysisError(f"{source}: exposure/settlement mismatch")
        measured: float | None = None
        released: float | None = None
        fallback: bool | None = None
        if settlement is not None:
            measured = _number(settlement, "measured_airtime_us", source)
            released = _number(settlement, "released_airtime_us", source)
            fallback = _flag(settlement, "fallback", source)
            if measured < 0 or released < 0:
                raise AnalysisError(f"{source}: negative settlement airtime")

        nominal = _number(execution, "nominal_airtime_us", source)
        estimated = _number(execution, "estimated_airtime_us", source)
        if nominal < 0 or estimated < 0:
            raise AnalysisError(f"{source}: negative descriptor airtime")
        duplicated = _flag(frame, "duplicated", source)
        if duplicated != launched:
            raise AnalysisError(f"{source}: exposure/duplicated outcome mismatch")

        observations.append(
            FrameObservation(
                run_id=run_id,
                seed=seed,
                run_number=run_number,
                frame_id=frame_id,
                frame_type=frame.get("frame_type", ""),
                frame_size_bytes=_integer(frame, "frame_size_bytes", source),
                packet_count=_integer(frame, "packet_count", source),
                arm=arm,
                propensity=propensity,
                eligible_t2=eligible,
                eligibility_reason=assignment.get("eligibility_reason", ""),
                execution_stage=execution.get("execution_stage", ""),
                attempted=_flag(execution, "attempted", source),
                launched=launched,
                noncompliance=_flag(execution, "noncompliance", source),
                status=execution.get("status", ""),
                incomplete=incomplete,
                deadline_miss=deadline_miss,
                union_latency_us=latency,
                descriptor_nominal_airtime_us=nominal,
                descriptor_estimated_airtime_us=estimated,
                measured_airtime_us=measured,
                released_airtime_us=released,
                fallback_settlement=fallback,
            )
        )

    if set(settlements) != launched_ids:
        raise AnalysisError(f"{run_dir}: settlement contains an unexposed frame")
    measured_total = sum(
        observation.measured_airtime_us or 0.0 for observation in observations
    )
    try:
        tagged_total = float(meter_summary["tagged_secondary_tx_airtime_us"])
    except (KeyError, TypeError, ValueError) as error:
        raise AnalysisError(f"{run_dir}: invalid secondary airtime summary") from error
    if not math.isfinite(tagged_total) or not math.isclose(
        measured_total, tagged_total, rel_tol=1e-9, abs_tol=1e-6
    ):
        raise AnalysisError(f"{run_dir}: settled airtime does not match meter summary")

    return RandomizedRun(
        run_dir=run_dir,
        run_id=run_id,
        seed=seed,
        run_number=run_number,
        design=_randomized_design(config),
        environment=_canonical_environment(config),
        build_identity=build_identity,
        meter_summary=meter_summary,
        observations=tuple(observations),
    )


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _outcomes(observations: Sequence[FrameObservation]) -> dict[str, Any]:
    latencies = [
        observation.union_latency_us
        for observation in observations
        if observation.union_latency_us is not None
    ]
    frame_count = len(observations)
    incomplete_count = sum(observation.incomplete for observation in observations)
    miss_count = sum(observation.deadline_miss for observation in observations)
    complete_count = frame_count - incomplete_count
    return {
        "frame_count": frame_count,
        "complete_count": complete_count,
        "incomplete_count": incomplete_count,
        "incomplete_rate": _rate(incomplete_count, frame_count),
        "deadline_miss_count": miss_count,
        "deadline_miss_rate": _rate(miss_count, frame_count),
        "on_time_complete_count": frame_count - miss_count,
        "on_time_complete_rate": _rate(frame_count - miss_count, frame_count),
        "complete_latency_mean_us": statistics.fmean(latencies) if latencies else None,
        "complete_latency_p50_us": _percentile(latencies, 0.50),
        "complete_latency_p90_us": _percentile(latencies, 0.90),
        "complete_latency_p95_us": _percentile(latencies, 0.95),
        "complete_latency_p99_us": _percentile(latencies, 0.99),
    }


def _arm_summary(
    observations: Sequence[FrameObservation],
    probability: float,
    measurement_duration_us: float,
) -> dict[str, Any]:
    eligible = [observation for observation in observations if observation.eligible_t2]
    exposed = [observation for observation in observations if observation.launched]
    attempted_count = sum(observation.attempted for observation in observations)
    noncompliance_count = sum(
        observation.noncompliance for observation in observations
    )
    status_counts: dict[str, int] = {}
    eligibility_counts: dict[str, int] = {}
    for observation in observations:
        status_counts[observation.status] = status_counts.get(observation.status, 0) + 1
        eligibility_counts[observation.eligibility_reason] = (
            eligibility_counts.get(observation.eligibility_reason, 0) + 1
        )

    estimated_total = sum(
        observation.descriptor_estimated_airtime_us for observation in exposed
    )
    nominal_total = sum(
        observation.descriptor_nominal_airtime_us for observation in exposed
    )
    measured_total = sum(
        observation.measured_airtime_us or 0.0 for observation in exposed
    )
    released_total = sum(
        observation.released_airtime_us or 0.0 for observation in exposed
    )
    fallback_count = sum(observation.fallback_settlement is True for observation in exposed)

    return {
        "configured_probability": probability,
        "assigned_count": len(observations),
        "eligible_t2_count": len(eligible),
        "eligible_t2_rate": _rate(len(eligible), len(observations)),
        "eligibility_reason_counts": dict(sorted(eligibility_counts.items())),
        "attempted_count": attempted_count,
        "attempt_rate_among_eligible": _rate(attempted_count, len(eligible)),
        "exposed_count": len(exposed),
        "exposure_rate_among_eligible": _rate(len(exposed), len(eligible)),
        "protocol_compliant_eligible_count": len(eligible) - noncompliance_count,
        "protocol_compliance_rate_among_eligible": _rate(
            len(eligible) - noncompliance_count, len(eligible)
        ),
        "noncompliance_count": noncompliance_count,
        "noncompliance_rate_among_eligible": _rate(
            noncompliance_count, len(eligible)
        ),
        "execution_status_counts": dict(sorted(status_counts.items())),
        "outcomes_all_assigned": _outcomes(observations),
        "outcomes_common_eligible_t2": _outcomes(eligible),
        "measured_airtime": {
            "settled_exposure_count": len(exposed),
            "fallback_settlement_count": fallback_count,
            "nominal_exposure_airtime_us": nominal_total,
            "estimated_exposure_airtime_us": estimated_total,
            "measured_exposure_airtime_us": measured_total,
            "released_reservation_airtime_us": released_total,
            "actual_to_estimated_airtime_ratio": (
                measured_total / estimated_total if estimated_total else None
            ),
            "mean_measured_airtime_us_per_exposure": _rate(
                measured_total, len(exposed)
            ),
            "mean_measured_airtime_us_per_eligible_assignment": _rate(
                measured_total, len(eligible)
            ),
            "fraction_of_pooled_measurement_time": _rate(
                measured_total, measurement_duration_us
            ),
        },
    }


def _summary_number(summary: dict[str, Any], key: str, run_id: str) -> float:
    value = summary.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisError(f"{run_id}: invalid meter summary field {key}")
    result = float(value)
    if not math.isfinite(result):
        raise AnalysisError(f"{run_id}: non-finite meter summary field {key}")
    return result


def analyze_randomized_runs(run_dirs: Sequence[Path | str]) -> dict[str, Any]:
    """Load compatible randomized runs and return a deterministic report."""

    if not run_dirs:
        raise AnalysisError("at least one run directory is required")
    resolved = [Path(path).resolve() for path in run_dirs]
    if len(resolved) != len(set(resolved)):
        raise AnalysisError("a run directory was supplied more than once")
    runs = [load_randomized_run(path) for path in sorted(resolved)]
    runs.sort(key=lambda item: (item.seed, item.run_number, item.run_id))

    run_ids = [run.run_id for run in runs]
    seed_runs = [(run.seed, run.run_number) for run in runs]
    if len(run_ids) != len(set(run_ids)):
        raise AnalysisError("pooled runs contain duplicate run_id values")
    if len(seed_runs) != len(set(seed_runs)):
        raise AnalysisError("pooled runs contain duplicate (seed, run) identities")

    reference = runs[0]
    for run in runs[1:]:
        if run.design != reference.design:
            raise AnalysisError(
                f"{run.run_id}: randomized design differs from {reference.run_id}"
            )
        if run.environment != reference.environment:
            raise AnalysisError(
                f"{run.run_id}: resolved environment differs from {reference.run_id}"
            )
        if run.build_identity != reference.build_identity:
            raise AnalysisError(
                f"{run.run_id}: build identity differs from {reference.run_id}"
            )

    observations = [
        observation for run in runs for observation in run.observations
    ]
    durations = [
        _summary_number(run.meter_summary, "measurement_duration_us", run.run_id)
        for run in runs
    ]
    measurement_duration_us = sum(durations)
    tagged_airtime_us = sum(
        _summary_number(
            run.meter_summary, "tagged_secondary_tx_airtime_us", run.run_id
        )
        for run in runs
    )
    estimated_airtime_us = sum(
        _summary_number(
            run.meter_summary, "estimated_action_airtime_us", run.run_id
        )
        for run in runs
    )
    fallback_count = sum(
        int(_summary_number(
            run.meter_summary, "forced_reservation_settlements", run.run_id
        ))
        for run in runs
    )
    max_budget_debt_us = max(
        _summary_number(run.meter_summary, "maximum_budget_debt_us", run.run_id)
        for run in runs
    )

    probabilities = reference.design.get("arm_probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(ARMS):
        raise AnalysisError("randomized design has invalid arm probabilities")
    arms: dict[str, Any] = {}
    for arm in ARMS:
        probability = probabilities[arm]
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(float(probability))
        ):
            raise AnalysisError(f"randomized design has invalid probability for {arm}")
        arm_observations = [
            observation for observation in observations if observation.arm == arm
        ]
        arms[arm] = _arm_summary(
            arm_observations, float(probability), measurement_duration_us
        )
        arms[arm]["observed_assignment_fraction"] = _rate(
            len(arm_observations), len(observations)
        )

    settled_total = sum(
        arm["measured_airtime"]["measured_exposure_airtime_us"]
        for arm in arms.values()
    )
    if not math.isclose(
        settled_total, tagged_airtime_us, rel_tol=1e-9, abs_tol=1e-6
    ):
        raise AnalysisError("pooled arm airtime does not reconcile with meter summaries")

    all_eligible = [
        observation for observation in observations if observation.eligible_t2
    ]
    return {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_kind": "randomized_intervention_descriptive_audit",
        "causal_model_fitted": False,
        "run_count": len(runs),
        "run_ids": run_ids,
        "seed_run_pairs": [
            {"seed": seed, "run": run_number}
            for seed, run_number in seed_runs
        ],
        "design": reference.design,
        "build_identity": reference.build_identity,
        "overall": {
            "assigned_frame_count": len(observations),
            "eligible_t2_count": len(all_eligible),
            "eligible_t2_rate": _rate(len(all_eligible), len(observations)),
            "outcomes_all_assigned": _outcomes(observations),
            "outcomes_common_eligible_t2": _outcomes(all_eligible),
            "meter": {
                "measurement_duration_us": measurement_duration_us,
                "tagged_secondary_tx_airtime_us": tagged_airtime_us,
                "tagged_secondary_tx_airtime_fraction": _rate(
                    tagged_airtime_us, measurement_duration_us
                ),
                "estimated_action_airtime_us": estimated_airtime_us,
                "actual_to_estimated_airtime_ratio": (
                    tagged_airtime_us / estimated_airtime_us
                    if estimated_airtime_us
                    else None
                ),
                "forced_reservation_settlements": fallback_count,
                "maximum_budget_debt_us_across_runs": max_budget_debt_us,
            },
        },
        "arms": arms,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dirs",
        nargs="+",
        type=Path,
        help="strictly validated randomized run directories",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit one-line JSON instead of indented JSON",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = analyze_randomized_runs(args.run_dirs)
    print(
        json.dumps(
            report,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
