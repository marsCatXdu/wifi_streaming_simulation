#!/usr/bin/env python3
"""Summarize strictly validated randomized-intervention pilot runs.

This is deliberately a point-estimate and engineering audit.  It exposes a
reusable frame-level loader and reports randomization balance, common-risk-set
arm contrasts, placebo diagnostics, realized diversity, protocol compliance,
and settled secondary airtime.  It reports deterministic run-cluster
percentile intervals, but does not fit a causal/CATE model or adjust the many
pilot endpoints for multiplicity.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from validate_outputs import (
    FRAME_COLUMNS,
    RANDOMIZED_ASSIGNMENT_COLUMNS,
    RANDOMIZED_EXECUTION_COLUMNS,
    RANDOMIZED_PREDICTION_COLUMNS,
    SECONDARY_AIRTIME_SETTLEMENT_COLUMNS,
    validate_run,
)


ANALYSIS_SCHEMA_VERSION = 2
ARMS = ("CONTROL", "FULL_COPY_T2", "FULL_COPY_T4")
PAIRWISE_ARMS = (
    ("t2_vs_control", "FULL_COPY_T2", "CONTROL"),
    ("t4_regime_vs_control", "FULL_COPY_T4", "CONTROL"),
    ("t2_vs_t4_regime", "FULL_COPY_T2", "FULL_COPY_T4"),
)
TAIL_THRESHOLDS_MS = (10, 11, 12, 12.5, 13, 14, 15, 16, 20)
RANDOMIZED_SAMPLE_OFFSETS_US = (2000, 4000)
RANDOMIZED_PATH_COPIES = ((1, 0), (0, 1))
DEFAULT_BOOTSTRAP_REPLICATES = 20_000
BOOTSTRAP_SEED = 0x52414E444F4D495A
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
    generation_time_us: int
    deadline_us: int
    incomplete: bool
    deadline_miss: bool
    union_latency_us: float | None
    primary_completion_us: int | None
    primary_latency_us: float | None
    primary_deadline_miss: bool
    secondary_completion_us: int | None
    t4_primary_actionable: bool
    t4_secondary_actionable: bool
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


@dataclass(frozen=True)
class _ClusterBootstrapPlan:
    """Shared deterministic run-cluster resamples for all pilot endpoints."""

    cluster_ids: tuple[str, ...]
    replicates: int
    seed: int
    multiplicities: tuple[tuple[int, ...], ...]


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


def _optional_integer(
    row: dict[str, str], key: str, source: str
) -> int | None:
    value = row.get(key)
    if value == "":
        return None
    return _integer(row, key, source)


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


def _load_randomized_prediction_pairs(
    run_dir: Path,
    run_id: str,
    frames: dict[int, dict[str, str]],
) -> dict[int, tuple[bool, bool]]:
    """Strictly join the paired T2/T4 telemetry and return T4 actionability."""

    path = run_dir / "prediction_samples.csv"
    rows = _read_csv(path, RANDOMIZED_PREDICTION_COLUMNS, exact_schema=False)
    indexed: dict[tuple[int, int, int, int], dict[str, str]] = {}
    for row in rows:
        offset = _integer(row, "sample_offset_us", "prediction_samples.csv")
        if offset not in RANDOMIZED_SAMPLE_OFFSETS_US:
            continue
        key = (
            _integer(row, "frame_id", "prediction_samples.csv"),
            offset,
            _integer(row, "path_id", "prediction_samples.csv"),
            _integer(row, "copy_id", "prediction_samples.csv"),
        )
        if key in indexed:
            raise AnalysisError(
                "prediction_samples.csv: duplicate randomized paired sample"
            )
        indexed[key] = row

    expected = {
        (frame_id, offset, path_id, copy_id)
        for frame_id in frames
        for offset in RANDOMIZED_SAMPLE_OFFSETS_US
        for path_id, copy_id in RANDOMIZED_PATH_COPIES
    }
    if set(indexed) != expected:
        raise AnalysisError(
            "prediction_samples.csv: randomized paired sample coverage mismatch"
        )

    t4_actionability: dict[int, tuple[bool, bool]] = {}
    for frame_id, frame in frames.items():
        for offset, stage in ((2000, "T2"), (4000, "T4")):
            primary = indexed[(frame_id, offset, 1, 0)]
            secondary = indexed[(frame_id, offset, 0, 1)]
            source = f"prediction_samples.csv: frame {frame_id} {stage}"
            if primary.get("run_id") != run_id or secondary.get("run_id") != run_id:
                raise AnalysisError(f"{source}: run_id mismatch")
            for field in (
                "frame_id",
                "sample_stage",
                "sample_offset_us",
                "sample_time_ns",
                "generation_time_ns",
                "deadline_time_ns",
                "frame_size_bytes",
                "frame_packet_count",
                "frame_type",
            ):
                if primary.get(field) != secondary.get(field):
                    raise AnalysisError(f"{source}: paired {field} mismatch")
            if primary.get("sample_stage") != stage:
                raise AnalysisError(f"{source}: stage mismatch")
            for prediction_field, frame_field in (
                ("frame_size_bytes", "frame_size_bytes"),
                ("frame_packet_count", "packet_count"),
                ("frame_type", "frame_type"),
            ):
                if primary.get(prediction_field) != frame.get(frame_field):
                    raise AnalysisError(
                        f"{source}: immutable {prediction_field} mismatch"
                    )
        t4_primary = indexed[(frame_id, 4000, 1, 0)]
        t4_secondary = indexed[(frame_id, 4000, 0, 1)]
        t4_actionability[frame_id] = (
            _flag(t4_primary, "actionable", "prediction_samples.csv"),
            _flag(t4_secondary, "actionable", "prediction_samples.csv"),
        )
    return t4_actionability


def _canonical_environment(config: dict[str, Any]) -> dict[str, Any]:
    """Return invariant environment-generating inputs without run identity.

    The resolved OBSS BSS list contains seed-realized AP/STA coordinates.  The
    placement bounds, stream bases, BSS identities, standards, and station
    cardinalities are invariant design inputs and remain in this identity;
    only the realized coordinates are normalized away.
    """

    environment = {
        key: value
        for key, value in config.items()
        if key not in RUN_IDENTITY_KEYS
    }
    # A JSON round trip gives us a deterministic deep copy without adding a
    # dependency.  Resolved configuration values are already JSON-native.
    environment = json.loads(json.dumps(environment, sort_keys=True))
    background = environment.get("background")
    if not isinstance(background, dict):
        return environment
    obss = background.get("obss")
    if not isinstance(obss, dict):
        return environment
    bsses = obss.get("bsses")
    if not isinstance(bsses, list):
        return environment
    normalized_bsses: list[Any] = []
    for bss in bsses:
        if not isinstance(bss, dict):
            normalized_bsses.append(bss)
            continue
        normalized = {
            key: value for key, value in bss.items() if key not in {"ap", "stas"}
        }
        stations = bss.get("stas")
        normalized["seed_realized_ap_coordinates_omitted"] = "ap" in bss
        normalized["seed_realized_sta_coordinates_omitted"] = (
            isinstance(stations, list)
        )
        normalized["station_count"] = (
            len(stations) if isinstance(stations, list) else None
        )
        normalized_bsses.append(normalized)
    obss["bsses"] = normalized_bsses
    return environment


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
    t4_actionability = _load_randomized_prediction_pairs(run_dir, run_id, frames)

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

        generation_time_us = _integer(frame, "generation_time_us", source)
        deadline_us = _integer(frame, "deadline_us", source)
        if generation_time_us < 0 or deadline_us <= 0:
            raise AnalysisError(f"{source}: invalid frame timing")
        incomplete = _flag(frame, "incomplete", source)
        deadline_miss = _flag(frame, "deadline_miss", source)
        latency_text = frame.get("union_latency_us", "")
        union_completion_us = _optional_integer(
            frame, "union_completion_us", source
        )
        if incomplete:
            if latency_text or union_completion_us is not None:
                raise AnalysisError(
                    f"{source}: incomplete frame has a union completion"
                )
            latency: float | None = None
        else:
            latency = _number(frame, "union_latency_us", source)
            if (
                latency < 0
                or union_completion_us is None
                or not math.isclose(
                    latency,
                    union_completion_us - generation_time_us,
                    rel_tol=0,
                    abs_tol=1e-9,
                )
            ):
                raise AnalysisError(f"{source}: invalid union completion")
        primary_completion_us = _optional_integer(
            frame, "copy_0_completion_us", source
        )
        secondary_completion_us = _optional_integer(
            frame, "copy_1_completion_us", source
        )
        for name, completion in (
            ("primary", primary_completion_us),
            ("secondary", secondary_completion_us),
        ):
            if completion is not None and completion < generation_time_us:
                raise AnalysisError(f"{source}: {name} completion precedes generation")
        copy_completions = [
            completion
            for completion in (primary_completion_us, secondary_completion_us)
            if completion is not None
        ]
        if union_completion_us is None and copy_completions:
            raise AnalysisError(f"{source}: completed copy is absent from union")
        if (
            union_completion_us is not None
            and copy_completions
            and union_completion_us > min(copy_completions)
        ):
            raise AnalysisError(f"{source}: union completion exceeds copy completion")
        primary_latency_us = (
            None
            if primary_completion_us is None
            else float(primary_completion_us - generation_time_us)
        )
        primary_deadline_miss = (
            primary_completion_us is None
            or primary_completion_us > generation_time_us + deadline_us
        )
        expected_deadline_miss = (
            union_completion_us is None
            or union_completion_us > generation_time_us + deadline_us
        )
        if deadline_miss != expected_deadline_miss:
            raise AnalysisError(f"{source}: deadline-miss label mismatch")

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

        t4_primary_actionable, t4_secondary_actionable = t4_actionability[frame_id]
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
                generation_time_us=generation_time_us,
                deadline_us=deadline_us,
                incomplete=incomplete,
                deadline_miss=deadline_miss,
                union_latency_us=latency,
                primary_completion_us=primary_completion_us,
                primary_latency_us=primary_latency_us,
                primary_deadline_miss=primary_deadline_miss,
                secondary_completion_us=secondary_completion_us,
                t4_primary_actionable=t4_primary_actionable,
                t4_secondary_actionable=t4_secondary_actionable,
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


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    """Return deterministic population dispersion and interpolated quantiles."""

    numeric = [float(value) for value in values]
    if any(not math.isfinite(value) for value in numeric):
        raise AnalysisError("cannot summarize a non-finite distribution")
    if not numeric:
        return {
            "count": 0,
            "sum": 0.0,
            "quantile_method": "linear interpolation at rank (n - 1) * p",
            "min": None,
            "mean": None,
            "population_stddev": None,
            "coefficient_of_variation": None,
            "p10": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    mean = statistics.fmean(numeric)
    stddev = statistics.pstdev(numeric)
    return {
        "count": len(numeric),
        "sum": sum(numeric),
        "quantile_method": "linear interpolation at rank (n - 1) * p",
        "min": min(numeric),
        "mean": mean,
        "population_stddev": stddev,
        "coefficient_of_variation": stddev / mean if mean else None,
        "p10": _percentile(numeric, 0.10),
        "p25": _percentile(numeric, 0.25),
        "p50": _percentile(numeric, 0.50),
        "p75": _percentile(numeric, 0.75),
        "p90": _percentile(numeric, 0.90),
        "p95": _percentile(numeric, 0.95),
        "p99": _percentile(numeric, 0.99),
        "max": max(numeric),
    }


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _cluster_bootstrap_plan(
    cluster_ids: Sequence[str], replicates: int
) -> _ClusterBootstrapPlan:
    if (
        isinstance(replicates, bool)
        or not isinstance(replicates, int)
        or replicates < 1
    ):
        raise AnalysisError("bootstrap replicate count must be a positive integer")
    ids = tuple(cluster_ids)
    if not ids or len(ids) != len(set(ids)):
        raise AnalysisError("cluster bootstrap requires unique run IDs")
    if len(ids) == 1:
        multiplicities: tuple[tuple[int, ...], ...] = ()
    else:
        generator = random.Random(BOOTSTRAP_SEED)
        generated: list[tuple[int, ...]] = []
        for _ in range(replicates):
            counts = [0] * len(ids)
            for _ in ids:
                counts[generator.randrange(len(ids))] += 1
            generated.append(tuple(counts))
        multiplicities = tuple(generated)
    return _ClusterBootstrapPlan(
        cluster_ids=ids,
        replicates=replicates,
        seed=BOOTSTRAP_SEED,
        multiplicities=multiplicities,
    )


def _bootstrap_interval(
    values: Sequence[float],
    estimate: float | None,
    plan: _ClusterBootstrapPlan,
) -> dict[str, Any]:
    available = len(plan.cluster_ids) >= 2 and bool(values)
    return {
        "method": "run-cluster percentile bootstrap",
        "independent_unit": ["seed", "run"],
        "confidence_level": 0.95,
        "cluster_count": len(plan.cluster_ids),
        "requested_replicates": plan.replicates,
        "valid_replicates": len(values),
        "invalid_replicates": (
            plan.replicates - len(values) if len(plan.cluster_ids) >= 2 else 0
        ),
        "seed": plan.seed,
        "available": available,
        "estimate": estimate,
        "ci95_low": _percentile(values, 0.025) if available else None,
        "ci95_high": _percentile(values, 0.975) if available else None,
    }


def _cluster_bootstrap_event_contrast(
    population: Sequence[FrameObservation],
    left_arm: str,
    right_arm: str,
    event: Callable[[FrameObservation], bool],
    propensity_normalizer: float,
    plan: _ClusterBootstrapPlan,
    point: dict[str, Any],
) -> dict[str, Any]:
    """Bootstrap pooled arm-rate differences by resampling whole runs."""

    # Per-run sufficient statistics avoid rescanning all frames for every
    # replicate.  Tuple order is target count, then count/event/weighted
    # count/weighted event for the left and right arm.
    statistics_by_cluster: list[tuple[float, ...]] = []
    for cluster_id in plan.cluster_ids:
        rows = [row for row in population if row.run_id == cluster_id]
        values: list[float] = [float(len(rows))]
        for arm in (left_arm, right_arm):
            assigned = [row for row in rows if row.arm == arm]
            outcomes = [bool(event(row)) for row in assigned]
            weights = [
                propensity_normalizer / row.propensity for row in assigned
            ]
            values.extend((
                float(len(assigned)),
                float(sum(outcomes)),
                sum(weights),
                sum(
                    weight * outcome
                    for weight, outcome in zip(weights, outcomes)
                ),
            ))
        statistics_by_cluster.append(tuple(values))

    replicate_values: dict[str, list[float]] = {
        "observed_rate_difference": [],
        "hajek_rate_difference": [],
        "horvitz_thompson_rate_difference": [],
    }
    for multiplicities in plan.multiplicities:
        totals = [0.0] * 9
        for multiplier, cluster_statistics in zip(
            multiplicities, statistics_by_cluster
        ):
            if not multiplier:
                continue
            for index, value in enumerate(cluster_statistics):
                totals[index] += multiplier * value
        target_count = totals[0]
        left_count, left_events, left_weight, left_weighted_events = totals[1:5]
        right_count, right_events, right_weight, right_weighted_events = totals[5:9]
        if left_count and right_count:
            replicate_values["observed_rate_difference"].append(
                left_events / left_count - right_events / right_count
            )
        if left_weight and right_weight:
            replicate_values["hajek_rate_difference"].append(
                left_weighted_events / left_weight
                - right_weighted_events / right_weight
            )
        if target_count:
            replicate_values["horvitz_thompson_rate_difference"].append(
                (left_weighted_events - right_weighted_events) / target_count
            )
    return {
        key: _bootstrap_interval(values, point[key], plan)
        for key, values in replicate_values.items()
    }


def _arm_event_estimate(
    population: Sequence[FrameObservation],
    arm: str,
    event: Callable[[FrameObservation], bool],
    propensity_normalizer: float = 1.0,
) -> dict[str, Any]:
    """Estimate an adverse-event rate using the logged known propensities."""

    assigned = [observation for observation in population if observation.arm == arm]
    outcomes = [bool(event(observation)) for observation in assigned]
    if propensity_normalizer <= 0 or propensity_normalizer > 1:
        raise AnalysisError("invalid conditional propensity normalizer")
    conditional_propensities = [
        observation.propensity / propensity_normalizer
        for observation in assigned
    ]
    if any(
        probability <= 0 or probability > 1
        for probability in conditional_propensities
    ):
        raise AnalysisError("invalid known conditional propensity")
    weights = [1.0 / probability for probability in conditional_propensities]
    weighted_event_total = sum(
        weight * outcome for weight, outcome in zip(weights, outcomes)
    )
    weight_total = sum(weights)
    observed_rate = _rate(sum(outcomes), len(outcomes))
    hajek_rate = _rate(weighted_event_total, weight_total)
    target_count = len(population)
    return {
        "assigned_count": len(assigned),
        "event_count": sum(outcomes),
        "observed_event_rate": observed_rate,
        "known_propensity_normalizer": propensity_normalizer,
        "known_propensity_weight_sum": weight_total,
        "known_propensity_weighted_event_sum": weighted_event_total,
        "hajek_event_rate": hajek_rate,
        "horvitz_thompson_event_rate": _rate(
            weighted_event_total, target_count
        ),
        "effective_sample_size": (
            weight_total * weight_total / sum(weight * weight for weight in weights)
            if weights
            else None
        ),
    }


def _event_contrast(
    population: Sequence[FrameObservation],
    left_arm: str,
    right_arm: str,
    event: Callable[[FrameObservation], bool],
    propensity_normalizer: float = 1.0,
    bootstrap_plan: _ClusterBootstrapPlan | None = None,
) -> dict[str, Any]:
    """Return point contrasts; positive differences mean the left arm is worse."""

    left = _arm_event_estimate(
        population, left_arm, event, propensity_normalizer
    )
    right = _arm_event_estimate(
        population, right_arm, event, propensity_normalizer
    )
    result = {
        "left_arm": left_arm,
        "right_arm": right_arm,
        "direction": "left_minus_right; positive means more adverse events in left",
        "left": left,
        "right": right,
        "observed_rate_difference": _difference(
            left["observed_event_rate"], right["observed_event_rate"]
        ),
        "hajek_rate_difference": _difference(
            left["hajek_event_rate"], right["hajek_event_rate"]
        ),
        "horvitz_thompson_rate_difference": _difference(
            left["horvitz_thompson_event_rate"],
            right["horvitz_thompson_event_rate"],
        ),
    }
    result["run_cluster_bootstrap"] = (
        None
        if bootstrap_plan is None
        else _cluster_bootstrap_event_contrast(
            population,
            left_arm,
            right_arm,
            event,
            propensity_normalizer,
            bootstrap_plan,
            result,
        )
    )
    return result


def _arm_contrasts(
    population: Sequence[FrameObservation],
    deadline_event: Callable[[FrameObservation], bool],
    latency: Callable[[FrameObservation], float | None],
    pairs: Sequence[tuple[str, str, str]] = PAIRWISE_ARMS,
    propensity_normalizers: dict[str, float] | None = None,
    bootstrap_plan: _ClusterBootstrapPlan | None = None,
) -> dict[str, Any]:
    """Return deadline and unconditional latency-tail arm contrasts."""

    contrasts: dict[str, Any] = {}
    for name, left_arm, right_arm in pairs:
        propensity_normalizer = (
            1.0
            if propensity_normalizers is None
            else propensity_normalizers[name]
        )
        tails = []
        for threshold_ms in TAIL_THRESHOLDS_MS:
            threshold_us = float(threshold_ms) * 1000.0
            tails.append({
                "threshold_ms": threshold_ms,
                "threshold_us": threshold_us,
                "contrast": _event_contrast(
                    population,
                    left_arm,
                    right_arm,
                    lambda observation, threshold=threshold_us: (
                        latency(observation) is None
                        or latency(observation) > threshold
                    ),
                    propensity_normalizer,
                    bootstrap_plan,
                ),
            })
        contrasts[name] = {
            "left_arm": left_arm,
            "right_arm": right_arm,
            "deadline_miss": _event_contrast(
                population,
                left_arm,
                right_arm,
                deadline_event,
                propensity_normalizer,
                bootstrap_plan,
            ),
            "unconditional_latency_tail": {
                "event_definition": (
                    "copy incomplete OR latency strictly greater than threshold"
                ),
                "denominator_definition": "all frames in the stated risk set",
                "thresholds": tails,
            },
        }
    return contrasts


def _assignment_balance(
    observations: Sequence[FrameObservation],
    probabilities: dict[str, float],
) -> dict[str, Any]:
    """Compare configured multinomial probabilities with realized assignment."""

    count = len(observations)
    arms: dict[str, Any] = {}
    chi_square = 0.0
    standardized: list[float] = []
    for arm in ARMS:
        probability = probabilities[arm]
        observed = sum(observation.arm == arm for observation in observations)
        expected = count * probability
        fraction = _rate(observed, count)
        variance = count * probability * (1.0 - probability)
        z_score = (observed - expected) / math.sqrt(variance) if variance else None
        if expected:
            chi_square += (observed - expected) ** 2 / expected
        if z_score is not None:
            standardized.append(abs(z_score))
        arms[arm] = {
            "configured_probability": probability,
            "expected_count": expected,
            "observed_count": observed,
            "observed_fraction": fraction,
            "observed_minus_configured_fraction": (
                None if fraction is None else fraction - probability
            ),
            "observed_to_expected_count_ratio": _rate(observed, expected),
            "binomial_standardized_count_deviation": z_score,
        }
    return {
        "frame_count": count,
        "arms": arms,
        "multinomial_pearson_chi_square": chi_square if count else None,
        "multinomial_pearson_degrees_of_freedom": len(ARMS) - 1,
        "max_absolute_binomial_standardized_count_deviation": (
            max(standardized) if standardized else None
        ),
        "p_value_computed": False,
    }


def _nominal_cluster_interval_flags(
    contrasts: dict[str, Any],
) -> list[dict[str, Any]]:
    """List unadjusted 95% cluster intervals that exclude zero."""

    flagged: list[dict[str, Any]] = []
    for contrast_name in sorted(contrasts):
        contrast = contrasts[contrast_name]
        endpoints = [("deadline_miss", None, contrast["deadline_miss"])]
        endpoints.extend(
            (
                "unconditional_primary_latency_tail",
                item["threshold_ms"],
                item["contrast"],
            )
            for item in contrast["unconditional_latency_tail"]["thresholds"]
        )
        for endpoint, threshold_ms, result in endpoints:
            interval = result["run_cluster_bootstrap"][
                "observed_rate_difference"
            ]
            low = interval["ci95_low"]
            high = interval["ci95_high"]
            if (
                interval["available"]
                and low is not None
                and high is not None
                and (high < 0 or low > 0)
            ):
                flagged.append({
                    "contrast": contrast_name,
                    "endpoint": endpoint,
                    "threshold_ms": threshold_ms,
                    "estimate": interval["estimate"],
                    "ci95_low": low,
                    "ci95_high": high,
                })
    return flagged


def _exposure_cost_distribution(
    observations: Sequence[FrameObservation],
) -> dict[str, Any]:
    exposed = [observation for observation in observations if observation.launched]
    ratios: list[float] = []
    residuals: list[float] = []
    for observation in exposed:
        measured = observation.measured_airtime_us
        estimated = observation.descriptor_estimated_airtime_us
        if measured is None or estimated <= 0:
            raise AnalysisError("launched frame has invalid settled cost")
        ratios.append(measured / estimated)
        residuals.append(measured - estimated)
    return {
        "nominal_airtime_us": _distribution(
            [observation.descriptor_nominal_airtime_us for observation in exposed]
        ),
        "estimated_airtime_us": _distribution(
            [observation.descriptor_estimated_airtime_us for observation in exposed]
        ),
        "measured_airtime_us": _distribution(
            [float(observation.measured_airtime_us) for observation in exposed]
        ),
        "released_reservation_airtime_us": _distribution(
            [float(observation.released_airtime_us) for observation in exposed]
        ),
        "measured_to_estimated_ratio": _distribution(ratios),
        "measured_minus_estimated_airtime_us": _distribution(residuals),
    }


def _realized_diversity(
    observations: Sequence[FrameObservation],
) -> dict[str, Any]:
    """Describe realized union benefit after launch without a causal claim."""

    exposed = [observation for observation in observations if observation.launched]
    rescues = [
        observation
        for observation in exposed
        if observation.primary_deadline_miss and not observation.deadline_miss
    ]
    primary_misses = [
        observation for observation in exposed if observation.primary_deadline_miss
    ]
    diversity_benefits = [
        observation
        for observation in exposed
        if observation.union_latency_us is not None
        and (
            observation.primary_latency_us is None
            or observation.union_latency_us < observation.primary_latency_us
        )
    ]
    capped_savings: list[float] = []
    quantified_acceleration: list[float] = []
    for observation in exposed:
        deadline = float(observation.deadline_us)
        union_capped = min(
            observation.union_latency_us
            if observation.union_latency_us is not None
            else deadline,
            deadline,
        )
        primary_capped = min(
            observation.primary_latency_us
            if observation.primary_latency_us is not None
            else deadline,
            deadline,
        )
        saving = primary_capped - union_capped
        if saving < -1e-9:
            raise AnalysisError("union completion is slower than primary completion")
        capped_savings.append(max(0.0, saving))
        if (
            observation.union_latency_us is not None
            and observation.primary_latency_us is not None
        ):
            acceleration = (
                observation.primary_latency_us - observation.union_latency_us
            )
            if acceleration < -1e-9:
                raise AnalysisError(
                    "union completion is slower than primary completion"
                )
            quantified_acceleration.append(max(0.0, acceleration))
    return {
        "launched_frame_count": len(exposed),
        "primary_copy_deadline_miss_count": len(primary_misses),
        "union_deadline_miss_count": sum(
            observation.deadline_miss for observation in exposed
        ),
        "deadline_rescue_count": len(rescues),
        "deadline_rescue_rate_per_launch": _rate(len(rescues), len(exposed)),
        "deadline_rescue_rate_among_primary_copy_misses": _rate(
            len(rescues), len(primary_misses)
        ),
        "realized_diversity_benefit_count": len(diversity_benefits),
        "realized_diversity_benefit_rate_per_launch": _rate(
            len(diversity_benefits), len(exposed)
        ),
        "union_completed_without_primary_copy_count": sum(
            observation.union_latency_us is not None
            and observation.primary_latency_us is None
            for observation in exposed
        ),
        "positive_capped_latency_saving_count": sum(
            saving > 0 for saving in capped_savings
        ),
        "deadline_rescue_definition": (
            "primary copy misses its deadline AND packet union meets the deadline"
        ),
        "capped_latency_saving_definition": (
            "min(primary latency, deadline) minus min(union latency, deadline); "
            "an incomplete primary or union is assigned the deadline cap"
        ),
        "capped_latency_saving_us_per_launch": _distribution(capped_savings),
        "quantified_primary_completion_acceleration_us": _distribution(
            quantified_acceleration
        ),
        "interpretation": (
            "post-launch realized benefit only; selection into launch prevents a "
            "causal treatment-effect interpretation"
        ),
    }


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
    fallback_count = sum(
        observation.fallback_settlement is True for observation in exposed
    )

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
        "realized_diversity_among_launched": _realized_diversity(observations),
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
            "exposure_cost_distribution": _exposure_cost_distribution(
                observations
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


def _per_run_counts(run: RandomizedRun) -> dict[str, Any]:
    observations = run.observations
    status_counts: dict[str, int] = {}
    for observation in observations:
        status_counts[observation.status] = status_counts.get(observation.status, 0) + 1
    arm_counts: dict[str, Any] = {}
    for arm in ARMS:
        assigned = [
            observation for observation in observations if observation.arm == arm
        ]
        arm_counts[arm] = {
            "assigned": len(assigned),
            "eligible_t2": sum(observation.eligible_t2 for observation in assigned),
            "attempted": sum(observation.attempted for observation in assigned),
            "launched": sum(observation.launched for observation in assigned),
            "noncompliant": sum(
                observation.noncompliance for observation in assigned
            ),
            "deadline_miss": sum(
                observation.deadline_miss for observation in assigned
            ),
            "incomplete": sum(observation.incomplete for observation in assigned),
            "t4_common_risk_set": sum(
                observation.eligible_t2
                and observation.t4_primary_actionable
                and observation.t4_secondary_actionable
                and arm in {"CONTROL", "FULL_COPY_T4"}
                for observation in assigned
            ),
        }
    diversity = _realized_diversity(observations)
    return {
        "run_id": run.run_id,
        "seed": run.seed,
        "run": run.run_number,
        "assigned_frame_count": len(observations),
        "eligible_t2_count": sum(
            observation.eligible_t2 for observation in observations
        ),
        "attempted_count": sum(observation.attempted for observation in observations),
        "launched_count": sum(observation.launched for observation in observations),
        "noncompliance_count": sum(
            observation.noncompliance for observation in observations
        ),
        "deadline_miss_count": sum(
            observation.deadline_miss for observation in observations
        ),
        "incomplete_count": sum(observation.incomplete for observation in observations),
        "deadline_rescue_count_among_launched": diversity["deadline_rescue_count"],
        "execution_status_counts": dict(sorted(status_counts.items())),
        "arm_counts": arm_counts,
    }


def analyze_randomized_runs(
    run_dirs: Sequence[Path | str],
    *,
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
) -> dict[str, Any]:
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
    bootstrap_plan = _cluster_bootstrap_plan(run_ids, bootstrap_replicates)

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
    configured_probabilities: dict[str, float] = {}
    arms: dict[str, Any] = {}
    for arm in ARMS:
        probability = probabilities[arm]
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(float(probability))
            or not 0 < float(probability) < 1
        ):
            raise AnalysisError(f"randomized design has invalid probability for {arm}")
        configured_probabilities[arm] = float(probability)
        arm_observations = [
            observation for observation in observations if observation.arm == arm
        ]
        arms[arm] = _arm_summary(
            arm_observations, float(probability), measurement_duration_us
        )
        arms[arm]["observed_assignment_fraction"] = _rate(
            len(arm_observations), len(observations)
        )
    if not math.isclose(
        sum(configured_probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-12
    ):
        raise AnalysisError("randomized design probabilities do not sum to one")
    for observation in observations:
        if not math.isclose(
            observation.propensity,
            configured_probabilities[observation.arm],
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise AnalysisError(
                f"{observation.run_id}: frame {observation.frame_id} propensity "
                "differs from configured arm probability"
            )

    settled_total = sum(
        arm["measured_airtime"]["measured_exposure_airtime_us"]
        for arm in arms.values()
    )
    if not math.isclose(
        settled_total, tagged_airtime_us, rel_tol=1e-9, abs_tol=1e-6
    ):
        raise AnalysisError(
            "pooled arm airtime does not reconcile with meter summaries"
        )

    all_eligible = [
        observation for observation in observations if observation.eligible_t2
    ]
    t4_common_risk_set = [
        observation
        for observation in all_eligible
        if observation.arm in {"CONTROL", "FULL_COPY_T4"}
        and observation.t4_primary_actionable
        and observation.t4_secondary_actionable
    ]
    t4_pair_name = "t4_regime_vs_control"
    t4_pair = ((t4_pair_name, "FULL_COPY_T4", "CONTROL"),)
    t4_pair_probability = (
        configured_probabilities["FULL_COPY_T4"]
        + configured_probabilities["CONTROL"]
    )
    t2_contrasts = _arm_contrasts(
        all_eligible,
        lambda observation: observation.deadline_miss,
        lambda observation: observation.union_latency_us,
        bootstrap_plan=bootstrap_plan,
    )
    t4_contrasts = _arm_contrasts(
        t4_common_risk_set,
        lambda observation: observation.deadline_miss,
        lambda observation: observation.union_latency_us,
        pairs=t4_pair,
        propensity_normalizers={t4_pair_name: t4_pair_probability},
        bootstrap_plan=bootstrap_plan,
    )
    primary_placebo = _arm_contrasts(
        all_eligible,
        lambda observation: observation.primary_deadline_miss,
        lambda observation: observation.primary_latency_us,
        bootstrap_plan=bootstrap_plan,
    )
    placebo_flags = _nominal_cluster_interval_flags(primary_placebo)
    return {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_kind": "randomized_intervention_pilot_diagnostics",
        "causal_model_fitted": False,
        "inference": {
            "pilot_diagnostics_only": True,
            "known_assignment_propensities_used": True,
            "run_cluster_uncertainty_computed": len(runs) >= 2,
            "run_cluster_bootstrap_replicates": bootstrap_replicates,
            "run_cluster_bootstrap_seed": BOOTSTRAP_SEED,
            "causal_claim_made": False,
            "caveat": (
                "arm contrasts and run-cluster percentile intervals are pilot "
                "diagnostics; multiplicity control, interference assessment, "
                "and held-out confirmation are required before a causal or "
                "performance claim"
            ),
        },
        "run_count": len(runs),
        "run_ids": run_ids,
        "seed_run_pairs": [
            {"seed": seed, "run": run_number}
            for seed, run_number in seed_runs
        ],
        "design": reference.design,
        "build_identity": reference.build_identity,
        "per_run_counts": [_per_run_counts(run) for run in runs],
        "assignment_balance": {
            "all_assigned_frames": _assignment_balance(
                observations, configured_probabilities
            ),
            "common_eligible_t2_frames": _assignment_balance(
                all_eligible, configured_probabilities
            ),
            "interpretation": (
                "standardized deviations and Pearson statistics are diagnostics; "
                "no balance-test p-value is computed"
            ),
        },
        "randomized_arm_contrasts": {
            "t2_common_eligible_itt": {
                "risk_set_definition": (
                    "eligible_t2=1, fixed before any randomized intervention"
                ),
                "frame_count": len(all_eligible),
                "arm_counts": {
                    arm: sum(observation.arm == arm for observation in all_eligible)
                    for arm in ARMS
                },
                "contrasts": t2_contrasts,
            },
            "t4_common_risk_set": {
                "risk_set_definition": (
                    "eligible_t2=1, assigned CONTROL or FULL_COPY_T4, and both "
                    "strictly paired T4 prediction samples actionable; neither arm "
                    "has launched before the T4 snapshot"
                ),
                "frame_count": len(t4_common_risk_set),
                "configured_conditional_probabilities": {
                    "CONTROL": configured_probabilities["CONTROL"]
                    / t4_pair_probability,
                    "FULL_COPY_T4": configured_probabilities["FULL_COPY_T4"]
                    / t4_pair_probability,
                },
                "arm_counts": {
                    arm: sum(
                        observation.arm == arm
                        for observation in t4_common_risk_set
                    )
                    for arm in ("CONTROL", "FULL_COPY_T4")
                },
                "contrasts": t4_contrasts,
                "interpretation": (
                    "the symmetric pre-launch T4 risk set avoids conditioning on "
                    "the T4 execution status; this remains a pilot point contrast"
                ),
            },
        },
        "primary_copy_placebo_diagnostics": {
            "risk_set_definition": (
                "the same common T2-eligible frames used for the union ITT contrasts"
            ),
            "contrasts": primary_placebo,
            "unadjusted_cluster_ci_excludes_zero": placebo_flags,
            "any_unadjusted_cluster_ci_excludes_zero": bool(placebo_flags),
            "multiplicity_adjustment_applied": False,
            "mechanistic_rescue_claim_allowed": False,
            "interpretation": (
                "primary-copy arm differences diagnose random imbalance or possible "
                "copy-0 measurement dependence/cross-link interference; nominal "
                "interval flags may also be chance findings under multiple testing. "
                "They are not completion-conditioned, and collection-scale placebo "
                "clearance is required before interpreting realized rescue as a "
                "causal mechanism"
            ),
        },
        "realized_diversity_among_launched": _realized_diversity(observations),
        "exposure_cost_distribution": _exposure_cost_distribution(observations),
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


def _positive_integer(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a positive integer") from error
    if result < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


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
    parser.add_argument(
        "--bootstrap-replicates",
        type=_positive_integer,
        default=DEFAULT_BOOTSTRAP_REPLICATES,
        help=(
            "deterministic run-cluster percentile-bootstrap replicates "
            f"(default: {DEFAULT_BOOTSTRAP_REPLICATES})"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = analyze_randomized_runs(
        args.run_dirs, bootstrap_replicates=args.bootstrap_replicates
    )
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
