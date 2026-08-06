#!/usr/bin/env python3
"""Build an action-clean temporal T2 randomized-intervention dataset.

This augmenter deliberately consumes, rather than reconstructs, the v1 causal
dataset.  The v1 manifest and every recorded raw-source hash are verified before
use, every raw run is admitted by ``validate_outputs.validate_run``, and each v1
row is regenerated from the raw ledgers before temporal features are attached.

Only information available at the T2 decision is admitted.  Temporal endpoints
are the current frame and exact frame lags 1, 3, and 8.  Secondary passive PHY
history is retained only when no tagged secondary PPDU overlaps the endpoint's
preceding 20 ms and no tagged secondary reservation is active at the endpoint.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence, TextIO

import build_randomized_intervention_dataset as base


DATASET_SCHEMA_VERSION = 1
FEATURE_CONTRACT_ID = "randomized_intervention_t2_temporal_action_clean_v1"
HASH_ALGORITHM = "sha256"
OUTPUT_CSV = "randomized_t2_temporal.csv"
OUTPUT_METADATA = "dataset_metadata.json"
OUTPUT_MANIFEST = "artifact_manifest.json"

LAGS = (1, 3, 8)
WINDOWS_US = (("1ms", 1_000), ("5ms", 5_000), ("20ms", 20_000))
PRIMARY_PATH = (1, 0)
SECONDARY_PATH = (0, 1)
T2_OFFSET_NS = 2_000_000
SECONDARY_CLEAN_WINDOW_NS = 20_000_000
LAST_EVENT_AGE_CAP_US = 1_000_000.0
CLEARANCE_TIME_CAP_US = 1_000_000.0
MIN_WORKING_RATE_BYTES_PER_US = 1e-6

RADIO_FIELDS = (
    ("mcs", "current_mcs"),
    ("nss", "current_nss"),
    ("channel_width_mhz", "current_channel_width_mhz"),
    ("guard_interval_ns", "current_guard_interval_ns"),
)
AGE_FIELDS = (
    ("last_tx_age_us", "last_tx_attempt_time_ns"),
    ("last_ack_age_us", "last_positive_ack_time_ns"),
)
ROLLING_COUNT_FIELDS = (
    "mpdu_attempts",
    "mpdu_positive_acks",
    "mpdu_attempt_failures",
    "mpdu_retries",
    "acknowledged_mac_service_bytes",
)
PHY_FRACTION_FIELDS = (
    "phy_tx_fraction",
    "phy_rx_fraction",
    "phy_busy_fraction",
    "phy_idle_fraction",
    "phy_other_fraction",
)
CUMULATIVE_RATE_FIELDS = (
    ("mpdu_attempts", "mpdu_tx_attempts_total"),
    ("mpdu_positive_acks", "mpdu_positive_acks_total"),
    ("mpdu_attempt_failures", "mpdu_tx_attempt_failures_total"),
    ("mpdu_retries", "mpdu_retries_total"),
    ("ppdu_tx_count", "ppdu_tx_count_total"),
)
CUMULATIVE_VALIDATION_FIELDS = tuple(
    dict.fromkeys(
        raw
        for _, raw in CUMULATIVE_RATE_FIELDS
    )
) + (
    "mpdu_terminal_drops_total",
    "mpdu_retry_limit_drops_total",
    "mpdu_lifetime_drops_total",
    "mpdu_queue_drops_total",
)


def _radio_columns(prefix: str) -> tuple[str, ...]:
    columns: list[str] = []
    for name, _ in RADIO_FIELDS:
        columns.extend((f"{prefix}_{name}", f"{prefix}_{name}_missing"))
    for name, _ in AGE_FIELDS:
        columns.extend((f"{prefix}_{name}", f"{prefix}_{name}_missing"))
    return tuple(columns)


CURRENT_RADIO_COLUMNS = _radio_columns("x_primary_delayed_current")

PHYSICS_COLUMNS = (
    "x_physics_working_rate_margin_5ms_bytes_per_us",
    "x_physics_working_rate_margin_20ms_bytes_per_us",
    "x_physics_ahead_clearance_margin_1ms_us",
    "x_physics_ahead_clearance_margin_5ms_us",
    "x_physics_queue_clearance_margin_1ms_us",
    "x_physics_queue_clearance_margin_5ms_us",
    "x_physics_ack_rate_trend_5ms_vs_20ms_per_us",
    "x_physics_submitted_progress_fraction",
    "x_physics_enqueued_progress_fraction",
    "x_physics_succeeded_progress_fraction",
    "x_physics_pending_progress_fraction",
    "x_physics_submitted_byte_progress_fraction",
    "x_physics_secondary_idle_minus_primary_5ms",
    "x_physics_secondary_idle_minus_primary_20ms",
    "x_physics_primary_busy_trend_5ms_vs_20ms",
    "x_physics_secondary_busy_trend_5ms_vs_20ms",
    "x_physics_joint_busy_trend_5ms_vs_20ms",
    "x_physics_is_i_frame",
    "x_physics_is_p_frame",
    "x_physics_i_working_rate_margin_5ms",
    "x_physics_p_working_rate_margin_5ms",
    "x_physics_i_queue_clearance_margin_5ms",
    "x_physics_p_queue_clearance_margin_5ms",
    "x_physics_i_secondary_idle_margin_20ms",
    "x_physics_p_secondary_idle_margin_20ms",
    "x_physics_i_joint_busy_trend",
    "x_physics_p_joint_busy_trend",
)


def _lag_columns(lag: int) -> tuple[str, ...]:
    prefix = f"x_primary_lag{lag}"
    columns: list[str] = []
    for window, _ in WINDOWS_US:
        columns.extend(f"{prefix}_{field}_{window}" for field in ROLLING_COUNT_FIELDS)
        columns.extend(f"{prefix}_{field}_{window}" for field in PHY_FRACTION_FIELDS)
        columns.extend(
            (
                f"{prefix}_mpdu_retry_ratio_{window}",
                f"{prefix}_mpdu_retry_structural_zero_{window}",
            )
        )
    columns.extend(_radio_columns(prefix))
    for window, _ in WINDOWS_US:
        columns.extend(
            f"x_secondary_lag{lag}_{field}_{window}"
            for field in PHY_FRACTION_FIELDS
        )
    columns.extend(
        f"x_primary_since_lag{lag}_{name}_per_ms"
        for name, _ in CUMULATIVE_RATE_FIELDS
    )
    return tuple(columns)


TEMPORAL_COLUMNS = tuple(
    column for lag in LAGS for column in _lag_columns(lag)
)
FEATURE_COLUMNS = (
    tuple(base.FEATURE_COLUMNS)
    + CURRENT_RADIO_COLUMNS
    + PHYSICS_COLUMNS
    + TEMPORAL_COLUMNS
)
NON_FEATURE_COLUMNS = tuple(base.NON_FEATURE_COLUMNS)
DATASET_COLUMNS = NON_FEATURE_COLUMNS + FEATURE_COLUMNS

EXTRA_RAW_FILES = ("secondary_airtime_events.csv",)
V1_ARTIFACTS = (
    "randomized_t2.csv",
    "randomized_t4_wait.csv",
    "dataset_metadata.json",
)


class TemporalDatasetError(base.DatasetError):
    """Raised when the temporal contract cannot be established exactly."""


@dataclass(frozen=True)
class RawDescriptor:
    """Identity of a validated raw run before its ledgers are loaded."""

    path: Path
    run_id: str
    seed: int
    run_number: int


@dataclass(frozen=True)
class Endpoint:
    """One paired delayed-polling endpoint at T2."""

    frame_id: int
    generation_time_ns: int
    sample_time_ns: int
    capture_time_ns: int
    primary_sample: dict[str, str]
    secondary_sample: dict[str, str]
    primary_poll: dict[str, str]
    secondary_poll: dict[str, str]


@dataclass(frozen=True)
class SecondaryInterval:
    """Half-open secondary reservation or PPDU interval."""

    start_ns: int
    stop_ns: int


@dataclass(frozen=True)
class RunTemporalContext:
    """Validated temporal endpoints and secondary-action intervals."""

    endpoints: dict[int, Endpoint]
    tx_intervals: tuple[SecondaryInterval, ...]
    reservation_intervals: tuple[SecondaryInterval, ...]
    source_hashes: dict[str, str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise TemporalDatasetError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TemporalDatasetError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise TemporalDatasetError(f"{path}: expected a JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _integer(row: dict[str, str], field: str, source: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, TypeError, ValueError) as error:
        raise TemporalDatasetError(f"{source}: invalid integer {field}") from error
    return value


def _finite(row: dict[str, str], field: str, source: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as error:
        raise TemporalDatasetError(f"{source}: invalid number {field}") from error
    if not math.isfinite(value):
        raise TemporalDatasetError(f"{source}: non-finite number {field}")
    return value


def _format(value: float) -> str:
    if not math.isfinite(value):
        raise TemporalDatasetError("internal non-finite derived feature")
    if value == 0:
        value = 0.0
    return format(value, ".17g")


def _require_columns(header: Iterable[str], required: Iterable[str], source: str) -> None:
    missing = sorted(set(required) - set(header))
    if missing:
        raise TemporalDatasetError(f"{source}: missing columns {', '.join(missing)}")


def _load_v1_contract(dataset_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Verify the complete v1 artifact closure before parsing any dataset row."""

    directory = dataset_dir.resolve()
    manifest_path = directory / "artifact_manifest.json"
    manifest = _read_json(manifest_path)
    if set(manifest) != {
        "manifest_schema_version",
        "hash_algorithm",
        "artifacts_sha256",
    }:
        raise TemporalDatasetError("v1 manifest has an unexpected schema")
    if (
        manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != HASH_ALGORITHM
    ):
        raise TemporalDatasetError("v1 manifest version or hash algorithm differs")
    hashes = manifest.get("artifacts_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(V1_ARTIFACTS):
        raise TemporalDatasetError("v1 manifest artifact closure differs")
    for name in V1_ARTIFACTS:
        expected = hashes.get(name)
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise TemporalDatasetError(f"v1 manifest has invalid hash for {name}")
        actual = _sha256(directory / name)
        if actual != expected:
            raise TemporalDatasetError(f"v1 artifact hash mismatch: {name}")

    metadata = _read_json(directory / "dataset_metadata.json")
    if metadata.get("dataset_schema_version") != base.DATASET_SCHEMA_VERSION:
        raise TemporalDatasetError("v1 dataset schema version differs")
    if metadata.get("feature_contract_id") != base.FEATURE_CONTRACT_ID:
        raise TemporalDatasetError("input is not the pinned v1 feature contract")
    feature_contract = metadata.get("feature_contract")
    if (
        not isinstance(feature_contract, dict)
        or feature_contract.get("feature_columns") != list(base.FEATURE_COLUMNS)
    ):
        raise TemporalDatasetError("v1 feature allowlist differs")
    if metadata.get("non_feature_columns") != list(base.NON_FEATURE_COLUMNS):
        raise TemporalDatasetError("v1 non-feature schema differs")
    comparison = metadata.get("comparisons", {}).get("T2")
    if not isinstance(comparison, dict) or comparison.get("file") != "randomized_t2.csv":
        raise TemporalDatasetError("v1 T2 comparison contract differs")
    split = metadata.get("split")
    if not isinstance(split, dict) or split.get("algorithm") != base.SPLIT_ALGORITHM:
        raise TemporalDatasetError("v1 grouped split contract differs")
    return metadata, {
        **{name: hashes[name] for name in V1_ARTIFACTS},
        "artifact_manifest.json": _sha256(manifest_path),
    }


def _expected_source_runs(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source_runs = metadata.get("source_runs")
    if not isinstance(source_runs, list) or not source_runs:
        raise TemporalDatasetError("v1 metadata has no source runs")
    expected: dict[str, dict[str, Any]] = {}
    identities: set[tuple[int, int]] = set()
    for item in source_runs:
        if not isinstance(item, dict):
            raise TemporalDatasetError("v1 source run is not an object")
        run_id = item.get("run_id")
        seed = item.get("seed")
        run_number = item.get("run_number")
        hashes = item.get("files_sha256")
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(seed, int)
            or isinstance(seed, bool)
            or not isinstance(run_number, int)
            or isinstance(run_number, bool)
            or not isinstance(hashes, dict)
            or set(hashes) != set(base.SOURCE_FILES)
        ):
            raise TemporalDatasetError("v1 source-run identity or hash closure differs")
        if run_id in expected or (seed, run_number) in identities:
            raise TemporalDatasetError("v1 source-run identity is duplicated")
        if any(
            not isinstance(hashes[name], str)
            or re.fullmatch(r"[0-9a-f]{64}", hashes[name]) is None
            for name in base.SOURCE_FILES
        ):
            raise TemporalDatasetError("v1 source-run hash is invalid")
        expected[run_id] = item
        identities.add((seed, run_number))
    return expected


def _discover_validated_runs(
    run_dirs: Sequence[Path | str], expected: dict[str, dict[str, Any]]
) -> list[RawDescriptor]:
    """Validate every discovered run before reading its configuration."""

    discovered = base._discover_run_dirs(run_dirs)
    descriptors: list[RawDescriptor] = []
    for path in discovered:
        base.validate_run(path)
        config = _read_json(path / "resolved_config.json")
        run_id = config.get("run_id")
        seed = config.get("seed")
        run_number = config.get("run")
        if (
            not isinstance(run_id, str)
            or not isinstance(seed, int)
            or isinstance(seed, bool)
            or not isinstance(run_number, int)
            or isinstance(run_number, bool)
        ):
            raise TemporalDatasetError(f"{path}: invalid raw run identity")
        descriptors.append(RawDescriptor(path, run_id, seed, run_number))
    if len({descriptor.run_id for descriptor in descriptors}) != len(descriptors):
        raise TemporalDatasetError("raw inputs contain duplicate run_id values")
    if {descriptor.run_id for descriptor in descriptors} != set(expected):
        raise TemporalDatasetError("raw run_id set does not exactly match v1 metadata")
    for descriptor in descriptors:
        item = expected[descriptor.run_id]
        if (descriptor.seed, descriptor.run_number) != (
            item["seed"],
            item["run_number"],
        ):
            raise TemporalDatasetError(
                f"{descriptor.path}: raw seed/run identity differs from v1 metadata"
            )
    return sorted(descriptors, key=lambda item: (item.seed, item.run_number, item.run_id))


def _poll_integer(row: dict[str, str], field: str, source: str) -> int:
    value = _integer(row, field, source)
    if value < 0:
        raise TemporalDatasetError(f"{source}: negative {field}")
    return value


def _validate_rolling(row: dict[str, str], source: str) -> None:
    for window, coverage_us in WINDOWS_US:
        if _poll_integer(row, f"history_coverage_{window}_us", source) != coverage_us:
            raise TemporalDatasetError(f"{source}: incomplete {window} history coverage")
        attempts = _poll_integer(row, f"mpdu_attempts_{window}", source)
        failures = _poll_integer(row, f"mpdu_attempt_failures_{window}", source)
        retries = _poll_integer(row, f"mpdu_retries_{window}", source)
        acks = _poll_integer(row, f"mpdu_positive_acks_{window}", source)
        _poll_integer(row, f"acknowledged_mac_service_bytes_{window}", source)
        # ACK/failure callbacks may fall just inside a window when the
        # corresponding attempt began just outside it.  Retry count, however,
        # is defined on the attempt ledger and cannot exceed attempts.
        if retries > attempts:
            raise TemporalDatasetError(f"{source}: impossible {window} retry counters")
        fractions = [_finite(row, f"{field}_{window}", source) for field in PHY_FRACTION_FIELDS]
        if any(value < 0 or value > 1 for value in fractions):
            raise TemporalDatasetError(f"{source}: invalid {window} PHY fraction")
        if not math.isclose(sum(fractions), 1.0, rel_tol=0.0, abs_tol=2e-6):
            raise TemporalDatasetError(f"{source}: {window} PHY fractions do not sum to one")


def _validate_poll_endpoint(
    frame_id: int,
    sample: dict[str, str],
    poll: dict[str, str],
    polling_interval_ns: int,
    report_delay_ns: int,
    source: str,
) -> tuple[int, int, int]:
    if sample.get("sample_stage") != "T2" or poll.get("sample_stage") != "T2":
        raise TemporalDatasetError(f"{source}: endpoint is not T2")
    if (
        _integer(sample, "sample_offset_us", source) != 2_000
        or _integer(poll, "sample_offset_us", source) != 2_000
    ):
        raise TemporalDatasetError(f"{source}: T2 offset differs")
    generation_ns = _integer(sample, "generation_time_ns", source)
    sample_ns = _integer(sample, "sample_time_ns", source)
    if sample_ns != generation_ns + T2_OFFSET_NS:
        raise TemporalDatasetError(f"{source}: T2 sample timing differs")
    capture_ns = _integer(poll, "capture_time_ns", source)
    available_ns = _integer(poll, "available_time_ns", source)
    if capture_ns % polling_interval_ns != 0:
        raise TemporalDatasetError(f"{source}: polling capture cadence differs")
    if capture_ns + report_delay_ns != available_ns or available_ns > sample_ns:
        raise TemporalDatasetError(f"{source}: polling availability timing differs")
    staleness_ns = sample_ns - capture_ns
    if not (report_delay_ns <= staleness_ns < report_delay_ns + polling_interval_ns):
        raise TemporalDatasetError(f"{source}: polling staleness is outside one cadence")
    if _integer(poll, "staleness_us", source) != staleness_ns // 1_000:
        raise TemporalDatasetError(f"{source}: polling staleness field differs")
    if poll.get("report_available") != "1":
        raise TemporalDatasetError(f"{source}: polling report is unavailable")
    watermark_ns = _integer(poll, "latest_feature_event_time_ns", source)
    sequence = _integer(poll, "latest_feature_event_sequence", source)
    if watermark_ns < 0 or watermark_ns > capture_ns or sequence < 0:
        raise TemporalDatasetError(f"{source}: invalid polling watermark")
    _validate_rolling(poll, source)
    for raw in CUMULATIVE_VALIDATION_FIELDS:
        _poll_integer(poll, raw, source)
    return generation_ns, sample_ns, capture_ns


def _build_endpoints(run: base.RunInput) -> dict[int, Endpoint]:
    telemetry = run.config.get("predictionTelemetry")
    stream = run.config.get("stream")
    if not isinstance(telemetry, dict) or not isinstance(stream, dict):
        raise TemporalDatasetError(f"{run.path}: missing telemetry or stream contract")
    interval_us = telemetry.get("polling_interval_us")
    delay_us = telemetry.get("polling_report_delay_us")
    fps = stream.get("fps")
    if (
        not isinstance(interval_us, int)
        or isinstance(interval_us, bool)
        or interval_us != 1_000
        or not isinstance(delay_us, int)
        or isinstance(delay_us, bool)
        or delay_us != 1_000
        or telemetry.get("history_windows_us") != [1_000, 5_000, 20_000]
        or not isinstance(fps, int)
        or isinstance(fps, bool)
        or fps <= 0
    ):
        raise TemporalDatasetError(f"{run.path}: temporal telemetry contract differs")
    interval_ns = interval_us * 1_000
    delay_ns = delay_us * 1_000
    frame_ids = sorted(run.frames)
    if frame_ids != list(range(len(frame_ids))):
        raise TemporalDatasetError(f"{run.path}: frame positions are not contiguous from zero")

    endpoints: dict[int, Endpoint] = {}
    previous_poll: dict[tuple[int, int], tuple[int, int, tuple[int, ...]]] = {}
    previous_live: dict[tuple[int, int], tuple[int, int]] = {}
    anchor_generation_ns: int | None = None
    for frame_id in frame_ids:
        primary_sample = run.samples[(frame_id, "T2", *PRIMARY_PATH)]
        secondary_sample = run.samples[(frame_id, "T2", *SECONDARY_PATH)]
        primary_poll = run.polling[(frame_id, "T2", *PRIMARY_PATH)]
        secondary_poll = run.polling[(frame_id, "T2", *SECONDARY_PATH)]
        source = f"{run.path}: frame {frame_id} T2"
        primary_timing = _validate_poll_endpoint(
            frame_id, primary_sample, primary_poll, interval_ns, delay_ns, source + " primary"
        )
        secondary_timing = _validate_poll_endpoint(
            frame_id, secondary_sample, secondary_poll, interval_ns, delay_ns, source + " secondary"
        )
        if primary_timing != secondary_timing:
            raise TemporalDatasetError(f"{source}: paired polling timing differs")
        generation_ns, sample_ns, capture_ns = primary_timing
        if anchor_generation_ns is None:
            anchor_generation_ns = generation_ns
        expected_generation_ns = anchor_generation_ns + (frame_id * 1_000_000_000 + fps // 2) // fps
        if generation_ns != expected_generation_ns:
            raise TemporalDatasetError(f"{source}: frame cadence/position differs")
        for path, row in ((PRIMARY_PATH, primary_sample), (SECONDARY_PATH, secondary_sample)):
            if _integer(row, "sample_time_ns", source) != sample_ns:
                raise TemporalDatasetError(f"{source}: paired sample time differs")
            live_watermark = _integer(row, "latest_feature_event_time_ns", source)
            live_sequence = _integer(row, "latest_feature_event_sequence", source)
            if live_watermark < 0 or live_watermark > sample_ns or live_sequence < 0:
                raise TemporalDatasetError(f"{source}: invalid live watermark")
            old_live = previous_live.get(path)
            if old_live is not None and (
                live_watermark < old_live[0] or live_sequence < old_live[1]
            ):
                raise TemporalDatasetError(f"{source}: live watermark reset or reordering")
            previous_live[path] = (live_watermark, live_sequence)

        for path, poll in ((PRIMARY_PATH, primary_poll), (SECONDARY_PATH, secondary_poll)):
            watermark = _integer(poll, "latest_feature_event_time_ns", source)
            sequence = _integer(poll, "latest_feature_event_sequence", source)
            counters = (
                tuple(
                    _integer(poll, raw, source)
                    for raw in CUMULATIVE_VALIDATION_FIELDS
                )
                if path == PRIMARY_PATH
                else ()
            )
            old = previous_poll.get(path)
            if old is not None:
                if watermark < old[0] or sequence < old[1]:
                    raise TemporalDatasetError(f"{source}: polling watermark reset or reordering")
                if counters and any(new < prior for new, prior in zip(counters, old[2])):
                    raise TemporalDatasetError(f"{source}: primary cumulative counter reset")
            previous_poll[path] = (watermark, sequence, counters)

        endpoints[frame_id] = Endpoint(
            frame_id,
            generation_ns,
            sample_ns,
            capture_ns,
            primary_sample,
            secondary_sample,
            primary_poll,
            secondary_poll,
        )
    return endpoints


def _read_secondary_intervals(
    run: base.RunInput,
) -> tuple[tuple[SecondaryInterval, ...], tuple[SecondaryInterval, ...], dict[str, str]]:
    events_name = EXTRA_RAW_FILES[0]
    events_path = run.path / events_name
    header, rows = base._read_csv(events_path)
    _require_columns(
        header,
        {
            "run_id",
            "time_ns",
            "path_id",
            "ppdu_duration_us",
            "tagged_mpdu_bytes",
            "frame_ids",
            "mixed_ppdu",
            "cumulative_tagged_airtime_us",
        },
        str(events_path),
    )
    launched = {
        frame_id
        for frame_id, row in run.executions.items()
        if row.get("launched") == "1"
    }
    tx_intervals: list[SecondaryInterval] = []
    previous_start = -1
    for row in rows:
        source = f"{events_path}: event"
        if row.get("run_id") != run.run_id or _integer(row, "path_id", source) != SECONDARY_PATH[0]:
            raise TemporalDatasetError(f"{source}: identity/path differs")
        start_ns = _integer(row, "time_ns", source)
        try:
            duration_ns = int(
                (Decimal(row["ppdu_duration_us"]) * Decimal(1_000)).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
        except (KeyError, InvalidOperation, ValueError) as error:
            raise TemporalDatasetError(f"{source}: invalid PPDU duration") from error
        if start_ns < previous_start or duration_ns <= 0:
            raise TemporalDatasetError(f"{source}: event ordering/duration differs")
        previous_start = start_ns
        frame_tokens = row.get("frame_ids", "").split(";")
        if not frame_tokens or any(not token.isdigit() for token in frame_tokens):
            raise TemporalDatasetError(f"{source}: invalid tagged frame IDs")
        if not {int(token) for token in frame_tokens} <= launched:
            raise TemporalDatasetError(f"{source}: event references an unlaunched frame")
        tx_intervals.append(SecondaryInterval(start_ns, start_ns + duration_ns))

    reservation_intervals: list[SecondaryInterval] = []
    for frame_id in sorted(launched):
        execution = run.executions[frame_id]
        settlement = run.settlements.get(frame_id)
        source = f"{run.path}: frame {frame_id} secondary reservation"
        if settlement is None:
            raise TemporalDatasetError(f"{source}: missing settlement")
        start_ns = _integer(execution, "secondary_sample_time_ns", source)
        stop_ns = _integer(settlement, "settlement_time_ns", source)
        if start_ns < 0 or stop_ns <= start_ns:
            raise TemporalDatasetError(f"{source}: invalid active interval")
        reservation_intervals.append(SecondaryInterval(start_ns, stop_ns))
    return (
        tuple(tx_intervals),
        tuple(reservation_intervals),
        {events_name: _sha256(events_path)},
    )


def _build_context(run: base.RunInput) -> RunTemporalContext:
    endpoints = _build_endpoints(run)
    tx, reservations, extra_hashes = _read_secondary_intervals(run)
    return RunTemporalContext(
        endpoints,
        tx,
        reservations,
        {**run.source_hashes, **extra_hashes},
    )


def _endpoint_dirty(context: RunTemporalContext, endpoint_ns: int) -> tuple[bool, bool]:
    window_start_ns = endpoint_ns - SECONDARY_CLEAN_WINDOW_NS
    direct_tx = any(
        interval.start_ns <= endpoint_ns and interval.stop_ns > window_start_ns
        for interval in context.tx_intervals
    )
    active_reservation = any(
        interval.start_ns <= endpoint_ns < interval.stop_ns
        for interval in context.reservation_intervals
    )
    return direct_tx, active_reservation


def _radio_features(
    prefix: str,
    poll: dict[str, str],
    capture_ns: int,
    source: str,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, raw in RADIO_FIELDS:
        text = poll.get(raw, "")
        if text == "":
            values[f"{prefix}_{name}"] = ""
            values[f"{prefix}_{name}_missing"] = "1"
            continue
        number = _finite(poll, raw, source)
        if number < 0:
            raise TemporalDatasetError(f"{source}: negative {raw}")
        values[f"{prefix}_{name}"] = _format(number)
        values[f"{prefix}_{name}_missing"] = "0"
    for name, raw in AGE_FIELDS:
        text = poll.get(raw, "")
        if text == "":
            values[f"{prefix}_{name}"] = ""
            values[f"{prefix}_{name}_missing"] = "1"
            continue
        event_ns = _integer(poll, raw, source)
        if event_ns < 0 or event_ns > capture_ns:
            raise TemporalDatasetError(f"{source}: future or negative {raw}")
        age_us = min((capture_ns - event_ns) / 1_000.0, LAST_EVENT_AGE_CAP_US)
        values[f"{prefix}_{name}"] = _format(age_us)
        values[f"{prefix}_{name}_missing"] = "0"
    return values


def _ratio(numerator: float, denominator: float, source: str) -> float:
    if numerator < 0 or denominator <= 0:
        raise TemporalDatasetError(f"{source}: invalid ratio operands")
    value = numerator / denominator
    if not math.isfinite(value):
        raise TemporalDatasetError(f"{source}: non-finite ratio")
    return value


def _physics_features(row: dict[str, str], endpoint: Endpoint, source: str) -> dict[str, str]:
    primary = endpoint.primary_poll
    secondary = endpoint.secondary_poll
    pending_bytes = _finite(row, "x_primary_frame_mac_service_bytes_pending_primary", source)
    ahead_bytes = _finite(row, "x_primary_mac_service_bytes_ahead_of_frame", source)
    queue_bytes = _finite(row, "x_primary_mac_queue_service_bytes", source)
    slack_us = _finite(row, "x_f0_deadline_slack_us", source)
    packet_count = _finite(row, "x_f0_frame_packet_count", source)
    frame_bytes = _finite(row, "x_f0_frame_size_bytes", source)
    if (
        min(pending_bytes, ahead_bytes, queue_bytes, slack_us, packet_count, frame_bytes)
        < 0
        or packet_count <= 0
        or frame_bytes <= 0
    ):
        raise TemporalDatasetError(f"{source}: invalid current frame/queue state")

    required_rate = pending_bytes / max(slack_us, 1.0)
    acked_bytes = {
        window: _finite(primary, f"acknowledged_mac_service_bytes_{window}", source)
        for window, _ in WINDOWS_US
    }
    working_rates = {
        window: acked_bytes[window] / coverage_us
        for window, coverage_us in WINDOWS_US
    }
    work_margin_5 = working_rates["5ms"] - required_rate
    work_margin_20 = working_rates["20ms"] - required_rate

    def clearance_margin(bytes_to_clear: float, window: str) -> float:
        rate = max(working_rates[window], MIN_WORKING_RATE_BYTES_PER_US)
        clearance_us = min(bytes_to_clear / rate, CLEARANCE_TIME_CAP_US)
        return slack_us - clearance_us

    ack_rate_trend = (
        _finite(primary, "mpdu_positive_acks_5ms", source) / 5_000.0
        - _finite(primary, "mpdu_positive_acks_20ms", source) / 20_000.0
    )
    denominator_fields = {
        "submitted": "x_f0_packets_submitted",
        "enqueued": "x_primary_frame_packets_mac_enqueued",
        "succeeded": "x_primary_frame_packets_tx_succeeded",
        "pending": "x_primary_frame_packets_pending_primary",
    }
    progress = {
        name: _ratio(_finite(row, field, source), packet_count, source)
        for name, field in denominator_fields.items()
    }
    if any(value > 1.0 + 1e-12 for value in progress.values()):
        raise TemporalDatasetError(f"{source}: frame progress exceeds one")
    submitted_byte_progress = _ratio(
        _finite(row, "x_f0_application_socket_packet_bytes_submitted", source),
        frame_bytes,
        source,
    )
    # Socket packet bytes include protocol payload framing and may modestly
    # exceed source bytes; cap the progress feature without changing raw F0.
    submitted_byte_progress = min(submitted_byte_progress, 1.0)

    p_idle_5 = _finite(primary, "phy_idle_fraction_5ms", source)
    p_idle_20 = _finite(primary, "phy_idle_fraction_20ms", source)
    s_idle_5 = _finite(secondary, "phy_idle_fraction_5ms", source)
    s_idle_20 = _finite(secondary, "phy_idle_fraction_20ms", source)
    p_busy_5 = _finite(primary, "phy_busy_fraction_5ms", source)
    p_busy_20 = _finite(primary, "phy_busy_fraction_20ms", source)
    s_busy_5 = _finite(secondary, "phy_busy_fraction_5ms", source)
    s_busy_20 = _finite(secondary, "phy_busy_fraction_20ms", source)
    idle_margin_5 = s_idle_5 - p_idle_5
    idle_margin_20 = s_idle_20 - p_idle_20
    primary_busy_trend = p_busy_5 - p_busy_20
    secondary_busy_trend = s_busy_5 - s_busy_20
    joint_busy_trend = p_busy_5 * s_busy_5 - p_busy_20 * s_busy_20
    frame_type = row.get("x_f0_frame_type")
    if frame_type not in {"I_FRAME", "P_FRAME"}:
        raise TemporalDatasetError(f"{source}: unsupported frame type")
    is_i = float(frame_type == "I_FRAME")
    is_p = float(frame_type == "P_FRAME")
    queue_margin_5 = clearance_margin(queue_bytes, "5ms")

    raw_values = (
        work_margin_5,
        work_margin_20,
        clearance_margin(ahead_bytes, "1ms"),
        clearance_margin(ahead_bytes, "5ms"),
        clearance_margin(queue_bytes, "1ms"),
        queue_margin_5,
        ack_rate_trend,
        progress["submitted"],
        progress["enqueued"],
        progress["succeeded"],
        progress["pending"],
        submitted_byte_progress,
        idle_margin_5,
        idle_margin_20,
        primary_busy_trend,
        secondary_busy_trend,
        joint_busy_trend,
        is_i,
        is_p,
        is_i * work_margin_5,
        is_p * work_margin_5,
        is_i * queue_margin_5,
        is_p * queue_margin_5,
        is_i * idle_margin_20,
        is_p * idle_margin_20,
        is_i * joint_busy_trend,
        is_p * joint_busy_trend,
    )
    return {
        column: _format(value)
        for column, value in zip(PHYSICS_COLUMNS, raw_values, strict=True)
    }


def _lag_features(current: Endpoint, lagged: Endpoint, lag: int, source: str) -> dict[str, str]:
    values: dict[str, str] = {}
    prefix = f"x_primary_lag{lag}"
    primary = lagged.primary_poll
    secondary = lagged.secondary_poll
    for window, _ in WINDOWS_US:
        for field in ROLLING_COUNT_FIELDS:
            raw = f"{field}_{window}"
            number = _finite(primary, raw, source)
            if number < 0:
                raise TemporalDatasetError(f"{source}: negative {raw}")
            values[f"{prefix}_{raw}"] = _format(number)
        for field in PHY_FRACTION_FIELDS:
            raw = f"{field}_{window}"
            values[f"{prefix}_{raw}"] = _format(_finite(primary, raw, source))
        attempts = _finite(primary, f"mpdu_attempts_{window}", source)
        retries = _finite(primary, f"mpdu_retries_{window}", source)
        if attempts < 0 or retries < 0 or retries > attempts:
            raise TemporalDatasetError(f"{source}: impossible retry counts")
        structural_zero = attempts == 0
        retry_ratio = 0.0 if structural_zero else retries / attempts
        values[f"{prefix}_mpdu_retry_ratio_{window}"] = _format(retry_ratio)
        values[f"{prefix}_mpdu_retry_structural_zero_{window}"] = str(int(structural_zero))

    values.update(_radio_features(prefix, primary, lagged.capture_time_ns, source))
    for window, _ in WINDOWS_US:
        for field in PHY_FRACTION_FIELDS:
            raw = f"{field}_{window}"
            values[f"x_secondary_lag{lag}_{raw}"] = _format(_finite(secondary, raw, source))

    span_ns = current.capture_time_ns - lagged.capture_time_ns
    if span_ns <= 0:
        raise TemporalDatasetError(f"{source}: nonpositive exact lag span")
    span_ms = span_ns / 1_000_000.0
    for name, raw in CUMULATIVE_RATE_FIELDS:
        current_value = _integer(current.primary_poll, raw, source)
        lagged_value = _integer(primary, raw, source)
        delta = current_value - lagged_value
        if delta < 0:
            raise TemporalDatasetError(f"{source}: primary cumulative counter reset")
        values[f"x_primary_since_lag{lag}_{name}_per_ms"] = _format(delta / span_ms)
    expected_columns = _lag_columns(lag)
    if tuple(values) != expected_columns:
        raise TemporalDatasetError(f"internal lag-{lag} feature order differs")
    return values


def _validate_base_feature_values(row: dict[str, str], source: str) -> None:
    for column in base.FEATURE_COLUMNS:
        value = row.get(column)
        if value is None:
            raise TemporalDatasetError(f"{source}: missing base feature {column}")
        if column == "x_f0_frame_type":
            if value not in {"I_FRAME", "P_FRAME"}:
                raise TemporalDatasetError(f"{source}: invalid frame type")
        elif value != "":
            try:
                number = float(value)
            except ValueError as error:
                raise TemporalDatasetError(f"{source}: invalid base feature {column}") from error
            if not math.isfinite(number):
                raise TemporalDatasetError(f"{source}: non-finite base feature {column}")


def _temporal_row(
    v1_row: dict[str, str],
    current: Endpoint,
    lagged: dict[int, Endpoint],
    source: str,
) -> dict[str, str]:
    _validate_base_feature_values(v1_row, source)
    row = {column: v1_row[column] for column in base.DATASET_COLUMNS}
    row.update(
        _radio_features(
            "x_primary_delayed_current",
            current.primary_poll,
            current.capture_time_ns,
            source,
        )
    )
    row.update(_physics_features(v1_row, current, source))
    for lag in LAGS:
        row.update(_lag_features(current, lagged[lag], lag, source))
    if tuple(row) != DATASET_COLUMNS:
        raise TemporalDatasetError("internal temporal dataset column order differs")
    return row


def _iter_v1_groups(path: Path) -> Iterator[tuple[tuple[int, int, str], list[dict[str, str]]]]:
    try:
        source: TextIO = path.open(newline="", encoding="utf-8")
    except OSError as error:
        raise TemporalDatasetError(f"cannot read {path}: {error}") from error
    with source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or tuple(reader.fieldnames) != tuple(base.DATASET_COLUMNS):
            raise TemporalDatasetError("v1 T2 CSV schema/order differs")

        def key(row: dict[str, str]) -> tuple[int, int, str]:
            return (
                _integer(row, "seed", str(path)),
                _integer(row, "run_number", str(path)),
                row.get("run_id", ""),
            )

        previous_key: tuple[int, int, str] | None = None
        for group_key, group in itertools.groupby(reader, key=key):
            if previous_key is not None and group_key <= previous_key:
                raise TemporalDatasetError("v1 T2 rows are not grouped in identity order")
            rows = list(group)
            frame_ids = [_integer(row, "frame_id", str(path)) for row in rows]
            if frame_ids != sorted(frame_ids) or len(frame_ids) != len(set(frame_ids)):
                raise TemporalDatasetError("v1 T2 frame keys are duplicated or reordered")
            previous_key = group_key
            yield group_key, rows


def _assert_v1_row_exact(
    run: base.RunInput,
    row: dict[str, str],
    split_role: str,
    treatment_probability: float,
) -> int:
    frame_id = _integer(row, "frame_id", f"{run.path}: v1 row")
    if frame_id not in run.frames:
        raise TemporalDatasetError(f"{run.path}: v1 row references unknown frame {frame_id}")
    regenerated = base._dataset_row(
        run,
        frame_id,
        "T2",
        split_role,
        treatment_probability,
    )
    if regenerated != row:
        differing = sorted(
            column for column in base.DATASET_COLUMNS if regenerated.get(column) != row.get(column)
        )
        raise TemporalDatasetError(
            f"{run.path}: v1/raw join differs for frame {frame_id}: {', '.join(differing[:8])}"
        )
    return frame_id


def _split_assignments(metadata: dict[str, Any]) -> dict[tuple[int, int], str]:
    rows = metadata.get("split", {}).get("assignments")
    if not isinstance(rows, list):
        raise TemporalDatasetError("v1 split assignments are missing")
    result: dict[tuple[int, int], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TemporalDatasetError("v1 split assignment is invalid")
        identity = (row.get("seed"), row.get("run_number"))
        role = row.get("split_role")
        if (
            not all(isinstance(value, int) and not isinstance(value, bool) for value in identity)
            or role not in {"train", "calibration", "test"}
            or identity in result
        ):
            raise TemporalDatasetError("v1 split assignment differs")
        result[identity] = role
    return result


def build_temporal_dataset(
    v1_dataset_dir: Path | str,
    run_dirs: Sequence[Path | str],
    output_dir: Path | str,
) -> dict[str, Any]:
    """Build and atomically publish the strictly verified temporal T2 dataset."""

    v1_dir = Path(v1_dataset_dir).resolve()
    metadata, v1_hashes = _load_v1_contract(v1_dir)
    expected = _expected_source_runs(metadata)
    descriptors = _discover_validated_runs(run_dirs, expected)
    splits = _split_assignments(metadata)
    identities = {(item.seed, item.run_number) for item in descriptors}
    if set(splits) != identities:
        raise TemporalDatasetError("v1 split identity set differs from raw runs")

    output = Path(output_dir).resolve()
    if output.exists():
        raise TemporalDatasetError(f"refusing to overwrite existing output directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    csv_path = temporary / OUTPUT_CSV
    filters: dict[str, Any] = {
        "candidate_v1_t2_rows": 0,
        "excluded_lag8_warmup": 0,
        "excluded_secondary_direct_tx_dirty": 0,
        "excluded_secondary_active_reservation": 0,
        "excluded_any_secondary_action_dirty": 0,
        "included_rows": 0,
        "dirty_endpoint_counts": {
            "current": {"direct_tx": 0, "active_reservation": 0},
            "lag1": {"direct_tx": 0, "active_reservation": 0},
            "lag3": {"direct_tx": 0, "active_reservation": 0},
            "lag8": {"direct_tx": 0, "active_reservation": 0},
        },
    }
    raw_provenance: list[dict[str, Any]] = []
    row_count_seen = 0
    try:
        groups = iter(_iter_v1_groups(v1_dir / "randomized_t2.csv"))
        next_group = next(groups, None)
        with csv_path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=DATASET_COLUMNS,
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            for descriptor in descriptors:
                expected_key = (descriptor.seed, descriptor.run_number, descriptor.run_id)
                if next_group is None or next_group[0] != expected_key:
                    raise TemporalDatasetError(
                        f"v1 T2 rows do not join raw run exactly: {descriptor.run_id}"
                    )
                v1_rows = next_group[1]
                row_count_seen += len(v1_rows)
                run = base._load_run(descriptor.path)
                item = expected[descriptor.run_id]
                if run.source_hashes != item["files_sha256"]:
                    raise TemporalDatasetError(
                        f"{descriptor.path}: raw source hashes differ from v1 metadata"
                    )
                context = _build_context(run)
                raw_provenance.append(
                    {
                        "run_id": run.run_id,
                        "seed": run.seed,
                        "run_number": run.run_number,
                        "path": str(run.path),
                        "files_sha256": context.source_hashes,
                    }
                )
                probabilities = run.config["randomizedIntervention"]["arm_probabilities"]
                p_t2 = float(probabilities["FULL_COPY_T2"])
                p_control = float(probabilities["CONTROL"])
                treatment_probability = p_t2 / (p_t2 + p_control)
                split_role = splits[(run.seed, run.run_number)]
                for v1_row in v1_rows:
                    filters["candidate_v1_t2_rows"] += 1
                    frame_id = _assert_v1_row_exact(
                        run, v1_row, split_role, treatment_probability
                    )
                    if frame_id < max(LAGS):
                        filters["excluded_lag8_warmup"] += 1
                        continue
                    current = context.endpoints.get(frame_id)
                    lagged = {
                        lag: context.endpoints.get(frame_id - lag) for lag in LAGS
                    }
                    if current is None or any(endpoint is None for endpoint in lagged.values()):
                        raise TemporalDatasetError(
                            f"{run.path}: frame {frame_id} has a missing exact temporal endpoint"
                        )
                    endpoint_map = {
                        "current": current,
                        **{f"lag{lag}": lagged[lag] for lag in LAGS},
                    }
                    dirty_tx = False
                    dirty_reservation = False
                    for name, endpoint in endpoint_map.items():
                        assert endpoint is not None
                        tx_dirty, reservation_dirty = _endpoint_dirty(
                            context, endpoint.capture_time_ns
                        )
                        filters["dirty_endpoint_counts"][name]["direct_tx"] += int(tx_dirty)
                        filters["dirty_endpoint_counts"][name][
                            "active_reservation"
                        ] += int(reservation_dirty)
                        dirty_tx = dirty_tx or tx_dirty
                        dirty_reservation = dirty_reservation or reservation_dirty
                    if dirty_tx:
                        filters["excluded_secondary_direct_tx_dirty"] += 1
                    if dirty_reservation:
                        filters["excluded_secondary_active_reservation"] += 1
                    if dirty_tx or dirty_reservation:
                        filters["excluded_any_secondary_action_dirty"] += 1
                        continue
                    temporal = _temporal_row(
                        v1_row,
                        current,
                        {lag: endpoint for lag, endpoint in lagged.items() if endpoint is not None},
                        f"{run.path}: frame {frame_id}",
                    )
                    writer.writerow(temporal)
                    filters["included_rows"] += 1
                next_group = next(groups, None)
            if next_group is not None:
                raise TemporalDatasetError("v1 T2 contains a run absent from raw inputs")

        expected_row_count = metadata["comparisons"]["T2"].get("row_count")
        if (
            row_count_seen != expected_row_count
            or filters["candidate_v1_t2_rows"] != expected_row_count
        ):
            raise TemporalDatasetError("v1 T2 row count differs from metadata")
        if filters["included_rows"] <= 0:
            raise TemporalDatasetError("temporal action-clean filter retained no rows")

        temporal_metadata: dict[str, Any] = {
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "feature_contract_id": FEATURE_CONTRACT_ID,
            "comparison": {
                "file": OUTPUT_CSV,
                "analysis_stage": "T2",
                "arms": ["CONTROL", "FULL_COPY_T2"],
                "estimand": "binary_assignment_ITT_on_action_clean_temporal_population",
                "row_count": filters["included_rows"],
                "population": (
                    "v1 common-T2-eligible rows with exact lags 1/3/8 and "
                    "action-clean secondary delayed-PHY endpoints"
                ),
            },
            "validation": {
                "authoritative_validator": "validate_outputs.validate_run",
                "every_raw_run_validated_before_augmenter_source_reads": True,
                "v1_rows_regenerated_and_joined_exactly_by_run_id_frame_id": True,
                "v1_artifact_manifest_verified_before_row_reads": True,
                "raw_v1_source_hashes_verified": True,
            },
            "input_v1": {
                "path": str(v1_dir),
                "feature_contract_id": base.FEATURE_CONTRACT_ID,
                "artifacts_sha256": v1_hashes,
            },
            "feature_contract": {
                "feature_columns": list(FEATURE_COLUMNS),
                "base_feature_columns": list(base.FEATURE_COLUMNS),
                "new_feature_columns": list(FEATURE_COLUMNS[len(base.FEATURE_COLUMNS):]),
                "categorical_features": ["x_f0_frame_type"],
                "exact_frame_lags": list(LAGS),
                "endpoint": (
                    "T2 polling capture; capture + 1 ms = available <= sample, "
                    "with sample-capture staleness in [1,2) ms"
                ),
                "history_coverage_us": [coverage for _, coverage in WINDOWS_US],
                "last_event_age_cap_us": LAST_EVENT_AGE_CAP_US,
                "clearance_time_cap_us": CLEARANCE_TIME_CAP_US,
                "minimum_working_rate_bytes_per_us": MIN_WORKING_RATE_BYTES_PER_US,
                "secondary_action_cleanliness": {
                    "direct_self_tx": (
                        "no tagged secondary PPDU interval overlaps the 20 ms ending "
                        "at any current/lag polling capture"
                    ),
                    "active_or_queued_tagged_reservation": (
                        "no launched secondary reservation is active in the half-open "
                        "launch-to-settlement interval at any polling capture"
                    ),
                    "cleanliness_flags_are_features": False,
                },
                "physics_formulas": {
                    "working_rate_margin": (
                        "acknowledged bytes/window us - pending bytes/max(slack us,1)"
                    ),
                    "clearance_margin": (
                        "slack us - min(bytes/max(working rate,1e-6),1e6 us)"
                    ),
                    "joint_busy": "primary busy fraction * secondary busy fraction",
                    "trend": "5 ms value - 20 ms value",
                    "explicit_frame_type_interactions": True,
                },
                "missing_values": (
                    "empty CSV is retained only for genuinely unavailable values; "
                    "radio/last-event fields have explicit missing indicators"
                ),
                "conservative_exclusions": [
                    "identifiers and absolute timestamps from X",
                    "polling watermarks and raw lifetime totals from X",
                    "assignment, execution, compliance, outcomes, and settlements from X",
                    "receiver outcomes from all prior frames",
                    "secondary tagged totals, MCS, retry, and ACK state",
                    "current or lagged ACK signal",
                    "cleanliness/filter bits",
                ],
            },
            "non_feature_columns": list(NON_FEATURE_COLUMNS),
            "split": metadata["split"],
            "filter_counts": filters,
            "build_identity": metadata.get("build_identity"),
            "environment_compatibility": metadata.get("environment_compatibility"),
            "design_contract_sha256": metadata.get("design_contract_sha256"),
            "source_runs": raw_provenance,
        }
        _write_json(temporary / OUTPUT_METADATA, temporal_metadata)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": HASH_ALGORITHM,
            "artifacts_sha256": {
                OUTPUT_CSV: _sha256(csv_path),
                OUTPUT_METADATA: _sha256(temporary / OUTPUT_METADATA),
            },
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.rename(temporary, output)
        return temporal_metadata
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="build a strictly verified action-clean temporal T2 dataset"
    )
    parser.add_argument("v1_dataset_dir", type=Path)
    parser.add_argument(
        "run_dirs",
        nargs="+",
        type=Path,
        help="raw run directories or roots recursively containing them",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    metadata = build_temporal_dataset(
        args.v1_dataset_dir,
        args.run_dirs,
        args.output_dir,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "run_count": len(metadata["source_runs"]),
                "candidate_rows": metadata["filter_counts"]["candidate_v1_t2_rows"],
                "included_rows": metadata["comparison"]["row_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
