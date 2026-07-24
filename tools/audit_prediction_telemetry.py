#!/usr/bin/env python3
"""Build machine-readable Increment-1 prediction telemetry audit evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from validate_outputs import validate_run


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _key(row: dict[str, str]) -> tuple[int, int, int, int]:
    return (
        int(row["path_id"]),
        int(row["copy_id"]),
        int(row["frame_id"]),
        int(row["packet_index"]),
    )


def _frame_key(row: dict[str, str]) -> tuple[int, int, int]:
    return (int(row["path_id"]), int(row["copy_id"]), int(row["frame_id"]))


def _event_excerpt(row: dict[str, str]) -> dict[str, Any]:
    return {
        key: row[key] if row[key] != "" else None
        for key in (
            "event_sequence",
            "event_time_ns",
            "event_type",
            "path_id",
            "copy_id",
            "frame_id",
            "packet_index",
            "attempt_number",
            "finalizes_attempt_success",
            "mac_service_bytes",
        )
    }


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-6)


def _lifecycle_evidence(
    samples: list[dict[str, str]],
    events: list[dict[str, str]],
) -> dict[str, Any]:
    candidate = min(
        (
            row for row in samples
            if row["sample_stage"] == "T0" and int(row["frame_packet_count"]) > 1
        ),
        key=lambda row: int(row["frame_packet_count"]),
    )
    frame_key = _frame_key(candidate)
    packet_count = int(candidate["frame_packet_count"])
    by_packet: dict[int, list[dict[str, Any]]] = {}
    for packet_index in range(packet_count):
        packet_events = [
            row for row in events
            if row["frame_id"] and _key(row) == frame_key + (packet_index,)
        ]
        types = {row["event_type"] for row in packet_events}
        required = {
            "PACKET_SUBMITTED",
            "MAC_ENQUEUE",
            "MPDU_TX_ATTEMPT",
            "MPDU_POSITIVE_ACK",
        }
        if not required <= types:
            raise RuntimeError(
                f"selected lifecycle packet {packet_index} is incomplete"
            )
        by_packet[packet_index] = [
            _event_excerpt(row) for row in packet_events
            if row["event_type"] != "FRAME_REGISTERED"
        ]

    attempt_groups: dict[int, list[dict[str, str]]] = {}
    for row in events:
        if row["event_type"] == "MPDU_TX_ATTEMPT":
            attempt_groups.setdefault(int(row["event_time_ns"]), []).append(row)
    aggregated = [
        {
            "event_time_ns": event_time,
            "constituent_mpdu_count": len(rows),
            "packet_keys": [list(_key(row)) for row in rows],
        }
        for event_time, rows in attempt_groups.items() if len(rows) > 1
    ]
    if not aggregated:
        raise RuntimeError("no multi-MPDU PHY transmission was observed")
    return {
        "frame_key": list(frame_key),
        "packet_count": packet_count,
        "stable_identity_fields": [
            "path_id", "copy_id", "frame_id", "packet_index",
        ],
        "packet_events": by_packet,
        "ampdu_examples": aggregated[:3],
    }


def _retry_ledger(events: list[dict[str, str]]) -> dict[str, Any]:
    packet_events: dict[tuple[int, int, int, int], list[dict[str, str]]] = {}
    for row in events:
        if row["frame_id"]:
            packet_events.setdefault(_key(row), []).append(row)
    selected_key: tuple[int, int, int, int] | None = None
    selected_rows: list[dict[str, str]] = []
    for key, rows in packet_events.items():
        types = {row["event_type"] for row in rows}
        if {
            "MPDU_TX_ATTEMPT_FAILURE",
            "MPDU_RETRY",
            "MPDU_POSITIVE_ACK",
        } <= types:
            selected_key = key
            selected_rows = rows
            break
    if selected_key is None:
        raise RuntimeError("retry run has no failure-retry-positive-ACK packet")
    attempts = sum(row["event_type"] == "MPDU_TX_ATTEMPT" for row in selected_rows)
    failures = sum(
        row["event_type"] == "MPDU_TX_ATTEMPT_FAILURE" for row in selected_rows
    )
    successful_finalizers = sum(
        row["event_type"] == "MPDU_POSITIVE_ACK" and
        row["finalizes_attempt_success"] == "1"
        for row in selected_rows
    )
    positive_acks = sum(
        row["event_type"] == "MPDU_POSITIVE_ACK" for row in selected_rows
    )
    retries = sum(row["event_type"] == "MPDU_RETRY" for row in selected_rows)
    unresolved = attempts - failures - successful_finalizers
    if unresolved != 0 or attempts != failures + successful_finalizers:
        raise RuntimeError("selected retry attempt ledger does not conserve")
    return {
        "packet_key": list(selected_key),
        "attempts": attempts,
        "successful_attempt_finalizers": successful_finalizers,
        "attempt_failures": failures,
        "unresolved_attempts": unresolved,
        "logical_positive_acks": positive_acks,
        "retries": retries,
        "arithmetic": f"{attempts} = {successful_finalizers} + {failures} + {unresolved}",
        "events": [_event_excerpt(row) for row in selected_rows],
    }


def _rolling_evidence(
    samples: list[dict[str, str]],
    events: list[dict[str, str]],
) -> dict[str, Any]:
    label = "5ms"
    sample = next(
        row for row in samples
        if int(row[f"mpdu_positive_acks_{label}"]) > 0 and
        float(row[f"history_coverage_{label}_us"]) > 0
    )
    sample_time = int(sample["sample_time_ns"])
    watermark = int(sample["latest_feature_event_sequence"])
    lower = max(sample_time - 5_000_000, 0)

    enqueue: dict[tuple[int, int, int, int], int] = {}
    first_attempt: dict[tuple[int, int, int, int], int] = {}
    mac_rows: list[dict[str, str]] = []
    queue_to_ack: list[float] = []
    attempt_to_ack: list[float] = []
    attempts = failures = retries = positive_acks = ack_bytes = finalizers = 0
    for row in events:
        sequence = int(row["event_sequence"])
        if sequence > watermark:
            break
        if row["frame_id"]:
            key = _key(row)
            event_type = row["event_type"]
            event_time = int(row["event_time_ns"])
            if event_type == "MAC_ENQUEUE":
                enqueue[key] = event_time
            elif event_type == "MPDU_TX_ATTEMPT":
                first_attempt.setdefault(key, event_time)
            in_window = lower < event_time <= sample_time
            if in_window and event_type in {
                "MPDU_TX_ATTEMPT",
                "MPDU_TX_ATTEMPT_FAILURE",
                "MPDU_RETRY",
                "MPDU_POSITIVE_ACK",
            }:
                mac_rows.append(row)
                attempts += event_type == "MPDU_TX_ATTEMPT"
                failures += event_type == "MPDU_TX_ATTEMPT_FAILURE"
                retries += event_type == "MPDU_RETRY"
                if event_type == "MPDU_POSITIVE_ACK":
                    positive_acks += 1
                    finalizers += row["finalizes_attempt_success"] == "1"
                    ack_bytes += int(row["mac_service_bytes"])
                    queue_to_ack.append((event_time - enqueue[key]) / 1000.0)
                    attempt_to_ack.append(
                        (event_time - first_attempt[key]) / 1000.0
                    )

    revisions: dict[
        tuple[int, str, bool], tuple[int, int, str, int, int, dict[str, str]]
    ] = {}
    telemetry_start: int | None = None
    for row in events:
        sequence = int(row["event_sequence"])
        if sequence > watermark:
            break
        if row["event_type"] != "PHY_INTERVAL_REVISION":
            continue
        start = int(row["phy_interval_start_ns"])
        end = int(row["phy_interval_end_ns"])
        state = row["phy_interval_state"]
        initial = row["phy_interval_revision_kind"] == "INITIAL"
        revisions[(start, state, initial)] = (
            start, end, state, int(row["event_time_ns"]), sequence, row
        )
        if initial:
            telemetry_start = start
    if telemetry_start is None:
        raise RuntimeError("initial PHY interval is absent")
    coverage_start = max(telemetry_start, lower)
    intervals = [
        interval for interval in revisions.values()
        if interval[1] > coverage_start and interval[0] < sample_time
    ]
    boundaries = {coverage_start, sample_time}
    for interval in intervals:
        boundaries.add(max(interval[0], coverage_start))
        boundaries.add(min(interval[1], sample_time))
    priority = {
        "OFF": 7, "SLEEP": 6, "TX": 5, "RX": 4,
        "SWITCHING": 3, "CCA_BUSY": 2, "IDLE": 1,
    }
    durations = {
        "TX": 0.0, "RX": 0.0, "CCA_BUSY": 0.0, "IDLE": 0.0, "OTHER": 0.0,
    }
    segments = []
    ordered = sorted(boundaries)
    for start, end in zip(ordered, ordered[1:]):
        midpoint = start + (end - start) // 2
        candidates = [
            interval for interval in intervals
            if interval[0] <= midpoint < interval[1]
        ]
        if not candidates:
            raise RuntimeError("PHY audit reconstruction has a gap")
        selected = max(
            candidates,
            key=lambda interval: (
                priority[interval[2]], interval[3], interval[4]
            ),
        )
        bucket = (
            selected[2]
            if selected[2] in {"TX", "RX", "CCA_BUSY", "IDLE"}
            else "OTHER"
        )
        duration = (end - start) / 1000.0
        durations[bucket] += duration
        segments.append({
            "start_ns": start,
            "end_ns": end,
            "state": selected[2],
            "duration_us": duration,
            "source_event_sequence": selected[4],
        })
    coverage = (sample_time - coverage_start) / 1000.0
    emitted = {
        "mpdu_attempts": int(sample[f"mpdu_attempts_{label}"]),
        "successful_attempt_finalizers": finalizers,
        "mpdu_positive_acks": int(sample[f"mpdu_positive_acks_{label}"]),
        "mpdu_attempt_failures": int(sample[f"mpdu_attempt_failures_{label}"]),
        "mpdu_retries": int(sample[f"mpdu_retries_{label}"]),
        "acknowledged_mac_service_bytes": int(
            sample[f"acknowledged_mac_service_bytes_{label}"]
        ),
        "mpdu_queue_to_ack_mean_us": float(
            sample[f"mpdu_queue_to_ack_mean_{label}_us"]
        ),
        "mpdu_queue_to_ack_p95_us": float(
            sample[f"mpdu_queue_to_ack_p95_{label}_us"]
        ),
        "mpdu_first_attempt_to_ack_mean_us": float(
            sample[f"mpdu_first_attempt_to_ack_mean_{label}_us"]
        ),
        "mpdu_first_attempt_to_ack_p95_us": float(
            sample[f"mpdu_first_attempt_to_ack_p95_{label}_us"]
        ),
        "phy_tx_time_us": float(sample[f"phy_tx_time_{label}_us"]),
        "phy_rx_time_us": float(sample[f"phy_rx_time_{label}_us"]),
        "phy_busy_time_us": float(sample[f"phy_busy_time_{label}_us"]),
        "phy_idle_time_us": float(sample[f"phy_idle_time_{label}_us"]),
        "phy_other_time_us": float(sample[f"phy_other_time_{label}_us"]),
        "history_coverage_us": float(sample[f"history_coverage_{label}_us"]),
    }
    reconstructed = {
        "mpdu_attempts": attempts,
        "successful_attempt_finalizers": finalizers,
        "mpdu_positive_acks": positive_acks,
        "mpdu_attempt_failures": failures,
        "mpdu_retries": retries,
        "acknowledged_mac_service_bytes": ack_bytes,
        "mpdu_queue_to_ack_mean_us": sum(queue_to_ack) / len(queue_to_ack),
        "mpdu_queue_to_ack_p95_us": _percentile(queue_to_ack, 0.95),
        "mpdu_first_attempt_to_ack_mean_us": (
            sum(attempt_to_ack) / len(attempt_to_ack)
        ),
        "mpdu_first_attempt_to_ack_p95_us": _percentile(attempt_to_ack, 0.95),
        "phy_tx_time_us": durations["TX"],
        "phy_rx_time_us": durations["RX"],
        "phy_busy_time_us": durations["CCA_BUSY"],
        "phy_idle_time_us": durations["IDLE"],
        "phy_other_time_us": durations["OTHER"],
        "history_coverage_us": coverage,
    }
    for field, expected in reconstructed.items():
        actual = emitted[field]
        if isinstance(expected, float):
            if not _close(float(actual), expected):
                raise RuntimeError(f"rolling reconstruction differs for {field}")
        elif actual != expected:
            raise RuntimeError(f"rolling reconstruction differs for {field}")
    if not _close(sum(durations.values()), coverage):
        raise RuntimeError("PHY durations do not conserve history coverage")
    return {
        "sample_key": {
            "frame_id": int(sample["frame_id"]),
            "path_id": int(sample["path_id"]),
            "copy_id": int(sample["copy_id"]),
            "sample_stage": sample["sample_stage"],
            "sample_time_ns": sample_time,
            "latest_feature_event_sequence": watermark,
        },
        "window": "(sample_time - 5 ms, sample_time]",
        "lower_bound_ns": lower,
        "emitted": emitted,
        "reconstructed": reconstructed,
        "mac_source_events": [_event_excerpt(row) for row in mac_rows],
        "phy_source_events": [
            {
                "event_sequence": interval[4],
                "event_time_ns": interval[3],
                "revision_kind": interval[5]["phy_interval_revision_kind"],
                "state": interval[2],
                "start_ns": interval[0],
                "end_ns": interval[1],
            }
            for interval in intervals
        ],
        "phy_segments": segments,
        "phy_conservation_arithmetic": {
            **durations,
            "sum_us": sum(durations.values()),
            "history_coverage_us": coverage,
        },
    }


def _t0_evidence(samples: list[dict[str, str]]) -> dict[str, Any]:
    rows = [row for row in samples if row["sample_stage"] == "T0"]
    for row in rows:
        packet_count = int(row["frame_packet_count"])
        if not (
            packet_count > 0 and
            int(row["packets_remaining_to_submit"]) == packet_count and
            int(row["packets_submitted"]) == 0 and
            int(row["frame_packets_mac_enqueued"]) == 0 and
            int(row["frame_packets_tx_succeeded"]) == 0
        ):
            raise RuntimeError("T0 pre-submission invariant failed")
    return {
        "row_count": len(rows),
        "first_row": {
            key: rows[0][key]
            for key in (
                "frame_id",
                "sample_time_ns",
                "latest_feature_event_time_ns",
                "latest_feature_event_sequence",
                "frame_packet_count",
                "packets_remaining_to_submit",
                "packets_submitted",
                "frame_packets_mac_enqueued",
                "frame_packets_tx_succeeded",
            )
        },
    }


def _support_evidence(samples: list[dict[str, str]]) -> dict[str, Any]:
    masks = {row["feature_support_mask"] for row in samples}
    if len(masks) != 1:
        raise RuntimeError("support mask changes within a run")
    mask_text = next(iter(masks))
    mask = int(mask_text, 16)
    set_bits = [bit for bit in range(61) if mask & (1 << bit)]
    clear_bits = [bit for bit in range(61) if not mask & (1 << bit)]
    for row in samples:
        for field in (
            "current_ack_signal_dbm",
            "remaining_backoff_slots",
            "expected_access_reason_within_slack",
        ):
            if row[field] != "":
                raise RuntimeError(f"unsupported field is populated: {field}")
    return {
        "mask": mask_text,
        "set_bits": set_bits,
        "clear_bits": clear_bits,
        "required_clear_bits": [17, 55, 60],
        "unsupported_fields_are_null": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--retry-run", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    validation = validate_run(args.run_dir)
    samples_path = args.run_dir / "prediction_samples.csv"
    events_path = args.run_dir / "prediction_events.csv"
    samples = _rows(samples_path)
    events = _rows(events_path)
    report: dict[str, Any] = {
        "audit_schema_version": 1,
        "run_id": validation["run_id"],
        "run_dir": str(args.run_dir.resolve()),
        "telemetry_schema_version": int(samples[0]["telemetry_schema_version"]),
        "event_schema_version": int(events[0]["event_schema_version"]),
        "checksums": {
            path.name: _sha256(path)
            for path in (
                samples_path,
                events_path,
                args.run_dir / "resolved_config.json",
                args.run_dir / "frames.csv",
            )
        },
        "lifecycle": _lifecycle_evidence(samples, events),
        "rolling_5ms": _rolling_evidence(samples, events),
        "t0": _t0_evidence(samples),
        "support_mask": _support_evidence(samples),
    }
    if args.retry_run is not None:
        validate_run(args.retry_run)
        retry_events_path = args.retry_run / "prediction_events.csv"
        report["retry_run"] = {
            "run_dir": str(args.retry_run.resolve()),
            "prediction_events_sha256": _sha256(retry_events_path),
            "ledger": _retry_ledger(_rows(retry_events_path)),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    print(f"AUDIT {args.output}")


if __name__ == "__main__":
    main()
