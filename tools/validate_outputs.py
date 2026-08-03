#!/usr/bin/env python3
"""Validate one wifi-streaming run directory."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

CORE_FILES = {
    "resolved_config.json",
    "build_info.json",
    "frames.csv",
    "policy_decisions.csv",
    "link_intervals.csv",
    "mac_summary.csv",
    "summary.json",
    "stdout.log",
}
OFDMA_COLUMNS = {
    "device_group", "trigger_frames", "basic_trigger_frames", "bsrp_trigger_frames",
    "ru_grants", "tb_ppdus_transmitted", "tb_bytes_transmitted",
    "tb_mpdus_received", "tb_bytes_received",
}
FRAME_COLUMNS = {
    "run_id", "frame_id", "generation_time_us", "frame_size_bytes", "packet_count",
    "frame_type", "deadline_us", "policy", "primary_link", "duplicated",
    "decision_time_us", "union_first_packet_us", "union_completion_us",
    "union_latency_us", "copy_0_completion_us", "copy_1_completion_us",
    "unique_packets_received", "duplicate_packets_received", "deadline_miss",
    "incomplete", "completion_mode",
}
DECISION_COLUMNS = {
    "run_id", "frame_id", "decision_time_us", "policy", "primary_link",
    "duplicated", "secondary_link", "reason", "primary_score", "secondary_score",
}
SELECTIVE_DECISION_COLUMNS = {
    "run_id", "frame_id", "path_id", "copy_id", "sample_stage",
    "sample_offset_us", "sample_time_ns", "deadline_time_ns", "actionable",
    "calibrated_probability", "probability_threshold", "frame_budget",
    "token_capacity", "tokens_before", "tokens_after", "decision",
    "secondary_launched",
}
ADAPTIVE_DECISION_COLUMNS = {
    "run_id", "frame_id", "sample_stage", "sample_offset_us", "sample_time_ns",
    "actionable", "calibrated_probability", "estimated_airtime_us",
    "reference_airtime_us", "shadow_price", "normalized_cost", "net_utility",
    "airtime_budget_fraction", "bucket_capacity_us", "bucket_balance_us",
    "initial_bucket_capacity_us", "reserved_airtime_us", "available_airtime_us",
    "measured_airtime_total_us", "decision", "secondary_launched",
}
SECONDARY_AIRTIME_EVENT_COLUMNS = {
    "run_id", "time_ns", "path_id", "ppdu_duration_us", "tagged_mpdu_bytes",
    "frame_ids", "mixed_ppdu", "cumulative_tagged_airtime_us",
}
SECONDARY_AIRTIME_SETTLEMENT_COLUMNS = {
    "run_id", "frame_id", "settlement_time_ns", "released_airtime_us",
    "measured_airtime_us", "nominal_airtime_us", "fallback",
}
SECONDARY_AIRTIME_SUMMARY_KEYS = {
    "tagged_ppdu_count", "mixed_ppdu_count", "tagged_secondary_tx_airtime_us",
    "measurement_start_ns", "measurement_stop_ns", "measurement_duration_us",
    "tagged_secondary_tx_airtime_fraction", "maximum_budget_debt_us",
    "estimated_action_airtime_us", "actual_to_estimated_airtime_ratio",
    "forced_reservation_settlements", "budget_fraction",
    "initial_bucket_capacity_us", "finite_run_budget_us", "budget_excess_us",
}
LINK_COLUMNS = {
    "timestamp_us", "link_id", "application_bytes_sent",
    "application_bytes_received", "redundant_bytes", "successful_mpdus",
    "failed_mpdus", "retransmissions", "phy_tx_time_us", "phy_rx_time_us",
    "phy_cca_busy_time_us",
}
MAC_COLUMNS = {
    "link_id", "node_id", "device_id", "successful_mpdus", "failed_mpdus",
    "retransmissions", "retry_limit_drops",
}
BACKGROUND_FLOW_COLUMNS = {
    "run_id", "bss_id", "link_id", "standard", "sta_index", "direction",
    "source_node_id", "destination_node_id", "port", "rate_stream", "on_stream",
    "off_stream", "period_count", "bytes_sent", "bytes_received",
}
BACKGROUND_PERIOD_COLUMNS = {
    "run_id", "bss_id", "sta_index", "direction", "period_index", "start_us",
    "end_us", "rate_mbps",
}
SUMMARY_KEYS = {
    "frame_count", "complete_frame_count", "incomplete_frame_count",
    "deadline_miss_count", "complete_ratio", "incomplete_ratio",
    "deadline_miss_ratio", "application_bytes_sent",
    "application_bytes_delivered", "redundant_bytes_sent", "successful_mpdus",
    "duplicate_frame_count", "failed_mpdus", "retransmissions",
    "redundant_byte_ratio", "phy_tx_time_us", "phy_rx_time_us",
    "phy_cca_busy_time_us",
}
BUILD_KEYS = {
    "ns3_version", "ns3_upstream_commit", "project_git_commit", "compiler",
    "build_profile", "execution_timestamp_utc", "host",
}
PREDICTION_SCHEMA_VERSION = 3
PREDICTION_POLLING_SCHEMA_VERSION = 1
PREDICTION_EVENT_SCHEMA_VERSION = 2
PREDICTION_SUPPORT_MASK_VERSION = 2
PREDICTION_BASE_COLUMNS = {
    "telemetry_schema_version", "run_id", "frame_id", "path_id", "copy_id",
    "sample_stage", "sample_offset_us", "sample_time_ns",
    "latest_feature_event_time_ns", "latest_feature_event_sequence",
    "generation_time_ns", "deadline_time_ns",
    "frame_age_us", "deadline_slack_us", "sender_mac_complete", "actionable",
    "frame_size_bytes", "frame_packet_count", "frame_type", "packets_submitted",
    "application_socket_packet_bytes_submitted", "packets_remaining_to_submit",
    "mpdu_tx_attempts_total", "mpdu_positive_acks_total",
    "mpdu_tx_attempt_failures_total", "mpdu_retries_total",
    "mpdu_terminal_drops_total", "mpdu_retry_limit_drops_total",
    "mpdu_lifetime_drops_total", "mpdu_queue_drops_total", "ppdu_tx_count_total",
    "last_tx_attempt_time_ns", "last_positive_ack_time_ns", "current_mcs",
    "current_nss", "current_channel_width_mhz", "current_guard_interval_ns",
    "frequency_band", "center_frequency_mhz", "current_ack_signal_dbm",
    "frame_packets_mac_enqueued", "frame_packets_mac_dequeued",
    "frame_packets_tx_succeeded", "frame_mpdu_attempt_failures",
    "frame_packets_terminally_dropped", "frame_packets_currently_queued",
    "frame_mac_service_bytes_currently_queued", "mac_queue_packets",
    "mac_queue_service_bytes", "mac_queue_oldest_enqueue_time_ns",
    "packets_ahead_of_frame", "mac_service_bytes_ahead_of_frame",
    "frame_packets_pending_primary", "frame_mac_service_bytes_not_acknowledged",
    "frame_mac_service_bytes_pending_primary", "current_cw",
    "remaining_backoff_slots", "nav_remaining_us", "current_phy_state",
    "channel_access_status", "medium_busy_now",
    "expected_access_reason_within_slack", "feature_support_mask",
}
PREDICTION_POLLING_BASE_COLUMNS = {
    "polling_schema_version", "run_id", "frame_id", "path_id", "copy_id",
    "sample_stage", "sample_offset_us", "report_available", "capture_time_ns",
    "available_time_ns", "staleness_us", "latest_feature_event_time_ns",
    "latest_feature_event_sequence", "feature_support_mask",
    "mpdu_tx_attempts_total", "mpdu_positive_acks_total",
    "mpdu_tx_attempt_failures_total", "mpdu_retries_total",
    "mpdu_terminal_drops_total", "mpdu_retry_limit_drops_total",
    "mpdu_lifetime_drops_total", "mpdu_queue_drops_total", "ppdu_tx_count_total",
    "last_tx_attempt_time_ns", "last_positive_ack_time_ns", "current_mcs",
    "current_nss", "current_channel_width_mhz", "current_guard_interval_ns",
    "frequency_band", "center_frequency_mhz", "current_ack_signal_dbm",
}
PREDICTION_ROLLING_PREFIXES = {
    "mpdu_attempts", "mpdu_positive_acks", "mpdu_attempt_failures", "mpdu_retries",
    "mpdu_retry_ratio", "acknowledged_mac_service_bytes",
    "mpdu_queue_to_ack_mean", "mpdu_queue_to_ack_p95",
    "mpdu_first_attempt_to_ack_mean", "mpdu_first_attempt_to_ack_p95",
    "phy_tx_time", "phy_rx_time", "phy_busy_time", "phy_idle_time",
    "phy_other_time", "phy_tx_fraction", "phy_rx_fraction",
    "phy_busy_fraction", "phy_idle_fraction", "phy_other_fraction",
    "history_coverage",
}
PREDICTION_EVENT_COLUMNS = {
    "event_schema_version", "run_id", "event_time_ns", "event_sequence",
    "event_type", "path_id", "copy_id", "frame_id", "packet_index",
    "attempt_number", "finalizes_attempt_success",
    "mac_service_bytes", "mac_queue_packets", "mac_queue_service_bytes",
    "current_mcs", "current_nss", "current_channel_width_mhz",
    "current_guard_interval_ns", "current_phy_state",
    "phy_interval_revision_kind", "phy_interval_state",
    "phy_interval_start_ns", "phy_interval_end_ns",
}
PREDICTION_EVENT_TYPES = {
    "FRAME_REGISTERED", "PACKET_SUBMITTED", "MAC_ENQUEUE", "MAC_DEQUEUE",
    "MAC_DROP", "MPDU_TX_ATTEMPT", "MPDU_POSITIVE_ACK",
    "MPDU_TX_ATTEMPT_FAILURE", "MPDU_RETRY", "MPDU_TERMINAL_DROP", "PPDU_TX",
    "PHY_INTERVAL_REVISION",
}
PHY_STATES = {"IDLE", "CCA_BUSY", "TX", "RX", "SWITCHING", "SLEEP", "OFF"}
PHY_INTERVAL_REVISION_KINDS = {
    "INITIAL", "PREDICTED_START", "AUTHORITATIVE", "EXPLICIT_END",
}
PREDICTION_ROLLING_SUPPORT_BITS = {
    "mpdu_attempts": 18,
    "mpdu_positive_acks": 19,
    "mpdu_attempt_failures": 20,
    "mpdu_retries": 21,
    "mpdu_retry_ratio": 22,
    "acknowledged_mac_service_bytes": 23,
    "mpdu_queue_to_ack_mean": 24,
    "mpdu_queue_to_ack_p95": 25,
    "mpdu_first_attempt_to_ack_mean": 26,
    "mpdu_first_attempt_to_ack_p95": 27,
    "phy_tx_time": 28,
    "phy_rx_time": 29,
    "phy_busy_time": 30,
    "phy_idle_time": 31,
    "phy_other_time": 32,
    "phy_tx_fraction": 33,
    "phy_rx_fraction": 34,
    "phy_busy_fraction": 35,
    "phy_idle_fraction": 36,
    "phy_other_fraction": 37,
    "history_coverage": 38,
}
PREDICTION_ORACLE_SUPPORT_BITS = {54, 56, 57, 58, 59}
PREDICTION_REQUIRED_WIFI_SUPPORT_BITS = set(range(0, 17)) | set(range(18, 54))
PREDICTION_FIELD_SUPPORT_BITS = {
    "mpdu_tx_attempts_total": 0,
    "mpdu_positive_acks_total": 1,
    "mpdu_tx_attempt_failures_total": 2,
    "mpdu_retries_total": 3,
    "mpdu_terminal_drops_total": 4,
    "mpdu_retry_limit_drops_total": 5,
    "mpdu_lifetime_drops_total": 6,
    "mpdu_queue_drops_total": 7,
    "ppdu_tx_count_total": 8,
    "last_tx_attempt_time_ns": 9,
    "last_positive_ack_time_ns": 10,
    "current_mcs": 11,
    "current_nss": 12,
    "current_channel_width_mhz": 13,
    "current_guard_interval_ns": 14,
    "frequency_band": 15,
    "center_frequency_mhz": 16,
    "current_ack_signal_dbm": 17,
    "frame_packets_mac_enqueued": 39,
    "frame_packets_mac_dequeued": 40,
    "frame_packets_tx_succeeded": 41,
    "frame_mpdu_attempt_failures": 42,
    "frame_packets_terminally_dropped": 43,
    "frame_packets_currently_queued": 44,
    "frame_mac_service_bytes_currently_queued": 45,
    "mac_queue_packets": 46,
    "mac_queue_service_bytes": 47,
    "mac_queue_oldest_enqueue_time_ns": 48,
    "packets_ahead_of_frame": 49,
    "mac_service_bytes_ahead_of_frame": 50,
    "frame_packets_pending_primary": 51,
    "frame_mac_service_bytes_not_acknowledged": 52,
    "frame_mac_service_bytes_pending_primary": 53,
    "current_cw": 54,
    "remaining_backoff_slots": 55,
    "nav_remaining_us": 56,
    "current_phy_state": 57,
    "channel_access_status": 58,
    "medium_busy_now": 59,
    "expected_access_reason_within_slack": 60,
}
ACCESS_STATUSES = {"NOT_REQUESTED", "REQUESTED", "GRANTED"}


class ValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"{path.name}: invalid JSON: {error}") from error
    _require(isinstance(value, dict), f"{path.name}: root must be an object")
    return value


def _csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            _require(reader.fieldnames is not None, f"{path.name}: missing header")
            _require(required <= set(reader.fieldnames), f"{path.name}: missing columns")
            return list(reader)
    except OSError as error:
        raise ValidationError(f"{path.name}: cannot read: {error}") from error


def _integer(row: dict[str, str], key: str, file_name: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"{file_name}: invalid integer {key}") from error
    _require(value >= 0, f"{file_name}: negative {key}")
    return value


def _flag(row: dict[str, str], key: str, file_name: str) -> bool:
    _require(row[key] in {"0", "1"}, f"{file_name}: {key} must be 0 or 1")
    return row[key] == "1"


def _optional_integer(row: dict[str, str], key: str, file_name: str) -> int | None:
    if row.get(key, "") == "":
        return None
    return _integer(row, key, file_name)


def _number(row: dict[str, str], key: str, file_name: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"{file_name}: invalid number {key}") from error
    _require(math.isfinite(value), f"{file_name}: non-finite {key}")
    _require(value >= 0, f"{file_name}: negative {key}")
    return value


def _signed_number(row: dict[str, str], key: str, file_name: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"{file_name}: invalid number {key}") from error
    _require(math.isfinite(value), f"{file_name}: non-finite {key}")
    return value


def _optional_number(row: dict[str, str], key: str, file_name: str) -> float | None:
    if row.get(key, "") == "":
        return None
    return _number(row, key, file_name)


def _optional_signed_number(row: dict[str, str], key: str, file_name: str) -> float | None:
    if row.get(key, "") == "":
        return None
    try:
        value = float(row[key])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"{file_name}: invalid number {key}") from error
    _require(math.isfinite(value), f"{file_name}: non-finite {key}")
    return value


def _strict_integer_list(value: Any, name: str, *, positive: bool) -> list[int]:
    _require(isinstance(value, list) and value, f"resolved_config.json: invalid {name}")
    _require(all(isinstance(item, int) and not isinstance(item, bool) for item in value),
             f"resolved_config.json: {name} must contain integers")
    minimum = 1 if positive else 0
    _require(all(item >= minimum for item in value),
             f"resolved_config.json: invalid value in {name}")
    _require(all(left < right for left, right in zip(value, value[1:])),
             f"resolved_config.json: {name} must be strictly increasing")
    return value


def _window_label(window_us: int) -> str:
    return f"{window_us // 1000}ms" if window_us % 1000 == 0 else f"{window_us}us"


def _stage_name(offset_us: int) -> str:
    return f"T{offset_us // 1000}" if offset_us % 1000 == 0 else f"offset_{offset_us}us"


def _close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)


def _config_number(config: dict[str, Any], key: str, object_name: str) -> float:
    value = config.get(key)
    _require(isinstance(value, (int, float)) and not isinstance(value, bool),
             f"resolved_config.json: invalid {object_name}.{key}")
    resolved = float(value)
    _require(math.isfinite(resolved),
             f"resolved_config.json: non-finite {object_name}.{key}")
    return resolved


def _summary_number(summary: dict[str, Any], key: str) -> float:
    value = summary.get(key)
    _require(isinstance(value, (int, float)) and not isinstance(value, bool),
             f"secondary_airtime_summary.json: invalid {key}")
    resolved = float(value)
    _require(math.isfinite(resolved) and resolved >= 0,
             f"secondary_airtime_summary.json: invalid {key}")
    return resolved


def _summary_integer(summary: dict[str, Any], key: str) -> int:
    value = summary.get(key)
    _require(isinstance(value, int) and not isinstance(value, bool) and value >= 0,
             f"secondary_airtime_summary.json: invalid {key}")
    return value


def _validate_adaptive_config(config: dict[str, Any]) -> list[int]:
    """Validate adaptive controller provenance and return decision offsets."""
    source_sha = config.get("source_model_sha256")
    _require(
        config.get("model_id") == "commodity_polling_1ms_genuine_v1" and
        isinstance(source_sha, str) and re.fullmatch(r"[0-9a-f]{64}", source_sha) is not None and
        config.get("feature_set") == "F0+F1-degraded" and
        config.get("degradation_profile") == "polling_1ms" and
        config.get("calibration") == "platt" and
        config.get("budget_definition") == "secondary_sender_phy_tx_airtime" and
        config.get("primary_path") == 1 and config.get("secondary_path") == 0,
        "resolved_config.json: invalid adaptive airtime provenance",
    )
    offsets = _strict_integer_list(
        config.get("decision_offsets_us"),
        "adaptiveAirtimeDuplication.decision_offsets_us",
        positive=False,
    )
    _require(offsets[0] == 0,
             "resolved_config.json: adaptive decision offsets must include T0")
    _require(config.get("stages") == [_stage_name(offset) for offset in offsets],
             "resolved_config.json: adaptive stages do not match decision offsets")

    fraction = _config_number(config, "budget_fraction", "adaptiveAirtimeDuplication")
    initial_price = _config_number(
        config, "initial_shadow_price", "adaptiveAirtimeDuplication"
    )
    dual_step = _config_number(config, "dual_step", "adaptiveAirtimeDuplication")
    safety = _config_number(config, "cost_safety_factor", "adaptiveAirtimeDuplication")
    alpha = _config_number(config, "cost_ewma_alpha", "adaptiveAirtimeDuplication")
    horizon = config.get("bucket_horizon_us")
    _require(isinstance(horizon, int) and not isinstance(horizon, bool) and horizon > 0,
             "resolved_config.json: invalid adaptive bucket horizon")
    _require(0 < fraction <= 1 and 0 <= initial_price <= 1 and dual_step > 0 and
             safety >= 1 and 0 < alpha <= 1,
             "resolved_config.json: adaptive parameter outside its domain")
    initial_capacity = _config_number(
        config, "initial_bucket_capacity_us", "adaptiveAirtimeDuplication"
    )
    _require(_close(initial_capacity, fraction * horizon),
             "resolved_config.json: adaptive initial capacity mismatch")
    return offsets


def _adaptive_utility(row: dict[str, str]) -> float:
    try:
        value = float(row["net_utility"])
    except (KeyError, ValueError) as error:
        raise ValidationError(
            "adaptive_airtime_decisions.csv: invalid number net_utility"
        ) from error
    _require(math.isfinite(value) or math.isnan(value),
             "adaptive_airtime_decisions.csv: invalid net_utility")
    return value


def _validate_adaptive_decisions(
    rows: list[dict[str, str]],
    config: dict[str, Any],
    frames: list[dict[str, str]],
    run_id: str,
) -> dict[int, float]:
    """Validate controller arithmetic and decision semantics.

    The returned mapping contains the reservation estimate for every launched
    frame and is used to reconcile the independent PHY-airtime ledger.
    """
    offsets = _validate_adaptive_config(config)
    file_name = "adaptive_airtime_decisions.csv"
    frame_generation_ns = {
        _integer(frame, "frame_id", "frames.csv"):
        _integer(frame, "generation_time_us", "frames.csv") * 1000
        for frame in frames
    }
    _require(len(rows) == len(frames) * len(offsets),
             "adaptive decisions: frame/stage cardinality mismatch")
    _require(all(row.get("run_id") == run_id for row in rows),
             "adaptive decisions: run_id mismatch")

    fraction = _config_number(config, "budget_fraction", "adaptiveAirtimeDuplication")
    horizon = int(config["bucket_horizon_us"])
    capacity = fraction * horizon
    initial_price = _config_number(
        config, "initial_shadow_price", "adaptiveAirtimeDuplication"
    )
    dual_step = _config_number(config, "dual_step", "adaptiveAirtimeDuplication")
    allowed_decisions = {
        "price_rejected", "airtime_deferred", "action", "already_resolved",
        "not_actionable", "launch_rejected",
    }
    seen_samples: set[tuple[int, int]] = set()
    action_estimates: dict[int, float] = {}
    previous_sample_time = -1
    previous_measured = 0.0
    reference_airtime: float | None = None
    last_t0_time: int | None = None
    last_t0_measured = 0.0
    last_t0_shadow = initial_price

    for row in rows:
        frame_id = _integer(row, "frame_id", file_name)
        offset = _integer(row, "sample_offset_us", file_name)
        _require(frame_id in frame_generation_ns and offset in offsets,
                 "adaptive decisions: unknown frame or stage")
        _require((frame_id, offset) not in seen_samples,
                 "adaptive decisions: duplicate frame/stage")
        seen_samples.add((frame_id, offset))
        _require(row.get("sample_stage") == _stage_name(offset),
                 "adaptive decisions: sample stage/offset mismatch")
        sample_time = _integer(row, "sample_time_ns", file_name)
        _require(sample_time == frame_generation_ns[frame_id] + offset * 1000,
                 "adaptive decisions: sample time mismatch")
        _require(sample_time >= previous_sample_time,
                 "adaptive decisions: rows are not chronological")
        previous_sample_time = sample_time

        actionable = _flag(row, "actionable", file_name)
        probability = _number(row, "calibrated_probability", file_name)
        estimated = _number(row, "estimated_airtime_us", file_name)
        reference = _number(row, "reference_airtime_us", file_name)
        shadow = _number(row, "shadow_price", file_name)
        normalized = _number(row, "normalized_cost", file_name)
        utility = _adaptive_utility(row)
        row_fraction = _number(row, "airtime_budget_fraction", file_name)
        row_capacity = _number(row, "bucket_capacity_us", file_name)
        balance = _signed_number(row, "bucket_balance_us", file_name)
        initial_capacity = _number(row, "initial_bucket_capacity_us", file_name)
        reserved = _number(row, "reserved_airtime_us", file_name)
        available = _signed_number(row, "available_airtime_us", file_name)
        measured = _number(row, "measured_airtime_total_us", file_name)
        _require(0 <= probability <= 1 and 0 <= shadow <= 1 and reference > 0,
                 "adaptive decisions: probability, price, or reference out of bounds")
        _require(_close(row_fraction, fraction) and _close(row_capacity, capacity) and
                 _close(initial_capacity, capacity),
                 "adaptive decisions: logged configuration mismatch")
        _require(balance <= capacity + 1e-9 and _close(available, balance - reserved),
                 "adaptive decisions: invalid bucket or reservation accounting")
        _require(measured + 1e-9 >= previous_measured,
                 "adaptive decisions: measured airtime decreased")
        previous_measured = measured
        if reference_airtime is None:
            reference_airtime = reference
        else:
            _require(_close(reference, reference_airtime),
                     "adaptive decisions: reference airtime changed")

        decision = row.get("decision")
        _require(decision in allowed_decisions,
                 "adaptive decisions: unknown decision")
        launched = _flag(row, "secondary_launched", file_name)
        _require(launched == (decision == "action"),
                 "adaptive decisions: action/launch mismatch")

        if estimated > 0:
            _require(_close(normalized, estimated / reference) and
                     math.isfinite(utility) and
                     _close(utility, probability - shadow * normalized),
                     "adaptive decisions: cost or utility arithmetic mismatch")
        else:
            _require(_close(normalized, 0) and math.isnan(utility),
                     "adaptive decisions: absent descriptor must have zero cost and NaN utility")

        if decision == "action":
            _require(actionable and utility > 0 and available + 1e-9 >= estimated and
                     estimated > 0 and frame_id not in action_estimates,
                     "adaptive decisions: invalid action predicate")
            action_estimates[frame_id] = estimated
        elif decision == "price_rejected":
            _require(actionable and estimated > 0 and utility <= 0,
                     "adaptive decisions: invalid price rejection predicate")
        elif decision == "airtime_deferred":
            _require(actionable and estimated > 0 and utility > 0 and
                     available + 1e-9 < estimated,
                     "adaptive decisions: invalid airtime deferral predicate")
        elif decision == "not_actionable":
            _require(not actionable and estimated > 0,
                     "adaptive decisions: invalid not-actionable predicate")
        elif decision == "already_resolved":
            _require(frame_id in action_estimates and estimated == 0,
                     "adaptive decisions: invalid already-resolved predicate")
        else:
            _require(actionable and estimated > 0 and utility > 0 and
                     available + 1e-9 >= estimated,
                     "adaptive decisions: invalid launch-rejected predicate")

        if offset == 0:
            if last_t0_time is None:
                _require(_close(shadow, initial_price),
                         "adaptive decisions: initial shadow price mismatch")
            else:
                elapsed_us = (sample_time - last_t0_time) / 1000.0
                measured_delta = measured - last_t0_measured
                expected_shadow = min(
                    1.0,
                    max(
                        0.0,
                        last_t0_shadow +
                        dual_step * (measured_delta - fraction * elapsed_us) / reference,
                    ),
                )
                _require(_close(shadow, expected_shadow),
                         "adaptive decisions: shadow-price recurrence mismatch")
            last_t0_time = sample_time
            last_t0_measured = measured
            last_t0_shadow = shadow
        else:
            _require(last_t0_time is not None and _close(shadow, last_t0_shadow),
                     "adaptive decisions: shadow price changed outside T0")

    return action_estimates


def _validate_secondary_airtime(
    events: list[dict[str, str]],
    settlements: list[dict[str, str]],
    summary: dict[str, Any],
    meter_config: dict[str, Any],
    links: list[dict[str, str]],
    policy: str,
    run_id: str,
    adaptive_config: dict[str, Any] | None,
    action_estimates: dict[int, float],
    duplicated_frame_ids: set[int],
    observed_budget_debt_us: float,
) -> None:
    """Reconcile secondary PHY events, reservations, and the run budget."""
    _require(SECONDARY_AIRTIME_SUMMARY_KEYS <= summary.keys(),
             "secondary_airtime_summary.json: missing fields")
    _require(policy in {
        "selective_duplication", "adaptive_airtime_duplication", "full_duplication",
    }, "secondary airtime meter enabled for an unsupported policy")
    _require(meter_config.get("definition") == "secondary_sender_phy_tx_airtime" and
             meter_config.get("path_id") == 0 and meter_config.get("copy_id") == 1,
             "resolved_config.json: invalid secondary airtime meter definition")
    start_ns = meter_config.get("measurement_start_ns")
    stop_ns = meter_config.get("measurement_stop_ns")
    _require(isinstance(start_ns, int) and not isinstance(start_ns, bool) and start_ns >= 0 and
             isinstance(stop_ns, int) and not isinstance(stop_ns, bool) and stop_ns > start_ns,
             "resolved_config.json: invalid secondary airtime measurement window")
    _require(_summary_integer(summary, "measurement_start_ns") == start_ns and
             _summary_integer(summary, "measurement_stop_ns") == stop_ns,
             "secondary airtime summary: measurement window mismatch")
    duration_us = (stop_ns - start_ns) / 1000.0
    _require(_close(_summary_number(summary, "measurement_duration_us"), duration_us),
             "secondary airtime summary: measurement duration mismatch")

    running_total = 0.0
    previous_time = -1
    observed_event_frames: set[int] = set()
    mixed_count = 0
    for row in events:
        _require(row.get("run_id") == run_id,
                 "secondary airtime events: run_id mismatch")
        time_ns = _integer(row, "time_ns", "secondary_airtime_events.csv")
        _require(start_ns <= time_ns < stop_ns,
                 "secondary airtime events: event outside half-open measurement window")
        _require(time_ns >= previous_time,
                 "secondary airtime events: rows are not chronological")
        previous_time = time_ns
        _require(_integer(row, "path_id", "secondary_airtime_events.csv") == 0,
                 "secondary airtime events: unexpected path")
        duration = _number(row, "ppdu_duration_us", "secondary_airtime_events.csv")
        tagged_bytes = _integer(row, "tagged_mpdu_bytes", "secondary_airtime_events.csv")
        _require(duration > 0 and tagged_bytes > 0,
                 "secondary airtime events: empty tagged PPDU")
        frame_tokens = row.get("frame_ids", "").split(";")
        _require(frame_tokens and all(re.fullmatch(r"[0-9]+", token) for token in frame_tokens),
                 "secondary airtime events: invalid frame_ids")
        frame_ids = [int(token) for token in frame_tokens]
        _require(len(frame_ids) == len(set(frame_ids)),
                 "secondary airtime events: repeated frame ID in PPDU")
        observed_event_frames.update(frame_ids)
        mixed = _flag(row, "mixed_ppdu", "secondary_airtime_events.csv")
        mixed_count += mixed
        _require(not mixed, "secondary airtime meter observed mixed PPDUs")
        running_total += duration
        cumulative = _number(
            row, "cumulative_tagged_airtime_us", "secondary_airtime_events.csv"
        )
        _require(_close(cumulative, running_total),
                 "secondary airtime events: cumulative airtime mismatch")

    tagged_total = _summary_number(summary, "tagged_secondary_tx_airtime_us")
    _require(_summary_integer(summary, "tagged_ppdu_count") == len(events) and
             _summary_integer(summary, "mixed_ppdu_count") == mixed_count and
             _close(tagged_total, running_total),
             "secondary airtime events do not reconcile with summary")
    _require(_close(
        _summary_number(summary, "tagged_secondary_tx_airtime_fraction"),
        tagged_total / duration_us,
    ), "secondary airtime summary: fraction mismatch")
    _require(observed_event_frames <= duplicated_frame_ids,
             "secondary airtime events: unlaunched frame observed")

    link0 = [row for row in links if _integer(row, "link_id", "link_intervals.csv") == 0]
    _require(len(link0) == 1, "link_intervals.csv: missing unique secondary link")
    link0_tx_us = _integer(link0[0], "phy_tx_time_us", "link_intervals.csv")
    _require(tagged_total <= link0_tx_us + 1.0,
             "secondary tagged airtime exceeds total secondary PHY TX airtime")

    settlement_by_frame: dict[int, dict[str, float | bool]] = {}
    previous_settlement_time = -1
    fallback_count = 0
    for row in settlements:
        _require(row.get("run_id") == run_id,
                 "secondary airtime settlements: run_id mismatch")
        frame_id = _integer(row, "frame_id", "secondary_airtime_settlements.csv")
        _require(frame_id not in settlement_by_frame,
                 "secondary airtime settlements: duplicate frame")
        settlement_time = _integer(
            row, "settlement_time_ns", "secondary_airtime_settlements.csv"
        )
        _require(settlement_time >= previous_settlement_time,
                 "secondary airtime settlements: rows are not chronological")
        previous_settlement_time = settlement_time
        released = _number(
            row, "released_airtime_us", "secondary_airtime_settlements.csv"
        )
        measured = _number(
            row, "measured_airtime_us", "secondary_airtime_settlements.csv"
        )
        nominal = _number(
            row, "nominal_airtime_us", "secondary_airtime_settlements.csv"
        )
        fallback = _flag(row, "fallback", "secondary_airtime_settlements.csv")
        fallback_count += fallback
        _require(nominal > 0,
                 "secondary airtime settlements: nonpositive nominal airtime")
        settlement_by_frame[frame_id] = {
            "released": released, "measured": measured, "fallback": fallback,
        }

    _require(_summary_integer(summary, "forced_reservation_settlements") == fallback_count,
             "secondary airtime summary: fallback settlement count mismatch")
    estimate_total = _summary_number(summary, "estimated_action_airtime_us")
    ratio = _summary_number(summary, "actual_to_estimated_airtime_ratio")
    maximum_debt = _summary_number(summary, "maximum_budget_debt_us")
    _require(maximum_debt + 1e-9 >= observed_budget_debt_us,
             "secondary airtime summary: maximum debt misses an observed deficit")

    if policy == "adaptive_airtime_duplication":
        _require(adaptive_config is not None,
                 "adaptive secondary airtime validation lacks controller config")
        _require(set(settlement_by_frame) == set(action_estimates),
                 "secondary airtime settlements do not match adaptive actions")
        _require(observed_event_frames <= set(action_estimates),
                 "secondary airtime events do not match adaptive actions")
        _require(_close(estimate_total, sum(action_estimates.values())),
                 "secondary airtime summary: action estimates do not sum")
        measured_total = sum(float(item["measured"]) for item in settlement_by_frame.values())
        _require(_close(measured_total, tagged_total),
                 "secondary airtime settlements: measured airtime does not sum")
        for frame_id, estimate in action_estimates.items():
            settlement = settlement_by_frame[frame_id]
            expected_release = max(0.0, estimate - float(settlement["measured"]))
            _require(_close(float(settlement["released"]), expected_release),
                     "secondary airtime settlements: released reservation mismatch")
        expected_ratio = tagged_total / estimate_total if estimate_total else 0.0
        _require(_close(ratio, expected_ratio),
                 "secondary airtime summary: estimate ratio mismatch")
        fraction = _config_number(
            adaptive_config, "budget_fraction", "adaptiveAirtimeDuplication"
        )
        capacity = _config_number(
            adaptive_config, "initial_bucket_capacity_us", "adaptiveAirtimeDuplication"
        )
        finite_budget = fraction * duration_us + capacity
        _require(_close(_summary_number(summary, "budget_fraction"), fraction) and
                 _close(_summary_number(summary, "initial_bucket_capacity_us"), capacity) and
                 _close(_summary_number(summary, "finite_run_budget_us"), finite_budget) and
                 _close(_summary_number(summary, "budget_excess_us"),
                        max(0.0, tagged_total - finite_budget)),
                 "secondary airtime summary: finite-run budget mismatch")
    else:
        _require(not settlements and not action_estimates and estimate_total == 0 and ratio == 0 and
                 fallback_count == 0,
                 "secondary airtime meter has adaptive reservations for a static policy")
        for key in (
            "budget_fraction", "initial_bucket_capacity_us", "finite_run_budget_us",
            "budget_excess_us",
        ):
            _require(summary.get(key) is None,
                     f"secondary_airtime_summary.json: {key} must be null")


def _type7_percentile(values: list[float], probability: float) -> float:
    _require(bool(values), "cannot calculate a percentile of an empty list")
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _rolling_column(prefix: str, label: str) -> str:
    if (prefix.startswith("mpdu_queue_to_ack_") or
            prefix.startswith("mpdu_first_attempt_to_ack_") or
            prefix.startswith("phy_") and prefix.endswith("_time") or
            prefix == "history_coverage"):
        return f"{prefix}_{label}_us"
    return f"{prefix}_{label}"


def _validate_prediction(
    run_dir: Path,
    config: dict[str, Any],
    run_id: str,
    frames: list[dict[str, str]],
) -> dict[str, int]:
    prediction = config.get("predictionTelemetry")
    samples_path = run_dir / "prediction_samples.csv"
    polling_path = run_dir / "prediction_polling_samples.csv"
    events_path = run_dir / "prediction_events.csv"
    if prediction is None:
        _require(not samples_path.exists(),
                 "prediction_samples.csv exists while telemetry is disabled")
        _require(not events_path.exists(),
                 "prediction_events.csv exists while telemetry is disabled")
        _require(not polling_path.exists(),
                 "prediction_polling_samples.csv exists while telemetry is disabled")
        return {"prediction_sample_count": 0, "prediction_event_count": 0}

    _require(isinstance(prediction, dict) and prediction.get("enabled") is True,
             "resolved_config.json: invalid predictionTelemetry")
    _require(config.get("topology") == "dual_interface",
             "prediction telemetry requires dual_interface")
    _require(config.get("policy") in {
        "fixed_link_0", "fixed_link_1", "selective_duplication",
        "adaptive_airtime_duplication",
    }, "prediction telemetry requires a supported primary-link policy")
    wifi = config.get("wifi", {})
    _require(wifi.get("standard") == "802.11be",
             "prediction telemetry requires 802.11be")
    _require(wifi.get("ul_ofdma_enabled") is False,
             "prediction telemetry requires disabled UL OFDMA")
    _require(int(wifi.get("max_amsdu_size_bytes", -1)) == 0,
             "prediction telemetry requires disabled A-MSDU")
    _require(int(wifi.get("fragmentation_threshold_bytes", 0)) == 65535,
             "prediction telemetry requires disabled fragmentation")

    offsets = _strict_integer_list(
        prediction.get("sample_offsets_us"), "prediction sample offsets", positive=False
    )
    windows = _strict_integer_list(
        prediction.get("history_windows_us"), "prediction history windows", positive=True
    )
    _require(offsets[0] == 0, "prediction sample offsets must start at zero")
    deadline_us = int(config.get("stream", {}).get("deadline_us", 0))
    _require(deadline_us > 0 and offsets[-1] < deadline_us,
             "prediction sample offsets must precede the deadline")
    _require(prediction.get("telemetry_schema_version") == PREDICTION_SCHEMA_VERSION,
             "resolved_config.json: unsupported prediction telemetry schema")
    _require(
        prediction.get("polling_schema_version") == PREDICTION_POLLING_SCHEMA_VERSION,
        "resolved_config.json: unsupported prediction polling schema",
    )
    polling_interval_us = int(prediction.get("polling_interval_us", 0))
    polling_delay_us = int(prediction.get("polling_report_delay_us", 0))
    _require(
        polling_interval_us == 1000 and polling_delay_us == 1000,
        "prediction telemetry does not use genuine delayed 1 ms polling",
    )
    _require(prediction.get("event_schema_version") == PREDICTION_EVENT_SCHEMA_VERSION,
             "resolved_config.json: unsupported prediction event schema")
    _require(prediction.get("feature_support_mask_version") ==
             PREDICTION_SUPPORT_MASK_VERSION,
             "resolved_config.json: unsupported feature support-mask version")
    event_enabled = prediction.get("event_log_enabled")
    oracle_enabled = prediction.get("oracle_features_enabled")
    _require(isinstance(event_enabled, bool),
             "resolved_config.json: invalid prediction event flag")
    _require(isinstance(oracle_enabled, bool),
             "resolved_config.json: invalid prediction oracle flag")
    _require(samples_path.is_file(), "missing core file: prediction_samples.csv")
    _require(polling_path.is_file(), "missing core file: prediction_polling_samples.csv")
    _require(events_path.is_file() == event_enabled,
             "prediction_events.csv presence does not match configuration")

    rolling_columns = {
        _rolling_column(prefix, _window_label(window))
        for prefix in PREDICTION_ROLLING_PREFIXES
        for window in windows
    }
    required_columns = PREDICTION_BASE_COLUMNS | rolling_columns
    samples = _csv(samples_path, required_columns)
    _require(samples, "prediction_samples.csv: no rows")
    _require(all(None not in row for row in samples),
             "prediction_samples.csv: rows exceed the declared schema")
    _require(len(samples) == len(frames) * len(offsets),
             "prediction_samples.csv: receiver-independent cardinality mismatch")

    selected_path = 0 if config["policy"] == "fixed_link_0" else 1
    frames_by_id = {int(row["frame_id"]): row for row in frames}
    sample_keys: set[tuple[int, int, int, int]] = set()
    observed_order: list[tuple[int, int, int, int, int]] = []
    by_frame: dict[int, list[dict[str, str]]] = {}
    cumulative_fields = (
        "packets_submitted", "application_socket_packet_bytes_submitted",
        "mpdu_tx_attempts_total", "mpdu_positive_acks_total",
        "mpdu_tx_attempt_failures_total", "mpdu_retries_total",
        "mpdu_terminal_drops_total", "mpdu_retry_limit_drops_total",
        "mpdu_lifetime_drops_total", "mpdu_queue_drops_total", "ppdu_tx_count_total",
        "frame_packets_mac_enqueued", "frame_packets_mac_dequeued",
        "frame_packets_tx_succeeded", "frame_mpdu_attempt_failures",
    )
    optional_nonnegative_integers = (
        "last_tx_attempt_time_ns", "last_positive_ack_time_ns", "current_mcs",
        "current_nss", "current_channel_width_mhz", "current_guard_interval_ns",
        "center_frequency_mhz",
        "frame_packets_mac_enqueued", "frame_packets_mac_dequeued",
        "frame_packets_tx_succeeded", "frame_mpdu_attempt_failures",
        "frame_packets_terminally_dropped", "frame_packets_currently_queued",
        "frame_mac_service_bytes_currently_queued", "mac_queue_packets",
        "mac_queue_service_bytes", "mac_queue_oldest_enqueue_time_ns",
        "packets_ahead_of_frame", "mac_service_bytes_ahead_of_frame",
        "frame_packets_pending_primary", "frame_mac_service_bytes_not_acknowledged",
        "frame_mac_service_bytes_pending_primary", "current_cw",
        "remaining_backoff_slots",
    )
    prohibited_fragments = ("received", "completion", "latency", "deadline_miss")
    _require(not any(
        any(fragment in column for fragment in prohibited_fragments)
        for column in samples[0]
    ), "prediction_samples.csv: receiver or label column leaked into features")

    for row in samples:
        file_name = "prediction_samples.csv"
        _require(row["run_id"] == run_id, f"{file_name}: run_id mismatch")
        _require(_integer(row, "telemetry_schema_version", file_name) ==
                 PREDICTION_SCHEMA_VERSION,
                 f"{file_name}: invalid telemetry schema version")
        frame_id = _integer(row, "frame_id", file_name)
        path_id = _integer(row, "path_id", file_name)
        copy_id = _integer(row, "copy_id", file_name)
        offset = _integer(row, "sample_offset_us", file_name)
        _require(frame_id in frames_by_id, f"{file_name}: unknown frame")
        _require(path_id == selected_path and copy_id == 0,
                 f"{file_name}: fixed-path isolation failed")
        _require(offset in offsets, f"{file_name}: unconfigured sample offset")
        key = (frame_id, path_id, copy_id, offset)
        _require(key not in sample_keys, f"{file_name}: duplicate sample key")
        sample_keys.add(key)
        sample_time = _integer(row, "sample_time_ns", file_name)
        generation_time = _integer(row, "generation_time_ns", file_name)
        deadline_time = _integer(row, "deadline_time_ns", file_name)
        latest_event = _optional_integer(
            row, "latest_feature_event_time_ns", file_name
        )
        latest_sequence = _integer(
            row, "latest_feature_event_sequence", file_name
        )
        frame = frames_by_id[frame_id]
        _require(generation_time // 1000 == int(frame["generation_time_us"]),
                 f"{file_name}: generation timestamp mismatch")
        _require(sample_time == generation_time + offset * 1000,
                 f"{file_name}: sample timestamp mismatch")
        _require(deadline_time == generation_time + int(frame["deadline_us"]) * 1000,
                 f"{file_name}: deadline timestamp mismatch")
        _require(sample_time < deadline_time, f"{file_name}: post-deadline sample")
        _require((latest_sequence == 0) == (latest_event is None),
                 f"{file_name}: inconsistent empty feature watermark")
        if latest_event is not None:
            _require(latest_event <= sample_time, f"{file_name}: future feature event")
        _require(row["sample_stage"] == _stage_name(offset),
                 f"{file_name}: incorrect stage name")
        _require(_integer(row, "frame_age_us", file_name) == offset,
                 f"{file_name}: frame age mismatch")
        _require(_integer(row, "deadline_slack_us", file_name) ==
                 int(frame["deadline_us"]) - offset,
                 f"{file_name}: deadline slack mismatch")
        sender_complete = _flag(row, "sender_mac_complete", file_name)
        actionable = _flag(row, "actionable", file_name)
        _require(actionable == (not sender_complete),
                 f"{file_name}: invalid pre-deadline actionability")
        packet_count = _integer(row, "frame_packet_count", file_name)
        _require(_integer(row, "frame_size_bytes", file_name) ==
                 int(frame["frame_size_bytes"]) and
                 packet_count == int(frame["packet_count"]) and
                 row["frame_type"] == frame["frame_type"],
                 f"{file_name}: frame metadata mismatch")
        submitted = _integer(row, "packets_submitted", file_name)
        submitted_bytes = _integer(
            row, "application_socket_packet_bytes_submitted", file_name
        )
        remaining = _integer(row, "packets_remaining_to_submit", file_name)
        _require(submitted + remaining == packet_count,
                 f"{file_name}: submission packet conservation failed")
        _require(submitted * 50 <= submitted_bytes <=
                 int(frame["frame_size_bytes"]) + submitted * 50,
                 f"{file_name}: application socket byte domain mismatch")
        if submitted == packet_count:
            _require(submitted_bytes == int(frame["frame_size_bytes"]) + packet_count * 50,
                     f"{file_name}: complete application byte accounting mismatch")
        for field in cumulative_fields[:11]:
            _require(_optional_integer(row, field, file_name) is not None,
                     f"{file_name}: bound cumulative field {field} is null")
        for field in optional_nonnegative_integers:
            _optional_integer(row, field, file_name)
        _optional_number(row, "nav_remaining_us", file_name)
        _require(_optional_signed_number(
            row, "current_ack_signal_dbm", file_name
        ) is None, f"{file_name}: unsupported ACK signal must remain null")
        _require(row["frequency_band"] in {"2.4GHz", "5GHz", "6GHz"},
                 f"{file_name}: invalid frequency band")
        _require(_optional_number(row, "center_frequency_mhz", file_name) is not None,
                 f"{file_name}: bound center frequency is null")
        _require(re.fullmatch(r"0x(?:0|[1-9a-f][0-9a-f]*)",
                              row["feature_support_mask"]) is not None,
                 f"{file_name}: noncanonical feature support mask")
        support_mask = int(row["feature_support_mask"], 16)
        expected_bits = set(PREDICTION_REQUIRED_WIFI_SUPPORT_BITS)
        if oracle_enabled:
            expected_bits |= PREDICTION_ORACLE_SUPPORT_BITS
        expected_mask = sum(1 << bit for bit in expected_bits)
        _require(support_mask == expected_mask,
                 f"{file_name}: per-field support mask disagrees with configuration")
        _require(support_mask >> 61 == 0,
                 f"{file_name}: reserved support-mask bit is set")
        for field, bit in PREDICTION_FIELD_SUPPORT_BITS.items():
            supported = bool(support_mask & (1 << bit))
            _require(supported or row[field] == "",
                     f"{file_name}: unsupported field {field} is non-null")
        for prefix, bit in PREDICTION_ROLLING_SUPPORT_BITS.items():
            supported = bool(support_mask & (1 << bit))
            for window in windows:
                field = _rolling_column(prefix, _window_label(window))
                _require(supported or row[field] == "",
                         f"{file_name}: unsupported rolling field {field} is non-null")

        f2_counts = {
            field: _optional_integer(row, field, file_name)
            for field in (
                "frame_packets_mac_enqueued", "frame_packets_mac_dequeued",
                "frame_packets_tx_succeeded", "frame_mpdu_attempt_failures",
                "frame_packets_terminally_dropped", "frame_packets_currently_queued",
                "frame_packets_pending_primary",
            )
        }
        _require(all(value is not None for value in f2_counts.values()),
                 f"{file_name}: bound F2 packet counters are null")
        enqueued = f2_counts["frame_packets_mac_enqueued"]
        dequeued = f2_counts["frame_packets_mac_dequeued"]
        succeeded = f2_counts["frame_packets_tx_succeeded"]
        dropped = f2_counts["frame_packets_terminally_dropped"]
        queued = f2_counts["frame_packets_currently_queued"]
        pending = f2_counts["frame_packets_pending_primary"]
        assert None not in (enqueued, dequeued, succeeded, dropped, queued, pending)
        _require(enqueued <= packet_count and dequeued <= enqueued and
                 succeeded <= packet_count and dropped <= packet_count and
                 queued <= enqueued,
                 f"{file_name}: frame MPDU count invariant failed")
        _require(pending == packet_count - succeeded - dropped,
                 f"{file_name}: primary-pending packet conservation failed")
        queue_packets = _optional_integer(row, "mac_queue_packets", file_name)
        queue_bytes = _optional_integer(row, "mac_queue_service_bytes", file_name)
        frame_queue_bytes = _optional_integer(
            row, "frame_mac_service_bytes_currently_queued", file_name
        )
        _require(queue_packets is not None and queue_bytes is not None and
                 frame_queue_bytes is not None,
                 f"{file_name}: bound queue fields are null")
        _require(queue_packets >= queued and queue_bytes >= frame_queue_bytes,
                 f"{file_name}: frame queue exceeds target queue")
        not_ack = _optional_integer(
            row, "frame_mac_service_bytes_not_acknowledged", file_name
        )
        pending_bytes = _optional_integer(
            row, "frame_mac_service_bytes_pending_primary", file_name
        )
        _require((not_ack is None) == (pending_bytes is None),
                 f"{file_name}: partial frame service-byte support")
        _require(not_ack is not None and pending_bytes is not None,
                 f"{file_name}: fixed IPv4/UDP stack omitted exact service bytes")
        _require(pending_bytes <= not_ack and frame_queue_bytes <= pending_bytes,
                 f"{file_name}: primary-pending byte conservation failed")
        expected_frame_service_bytes = int(frame["frame_size_bytes"]) + packet_count * 86
        if offset == 0:
            _require(not_ack == expected_frame_service_bytes and
                     pending_bytes == expected_frame_service_bytes,
                     f"{file_name}: T0 MAC service-byte plan mismatch")
        if succeeded == packet_count:
            _require(not_ack == 0 and pending_bytes == 0,
                     f"{file_name}: completed frame retains service work")
        ahead_packets = _optional_integer(row, "packets_ahead_of_frame", file_name)
        ahead_bytes = _optional_integer(
            row, "mac_service_bytes_ahead_of_frame", file_name
        )
        _require((ahead_packets is None) == (ahead_bytes is None),
                 f"{file_name}: partial ahead-of-frame state")
        if ahead_packets is not None and ahead_bytes is not None:
            _require(ahead_packets <= queue_packets and ahead_bytes <= queue_bytes,
                     f"{file_name}: ahead-of-frame state exceeds queue")

        supported_oracle_fields = (
            "current_cw", "nav_remaining_us", "current_phy_state",
            "channel_access_status", "medium_busy_now",
        )
        unsupported_oracle_fields = (
            "remaining_backoff_slots", "expected_access_reason_within_slack",
        )
        oracle_fields = supported_oracle_fields + unsupported_oracle_fields
        if oracle_enabled:
            _require(all(row[field] != "" for field in supported_oracle_fields),
                     f"{file_name}: enabled supported oracle field is null")
            _require(all(row[field] == "" for field in unsupported_oracle_fields),
                     f"{file_name}: unsafe oracle field is non-null")
            _require(row["current_phy_state"] in PHY_STATES,
                     f"{file_name}: invalid PHY state")
            _require(row["channel_access_status"] in ACCESS_STATUSES,
                     f"{file_name}: invalid access status")
            _require(row["medium_busy_now"] in {"0", "1"},
                     f"{file_name}: invalid medium-busy flag")
        else:
            _require(all(row[field] == "" for field in oracle_fields),
                     f"{file_name}: disabled oracle field is non-null")

        for window in windows:
            label = _window_label(window)
            attempts = _integer(row, f"mpdu_attempts_{label}", file_name)
            retries = _integer(row, f"mpdu_retries_{label}", file_name)
            _integer(row, f"mpdu_positive_acks_{label}", file_name)
            _integer(row, f"mpdu_attempt_failures_{label}", file_name)
            acknowledged_bytes = _integer(
                row, f"acknowledged_mac_service_bytes_{label}", file_name
            )
            ratio = _optional_number(row, f"mpdu_retry_ratio_{label}", file_name)
            if attempts == 0:
                _require(ratio is None, f"{file_name}: zero-attempt retry ratio is non-null")
            else:
                _require(ratio is not None and _close(ratio, retries / attempts),
                         f"{file_name}: retry ratio mismatch")
            for prefix in (
                "mpdu_queue_to_ack_mean", "mpdu_queue_to_ack_p95",
                "mpdu_first_attempt_to_ack_mean", "mpdu_first_attempt_to_ack_p95",
            ):
                _optional_number(row, f"{prefix}_{label}_us", file_name)
            coverage = _number(row, f"history_coverage_{label}_us", file_name)
            _require(coverage <= window + 1e-6,
                     f"{file_name}: history coverage exceeds window")
            durations = [
                _number(row, f"phy_{state}_time_{label}_us", file_name)
                for state in ("tx", "rx", "busy", "idle", "other")
            ]
            fractions = [
                _optional_number(row, f"phy_{state}_fraction_{label}", file_name)
                for state in ("tx", "rx", "busy", "idle", "other")
            ]
            _require(math.isclose(sum(durations), coverage, rel_tol=1e-9, abs_tol=1e-3),
                     f"{file_name}: incomplete PHY duration accounting")
            if coverage == 0:
                _require(all(value is None for value in fractions),
                         f"{file_name}: zero-coverage PHY fraction is non-null")
            else:
                _require(all(value is not None for value in fractions),
                         f"{file_name}: covered PHY fraction is null")
                _require(math.isclose(sum(value for value in fractions if value is not None),
                                      1.0, rel_tol=1e-9, abs_tol=1e-6),
                         f"{file_name}: PHY fractions do not sum to one")
                for duration, fraction in zip(durations, fractions):
                    assert fraction is not None
                    _require(math.isclose(fraction, duration / coverage,
                                          rel_tol=1e-9, abs_tol=1e-6),
                             f"{file_name}: PHY duration/fraction mismatch")
            if acknowledged_bytes == 0:
                # Zero acknowledged bytes does not imply zero ACKs for a malformed
                # zero-byte MPDU, which this experiment contract already excludes.
                _require(_integer(row, f"mpdu_positive_acks_{label}", file_name) == 0,
                         f"{file_name}: positive ACK has no acknowledged service bytes")

        observed_order.append((sample_time, frame_id, path_id, offset, copy_id))
        by_frame.setdefault(frame_id, []).append(row)

    _require(observed_order == sorted(observed_order),
             "prediction_samples.csv: rows are not deterministically ordered")
    for frame_id, rows in by_frame.items():
        _require({int(row["sample_offset_us"]) for row in rows} == set(offsets),
                 "prediction_samples.csv: frame is missing a configured stage")
        rows.sort(key=lambda row: int(row["sample_offset_us"]))
        previous_values: dict[str, int] = {}
        previous_complete = False
        previous_actionable = True
        for row in rows:
            for field in cumulative_fields:
                value = _optional_integer(row, field, "prediction_samples.csv")
                if value is not None:
                    _require(value >= previous_values.get(field, 0),
                             f"prediction_samples.csv: decreasing {field}")
                    previous_values[field] = value
            complete = _flag(row, "sender_mac_complete", "prediction_samples.csv")
            actionable = _flag(row, "actionable", "prediction_samples.csv")
            _require(not previous_complete or complete,
                     "prediction_samples.csv: sender completion reverted")
            _require(previous_actionable or not actionable,
                     "prediction_samples.csv: actionability reverted")
            previous_complete = complete
            previous_actionable = actionable
        t0 = rows[0]
        _require(int(t0["sample_offset_us"]) == 0 and
                 int(t0["packets_submitted"]) == 0 and
                 int(t0["frame_packets_mac_enqueued"]) == 0,
                 "prediction_samples.csv: T0 contains frame-caused sender state")

    polling_required = PREDICTION_POLLING_BASE_COLUMNS | rolling_columns
    polling_rows = _csv(polling_path, polling_required)
    _require(
        len(polling_rows) == len(samples),
        "prediction_polling_samples.csv: cardinality mismatch",
    )
    sample_times = {
        (
            row["frame_id"],
            row["path_id"],
            row["copy_id"],
            row["sample_stage"],
            row["sample_offset_us"],
        ): int(row["sample_time_ns"])
        for row in samples
    }
    polling_keys: set[tuple[str, str, str, str, str]] = set()
    for row in polling_rows:
        file_name = "prediction_polling_samples.csv"
        key = (
            row["frame_id"],
            row["path_id"],
            row["copy_id"],
            row["sample_stage"],
            row["sample_offset_us"],
        )
        _require(key in sample_times, f"{file_name}: orphan observation")
        _require(key not in polling_keys, f"{file_name}: duplicate observation key")
        polling_keys.add(key)
        _require(row["run_id"] == run_id, f"{file_name}: run_id mismatch")
        _require(
            _integer(row, "polling_schema_version", file_name)
            == PREDICTION_POLLING_SCHEMA_VERSION,
            f"{file_name}: invalid schema version",
        )
        _require(_flag(row, "report_available", file_name), f"{file_name}: missing report")
        sample_time = sample_times[key]
        capture_time = _integer(row, "capture_time_ns", file_name)
        available_time = _integer(row, "available_time_ns", file_name)
        staleness_us = _number(row, "staleness_us", file_name)
        _require(capture_time % (polling_interval_us * 1000) == 0,
                 f"{file_name}: report is off the polling cadence")
        _require(available_time == capture_time + polling_delay_us * 1000,
                 f"{file_name}: report delay mismatch")
        _require(available_time <= sample_time, f"{file_name}: report is not available")
        _require(
            staleness_us == (sample_time - capture_time) // 1000,
            f"{file_name}: staleness mismatch",
        )
        _require(1000 <= staleness_us < 2000,
                 f"{file_name}: staleness is outside [1 ms, 2 ms)")
        latest = _optional_integer(row, "latest_feature_event_time_ns", file_name)
        if latest is not None:
            _require(latest <= capture_time, f"{file_name}: future feature event")

    event_count = 0
    if event_enabled:
        events = _csv(events_path, PREDICTION_EVENT_COLUMNS)
        _require(all(None not in row for row in events),
                 "prediction_events.csv: rows exceed the declared schema")
        previous_time = -1
        previous_sequence = 0
        payload_size = int(config["stream"]["payload_size_bytes"])
        registered_frames: set[tuple[int, int, int]] = set()
        submitted_packets: set[tuple[int, int, int, int]] = set()
        enqueued_packets: set[tuple[int, int, int, int]] = set()
        positive_acked_packets: set[tuple[int, int, int, int]] = set()
        terminal_packets: set[tuple[int, int, int, int]] = set()
        attempt_counts: dict[tuple[int, int, int, int], int] = {}
        unresolved_attempts: dict[tuple[int, int, int, int], int] = {}
        events_by_sequence: dict[int, dict[str, str]] = {}
        for row in events:
            file_name = "prediction_events.csv"
            _require(row["run_id"] == run_id, f"{file_name}: run_id mismatch")
            _require(_integer(row, "event_schema_version", file_name) ==
                     PREDICTION_EVENT_SCHEMA_VERSION,
                     f"{file_name}: invalid event schema version")
            event_time = _integer(row, "event_time_ns", file_name)
            event_sequence = _integer(row, "event_sequence", file_name)
            _require(event_time >= previous_time, f"{file_name}: events are unordered")
            _require(event_sequence == previous_sequence + 1,
                     f"{file_name}: event sequence is not contiguous")
            previous_time = event_time
            previous_sequence = event_sequence
            events_by_sequence[event_sequence] = row
            _require(row["event_type"] in PREDICTION_EVENT_TYPES,
                     f"{file_name}: invalid event type")
            _require(_integer(row, "path_id", file_name) == selected_path,
                     f"{file_name}: event path isolation failed")
            _integer(row, "mac_queue_packets", file_name)
            _integer(row, "mac_queue_service_bytes", file_name)
            for field in (
                "copy_id", "frame_id", "packet_index", "attempt_number",
                "mac_service_bytes", "current_mcs", "current_nss",
                "current_channel_width_mhz", "current_guard_interval_ns",
                "phy_interval_start_ns", "phy_interval_end_ns",
            ):
                _optional_integer(row, field, file_name)
            if row["current_phy_state"]:
                _require(row["current_phy_state"] in PHY_STATES,
                         f"{file_name}: invalid PHY state")
            event_type = row["event_type"]
            if event_type == "MPDU_POSITIVE_ACK":
                finalizes_attempt = _flag(
                    row, "finalizes_attempt_success", file_name
                )
            else:
                _require(row["finalizes_attempt_success"] == "",
                         f"{file_name}: non-ACK event has attempt-success flag")
                finalizes_attempt = None
            if event_type == "PHY_INTERVAL_REVISION":
                _require(row["phy_interval_revision_kind"] in
                         PHY_INTERVAL_REVISION_KINDS,
                         f"{file_name}: invalid PHY interval revision kind")
                _require(row["phy_interval_state"] in PHY_STATES,
                         f"{file_name}: invalid PHY interval state")
                interval_start = _integer(
                    row, "phy_interval_start_ns", file_name
                )
                interval_end = _integer(row, "phy_interval_end_ns", file_name)
                _require(interval_end >= interval_start,
                         f"{file_name}: invalid PHY interval revision")
            else:
                _require(
                    row["phy_interval_revision_kind"] == "" and
                    row["phy_interval_state"] == "" and
                    row["phy_interval_start_ns"] == "" and
                    row["phy_interval_end_ns"] == "",
                    f"{file_name}: non-PHY event has interval fields",
                )
            if row["frame_id"]:
                event_frame_id = int(row["frame_id"])
                _require(event_frame_id in frames_by_id, f"{file_name}: unknown frame")
                _require(int(row["copy_id"]) == 0, f"{file_name}: invalid copy")
                packet_index = int(row["packet_index"])
                frame = frames_by_id[event_frame_id]
                packet_count = int(frame["packet_count"])
                _require(0 <= packet_index < packet_count,
                         f"{file_name}: invalid packet index")
                frame_key = (
                    selected_path, int(row["copy_id"]), event_frame_id
                )
                packet_key = frame_key + (packet_index,)
                if event_type == "FRAME_REGISTERED":
                    _require(frame_key not in registered_frames,
                             f"{file_name}: duplicate frame registration")
                    registered_frames.add(frame_key)
                elif event_type == "PACKET_SUBMITTED":
                    _require(frame_key in registered_frames,
                             f"{file_name}: submission precedes frame registration")
                    _require(packet_key not in submitted_packets,
                             f"{file_name}: duplicate packet submission")
                    submitted_packets.add(packet_key)
                elif event_type == "MAC_ENQUEUE":
                    _require(packet_key in submitted_packets,
                             f"{file_name}: MAC enqueue precedes submission")
                    _require(packet_key not in enqueued_packets,
                             f"{file_name}: duplicate MAC enqueue")
                    enqueued_packets.add(packet_key)
                elif event_type == "MPDU_TX_ATTEMPT":
                    _require(packet_key in enqueued_packets,
                             f"{file_name}: PHY attempt precedes MAC enqueue")
                    _require(packet_key not in unresolved_attempts,
                             f"{file_name}: overlapping unresolved attempts")
                    attempt = _integer(row, "attempt_number", file_name)
                    _require(attempt == attempt_counts.get(packet_key, 0) + 1,
                             f"{file_name}: noncontiguous attempt number")
                    attempt_counts[packet_key] = attempt
                    unresolved_attempts[packet_key] = attempt
                elif event_type == "MPDU_RETRY":
                    attempt = _integer(row, "attempt_number", file_name)
                    _require(attempt_counts.get(packet_key, 0) >= 2 and
                             unresolved_attempts.get(packet_key) == attempt,
                             f"{file_name}: retry is not an active repeated attempt")
                elif event_type == "MPDU_TX_ATTEMPT_FAILURE":
                    attempt = _integer(row, "attempt_number", file_name)
                    _require(unresolved_attempts.get(packet_key) == attempt,
                             f"{file_name}: failure does not finalize active attempt")
                    del unresolved_attempts[packet_key]
                elif event_type == "MPDU_POSITIVE_ACK":
                    _require(packet_key not in positive_acked_packets,
                             f"{file_name}: duplicate logical positive ACK")
                    if finalizes_attempt:
                        attempt = _integer(row, "attempt_number", file_name)
                        _require(unresolved_attempts.get(packet_key) == attempt,
                                 f"{file_name}: ACK finalizes wrong attempt")
                        del unresolved_attempts[packet_key]
                    else:
                        _require(row["attempt_number"] == "" and
                                 packet_key not in unresolved_attempts,
                                 f"{file_name}: late ACK has an active attempt")
                    positive_acked_packets.add(packet_key)
                    terminal_packets.discard(packet_key)
                elif event_type == "MPDU_TERMINAL_DROP":
                    _require(packet_key not in positive_acked_packets and
                             packet_key not in terminal_packets and
                             packet_key not in unresolved_attempts,
                             f"{file_name}: invalid terminal packet transition")
                    terminal_packets.add(packet_key)
                elif event_type == "MAC_DEQUEUE":
                    _require(packet_key in enqueued_packets,
                             f"{file_name}: MAC dequeue precedes enqueue")
                if row["mac_service_bytes"]:
                    frame_size = int(frame["frame_size_bytes"])
                    packet_payload = min(
                        payload_size, frame_size - packet_index * payload_size
                    )
                    expected_service_bytes = packet_payload + 50 + 28 + 8
                    _require(int(row["mac_service_bytes"]) == expected_service_bytes,
                             f"{file_name}: MAC service-byte domain mismatch")
        for row in samples:
            sequence = int(row["latest_feature_event_sequence"])
            if sequence == 0:
                continue
            _require(sequence in events_by_sequence,
                     "prediction_samples.csv: watermark event is absent")
            _require(int(events_by_sequence[sequence]["event_time_ns"]) ==
                     int(row["latest_feature_event_time_ns"]),
                     "prediction_samples.csv: watermark time/sequence mismatch")

        enqueue_times: dict[tuple[int, int, int, int], int] = {}
        first_attempt_times: dict[tuple[int, int, int, int], int] = {}
        rolling_events: dict[str, list[tuple[Any, ...]]] = {
            "attempt": [], "positive_ack": [], "failure": [], "retry": [],
        }
        for row in events:
            if not row["frame_id"]:
                continue
            key = (
                int(row["path_id"]), int(row["copy_id"]),
                int(row["frame_id"]), int(row["packet_index"]),
            )
            event_key = (int(row["event_time_ns"]), int(row["event_sequence"]))
            event_type = row["event_type"]
            if event_type == "MAC_ENQUEUE":
                enqueue_times[key] = event_key[0]
            elif event_type == "MPDU_TX_ATTEMPT":
                first_attempt_times.setdefault(key, event_key[0])
                rolling_events["attempt"].append(event_key)
            elif event_type == "MPDU_RETRY":
                rolling_events["retry"].append(event_key)
            elif event_type == "MPDU_TX_ATTEMPT_FAILURE":
                rolling_events["failure"].append(event_key)
            elif event_type == "MPDU_POSITIVE_ACK":
                _require(key in enqueue_times and key in first_attempt_times,
                         "prediction_events.csv: ACK latency origin is absent")
                rolling_events["positive_ack"].append(
                    event_key + (
                        int(row["mac_service_bytes"]),
                        (event_key[0] - enqueue_times[key]) / 1000.0,
                        (event_key[0] - first_attempt_times[key]) / 1000.0,
                    )
                )

        rolling_keys = {
            name: [(event[0], event[1]) for event in rows]
            for name, rows in rolling_events.items()
        }

        def window_slice(name: str, lower_ns: int, sample_ns: int,
                         watermark: int) -> list[tuple[Any, ...]]:
            keys = rolling_keys[name]
            rows = rolling_events[name]
            left = bisect.bisect_right(keys, (lower_ns, 2**64 - 1))
            right = bisect.bisect_right(keys, (sample_ns, watermark))
            return rows[left:right]

        for sample in samples:
            sample_time = int(sample["sample_time_ns"])
            watermark = int(sample["latest_feature_event_sequence"])
            for window in windows:
                label = _window_label(window)
                lower_ns = max(sample_time - window * 1000, 0)
                attempts = window_slice("attempt", lower_ns, sample_time, watermark)
                positive_acks = window_slice(
                    "positive_ack", lower_ns, sample_time, watermark
                )
                failures = window_slice("failure", lower_ns, sample_time, watermark)
                retries = window_slice("retry", lower_ns, sample_time, watermark)
                _require(int(sample[f"mpdu_attempts_{label}"]) == len(attempts),
                         "prediction_samples.csv: reconstructed attempt count differs")
                _require(int(sample[f"mpdu_positive_acks_{label}"]) ==
                         len(positive_acks),
                         "prediction_samples.csv: reconstructed ACK count differs")
                _require(int(sample[f"mpdu_attempt_failures_{label}"]) ==
                         len(failures),
                         "prediction_samples.csv: reconstructed failure count differs")
                _require(int(sample[f"mpdu_retries_{label}"]) == len(retries),
                         "prediction_samples.csv: reconstructed retry count differs")
                expected_bytes = sum(event[2] for event in positive_acks)
                _require(int(sample[f"acknowledged_mac_service_bytes_{label}"]) ==
                         expected_bytes,
                         "prediction_samples.csv: reconstructed ACK bytes differ")
                ratio = _optional_number(
                    sample, f"mpdu_retry_ratio_{label}", "prediction_samples.csv"
                )
                if attempts:
                    _require(ratio is not None and
                             _close(ratio, len(retries) / len(attempts)),
                             "prediction_samples.csv: reconstructed retry ratio differs")
                else:
                    _require(ratio is None,
                             "prediction_samples.csv: zero-attempt retry ratio is non-null")
                for offset, prefix in (
                    (3, "mpdu_queue_to_ack"),
                    (4, "mpdu_first_attempt_to_ack"),
                ):
                    values = [float(event[offset]) for event in positive_acks]
                    mean = _optional_number(
                        sample, f"{prefix}_mean_{label}_us",
                        "prediction_samples.csv",
                    )
                    p95 = _optional_number(
                        sample, f"{prefix}_p95_{label}_us",
                        "prediction_samples.csv",
                    )
                    if values:
                        _require(mean is not None and
                                 _close(mean, sum(values) / len(values)),
                                 "prediction_samples.csv: reconstructed latency mean differs")
                        _require(p95 is not None and
                                 _close(p95, _type7_percentile(values, 0.95)),
                                 "prediction_samples.csv: reconstructed latency P95 differs")
                    else:
                        _require(mean is None and p95 is None,
                                 "prediction_samples.csv: empty latency window is non-null")

        reconstruction_window = 5000 if 5000 in windows else windows[0]
        reconstruction_label = _window_label(reconstruction_window)
        reconstruction_sample = next(
            (
                row for row in samples
                if int(row["latest_feature_event_sequence"]) > 0 and
                float(row[f"history_coverage_{reconstruction_label}_us"]) > 0
            ),
            None,
        )
        if reconstruction_sample is not None:
            watermark = int(reconstruction_sample["latest_feature_event_sequence"])
            revisions: dict[
                tuple[int, str, bool], tuple[int, int, str, int, int]
            ] = {}
            telemetry_start_ns: int | None = None
            for row in events:
                sequence = int(row["event_sequence"])
                if sequence > watermark:
                    break
                if row["event_type"] != "PHY_INTERVAL_REVISION":
                    continue
                start_ns = int(row["phy_interval_start_ns"])
                end_ns = int(row["phy_interval_end_ns"])
                state = row["phy_interval_state"]
                initial = row["phy_interval_revision_kind"] == "INITIAL"
                revisions[(start_ns, state, initial)] = (
                    start_ns, end_ns, state, int(row["event_time_ns"]), sequence
                )
                if initial:
                    telemetry_start_ns = start_ns
            _require(telemetry_start_ns is not None,
                     "prediction_events.csv: initial PHY interval is absent")
            sample_time = int(reconstruction_sample["sample_time_ns"])
            lower_ns = max(sample_time - reconstruction_window * 1000, 0)
            coverage_start = max(telemetry_start_ns, lower_ns)
            boundaries = {coverage_start, sample_time}
            intervals = []
            for interval in revisions.values():
                start_ns, end_ns, _state, _reported, _sequence = interval
                if end_ns <= coverage_start or start_ns >= sample_time:
                    continue
                intervals.append(interval)
                boundaries.add(max(start_ns, coverage_start))
                boundaries.add(min(end_ns, sample_time))
            durations = {
                "TX": 0.0, "RX": 0.0, "CCA_BUSY": 0.0, "IDLE": 0.0,
                "OTHER": 0.0,
            }
            priority = {
                "OFF": 7, "SLEEP": 6, "TX": 5, "RX": 4,
                "SWITCHING": 3, "CCA_BUSY": 2, "IDLE": 1,
            }
            ordered_boundaries = sorted(boundaries)
            for start_ns, end_ns in zip(
                ordered_boundaries, ordered_boundaries[1:]
            ):
                if end_ns <= start_ns:
                    continue
                midpoint = start_ns + (end_ns - start_ns) // 2
                candidates = [
                    interval for interval in intervals
                    if interval[0] <= midpoint < interval[1]
                ]
                _require(bool(candidates),
                         "prediction_events.csv: PHY reconstruction has a gap")
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
                durations[bucket] += (end_ns - start_ns) / 1000.0
            coverage_us = (sample_time - coverage_start) / 1000.0
            comparisons = {
                "phy_tx_time": durations["TX"],
                "phy_rx_time": durations["RX"],
                "phy_busy_time": durations["CCA_BUSY"],
                "phy_idle_time": durations["IDLE"],
                "phy_other_time": durations["OTHER"],
                "history_coverage": coverage_us,
            }
            for prefix, expected in comparisons.items():
                actual = float(
                    reconstruction_sample[
                        f"{prefix}_{reconstruction_label}_us"
                    ]
                )
                _require(_close(actual, expected),
                         f"prediction_samples.csv: reconstructed {prefix} differs")
            if coverage_us > 0:
                for prefix, duration in (
                    ("phy_tx_fraction", durations["TX"]),
                    ("phy_rx_fraction", durations["RX"]),
                    ("phy_busy_fraction", durations["CCA_BUSY"]),
                    ("phy_idle_fraction", durations["IDLE"]),
                    ("phy_other_fraction", durations["OTHER"]),
                ):
                    actual = float(
                        reconstruction_sample[f"{prefix}_{reconstruction_label}"]
                    )
                    _require(_close(actual, duration / coverage_us),
                             f"prediction_samples.csv: reconstructed {prefix} differs")
        event_count = len(events)

    return {
        "prediction_sample_count": len(samples),
        "prediction_event_count": event_count,
    }


def validate_run(
    run_dir: Path | str,
    expected_run_id: str | None = None,
    expected_project_commit: str | None = None,
    expected_ns3_commit: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    missing = sorted(name for name in CORE_FILES if not (run_dir / name).is_file())
    _require(not missing, f"missing core files: {', '.join(missing)}")
    config = _json(run_dir / "resolved_config.json")
    build = _json(run_dir / "build_info.json")
    summary = _json(run_dir / "summary.json")
    _require(BUILD_KEYS <= build.keys(), "build_info.json: missing identity fields")
    _require(SUMMARY_KEYS <= summary.keys(), "summary.json: missing fields")
    run_id = expected_run_id or config.get("run_id")
    _require(isinstance(run_id, str) and run_id, "resolved_config.json: invalid run_id")
    _require(config.get("run_id") == run_id, "resolved_config.json: run_id mismatch")
    _require(isinstance(config.get("seed"), int) and config["seed"] > 0, "invalid seed")
    _require(isinstance(config.get("run"), int) and config["run"] > 0, "invalid run")
    _require(config.get("topology") in {"single_link", "dual_interface", "mlo_str"},
             "resolved_config.json: invalid topology")
    _require(isinstance(config.get("stream"), dict), "resolved_config.json: missing stream")
    wifi = config.get("wifi", {})
    max_inflights = int(wifi.get("sta_max_inflights", 1))
    _require(1 <= max_inflights <= 15,
             "resolved_config.json: invalid STA max inflights")
    _require(max_inflights == 1 or
             (config["topology"] == "mlo_str" and wifi.get("block_ack_enabled") is True),
             "resolved_config.json: multiple inflights require MLO Block Ack")
    ul_ofdma_enabled = wifi.get("ul_ofdma_enabled", False)
    _require(isinstance(ul_ofdma_enabled, bool),
             "resolved_config.json: invalid UL OFDMA enabled flag")
    if ul_ofdma_enabled:
        _require(wifi.get("ul_ofdma_scope") in {"target_aps", "all_he_eht_aps"},
                 "resolved_config.json: invalid UL OFDMA scope")
        _require(int(wifi.get("ul_ofdma_access_interval_ms", 0)) > 0,
                 "resolved_config.json: invalid UL OFDMA access interval")
        _require(1 <= int(wifi.get("ul_ofdma_max_stations", 0)) <= 74,
                 "resolved_config.json: invalid UL OFDMA station count")
        _require(int(wifi.get("ul_ofdma_psdu_size_bytes", 0)) > 0,
                 "resolved_config.json: invalid UL OFDMA PSDU size")
    if "ul_ofdma_enabled" in wifi:
        _require((run_dir / "ofdma_summary.csv").is_file(),
                 "missing core file: ofdma_summary.csv")
        ofdma_rows = _csv(run_dir / "ofdma_summary.csv", OFDMA_COLUMNS)
        _require({row["device_group"] for row in ofdma_rows} ==
                 {"target", "same_bss_background", "obss"},
                 "ofdma_summary.csv: invalid device groups")
        for row in ofdma_rows:
            for column in OFDMA_COLUMNS - {"device_group"}:
                _integer(row, column, "ofdma_summary.csv")
    if expected_project_commit is not None:
        _require(build["project_git_commit"] == expected_project_commit,
                 "build_info.json: project commit mismatch")
    if expected_ns3_commit is not None:
        _require(build["ns3_upstream_commit"] == expected_ns3_commit,
                 "build_info.json: ns-3 commit mismatch")
    _require(all(isinstance(build[key], str) and build[key] for key in BUILD_KEYS),
             "build_info.json: empty build identity")

    frames = _csv(run_dir / "frames.csv", FRAME_COLUMNS)
    decisions = _csv(run_dir / "policy_decisions.csv", DECISION_COLUMNS)
    links = _csv(run_dir / "link_intervals.csv", LINK_COLUMNS)
    mac = _csv(run_dir / "mac_summary.csv", MAC_COLUMNS)
    obss = config.get("background", {}).get("obss", {})
    obss_enabled = obss.get("profile", "none") != "none"
    flows: list[dict[str, str]] = []
    periods: list[dict[str, str]] = []
    if obss_enabled:
        _require((run_dir / "background_flows.csv").is_file(),
                 "missing core file: background_flows.csv")
        _require((run_dir / "background_rate_periods.csv").is_file(),
                 "missing core file: background_rate_periods.csv")
        flows = _csv(run_dir / "background_flows.csv", BACKGROUND_FLOW_COLUMNS)
        periods = _csv(run_dir / "background_rate_periods.csv", BACKGROUND_PERIOD_COLUMNS)
    _require(frames, "frames.csv: no frame rows")
    _require(links, "link_intervals.csv: no link rows")
    for rows, name in ((frames, "frames.csv"), (decisions, "policy_decisions.csv")):
        _require(all(row["run_id"] == run_id for row in rows), f"{name}: run_id mismatch")

    seen: set[int] = set()
    incomplete = misses = complete = delivered = duplicated_frames = 0
    for row in frames:
        frame_id = _integer(row, "frame_id", "frames.csv")
        _require(frame_id not in seen, "frames.csv: duplicate frame_id")
        seen.add(frame_id)
        generation = _integer(row, "generation_time_us", "frames.csv")
        size = _integer(row, "frame_size_bytes", "frames.csv")
        packets = _integer(row, "packet_count", "frames.csv")
        deadline = _integer(row, "deadline_us", "frames.csv")
        unique = _integer(row, "unique_packets_received", "frames.csv")
        is_incomplete = _flag(row, "incomplete", "frames.csv")
        is_miss = _flag(row, "deadline_miss", "frames.csv")
        is_duplicated = _flag(row, "duplicated", "frames.csv")
        duplicated_frames += is_duplicated
        _require(size > 0 and packets > 0, "frames.csv: nonpositive frame size/packet count")
        _require(row["frame_type"] in {
            "UNKNOWN", "I_FRAME", "P_FRAME", "B_FRAME", "PRIORITY_HIGH",
            "PRIORITY_NORMAL", "PRIORITY_LOW",
        }, "frames.csv: invalid frame_type")
        _require(unique <= packets, "frames.csv: received packet count exceeds packet count")
        completion = row["union_completion_us"]
        latency = row["union_latency_us"]
        if is_incomplete:
            incomplete += 1
            _require(not completion and not latency, "frames.csv: incomplete frame has completion")
        else:
            complete += 1
            delivered += size
            _require(bool(completion) and bool(latency), "frames.csv: complete frame lacks latency")
            _require(int(completion) - generation == int(latency),
                     "frames.csv: completion/latency invariant failed")
            _require(unique == packets, "frames.csv: complete frame lacks unique packets")
        if is_miss:
            misses += 1
        if deadline and completion:
            _require(is_miss == (int(latency) > deadline), "frames.csv: deadline flag mismatch")
        elif deadline and is_incomplete:
            _require(is_miss, "frames.csv: incomplete deadline frame must miss")

    total = len(frames)
    stream = config.get("stream", {})
    if stream.get("source") == "trace":
        trace_path = Path(stream.get("trace_file", ""))
        _require(trace_path.is_file(), "resolved_config.json: trace file is not readable")
        trace_rows = _csv(trace_path, {
            "frame_id", "generation_time_us", "size_bytes", "frame_type", "deadline_us",
        })
        _require(len(trace_rows) == total, "frames.csv: trace/frame count mismatch")
        warmup_us = round(float(config["warmup_s"]) * 1_000_000)
        by_id = {int(row["frame_id"]): row for row in frames}
        for trace in trace_rows:
            frame_id = int(trace["frame_id"])
            _require(frame_id in by_id, "frames.csv: trace frame ID missing")
            frame = by_id[frame_id]
            _require(int(frame["generation_time_us"]) ==
                     int(trace["generation_time_us"]) + warmup_us,
                     "frames.csv: trace generation interval changed")
            _require(frame["frame_size_bytes"] == trace["size_bytes"],
                     "frames.csv: trace frame size changed")
            _require(frame["frame_type"] == trace["frame_type"],
                     "frames.csv: trace frame type changed")
            _require(frame["deadline_us"] == trace["deadline_us"],
                     "frames.csv: trace deadline changed")
    elif stream.get("source") == "synthetic" and "gop_length" in stream:
        gop_length = int(stream["gop_length"])
        interframe_size = int(stream["frame_size_bytes"])
        multiplier = float(stream["keyframe_size_multiplier"])
        _require(gop_length > 0 and interframe_size > 0 and multiplier >= 1,
                 "resolved_config.json: invalid synthetic GOP")
        keyframe_size = int(interframe_size * multiplier + 0.5)
        for frame in frames:
            frame_id = int(frame["frame_id"])
            keyframe = frame_id % gop_length == 0
            _require(frame["frame_type"] == ("I_FRAME" if keyframe else "P_FRAME"),
                     "frames.csv: synthetic GOP frame type mismatch")
            _require(int(frame["frame_size_bytes"]) ==
                     (keyframe_size if keyframe else interframe_size),
                     "frames.csv: synthetic GOP frame size mismatch")
    _require(len(decisions) == total, "policy_decisions.csv: decision/frame count mismatch")
    _require({int(row["frame_id"]) for row in decisions} == seen,
             "policy_decisions.csv: frame IDs mismatch")
    frames_by_id = {row["frame_id"]: row for row in frames}
    for decision in decisions:
        frame = frames_by_id[decision["frame_id"]]
        _require(decision["run_id"] == run_id and
                 decision["policy"] == frame["policy"] and
                 decision["primary_link"] == frame["primary_link"] and
                 decision["duplicated"] == frame["duplicated"] and
                 decision["decision_time_us"] == frame["decision_time_us"],
                 "policy_decisions.csv: decision/frame mismatch")
        duplicated = _flag(decision, "duplicated", "policy_decisions.csv")
        _require(bool(decision["reason"]), "policy_decisions.csv: empty reason")
        if duplicated:
            secondary = _integer(decision, "secondary_link", "policy_decisions.csv")
            _require(secondary != _integer(decision, "primary_link",
                                           "policy_decisions.csv"),
                     "policy_decisions.csv: primary and secondary links match")
        else:
            _require(decision["secondary_link"] == "",
                     "policy_decisions.csv: nonduplicated decision has secondary link")
    duplicated_frame_ids = {
        int(row["frame_id"]) for row in frames
        if _flag(row, "duplicated", "frames.csv")
    }
    adaptive_config: dict[str, Any] | None = None
    action_estimates: dict[int, float] = {}
    observed_budget_debt_us = 0.0
    if config["policy"] == "selective_duplication":
        selective_config = config.get("selectiveDuplication")
        _require(isinstance(selective_config, dict),
                 "resolved_config.json: missing selectiveDuplication object")
        _require(
            selective_config.get("model_id") in {
                "commodity_polling_1ms_genuine_v1",
                "commodity_polling_1ms_legacy_frame_delayed_v1",
            } and
            isinstance(selective_config.get("source_model_sha256"), str) and
            len(selective_config["source_model_sha256"]) == 64 and
            all(character in "0123456789abcdef"
                for character in selective_config["source_model_sha256"]) and
            selective_config.get("feature_set") == "F0+F1-degraded" and
            selective_config.get("degradation_profile") == "polling_1ms" and
            selective_config.get("calibration") == "platt" and
            selective_config.get("stages") == ["T0", "T1", "T2", "T4"],
            "resolved_config.json: invalid selective predictor provenance",
        )
        selective_path = run_dir / "selective_duplication_decisions.csv"
        _require(selective_path.is_file(),
                 "missing core file: selective_duplication_decisions.csv")
        selective = _csv(selective_path, SELECTIVE_DECISION_COLUMNS)
        offsets = [int(value) for value in selective_config["decision_offsets_us"]]
        _require(len(selective) == total * len(offsets),
                 "selective decisions: frame/stage cardinality mismatch")
        _require(all(row["run_id"] == run_id for row in selective),
                 "selective decisions: run_id mismatch")
        allowed_decisions = {
            "below_threshold", "action", "budget_suppressed",
            "already_resolved", "not_actionable", "launch_rejected",
        }
        action_frames: set[int] = set()
        seen_samples: set[tuple[int, int]] = set()
        threshold = float(selective_config["probability_threshold"])
        budget = float(selective_config["frame_budget"])
        capacity = max(1.0, budget * int(selective_config["burst_horizon_frames"]))
        for row in selective:
            frame_id = _integer(row, "frame_id", "selective_duplication_decisions.csv")
            offset = _integer(row, "sample_offset_us",
                              "selective_duplication_decisions.csv")
            _require(frame_id in seen and offset in offsets,
                     "selective decisions: unknown frame or stage")
            _require((frame_id, offset) not in seen_samples,
                     "selective decisions: duplicate frame/stage")
            seen_samples.add((frame_id, offset))
            probability = float(row["calibrated_probability"])
            before = float(row["tokens_before"])
            after = float(row["tokens_after"])
            _require(0 <= probability <= 1 and
                     _close(float(row["probability_threshold"]), threshold) and
                     _close(float(row["frame_budget"]), budget) and
                     _close(float(row["token_capacity"]), capacity),
                     "selective decisions: invalid probability or configuration")
            _require(-1e-12 <= before <= capacity + 1e-12 and
                     -1e-12 <= after <= capacity + 1e-12,
                     "selective decisions: token balance out of bounds")
            decision_name = row["decision"]
            _require(decision_name in allowed_decisions,
                     "selective decisions: unknown decision")
            launched = _flag(row, "secondary_launched",
                             "selective_duplication_decisions.csv")
            _require(launched == (decision_name == "action"),
                     "selective decisions: action/launch mismatch")
            if launched:
                _require(frame_id not in action_frames and
                         _close(after, before - 1.0),
                         "selective decisions: invalid or repeated token consumption")
                action_frames.add(frame_id)
        _require(action_frames == duplicated_frame_ids,
                 "selective decisions: launched actions do not match duplicated frames")
    elif config["policy"] == "adaptive_airtime_duplication":
        adaptive_config = config.get("adaptiveAirtimeDuplication")
        _require(isinstance(adaptive_config, dict),
                 "resolved_config.json: missing adaptiveAirtimeDuplication object")
        _validate_adaptive_config(adaptive_config)
        adaptive_path = run_dir / "adaptive_airtime_decisions.csv"
        _require(adaptive_path.is_file(),
                 "missing core file: adaptive_airtime_decisions.csv")
        adaptive = _csv(adaptive_path, ADAPTIVE_DECISION_COLUMNS)
        action_estimates = _validate_adaptive_decisions(
            adaptive, adaptive_config, frames, run_id
        )
        observed_budget_debt_us = max(
            (max(0.0, -_signed_number(
                row, "available_airtime_us", "adaptive_airtime_decisions.csv"
            )) for row in adaptive),
            default=0.0,
        )
        _require(set(action_estimates) == duplicated_frame_ids,
                 "adaptive decisions: launched actions do not match duplicated frames")
        _require(not (run_dir / "selective_duplication_decisions.csv").exists(),
                 "selective decision output exists for adaptive policy")
    else:
        _require(not (run_dir / "selective_duplication_decisions.csv").exists(),
                 "selective decision output exists for a non-selective policy")
        _require(not (run_dir / "adaptive_airtime_decisions.csv").exists(),
                 "adaptive decision output exists for a non-adaptive policy")

    meter = config.get("secondaryAirtimeMeter")
    if isinstance(meter, dict) and meter.get("enabled") is True:
        events_path = run_dir / "secondary_airtime_events.csv"
        settlements_path = run_dir / "secondary_airtime_settlements.csv"
        summary_path = run_dir / "secondary_airtime_summary.json"
        _require(events_path.is_file(), "missing core file: secondary_airtime_events.csv")
        _require(settlements_path.is_file(),
                 "missing core file: secondary_airtime_settlements.csv")
        _require(summary_path.is_file(),
                 "missing core file: secondary_airtime_summary.json")
        events = _csv(events_path, SECONDARY_AIRTIME_EVENT_COLUMNS)
        settlements = _csv(settlements_path, SECONDARY_AIRTIME_SETTLEMENT_COLUMNS)
        meter_summary = _json(summary_path)
        _validate_secondary_airtime(
            events,
            settlements,
            meter_summary,
            meter,
            links,
            config["policy"],
            run_id,
            adaptive_config,
            action_estimates,
            duplicated_frame_ids,
            observed_budget_debt_us,
        )
    else:
        _require(not (run_dir / "secondary_airtime_events.csv").exists(),
                 "secondary airtime events exist while meter is disabled")
        _require(not (run_dir / "secondary_airtime_settlements.csv").exists(),
                 "secondary airtime settlements exist while meter is disabled")
        _require(not (run_dir / "secondary_airtime_summary.json").exists(),
                 "secondary airtime summary exists while meter is disabled")

    expected = {
        "frame_count": total, "complete_frame_count": complete,
        "incomplete_frame_count": incomplete, "deadline_miss_count": misses,
        "application_bytes_delivered": delivered,
        "duplicate_frame_count": duplicated_frames,
    }
    for key, value in expected.items():
        _require(summary[key] == value, f"summary.json: {key} mismatch")
    for key, value in (
        ("complete_ratio", complete / total),
        ("incomplete_ratio", incomplete / total),
        ("deadline_miss_ratio", misses / total),
    ):
        _require(_close(float(summary[key]), value), f"summary.json: {key} mismatch")
    sent = int(summary["application_bytes_sent"])
    expected_redundant_ratio = int(summary["redundant_bytes_sent"]) / sent if sent else 0
    _require(_close(float(summary["redundant_byte_ratio"]), expected_redundant_ratio),
             "summary.json: redundant_byte_ratio mismatch")

    link_ids = [_integer(row, "link_id", "link_intervals.csv") for row in links]
    _require(len(link_ids) == len(set(link_ids)), "link_intervals.csv: duplicate links")
    for summary_key, column in (
        ("application_bytes_sent", "application_bytes_sent"),
        ("redundant_bytes_sent", "redundant_bytes"),
        ("successful_mpdus", "successful_mpdus"), ("failed_mpdus", "failed_mpdus"),
        ("retransmissions", "retransmissions"), ("phy_tx_time_us", "phy_tx_time_us"),
        ("phy_rx_time_us", "phy_rx_time_us"),
        ("phy_cca_busy_time_us", "phy_cca_busy_time_us"),
    ):
        value = sum(_integer(row, column, "link_intervals.csv") for row in links)
        _require(summary[summary_key] == value, f"per-link total mismatch: {summary_key}")
    _require({int(row["link_id"]) for row in mac} == set(link_ids),
             "mac_summary.csv: link IDs mismatch")
    for key in ("successful_mpdus", "failed_mpdus", "retransmissions"):
        _require(sum(_integer(row, key, "mac_summary.csv") for row in mac) == summary[key],
                 f"mac_summary.csv: {key} total mismatch")
    if obss_enabled:
        station_count = int(obss["stations_per_bss"])
        _require(len(obss.get("bsses", [])) == 4, "resolved_config.json: expected four OBSSs")
        _require(len(flows) == 4 * station_count * 2,
                 "background_flows.csv: unexpected flow count")
        flow_keys: set[tuple[int, int, str]] = set()
        streams: set[int] = set()
        periods_by_flow: dict[tuple[int, int, str], int] = {}
        for row in flows:
            _require(row["run_id"] == run_id, "background_flows.csv: run_id mismatch")
            key = (int(row["bss_id"]), int(row["sta_index"]), row["direction"])
            _require(row["direction"] in {"uplink", "downlink"},
                     "background_flows.csv: invalid direction")
            _require(key not in flow_keys, "background_flows.csv: duplicate flow")
            flow_keys.add(key)
            for stream_key in ("rate_stream", "on_stream", "off_stream"):
                stream = int(row[stream_key])
                _require(stream not in streams, "background_flows.csv: reused RNG stream")
                streams.add(stream)
            periods_by_flow[key] = _integer(
                row, "period_count", "background_flows.csv"
            )
            _integer(row, "bytes_sent", "background_flows.csv")
            _integer(row, "bytes_received", "background_flows.csv")
        observed_periods: dict[tuple[int, int, str], int] = {}
        _require(obss.get("station_manager", "constant") in {
            "minstrel_ht", "ideal", "constant",
        },
                 "resolved_config.json: invalid OBSS station manager")
        for row in periods:
            _require(row["run_id"] == run_id,
                     "background_rate_periods.csv: run_id mismatch")
            key = (int(row["bss_id"]), int(row["sta_index"]), row["direction"])
            _require(key in flow_keys, "background_rate_periods.csv: unknown flow")
            direction = row["direction"]
            prefix = "ul" if direction == "uplink" else "dl"
            minimum = float(obss.get(f"{prefix}_min_rate_mbps", obss["min_rate_mbps"]))
            maximum = float(obss.get(f"{prefix}_max_rate_mbps", obss["max_rate_mbps"]))
            rate = float(row["rate_mbps"])
            _require(minimum <= rate <= maximum,
                     "background_rate_periods.csv: rate outside configured range")
            _require(int(row["end_us"]) >= int(row["start_us"]),
                     "background_rate_periods.csv: negative period")
            observed_periods[key] = observed_periods.get(key, 0) + 1
        _require(observed_periods == periods_by_flow,
                 "background_rate_periods.csv: period counts mismatch")
    prediction_result = _validate_prediction(run_dir, config, run_id, frames)
    return {
        "run_id": run_id,
        "frame_count": total,
        "valid": True,
        **prediction_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--project-commit")
    parser.add_argument("--ns3-commit")
    args = parser.parse_args()
    for directory in args.run_dirs:
        result = validate_run(directory, expected_project_commit=args.project_commit,
                              expected_ns3_commit=args.ns3_commit)
        print(f"VALID {result['run_id']} frames={result['frame_count']}")


if __name__ == "__main__":
    main()
