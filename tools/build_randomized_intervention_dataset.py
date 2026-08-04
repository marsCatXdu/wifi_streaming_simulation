#!/usr/bin/env python3
"""Build leakage-safe causal datasets from randomized intervention runs.

Every input run is admitted through :func:`validate_outputs.validate_run`
before this tool reads it.  The output deliberately keeps treatment,
execution, and receiver outcomes outside the explicit ``x_`` feature
allowlist.  It creates two binary randomized comparisons:

* T2: ``FULL_COPY_T2`` versus pure ``CONTROL`` among common-T2-eligible
  frames; and
* T4 wait path: ``FULL_COPY_T4`` versus ``CONTROL`` among frames that were
  untreated before T4 and whose primary copy was still actionable at T4.

The implementation uses only the Python standard library so the resulting
CSV artifacts can be rebuilt on a minimal analysis host.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from validate_outputs import validate_run


DATASET_SCHEMA_VERSION = 1
FEATURE_CONTRACT_ID = "randomized_intervention_leakage_safe_v1"
SPLIT_ALGORITHM = "sha256_seed_run_exact_64_16_16_v1"
HASH_ALGORITHM = "sha256"

STAGES = ("T2", "T4")
WINDOWS = ("1ms", "5ms", "20ms")
TAIL_THRESHOLDS_US = (10000, 11000, 12000, 12500)
ARMS = ("CONTROL", "FULL_COPY_T2", "FULL_COPY_T4")
BUILD_IDENTITY_FIELDS = (
    "ns3_version",
    "ns3_upstream_commit",
    "project_git_commit",
    "compiler",
    "build_profile",
)

# F0 is represented once, from the primary snapshot.  It is not duplicated
# for the hypothetical secondary copy.
F0_FIELDS = (
    "frame_age_us",
    "deadline_slack_us",
    "frame_size_bytes",
    "frame_packet_count",
    "frame_type",
    "packets_submitted",
    "application_socket_packet_bytes_submitted",
    "packets_remaining_to_submit",
)

# Driver-exportable current primary-frame state.  The absolute oldest-queue
# timestamp is intentionally absent.  Path-wide queue counts are included
# because they describe contention ahead of the current frame.
PRIMARY_CURRENT_FIELDS = (
    "frame_packets_mac_enqueued",
    "frame_packets_mac_dequeued",
    "frame_packets_tx_succeeded",
    "frame_mpdu_attempt_failures",
    "frame_packets_terminally_dropped",
    "frame_packets_currently_queued",
    "frame_mac_service_bytes_currently_queued",
    "mac_queue_packets",
    "mac_queue_service_bytes",
    "packets_ahead_of_frame",
    "mac_service_bytes_ahead_of_frame",
    "frame_packets_pending_primary",
    "frame_mac_service_bytes_not_acknowledged",
    "frame_mac_service_bytes_pending_primary",
)

# These are finite-window passive counters or occupancy measurements.  They
# come from the genuine delayed polling ledger, not the ideal live snapshot.
PRIMARY_ROLLING_PREFIXES = (
    "mpdu_attempts",
    "mpdu_positive_acks",
    "mpdu_attempt_failures",
    "mpdu_retries",
    "mpdu_retry_ratio",
    "acknowledged_mac_service_bytes",
    "mpdu_queue_to_ack_mean",
    "mpdu_queue_to_ack_p95",
    "mpdu_first_attempt_to_ack_mean",
    "mpdu_first_attempt_to_ack_p95",
    "phy_tx_fraction",
    "phy_rx_fraction",
    "phy_busy_fraction",
    "phy_idle_fraction",
    "phy_other_fraction",
)

# The hypothetical secondary copy contributes only path-level state that is
# available without injecting the frame: passive PHY occupancy and current
# target queue size.  Secondary per-frame ACK/progress state is forbidden.
SECONDARY_QUEUE_FIELDS = (
    "mac_queue_packets",
    "mac_queue_service_bytes",
)
SECONDARY_PHY_PREFIXES = (
    "phy_tx_fraction",
    "phy_rx_fraction",
    "phy_busy_fraction",
    "phy_idle_fraction",
    "phy_other_fraction",
)

# The rolling names have two special families whose unit suffix follows the
# window, e.g. mpdu_queue_to_ack_mean_1ms_us.  Construct them explicitly to
# make the serialized contract obvious and stable.
PRIMARY_ROLLING_FIELDS = tuple(
    (
        f"{prefix}_{window}_us"
        if prefix
        in {
            "mpdu_queue_to_ack_mean",
            "mpdu_queue_to_ack_p95",
            "mpdu_first_attempt_to_ack_mean",
            "mpdu_first_attempt_to_ack_p95",
        }
        else f"{prefix}_{window}"
    )
    for window in WINDOWS
    for prefix in PRIMARY_ROLLING_PREFIXES
)
SECONDARY_PHY_FIELDS = tuple(
    f"{prefix}_{window}"
    for window in WINDOWS
    for prefix in SECONDARY_PHY_PREFIXES
)

FEATURE_COLUMNS = tuple(
    [f"x_f0_{field}" for field in F0_FIELDS]
    + [f"x_primary_{field}" for field in PRIMARY_CURRENT_FIELDS]
    + [f"x_primary_{field}" for field in PRIMARY_ROLLING_FIELDS]
    + [f"x_secondary_{field}" for field in SECONDARY_QUEUE_FIELDS]
    + [f"x_secondary_{field}" for field in SECONDARY_PHY_FIELDS]
)

NON_FEATURE_COLUMNS = (
    "dataset_schema_version",
    "run_id",
    "seed",
    "run_number",
    "split_role",
    "frame_id",
    "analysis_stage",
    "assigned_arm",
    "treatment",
    "treatment_probability",
    "assigned_arm_probability",
    "eligible_t2",
    "decision_primary_actionable",
    "attempted",
    "launched",
    "noncompliance",
    "execution_stage",
    "execution_status",
    "outcome_incomplete",
    "outcome_deadline_miss",
    "outcome_union_latency_us",
    "outcome_complete_by_10000us",
    "outcome_complete_by_11000us",
    "outcome_complete_by_12000us",
    "outcome_complete_by_12500us",
    "outcome_primary_incomplete",
    "outcome_primary_deadline_miss",
    "outcome_primary_latency_us",
    "outcome_primary_complete_by_10000us",
    "outcome_primary_complete_by_11000us",
    "outcome_primary_complete_by_12000us",
    "outcome_primary_complete_by_12500us",
    "outcome_deadline_rescue",
    "outcome_tail_rescue_10000us",
    "outcome_tail_rescue_11000us",
    "outcome_tail_rescue_12000us",
    "outcome_tail_rescue_12500us",
    "outcome_deadline_capped_latency_saving_us",
    "outcome_secondary_airtime_us",
    "outcome_secondary_released_airtime_us",
    "outcome_secondary_fallback",
    "action_nominal_airtime_us",
    "action_estimated_airtime_us",
)
DATASET_COLUMNS = NON_FEATURE_COLUMNS + FEATURE_COLUMNS

SOURCE_FILES = (
    "resolved_config.json",
    "build_info.json",
    "frames.csv",
    "prediction_samples.csv",
    "prediction_polling_samples.csv",
    "randomized_intervention_assignments.csv",
    "randomized_intervention_executions.csv",
    "secondary_airtime_settlements.csv",
    "secondary_airtime_summary.json",
)

OUTPUT_FILES = (
    "randomized_t2.csv",
    "randomized_t4_wait.csv",
    "dataset_metadata.json",
    "artifact_manifest.json",
)


class DatasetError(ValueError):
    """Raised when validated inputs cannot form an unambiguous dataset."""


@dataclass(frozen=True)
class RunInput:
    """Parsed identity and source ledgers for one validated run."""

    path: Path
    run_id: str
    seed: int
    run_number: int
    config: dict[str, Any]
    build_identity: dict[str, str]
    source_hashes: dict[str, str]
    frames: dict[int, dict[str, str]]
    assignments: dict[int, dict[str, str]]
    executions: dict[int, dict[str, str]]
    settlements: dict[int, dict[str, str]]
    samples: dict[tuple[int, str, int, int], dict[str, str]]
    polling: dict[tuple[int, str, int, int], dict[str, str]]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise DatasetError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DatasetError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise DatasetError(f"{path}: expected a JSON object")
    return value


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            header = reader.fieldnames
            if header is None or len(header) != len(set(header)):
                raise DatasetError(f"{path}: missing or duplicate CSV columns")
            rows = list(reader)
    except OSError as error:
        raise DatasetError(f"cannot read {path}: {error}") from error
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise DatasetError(f"{path}: malformed CSV row")
    return list(header), rows


def _integer(row: dict[str, str], key: str, source: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise DatasetError(f"{source}: invalid integer {key}") from error


def _number(row: dict[str, str], key: str, source: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise DatasetError(f"{source}: invalid number {key}") from error
    if not math.isfinite(value):
        raise DatasetError(f"{source}: non-finite number {key}")
    return value


def _flag(row: dict[str, str], key: str, source: str) -> bool:
    value = row.get(key)
    if value not in {"0", "1"}:
        raise DatasetError(f"{source}: invalid flag {key}")
    return value == "1"


def _index_by_frame(
    rows: Iterable[dict[str, str]], source: str
) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        frame_id = _integer(row, "frame_id", source)
        if frame_id in result:
            raise DatasetError(f"{source}: duplicate frame_id {frame_id}")
        result[frame_id] = row
    return result


def _require_columns(header: Sequence[str], fields: Iterable[str], source: str) -> None:
    missing = sorted(set(fields) - set(header))
    if missing:
        raise DatasetError(f"{source}: missing columns {', '.join(missing)}")


def _index_samples(
    rows: Iterable[dict[str, str]], source: str
) -> dict[tuple[int, str, int, int], dict[str, str]]:
    result: dict[tuple[int, str, int, int], dict[str, str]] = {}
    for row in rows:
        stage = row.get("sample_stage", "")
        if stage not in STAGES:
            continue
        key = (
            _integer(row, "frame_id", source),
            stage,
            _integer(row, "path_id", source),
            _integer(row, "copy_id", source),
        )
        if key in result:
            raise DatasetError(f"{source}: duplicate frame-stage-path-copy key {key}")
        result[key] = row
    return result


def _discover_run_dirs(inputs: Sequence[Path | str]) -> list[Path]:
    discovered: set[Path] = set()
    for raw in inputs:
        path = Path(raw).resolve()
        if (path / "resolved_config.json").is_file():
            discovered.add(path)
            continue
        if not path.is_dir():
            raise DatasetError(f"input is not a run directory or run root: {path}")
        discovered.update(config.parent for config in path.rglob("resolved_config.json"))
    if not discovered:
        raise DatasetError("no run directories found")
    return sorted(discovered)


def _load_run(run_dir: Path) -> RunInput:
    # This call is deliberately unconditional and precedes every source read.
    validate_run(run_dir)

    config = _read_json(run_dir / "resolved_config.json")
    if (
        config.get("policy") != "randomized_full_copy_exploration"
        or config.get("topology") != "dual_interface"
    ):
        raise DatasetError(f"{run_dir}: expected randomized dual-interface policy")
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
        raise DatasetError(f"{run_dir}: invalid run identity")

    build = _read_json(run_dir / "build_info.json")
    build_identity: dict[str, str] = {}
    for field in BUILD_IDENTITY_FIELDS:
        value = build.get(field)
        if not isinstance(value, str) or not value:
            raise DatasetError(f"{run_dir}: invalid build identity {field}")
        build_identity[field] = value

    frame_header, frame_rows = _read_csv(run_dir / "frames.csv")
    assignment_header, assignment_rows = _read_csv(
        run_dir / "randomized_intervention_assignments.csv"
    )
    execution_header, execution_rows = _read_csv(
        run_dir / "randomized_intervention_executions.csv"
    )
    _, settlement_rows = _read_csv(run_dir / "secondary_airtime_settlements.csv")
    sample_header, sample_rows = _read_csv(run_dir / "prediction_samples.csv")
    polling_header, polling_rows = _read_csv(
        run_dir / "prediction_polling_samples.csv"
    )

    _require_columns(
        frame_header,
        {
            "run_id",
            "frame_id",
            "frame_type",
            "generation_time_us",
            "deadline_us",
            "copy_0_completion_us",
            "incomplete",
            "deadline_miss",
            "union_latency_us",
        },
        "frames.csv",
    )
    _require_columns(
        assignment_header,
        {
            "run_id",
            "frame_id",
            "eligible_t2",
            "assigned_arm",
            "propensity",
            "nominal_airtime_us",
            "estimated_airtime_us",
        },
        "randomized_intervention_assignments.csv",
    )
    _require_columns(
        execution_header,
        {
            "run_id",
            "frame_id",
            "assigned_arm",
            "attempted",
            "launched",
            "noncompliance",
            "execution_stage",
            "status",
        },
        "randomized_intervention_executions.csv",
    )
    _require_columns(
        sample_header,
        {
            "run_id",
            "frame_id",
            "path_id",
            "copy_id",
            "sample_stage",
            "sample_offset_us",
            "actionable",
            *F0_FIELDS,
            *PRIMARY_CURRENT_FIELDS,
            *SECONDARY_QUEUE_FIELDS,
        },
        "prediction_samples.csv",
    )
    _require_columns(
        polling_header,
        {
            "run_id",
            "frame_id",
            "path_id",
            "copy_id",
            "sample_stage",
            "sample_offset_us",
            "report_available",
            *PRIMARY_ROLLING_FIELDS,
            *SECONDARY_PHY_FIELDS,
        },
        "prediction_polling_samples.csv",
    )

    frames = _index_by_frame(frame_rows, "frames.csv")
    assignments = _index_by_frame(
        assignment_rows, "randomized_intervention_assignments.csv"
    )
    executions = _index_by_frame(
        execution_rows, "randomized_intervention_executions.csv"
    )
    settlements = _index_by_frame(
        settlement_rows, "secondary_airtime_settlements.csv"
    )
    samples = _index_samples(sample_rows, "prediction_samples.csv")
    polling = _index_samples(polling_rows, "prediction_polling_samples.csv")

    frame_ids = set(frames)
    if not frame_ids or set(assignments) != frame_ids or set(executions) != frame_ids:
        raise DatasetError(f"{run_dir}: frame/assignment/execution join is not exact")
    if not set(settlements) <= frame_ids:
        raise DatasetError(f"{run_dir}: settlement references an unknown frame")
    expected_samples = {
        (frame_id, stage, path_id, copy_id)
        for frame_id in frame_ids
        for stage in STAGES
        for path_id, copy_id in ((1, 0), (0, 1))
    }
    if set(samples) != expected_samples or set(polling) != expected_samples:
        raise DatasetError(f"{run_dir}: paired T2/T4 telemetry join is not exact")

    launched_ids = {
        frame_id
        for frame_id, row in executions.items()
        if _flag(row, "launched", "randomized_intervention_executions.csv")
    }
    if set(settlements) != launched_ids:
        raise DatasetError(f"{run_dir}: launch/settlement join is not exact")

    for frame_id in sorted(frame_ids):
        frame = frames[frame_id]
        assignment = assignments[frame_id]
        execution = executions[frame_id]
        if not (
            frame.get("run_id")
            == assignment.get("run_id")
            == execution.get("run_id")
            == run_id
        ):
            raise DatasetError(f"{run_dir}: frame {frame_id} has inconsistent run_id")
        if (
            assignment.get("assigned_arm") not in ARMS
            or execution.get("assigned_arm") != assignment.get("assigned_arm")
        ):
            raise DatasetError(f"{run_dir}: frame {frame_id} has inconsistent arm")
        for stage in STAGES:
            primary = samples[(frame_id, stage, 1, 0)]
            secondary = samples[(frame_id, stage, 0, 1)]
            primary_poll = polling[(frame_id, stage, 1, 0)]
            secondary_poll = polling[(frame_id, stage, 0, 1)]
            for row, source in (
                (primary, "prediction_samples.csv"),
                (secondary, "prediction_samples.csv"),
                (primary_poll, "prediction_polling_samples.csv"),
                (secondary_poll, "prediction_polling_samples.csv"),
            ):
                if row.get("run_id") != run_id:
                    raise DatasetError(
                        f"{run_dir}: frame {frame_id} {source} has inconsistent run_id"
                    )
            for field in (
                "frame_age_us",
                "deadline_slack_us",
                "frame_size_bytes",
                "frame_packet_count",
                "frame_type",
            ):
                if primary.get(field) != secondary.get(field):
                    raise DatasetError(
                        f"{run_dir}: frame {frame_id} paired {field} differs"
                    )
            for sample, poll in (
                (primary, primary_poll),
                (secondary, secondary_poll),
            ):
                for field in (
                    "run_id",
                    "frame_id",
                    "path_id",
                    "copy_id",
                    "sample_stage",
                    "sample_offset_us",
                ):
                    if sample.get(field) != poll.get(field):
                        raise DatasetError(
                            f"{run_dir}: frame {frame_id} polling {field} mismatch"
                        )
                if poll.get("report_available") != "1":
                    raise DatasetError(
                        f"{run_dir}: frame {frame_id} has unavailable delayed polling"
                    )

    source_hashes = {name: _sha256(run_dir / name) for name in SOURCE_FILES}
    return RunInput(
        path=run_dir,
        run_id=run_id,
        seed=seed,
        run_number=run_number,
        config=config,
        build_identity=build_identity,
        source_hashes=source_hashes,
        frames=frames,
        assignments=assignments,
        executions=executions,
        settlements=settlements,
        samples=samples,
        polling=polling,
    )


def _design_contract(config: dict[str, Any]) -> dict[str, Any]:
    """Extract the fixed experiment contract, omitting randomized placement."""

    background = copy.deepcopy(config.get("background"))
    if isinstance(background, dict):
        obss = background.get("obss")
        if isinstance(obss, dict):
            obss.pop("bsses", None)
    meter = copy.deepcopy(config.get("secondaryAirtimeMeter"))
    randomized = copy.deepcopy(config.get("randomizedIntervention"))
    return {
        "policy": config.get("policy"),
        "topology": config.get("topology"),
        "duration_s": config.get("duration_s"),
        "warmup_s": config.get("warmup_s"),
        "measurement_start_s": config.get("measurement_start_s"),
        "measurement_stop_s": config.get("measurement_stop_s"),
        "stream": config.get("stream"),
        "wifi": config.get("wifi"),
        "background_without_realized_obss_placement": background,
        "propagation": config.get("propagation"),
        "predictionTelemetry": config.get("predictionTelemetry"),
        "randomizedIntervention": randomized,
        "secondaryAirtimeMeter": meter,
    }


def _split_counts(run_count: int) -> dict[str, int]:
    if run_count <= 0:
        raise DatasetError("cannot split an empty run set")
    roles = ("train", "calibration", "test")
    weights = (64, 16, 16)
    total = sum(weights)
    quotas = [run_count * weight / total for weight in weights]
    counts = [math.floor(quota) for quota in quotas]
    remaining = run_count - sum(counts)
    order = sorted(
        range(len(roles)),
        key=lambda index: (-(quotas[index] - counts[index]), index),
    )
    for index in order[:remaining]:
        counts[index] += 1
    return dict(zip(roles, counts))


def deterministic_run_splits(
    identities: Sequence[tuple[int, int]]
) -> dict[tuple[int, int], str]:
    """Return exact 64/16/16-proportional splits after stable hash ordering."""

    if len(identities) != len(set(identities)):
        raise DatasetError("duplicate (seed, run) identity")
    ordered = sorted(
        identities,
        key=lambda identity: (
            hashlib.sha256(f"{identity[0]}:{identity[1]}".encode("ascii")).digest(),
            identity,
        ),
    )
    counts = _split_counts(len(ordered))
    result: dict[tuple[int, int], str] = {}
    start = 0
    for role in ("train", "calibration", "test"):
        stop = start + counts[role]
        result.update((identity, role) for identity in ordered[start:stop])
        start = stop
    return result


def _feature_values(
    primary: dict[str, str],
    secondary: dict[str, str],
    primary_polling: dict[str, str],
    secondary_polling: dict[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    values.update((f"x_f0_{field}", primary[field]) for field in F0_FIELDS)
    values.update(
        (f"x_primary_{field}", primary[field]) for field in PRIMARY_CURRENT_FIELDS
    )
    values.update(
        (f"x_primary_{field}", primary_polling[field])
        for field in PRIMARY_ROLLING_FIELDS
    )
    values.update(
        (f"x_secondary_{field}", secondary[field])
        for field in SECONDARY_QUEUE_FIELDS
    )
    values.update(
        (f"x_secondary_{field}", secondary_polling[field])
        for field in SECONDARY_PHY_FIELDS
    )
    if tuple(values) != FEATURE_COLUMNS:
        raise DatasetError("internal feature column order mismatch")
    return values


def _outcome_values(
    frame: dict[str, str], settlement: dict[str, str] | None, source: str
) -> dict[str, str]:
    generation_us = _integer(frame, "generation_time_us", source)
    deadline_us = _integer(frame, "deadline_us", source)
    if generation_us < 0 or deadline_us <= 0:
        raise DatasetError(f"{source}: invalid generation time or deadline")

    incomplete = _flag(frame, "incomplete", source)
    deadline_miss = _flag(frame, "deadline_miss", source)
    latency_text = frame.get("union_latency_us", "")
    latency: int | None
    if incomplete:
        if latency_text != "":
            raise DatasetError(f"{source}: incomplete frame has union latency")
        latency = None
    else:
        latency = _integer(frame, "union_latency_us", source)
        if latency < 0:
            raise DatasetError(f"{source}: negative union latency")
    expected_deadline_miss = latency is None or latency > deadline_us
    if deadline_miss != expected_deadline_miss:
        raise DatasetError(f"{source}: union deadline-miss arithmetic differs")

    primary_completion_text = frame.get("copy_0_completion_us", "")
    primary_latency: int | None
    if primary_completion_text == "":
        primary_latency = None
    else:
        primary_completion_us = _integer(frame, "copy_0_completion_us", source)
        if primary_completion_us < generation_us:
            raise DatasetError(f"{source}: primary completion precedes generation")
        primary_latency = primary_completion_us - generation_us
    if primary_latency is not None and latency is None:
        raise DatasetError(f"{source}: primary completed but union is incomplete")
    if primary_latency is not None and latency is not None and latency > primary_latency:
        raise DatasetError(f"{source}: union completion is later than primary completion")

    primary_incomplete = primary_latency is None
    primary_deadline_miss = primary_latency is None or primary_latency > deadline_us
    result = {
        "outcome_incomplete": str(int(incomplete)),
        "outcome_deadline_miss": str(int(deadline_miss)),
        "outcome_union_latency_us": latency_text,
        "outcome_primary_incomplete": str(int(primary_incomplete)),
        "outcome_primary_deadline_miss": str(int(primary_deadline_miss)),
        "outcome_primary_latency_us": (
            "" if primary_latency is None else str(primary_latency)
        ),
        "outcome_deadline_rescue": str(
            int(primary_deadline_miss and not deadline_miss)
        ),
    }
    for threshold in TAIL_THRESHOLDS_US:
        union_complete_by = latency is not None and latency <= threshold
        primary_complete_by = (
            primary_latency is not None and primary_latency <= threshold
        )
        result[f"outcome_complete_by_{threshold}us"] = str(int(union_complete_by))
        result[f"outcome_primary_complete_by_{threshold}us"] = str(
            int(primary_complete_by)
        )
        result[f"outcome_tail_rescue_{threshold}us"] = str(
            int(not primary_complete_by and union_complete_by)
        )

    primary_capped = min(
        deadline_us if primary_latency is None else primary_latency, deadline_us
    )
    union_capped = min(deadline_us if latency is None else latency, deadline_us)
    capped_saving = primary_capped - union_capped
    if capped_saving < 0:
        raise DatasetError(f"{source}: negative deadline-capped latency saving")
    result["outcome_deadline_capped_latency_saving_us"] = str(capped_saving)
    if settlement is None:
        result.update(
            {
                "outcome_secondary_airtime_us": "0",
                "outcome_secondary_released_airtime_us": "0",
                "outcome_secondary_fallback": "0",
            }
        )
    else:
        measured = _number(settlement, "measured_airtime_us", source)
        released = _number(settlement, "released_airtime_us", source)
        if measured < 0 or released < 0:
            raise DatasetError(f"{source}: negative settlement airtime")
        fallback = _flag(settlement, "fallback", source)
        result.update(
            {
                "outcome_secondary_airtime_us": settlement["measured_airtime_us"],
                "outcome_secondary_released_airtime_us": settlement[
                    "released_airtime_us"
                ],
                "outcome_secondary_fallback": str(int(fallback)),
            }
        )
    return result


def _dataset_row(
    run: RunInput,
    frame_id: int,
    stage: str,
    split_role: str,
    treatment_probability: float,
) -> dict[str, str]:
    frame = run.frames[frame_id]
    assignment = run.assignments[frame_id]
    execution = run.executions[frame_id]
    primary = run.samples[(frame_id, stage, 1, 0)]
    secondary = run.samples[(frame_id, stage, 0, 1)]
    primary_polling = run.polling[(frame_id, stage, 1, 0)]
    secondary_polling = run.polling[(frame_id, stage, 0, 1)]
    arm = assignment["assigned_arm"]
    treatment_arm = f"FULL_COPY_{stage}"
    source = f"{run.path}: frame {frame_id}"
    row = {
        "dataset_schema_version": str(DATASET_SCHEMA_VERSION),
        "run_id": run.run_id,
        "seed": str(run.seed),
        "run_number": str(run.run_number),
        "split_role": split_role,
        "frame_id": str(frame_id),
        "analysis_stage": stage,
        "assigned_arm": arm,
        "treatment": str(int(arm == treatment_arm)),
        "treatment_probability": format(treatment_probability, ".17g"),
        "assigned_arm_probability": assignment["propensity"],
        "eligible_t2": assignment["eligible_t2"],
        "decision_primary_actionable": primary["actionable"],
        "attempted": execution["attempted"],
        "launched": execution["launched"],
        "noncompliance": execution["noncompliance"],
        "execution_stage": execution["execution_stage"],
        "execution_status": execution["status"],
        "action_nominal_airtime_us": assignment["nominal_airtime_us"],
        "action_estimated_airtime_us": assignment["estimated_airtime_us"],
    }
    row.update(_outcome_values(frame, run.settlements.get(frame_id), source))
    row.update(_feature_values(primary, secondary, primary_polling, secondary_polling))
    if set(row) != set(DATASET_COLUMNS):
        missing = sorted(set(DATASET_COLUMNS) - set(row))
        extra = sorted(set(row) - set(DATASET_COLUMNS))
        raise DatasetError(f"internal dataset schema mismatch: missing={missing}, extra={extra}")
    return row


def _build_rows(
    runs: Sequence[RunInput], splits: dict[tuple[int, int], str]
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    t2_rows: list[dict[str, str]] = []
    t4_rows: list[dict[str, str]] = []
    exclusions = {
        "t2_ineligible": 0,
        "t2_other_arm": 0,
        "t4_ineligible": 0,
        "t4_t2_arm_post_treatment": 0,
        "t4_primary_not_actionable": 0,
    }
    for run in runs:
        randomized = run.config["randomizedIntervention"]
        probabilities = randomized["arm_probabilities"]
        try:
            p_t2 = float(probabilities["FULL_COPY_T2"])
            p_t4 = float(probabilities["FULL_COPY_T4"])
            p_control = float(probabilities["CONTROL"])
        except (KeyError, TypeError, ValueError) as error:
            raise DatasetError(f"{run.path}: invalid randomized arm probabilities") from error
        if min(p_t2, p_t4, p_control) <= 0:
            raise DatasetError(f"{run.path}: all randomized arms must have positive mass")
        t2_probability = p_t2 / (p_t2 + p_control)
        t4_probability = p_t4 / (p_t4 + p_control)
        split = splits[(run.seed, run.run_number)]
        for frame_id in sorted(run.frames):
            assignment = run.assignments[frame_id]
            arm = assignment["assigned_arm"]
            eligible = _flag(
                assignment, "eligible_t2", "randomized_intervention_assignments.csv"
            )
            if not eligible:
                exclusions["t2_ineligible"] += 1
                exclusions["t4_ineligible"] += 1
                continue

            if arm in {"CONTROL", "FULL_COPY_T2"}:
                t2_rows.append(
                    _dataset_row(run, frame_id, "T2", split, t2_probability)
                )
            else:
                exclusions["t2_other_arm"] += 1

            if arm == "FULL_COPY_T2":
                # Its T4 state can already contain intervention effects.
                exclusions["t4_t2_arm_post_treatment"] += 1
                continue
            if arm not in {"CONTROL", "FULL_COPY_T4"}:
                raise DatasetError(f"{run.path}: unknown randomized arm {arm}")
            t4_primary = run.samples[(frame_id, "T4", 1, 0)]
            if not _flag(t4_primary, "actionable", "prediction_samples.csv"):
                exclusions["t4_primary_not_actionable"] += 1
                continue
            t4_rows.append(
                _dataset_row(run, frame_id, "T4", split, t4_probability)
            )

    row_key = lambda row: (
        int(row["seed"]),
        int(row["run_number"]),
        int(row["frame_id"]),
    )
    t2_rows.sort(key=row_key)
    t4_rows.sort(key=row_key)
    return t2_rows, t4_rows, exclusions


def _write_csv(path: Path, rows: Sequence[dict[str, str]]) -> None:
    try:
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=DATASET_COLUMNS,
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(rows)
    except OSError as error:
        raise DatasetError(f"cannot write {path}: {error}") from error


def _write_json(path: Path, value: Any) -> None:
    try:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise DatasetError(f"cannot write {path}: {error}") from error


def build_dataset(
    run_dirs: Sequence[Path | str], output_dir: Path | str
) -> dict[str, Any]:
    """Validate runs and write deterministic T2/T4 causal dataset artifacts."""

    discovered = _discover_run_dirs(run_dirs)
    runs = [_load_run(path) for path in discovered]
    runs.sort(key=lambda run: (run.seed, run.run_number, run.run_id))
    identities = [(run.seed, run.run_number) for run in runs]
    if len(identities) != len(set(identities)):
        raise DatasetError("input contains duplicate (seed, run) identities")
    if len({run.run_id for run in runs}) != len(runs):
        raise DatasetError("input contains duplicate run_id values")

    reference_build = runs[0].build_identity
    reference_contract = _design_contract(runs[0].config)
    for run in runs[1:]:
        if run.build_identity != reference_build:
            raise DatasetError(f"{run.path}: build identity differs from the first run")
        if _design_contract(run.config) != reference_contract:
            raise DatasetError(f"{run.path}: experiment design differs from the first run")

    splits = deterministic_run_splits(identities)
    t2_rows, t4_rows, exclusions = _build_rows(runs, splits)
    if not t2_rows or not t4_rows:
        raise DatasetError("both the T2 and untreated wait-path T4 datasets need rows")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    targets = [output / name for name in OUTPUT_FILES]
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise DatasetError(f"refusing to overwrite existing artifacts: {', '.join(existing)}")

    _write_csv(output / "randomized_t2.csv", t2_rows)
    _write_csv(output / "randomized_t4_wait.csv", t4_rows)

    split_rows = [
        {
            "seed": seed,
            "run_number": run_number,
            "split_role": splits[(seed, run_number)],
        }
        for seed, run_number in sorted(identities)
    ]
    split_counts = {
        role: sum(value == role for value in splits.values())
        for role in ("train", "calibration", "test")
    }
    metadata: dict[str, Any] = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "feature_contract_id": FEATURE_CONTRACT_ID,
        "validation": {
            "validator": "validate_outputs.validate_run",
            "every_run_validated_before_read": True,
        },
        "comparisons": {
            "T2": {
                "file": "randomized_t2.csv",
                "arms": ["CONTROL", "FULL_COPY_T2"],
                "population": "common_T2_eligible",
                "estimand": "binary_assignment_ITT",
                "row_count": len(t2_rows),
            },
            "T4": {
                "file": "randomized_t4_wait.csv",
                "arms": ["CONTROL", "FULL_COPY_T4"],
                "population": "common_T2_eligible_and_primary_actionable_at_T4",
                "estimand": "untreated_wait_path_binary_assignment_ITT",
                "excludes": "all FULL_COPY_T2 post-treatment T4 rows",
                "row_count": len(t4_rows),
            },
        },
        "outcome_contract": {
            "union_source": "frames.csv union_latency_us/deadline_miss/incomplete",
            "primary_source": (
                "copy 0 latency = copy_0_completion_us - generation_time_us; "
                "a missing completion is primary incomplete"
            ),
            "primary_deadline_miss": (
                "primary incomplete or primary latency strictly greater than deadline_us"
            ),
            "deadline_rescue": "primary deadline miss and union deadline hit",
            "tail_rescue": (
                "primary not complete by threshold and union complete by threshold"
            ),
            "deadline_capped_latency_saving_us": (
                "min(primary latency or deadline, deadline) - "
                "min(union latency or deadline, deadline)"
            ),
            "tail_thresholds_us": list(TAIL_THRESHOLDS_US),
        },
        "feature_contract": {
            "feature_columns": list(FEATURE_COLUMNS),
            "feature_groups": {
                "shared_f0": [f"x_f0_{field}" for field in F0_FIELDS],
                "primary_current_frame_and_queue": [
                    f"x_primary_{field}" for field in PRIMARY_CURRENT_FIELDS
                ],
                "primary_delayed_rolling": [
                    f"x_primary_{field}" for field in PRIMARY_ROLLING_FIELDS
                ],
                "secondary_current_path_queue": [
                    f"x_secondary_{field}" for field in SECONDARY_QUEUE_FIELDS
                ],
                "secondary_delayed_phy_occupancy": [
                    f"x_secondary_{field}" for field in SECONDARY_PHY_FIELDS
                ],
            },
            "categorical_features": ["x_f0_frame_type"],
            "f0_source": "primary current prediction sample; represented once",
            "primary_current_source": "primary current prediction sample",
            "primary_rolling_source": (
                "primary genuine delayed 1 ms polling report for 1/5/20 ms windows"
            ),
            "secondary_queue_source": "hypothetical secondary current path queue",
            "secondary_phy_source": (
                "hypothetical secondary genuine delayed 1 ms polling report"
            ),
            "conservative_exclusions": [
                "identifiers",
                "absolute timestamps",
                "causal watermarks",
                "assignment arms and deterministic draws",
                "execution status and compliance",
                "receiver outcomes and airtime settlements",
                "eligibility flags",
                "F3 oracle state",
                "lifetime cumulative F1 totals",
                "secondary per-frame F2 state",
                "secondary stream retry/ACK telemetry",
                "T4 rows assigned FULL_COPY_T2",
                "redundant PHY duration fields when occupancy fractions are present",
                "polling history-coverage provenance",
            ],
            "missing_values": "empty CSV fields are preserved; zero is never imputed",
        },
        "non_feature_columns": list(NON_FEATURE_COLUMNS),
        "split": {
            "algorithm": SPLIT_ALGORITHM,
            "unit": "(seed, run_number)",
            "target_ratio": {"train": 64, "calibration": 16, "test": 16},
            "counts": split_counts,
            "assignments": split_rows,
        },
        "exclusion_counts": exclusions,
        "build_identity": reference_build,
        "environment_compatibility": {
            "invariant_projection": reference_contract,
            "ignored_seed_realization_fields": ["background.obss.bsses"],
        },
        "design_contract_sha256": hashlib.sha256(
            _canonical_json(reference_contract).encode("ascii")
        ).hexdigest(),
        "source_runs": [
            {
                "path": str(run.path),
                "run_id": run.run_id,
                "seed": run.seed,
                "run_number": run.run_number,
                "files_sha256": run.source_hashes,
            }
            for run in runs
        ],
    }
    _write_json(output / "dataset_metadata.json", metadata)
    manifest = {
        "manifest_schema_version": 1,
        "hash_algorithm": HASH_ALGORITHM,
        "artifacts_sha256": {
            name: _sha256(output / name)
            for name in (
                "randomized_t2.csv",
                "randomized_t4_wait.csv",
                "dataset_metadata.json",
            )
        },
    }
    _write_json(output / "artifact_manifest.json", manifest)
    return metadata


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="build strictly validated randomized-intervention datasets"
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        type=Path,
        help="run directories or roots recursively containing run directories",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    metadata = build_dataset(args.run_dirs, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "run_count": len(metadata["source_runs"]),
                "t2_rows": metadata["comparisons"]["T2"]["row_count"],
                "t4_rows": metadata["comparisons"]["T4"]["row_count"],
                "split_counts": metadata["split"]["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
