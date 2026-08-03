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

from randomized_frame_assignment import ALGORITHM_ID, assign_frame

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
    "actionable", "estimated_airtime_us",
    "reference_airtime_us", "shadow_price", "normalized_cost", "net_utility",
    "airtime_budget_fraction", "bucket_capacity_us", "bucket_balance_us",
    "initial_bucket_capacity_us", "reserved_airtime_us", "available_airtime_us",
    "measured_airtime_total_us", "decision", "secondary_launched",
}
DEFICIT_DECISION_COLUMNS = {
    "frame_packet_count", "primary_acked_packets", "primary_acked_packet_indices",
    "secondary_packet_count", "secondary_packet_indices", "secondary_packet_order",
}
SECONDARY_AIRTIME_EVENT_COLUMNS = {
    "run_id", "time_ns", "path_id", "ppdu_duration_us", "tagged_mpdu_bytes",
    "frame_ids", "mixed_ppdu", "cumulative_tagged_airtime_us",
}
SECONDARY_AIRTIME_SETTLEMENT_COLUMNS = {
    "run_id", "frame_id", "settlement_time_ns", "released_airtime_us",
    "measured_airtime_us", "nominal_airtime_us", "fallback",
}
RANDOMIZED_ASSIGNMENT_COLUMNS = {
    "schema_version", "run_id", "frame_id", "eligible_t2", "eligibility_reason",
    "assigned_arm", "assignment_seed", "assignment_run", "assignment_salt",
    "assignment_algorithm", "raw_draw", "unit_draw", "t2_probability",
    "t4_probability", "control_probability", "propensity", "primary_sample_time_ns",
    "secondary_sample_time_ns", "primary_feature_watermark_time_ns",
    "primary_feature_watermark_sequence", "secondary_feature_watermark_time_ns",
    "secondary_feature_watermark_sequence", "generation_time_ns", "deadline_time_ns",
    "prospective_t4_time_ns", "frame_size_bytes", "frame_packet_count", "frame_type",
    "descriptor_available", "secondary_packet_count", "secondary_packet_indices",
    "secondary_expected_mac_service_bytes", "cost_estimator", "cost_safety_factor",
    "nominal_airtime_us", "estimated_airtime_us",
}
RANDOMIZED_EXECUTION_COLUMNS = {
    "schema_version", "run_id", "frame_id", "eligible_t2", "eligibility_reason",
    "assigned_arm", "assignment_seed", "assignment_run", "assignment_salt",
    "assignment_algorithm", "raw_draw", "unit_draw", "t2_probability",
    "t4_probability", "control_probability", "propensity", "execution_stage",
    "primary_sample_time_ns", "secondary_sample_time_ns",
    "primary_feature_watermark_time_ns", "primary_feature_watermark_sequence",
    "secondary_feature_watermark_time_ns", "secondary_feature_watermark_sequence",
    "generation_time_ns", "deadline_time_ns", "descriptor_available_at_assignment",
    "descriptor_available_at_execution", "secondary_packet_count",
    "secondary_packet_indices", "secondary_expected_mac_service_bytes", "cost_estimator",
    "cost_safety_factor", "nominal_airtime_us", "estimated_airtime_us",
    "primary_actionable", "attempted", "launched", "noncompliance", "status",
}
RANDOMIZED_CSV_SCHEMA_VERSION = 1
RANDOMIZED_COST_ESTIMATOR = (
    "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1"
)
RANDOMIZED_COST_SAFETY_FACTOR = 1.25
RANDOMIZED_T2_OFFSET_US = 2000
RANDOMIZED_T4_OFFSET_US = 4000
RANDOMIZED_COMMON_ELIGIBILITY_RULE = (
    "T2_at_or_after_start_and_prospective_T4_before_stop_and_"
    "primary_actionable_and_canonical_secondary_descriptor_available"
)
RANDOMIZED_CONFIG_KEYS = {
    "csv_schema_version", "assignment_algorithm", "assignment_salt",
    "randomization_consumes_ns3_rng", "arm_probabilities", "stages",
    "stage_offsets_us", "primary_path", "primary_copy_id", "secondary_path",
    "secondary_copy_id", "assignment_window_start_ns",
    "assignment_window_stop_ns", "assignment_stop_guard_us",
    "common_eligibility_rule", "intervention", "token_gate_enabled",
    "cost_estimator_id",
}
RANDOMIZED_PREDICTION_COLUMNS = {
    "run_id", "frame_id", "path_id", "copy_id", "sample_stage",
    "sample_offset_us", "sample_time_ns", "latest_feature_event_time_ns",
    "latest_feature_event_sequence", "generation_time_ns", "deadline_time_ns",
    "sender_mac_complete", "actionable", "frame_size_bytes", "frame_packet_count",
    "frame_type", "packets_submitted", "application_socket_packet_bytes_submitted",
    "packets_remaining_to_submit", "frame_packets_mac_enqueued",
    "frame_packets_mac_dequeued", "frame_packets_tx_succeeded",
    "frame_mpdu_attempt_failures", "frame_packets_terminally_dropped",
    "frame_packets_currently_queued", "frame_mac_service_bytes_currently_queued",
}
SECONDARY_AIRTIME_SUMMARY_KEYS = {
    "tagged_ppdu_count", "mixed_ppdu_count", "tagged_secondary_tx_airtime_us",
    "measurement_start_ns", "measurement_stop_ns", "measurement_duration_us",
    "tagged_secondary_tx_airtime_fraction", "maximum_budget_debt_us",
    "estimated_action_airtime_us", "actual_to_estimated_airtime_ratio",
    "forced_reservation_settlements", "budget_fraction",
    "initial_bucket_capacity_us", "finite_run_budget_us", "budget_excess_us",
}
ADAPTIVE_RETRY_INFLATED_COST_DEFINITION = (
    "retry_inflated_estimated_secondary_sender_phy_tx_airtime"
)
ADAPTIVE_NOMINAL_COST_DEFINITION = (
    "nominal_estimated_secondary_sender_phy_tx_airtime"
)
# Exact V1 whole-copy estimator shared by the controller and the frozen
# primary-risk calibration. The MAC-service term is the 50-byte streaming
# header plus IPv4, UDP, and LLC/SNAP (20 + 8 + 8 bytes); the final 38 bytes
# per packet are the controller's additional airtime allowance.
ADAPTIVE_ESTIMATOR_PHY_PREAMBLE_US = 48.0
ADAPTIVE_ESTIMATOR_PHY_DATA_RATE_BPS = 68_823_530
ADAPTIVE_ESTIMATOR_STREAMING_HEADER_BYTES = 50
ADAPTIVE_ESTIMATOR_MAC_SERVICE_OVERHEAD_BYTES = 36
ADAPTIVE_ESTIMATOR_ADDITIONAL_BYTES_PER_PACKET = 38
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
PRIMARY_T0_MODEL_ID = "commodity_polling_1ms_obss_primary_t0_v1"
PRIMARY_TARGET_ID = "primary_copy_deadline_miss"
PRIMARY_T0_SOURCE_MODEL_SHA256 = (
    "735e69ea4ad0ce615b6f827aaa8e3362135cf3f18e4c727d69920af9898d73bf"
)
PRIMARY_TARGET_PROVENANCE_SHA256 = (
    "e3d62e814e13aaeb5e4aab495ba7222b2a910a8268fe6f8645299c3451756f84"
)
PRIMARY_TAIL_T4_MODEL_ID = "primary_tail_t4_obss_v1"
PRIMARY_TAIL_T4_PRIMARY_TARGET_ID = "primary_miss_t4_v1"
PRIMARY_TAIL_T4_COMPLETED_TARGET_ID = (
    "completed_primary_latency_ge_12500us_t4_v1"
)
PRIMARY_TAIL_T4_SOURCE_MODEL_SHA256 = (
    "1a9afc23452952d87c7b5845a22260321ba302f38f1c3fb1eeaafadb0a12856c"
)
PRIMARY_TAIL_T4_TARGET_PROVENANCE_SHA256 = (
    "2b16b96bef68a32ec282e01b18a30506eaab933039c85e9bb1f6302da7b73be5"
)
PRIMARY_TAIL_T4_FEATURE_CONTRACT_SHA256 = (
    "8ccf33d6af8dffb8da758016acbd809a7cc054be4a1abc070d129c788b9c7cb0"
)
PRIMARY_TAIL_T4_COMBINER_SHA256 = (
    "3d47b994ef5fcf579c73fb74492e0293dfe3ba377911f72f7a6b5fe764e6d9e0"
)
PRIMARY_TAIL_T4_PRIMARY_MODEL_SHA256 = (
    "8f8944a536166cb0f7dcc7c1a7bcf781f6a4d8fc25a995e3b8ed983b8886d98d"
)
PRIMARY_TAIL_T4_COMPLETED_MODEL_SHA256 = (
    "ce787f6aaa9e2607c10bdb9227ae831eb6eb94e1499e9e1240e4d4ddc62a1fec"
)
PRIMARY_T0_ESTIMATOR_COST_SAFETY_FACTOR = 1.25
PRIMARY_T0_ESTIMATOR_REFERENCE_FRAME_BYTES = 12_000
PRIMARY_T0_ESTIMATOR_PAYLOAD_BYTES = 1_200
PRIMARY_T0_ESTIMATOR_REFERENCE_AIRTIME_US = 1983.760667318285
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
PREDICTION_DECISION_COLUMNS = {
    "run_id", "frame_id", "path_id", "copy_id", "sample_stage",
    "sample_offset_us", "sample_time_ns", "generation_time_ns",
    "deadline_time_ns", "actionable",
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


def _prediction_target_provenance_valid(config: dict[str, Any]) -> bool:
    """Check target identity while preserving validation of historical outputs."""
    target_id = config.get("target_id")
    target_sha = config.get("target_provenance_sha256")
    if config.get("model_id") == PRIMARY_T0_MODEL_ID:
        return (
            target_id == PRIMARY_TARGET_ID
            and target_sha == PRIMARY_TARGET_PROVENANCE_SHA256
        )
    if target_id is None and target_sha is None:
        return True
    return (
        target_id == PRIMARY_TARGET_ID
        and isinstance(target_sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", target_sha) is not None
    )


def _prediction_model_offsets_valid(
    config: dict[str, Any], offsets: list[int]
) -> bool:
    """Restrict target-domain models to stages actually retrained for them."""
    return config.get("model_id") != PRIMARY_T0_MODEL_ID or offsets == [0]


def _staged_adaptive_identity_valid(config: dict[str, Any]) -> bool:
    """Check the exact frozen T0/T4 score identities and semantics."""
    scorers = config.get("stage_scorers")
    if config.get("score_contract") != "stage_specific" or not isinstance(scorers, dict):
        return False
    expected = {
        "T0": {
            "sample_offset_us": 0,
            "score_name": "primary_miss_calibrated_probability",
            "score_kind": "calibrated_primary_miss_probability",
            "model_id": PRIMARY_T0_MODEL_ID,
            "primary_miss_target_id": PRIMARY_TARGET_ID,
            "completed_tail_target_id": "",
            "source_model_sha256": PRIMARY_T0_SOURCE_MODEL_SHA256,
            "target_provenance_sha256": PRIMARY_TARGET_PROVENANCE_SHA256,
            "feature_contract_sha256": "",
            "combiner_sha256": "",
            "primary_miss_model_sha256": "",
            "completed_tail_model_sha256": "",
            "feature_count": 86,
        },
        "T4": {
            "sample_offset_us": 4000,
            "score_name": "admission_score",
            "score_kind": "weighted_head_probability_admission_score",
            "model_id": PRIMARY_TAIL_T4_MODEL_ID,
            "primary_miss_target_id": PRIMARY_TAIL_T4_PRIMARY_TARGET_ID,
            "completed_tail_target_id": PRIMARY_TAIL_T4_COMPLETED_TARGET_ID,
            "source_model_sha256": PRIMARY_TAIL_T4_SOURCE_MODEL_SHA256,
            "target_provenance_sha256": PRIMARY_TAIL_T4_TARGET_PROVENANCE_SHA256,
            "feature_contract_sha256": PRIMARY_TAIL_T4_FEATURE_CONTRACT_SHA256,
            "combiner_sha256": PRIMARY_TAIL_T4_COMBINER_SHA256,
            "primary_miss_model_sha256": PRIMARY_TAIL_T4_PRIMARY_MODEL_SHA256,
            "completed_tail_model_sha256": PRIMARY_TAIL_T4_COMPLETED_MODEL_SHA256,
            "feature_count": 101,
        },
    }
    offsets = config.get("decision_offsets_us")
    expected_keys = {"T0"} if offsets == [0] else {"T0", "T4"}
    return offsets in ([0], [0, 4000]) and scorers == {
        key: expected[key] for key in expected_keys
    }


def _adaptive_admission_cost_metadata(
    config: dict[str, Any],
    object_name: str,
    expected_selection: str = "full_forward",
) -> tuple[bool, bool, str]:
    """Return inflation mode, explicit-schema presence, and effective packet cost."""
    keys = {
        "admission_uses_retry_inflation",
        "admission_cost_definition",
        "reservation_cost_definition",
    }
    present = keys & config.keys()
    if not present:
        # Historical outputs priced and reserved the same retry-inflated
        # estimate, before this distinction was recorded explicitly.
        effective = (
            "whole_copy"
            if expected_selection == "full_forward"
            else "primary_unacknowledged_packet_set"
        )
        return True, False, effective
    _require(present == keys,
             f"resolved_config.json: incomplete {object_name} cost metadata")
    uses_retry_inflation = config.get("admission_uses_retry_inflation")
    _require(isinstance(uses_retry_inflation, bool),
             f"resolved_config.json: invalid {object_name}."
             "admission_uses_retry_inflation")
    packet_keys = {
        "configured_admission_packet_cost",
        "effective_admission_packet_cost",
        "operating_profile",
    }
    present_packet_keys = packet_keys & config.keys()
    if not present_packet_keys:
        expected_admission = (
            ADAPTIVE_RETRY_INFLATED_COST_DEFINITION
            if uses_retry_inflation
            else ADAPTIVE_NOMINAL_COST_DEFINITION
        )
        _require(config.get("admission_cost_definition") == expected_admission and
                 config.get("reservation_cost_definition") ==
                     ADAPTIVE_RETRY_INFLATED_COST_DEFINITION,
                 f"resolved_config.json: invalid {object_name} cost definition")
        effective = (
            "whole_copy"
            if expected_selection == "full_forward"
            else "primary_unacknowledged_packet_set"
        )
        return uses_retry_inflation, True, effective

    _require(present_packet_keys == packet_keys,
             f"resolved_config.json: incomplete {object_name} packet-cost metadata")
    configured = config.get("configured_admission_packet_cost")
    _require(configured in {"launched_packet_set", "whole_copy"},
             f"resolved_config.json: invalid {object_name} configured packet cost")
    effective = (
        "whole_copy"
        if expected_selection == "full_forward" or configured == "whole_copy"
        else "primary_unacknowledged_packet_set"
    )
    expected_profile = (
        "full_forward+whole_copy_priced"
        if expected_selection == "full_forward"
        else (
            "primary_unacknowledged+whole_copy_priced"
            if effective == "whole_copy"
            else "primary_unacknowledged+selected_packet_set_priced"
        )
    )
    expected_admission = (
        f"{'retry_inflated' if uses_retry_inflation else 'nominal'}_estimated_"
        f"{effective}_secondary_sender_phy_tx_airtime"
    )
    _require(
        config.get("effective_admission_packet_cost") == effective
        and config.get("operating_profile") == expected_profile
        and config.get("admission_cost_definition") == expected_admission
        and config.get("reservation_cost_definition") ==
            "retry_inflated_estimated_launched_packet_set_secondary_sender_phy_tx_airtime",
        f"resolved_config.json: invalid {object_name} cost definition",
    )
    return uses_retry_inflation, True, effective


def _requires_exact_adaptive_estimator(
    config: dict[str, Any], has_cost_metadata: bool
) -> bool:
    """Return whether output provenance identifies the corrected V1 estimator.

    Generic historical outputs predate the explicit admission/reservation cost
    schema and include both the original and corrected estimators. They remain
    readable through the older arithmetic checks. The primary T0 model was
    calibrated only against the corrected estimator, including for builds just
    before the explicit cost schema was added.
    """
    return has_cost_metadata or config.get("model_id") == PRIMARY_T0_MODEL_ID


def _adaptive_nominal_airtime_us(
    application_bytes: int,
    packet_count: int,
    cost_safety_factor: float,
) -> float:
    """Reproduce the controller's corrected nominal airtime estimate."""
    _require(application_bytes > 0 and packet_count > 0,
             "adaptive estimator: frame descriptor must be positive")
    expected_mac_service_bytes = application_bytes + packet_count * (
        ADAPTIVE_ESTIMATOR_STREAMING_HEADER_BYTES
        + ADAPTIVE_ESTIMATOR_MAC_SERVICE_OVERHEAD_BYTES
    )
    mac_bytes = (
        expected_mac_service_bytes
        + packet_count * ADAPTIVE_ESTIMATOR_ADDITIONAL_BYTES_PER_PACKET
    )
    payload_us = (
        8.0 * mac_bytes / ADAPTIVE_ESTIMATOR_PHY_DATA_RATE_BPS * 1e6
    )
    return cost_safety_factor * (
        ADAPTIVE_ESTIMATOR_PHY_PREAMBLE_US + payload_us
    )


def _adaptive_whole_copy_costs(
    frames: list[dict[str, str]],
    stream_config: dict[str, Any],
    cost_safety_factor: float,
) -> tuple[float, dict[int, float]]:
    """Return the exact reference and per-frame nominal whole-copy costs."""
    payload_size = stream_config.get("payload_size_bytes")
    reference_size = stream_config.get("frame_size_bytes")
    _require(isinstance(payload_size, int) and not isinstance(payload_size, bool) and
             payload_size > 0,
             "resolved_config.json: invalid adaptive estimator payload size")
    _require(isinstance(reference_size, int) and not isinstance(reference_size, bool) and
             reference_size > 0,
             "resolved_config.json: invalid adaptive estimator reference frame size")
    reference_packets = 1 + (reference_size - 1) // payload_size
    reference = _adaptive_nominal_airtime_us(
        reference_size, reference_packets, cost_safety_factor
    )
    costs: dict[int, float] = {}
    for frame in frames:
        frame_id = _integer(frame, "frame_id", "frames.csv")
        frame_size = _integer(frame, "frame_size_bytes", "frames.csv")
        packet_count = _integer(frame, "packet_count", "frames.csv")
        expected_packet_count = 1 + (frame_size - 1) // payload_size
        _require(packet_count == expected_packet_count,
                 "frames.csv: adaptive estimator packet count mismatch")
        costs[frame_id] = _adaptive_nominal_airtime_us(
            frame_size, packet_count, cost_safety_factor
        )
    return reference, costs


def _adaptive_primary_deficit_cost(
    frame: dict[str, str],
    selected_indices: list[int],
    stream_config: dict[str, Any],
    cost_safety_factor: float,
) -> float:
    """Reconstruct nominal airtime for an exact primary-deficit packet set."""
    payload_size = stream_config.get("payload_size_bytes")
    _require(isinstance(payload_size, int) and not isinstance(payload_size, bool) and
             payload_size > 0,
             "resolved_config.json: invalid adaptive estimator payload size")
    frame_size = _integer(frame, "frame_size_bytes", "frames.csv")
    packet_count = _integer(frame, "packet_count", "frames.csv")
    _require(packet_count == 1 + (frame_size - 1) // payload_size,
             "frames.csv: adaptive estimator packet count mismatch")
    _require(selected_indices,
             "adaptive deficit estimator: selected packet set is empty")
    final_packet_bytes = frame_size - payload_size * (packet_count - 1)
    _require(0 < final_packet_bytes <= payload_size,
             "adaptive deficit estimator: invalid final packet size")
    application_bytes = sum(
        final_packet_bytes if index == packet_count - 1 else payload_size
        for index in selected_indices
    )
    return _adaptive_nominal_airtime_us(
        application_bytes,
        len(selected_indices),
        cost_safety_factor,
    )


def _validate_calibrated_estimator_contract(
    config: dict[str, Any],
    stream_config: dict[str, Any],
    reference_airtime_us: float,
    expected_selection: str,
) -> None:
    """Pin estimator inputs used by a calibrated controller gate."""
    calibrated_identity = (
        config.get("model_id") == PRIMARY_T0_MODEL_ID or
        _staged_adaptive_identity_valid(config)
    )
    if not calibrated_identity:
        return
    object_name = (
        "adaptiveDeficitDuplication"
        if expected_selection == "primary_unacknowledged_reverse"
        else "adaptiveAirtimeDuplication"
    )
    safety = _config_number(
        config, "cost_safety_factor", object_name
    )
    _require(
        _close(safety, PRIMARY_T0_ESTIMATOR_COST_SAFETY_FACTOR)
        and stream_config.get("frame_size_bytes") == PRIMARY_T0_ESTIMATOR_REFERENCE_FRAME_BYTES
        and stream_config.get("payload_size_bytes") == PRIMARY_T0_ESTIMATOR_PAYLOAD_BYTES
        and _close(
            reference_airtime_us,
            PRIMARY_T0_ESTIMATOR_REFERENCE_AIRTIME_US,
        ),
        "resolved_config.json: calibrated adaptive estimator differs from calibration contract",
    )


def _validate_adaptive_config(
    config: dict[str, Any],
    expected_selection: str = "full_forward",
) -> list[int]:
    """Validate adaptive controller provenance and return decision offsets."""
    _require(expected_selection in {"full_forward", "primary_unacknowledged_reverse"},
             "resolved_config.json: unknown adaptive packet selection")
    object_name = (
        "adaptiveDeficitDuplication"
        if expected_selection == "primary_unacknowledged_reverse"
        else "adaptiveAirtimeDuplication"
    )
    legacy_identity = (
        config.get("model_id") in {
            "commodity_polling_1ms_genuine_v1", PRIMARY_T0_MODEL_ID,
        } and _prediction_target_provenance_valid(config)
    )
    staged_identity = _staged_adaptive_identity_valid(config)
    legacy_provenance = (
        legacy_identity
        and isinstance(config.get("source_model_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", config["source_model_sha256"]) is not None
        and config.get("feature_set") == "F0+F1-degraded"
        and config.get("calibration") == "platt"
        and config.get("admission_feature_set", "F0+F1-degraded") == "F0+F1-degraded"
    )
    staged_provenance = (
        staged_identity
        and config.get("admission_feature_set") == "stage_specific_compiled"
    )
    _require(
        (legacy_provenance or staged_provenance) and
        config.get("degradation_profile") == "polling_1ms" and
        config.get("budget_definition") == "secondary_sender_phy_tx_airtime" and
        config.get("primary_path") == 1 and config.get("secondary_path") == 0 and
        config.get("packet_selection", "full_forward") == expected_selection and
        config.get("packet_selection_feature_set", "none") == (
            "F2-primary-frame-ack-state"
            if expected_selection == "primary_unacknowledged_reverse"
            else "none"
        ),
        "resolved_config.json: invalid adaptive airtime provenance",
    )
    offsets = _strict_integer_list(
        config.get("decision_offsets_us"),
        f"{object_name}.decision_offsets_us",
        positive=False,
    )
    _require(offsets[0] == 0,
             "resolved_config.json: adaptive decision offsets must include T0")
    _require(set(offsets) <= {0, 1000, 2000, 4000},
             "resolved_config.json: adaptive predictor has an unsupported stage")
    _require(staged_identity or _prediction_model_offsets_valid(config, offsets),
             "resolved_config.json: primary T0 predictor only supports offset 0")
    _require(not staged_identity or offsets in ([0], [0, 4000]),
             "resolved_config.json: staged predictor supports exactly T0 or T0/T4")
    _require(config.get("stages") == [_stage_name(offset) for offset in offsets],
             "resolved_config.json: adaptive stages do not match decision offsets")

    offset_policy_keys = {
        "shadow_price_mode",
        "decision_offset_shadow_prices",
        "i_frame_only_decision_offsets_us",
    }
    present_offset_policy_keys = offset_policy_keys & config.keys()
    _require(not present_offset_policy_keys or
             present_offset_policy_keys == offset_policy_keys,
             "resolved_config.json: incomplete adaptive offset-policy metadata")
    raw_prices = config.get("decision_offset_shadow_prices", {})
    _require(isinstance(raw_prices, dict),
             "resolved_config.json: adaptive offset prices must be an object")
    offset_prices: dict[int, float] = {}
    for raw_offset, raw_price in raw_prices.items():
        _require(isinstance(raw_offset, str) and re.fullmatch(r"0|[1-9][0-9]*", raw_offset),
                 "resolved_config.json: invalid adaptive price offset")
        offset = int(raw_offset)
        _require(offset in offsets and offset not in offset_prices and
                 isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool) and
                 math.isfinite(float(raw_price)) and 0 <= float(raw_price) <= 1,
                 "resolved_config.json: invalid adaptive offset price")
        offset_prices[offset] = float(raw_price)
    expected_price_mode = (
        "offset_override_with_global_dual_fallback"
        if offset_prices else "global_dual"
    )
    if present_offset_policy_keys:
        _require(config.get("shadow_price_mode") == expected_price_mode,
                 "resolved_config.json: adaptive shadow-price mode mismatch")

    restricted_offsets = config.get("i_frame_only_decision_offsets_us", [])
    _require(isinstance(restricted_offsets, list) and
             all(isinstance(item, int) and not isinstance(item, bool)
                 for item in restricted_offsets) and
             all(left < right for left, right in
                 zip(restricted_offsets, restricted_offsets[1:])) and
             set(restricted_offsets) <= set(offsets),
             "resolved_config.json: invalid adaptive I-frame restrictions")

    fraction = _config_number(config, "budget_fraction", object_name)
    initial_price = _config_number(
        config, "initial_shadow_price", object_name
    )
    dual_step = _config_number(config, "dual_step", object_name)
    safety = _config_number(config, "cost_safety_factor", object_name)
    alpha = _config_number(config, "cost_ewma_alpha", object_name)
    horizon = config.get("bucket_horizon_us")
    _require(isinstance(horizon, int) and not isinstance(horizon, bool) and horizon > 0,
             "resolved_config.json: invalid adaptive bucket horizon")
    initial_horizon = config.get("initial_bucket_horizon_us", horizon)
    _require(isinstance(initial_horizon, int) and
             not isinstance(initial_horizon, bool) and
             0 < initial_horizon <= horizon,
             "resolved_config.json: invalid adaptive initial bucket horizon")
    _require(0 < fraction <= 1 and 0 <= initial_price <= 1 and dual_step >= 0 and
             safety >= 1 and 0 < alpha <= 1,
             "resolved_config.json: adaptive parameter outside its domain")
    initial_capacity = _config_number(
        config, "initial_bucket_capacity_us", object_name
    )
    _require(_close(initial_capacity, fraction * initial_horizon),
             "resolved_config.json: adaptive initial capacity mismatch")
    _adaptive_admission_cost_metadata(config, object_name, expected_selection)
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


def _prediction_decision_samples(
    run_dir: Path,
    run_id: str,
    expected_path: int,
) -> dict[tuple[int, int], dict[str, str]]:
    """Index the exact predictor samples consumed by closed-loop controllers."""
    path = run_dir / "prediction_samples.csv"
    _require(path.is_file(), "missing core file: prediction_samples.csv")
    rows = _csv(path, PREDICTION_DECISION_COLUMNS)
    result: dict[tuple[int, int], dict[str, str]] = {}
    for row in rows:
        file_name = "prediction_samples.csv"
        _require(row.get("run_id") == run_id,
                 f"{file_name}: run_id mismatch")
        frame_id = _integer(row, "frame_id", file_name)
        path_id = _integer(row, "path_id", file_name)
        copy_id = _integer(row, "copy_id", file_name)
        offset = _integer(row, "sample_offset_us", file_name)
        _require(path_id == expected_path and copy_id == 0,
                 f"{file_name}: controller sample path/copy mismatch")
        key = (frame_id, offset)
        _require(key not in result,
                 f"{file_name}: duplicate controller sample")
        result[key] = row
    return result


def _validate_adaptive_decisions(
    rows: list[dict[str, str]],
    config: dict[str, Any],
    frames: list[dict[str, str]],
    prediction_samples: dict[tuple[int, int], dict[str, str]],
    run_id: str,
    stream_config: dict[str, Any] | None = None,
    action_nominal_airtimes: dict[int, float] | None = None,
) -> dict[int, float]:
    """Validate controller arithmetic and decision semantics.

    The returned mapping contains the reservation estimate for every launched
    frame and is used to reconcile the independent PHY-airtime ledger. When
    ``action_nominal_airtimes`` is supplied, exact-model actions also populate
    it with the reconstructed nominal cost of the packet set actually launched.
    """
    packet_selection = config.get("packet_selection", "full_forward")
    offsets = _validate_adaptive_config(config, packet_selection)
    primary_deficit = packet_selection == "primary_unacknowledged_reverse"
    object_name = (
        "adaptiveDeficitDuplication" if primary_deficit
        else "adaptiveAirtimeDuplication"
    )
    file_name = "adaptive_airtime_decisions.csv"
    frames_by_id = {
        _integer(frame, "frame_id", "frames.csv"): frame
        for frame in frames
    }
    frame_generation_us = {
        frame_id: _integer(frame, "generation_time_us", "frames.csv")
        for frame_id, frame in frames_by_id.items()
    }
    _require(len(rows) == len(frames) * len(offsets),
             "adaptive decisions: frame/stage cardinality mismatch")
    _require(all(row.get("run_id") == run_id for row in rows),
             "adaptive decisions: run_id mismatch")

    fraction = _config_number(config, "budget_fraction", object_name)
    horizon = int(config["bucket_horizon_us"])
    capacity = fraction * horizon
    initial_capacity_config = _config_number(
        config, "initial_bucket_capacity_us", object_name
    )
    initial_price = _config_number(
        config, "initial_shadow_price", object_name
    )
    dual_step = _config_number(config, "dual_step", object_name)
    safety = _config_number(config, "cost_safety_factor", object_name)
    uses_retry_inflation, has_cost_metadata, effective_packet_cost = (
        _adaptive_admission_cost_metadata(config, object_name, packet_selection)
    )
    typed_score_contract = config.get("score_contract") == "stage_specific"
    stage_scorers = config.get("stage_scorers", {})
    has_admission_airtime = bool(rows) and "admission_airtime_us" in rows[0]
    has_typed_score = bool(rows) and "admission_score" in rows[0]
    has_packet_cost_audit = bool(rows) and "admission_packet_count" in rows[0]
    has_packet_selection_audit = bool(rows) and "secondary_packet_count" in rows[0]
    if rows:
        _require(all(("admission_airtime_us" in row) == has_admission_airtime
                     for row in rows),
                 "adaptive decisions: inconsistent admission-airtime schema")
        _require(all(("admission_score" in row) == has_typed_score and
                     ("admission_packet_count" in row) == has_packet_cost_audit and
                     ("secondary_packet_count" in row) == has_packet_selection_audit
                     for row in rows),
                 "adaptive decisions: inconsistent typed audit schema")
        _require(not typed_score_contract or
                 (has_typed_score and has_packet_cost_audit and
                  has_packet_selection_audit),
                 "adaptive decisions: staged score/cost audit fields are absent")
        _require(not has_cost_metadata or has_admission_airtime,
                 "adaptive decisions: explicit cost metadata requires admission airtime")
        _require(uses_retry_inflation or has_admission_airtime,
                 "adaptive decisions: nominal admission airtime is absent")
    exact_estimator = _requires_exact_adaptive_estimator(
        config, has_cost_metadata
    )
    expected_reference: float | None = None
    expected_whole_copy_costs: dict[int, float] = {}
    if exact_estimator:
        _require(isinstance(stream_config, dict),
                 "resolved_config.json: adaptive estimator stream metadata is absent")
        expected_reference, expected_whole_copy_costs = _adaptive_whole_copy_costs(
            frames,
            stream_config,
            safety,
        )
        _validate_calibrated_estimator_contract(
            config,
            stream_config,
            expected_reference,
            packet_selection,
        )
    allowed_decisions = {
        "price_rejected", "airtime_deferred", "action", "already_resolved",
        "not_actionable", "launch_rejected", "no_primary_deficit",
        "frame_type_restricted",
    }
    seen_samples: set[tuple[int, int]] = set()
    action_estimates: dict[int, float] = {}
    previous_sample_time = -1
    previous_measured = 0.0
    reference_airtime: float | None = None
    saw_retry_inflated_descriptor = False
    last_t0_time: int | None = None
    last_t0_measured = 0.0
    last_t0_dual_shadow = initial_price
    raw_offset_prices = config.get("decision_offset_shadow_prices", {})
    offset_prices = {int(offset): float(price)
                     for offset, price in raw_offset_prices.items()}
    restricted_offsets = set(config.get("i_frame_only_decision_offsets_us", []))
    has_dual_shadow = bool(rows) and "dual_shadow_price" in rows[0]
    has_shadow_source = bool(rows) and "shadow_price_source" in rows[0]
    has_offset_policy_metadata = "shadow_price_mode" in config
    if rows:
        _require(all(("dual_shadow_price" in row) == has_dual_shadow and
                     ("shadow_price_source" in row) == has_shadow_source
                     for row in rows),
                 "adaptive decisions: inconsistent shadow-price schema")
        _require(not has_offset_policy_metadata or
                 (has_dual_shadow and has_shadow_source),
                 "adaptive decisions: offset policy lacks dual/source telemetry")

    for row in rows:
        selected_indices_for_cost: list[int] | None = None
        frame_id = _integer(row, "frame_id", file_name)
        offset = _integer(row, "sample_offset_us", file_name)
        _require(frame_id in frame_generation_us and offset in offsets,
                 "adaptive decisions: unknown frame or stage")
        _require((frame_id, offset) not in seen_samples,
                 "adaptive decisions: duplicate frame/stage")
        seen_samples.add((frame_id, offset))
        prediction = prediction_samples.get((frame_id, offset))
        _require(prediction is not None,
                 "adaptive decisions: predictor sample is absent")
        generation_time = _integer(
            prediction, "generation_time_ns", "prediction_samples.csv"
        )
        _require(generation_time // 1000 == frame_generation_us[frame_id],
                 "adaptive decisions: frame/predictor generation mismatch")
        _require(row.get("sample_stage") == prediction.get("sample_stage") ==
                 _stage_name(offset),
                 "adaptive decisions: sample stage/offset mismatch")
        sample_time = _integer(row, "sample_time_ns", file_name)
        _require(sample_time == _integer(
            prediction, "sample_time_ns", "prediction_samples.csv"
        ), "adaptive decisions: sample time/telemetry mismatch")
        _require(sample_time >= previous_sample_time,
                 "adaptive decisions: rows are not chronological")
        previous_sample_time = sample_time

        actionable = _flag(row, "actionable", file_name)
        _require(actionable == _flag(
            prediction, "actionable", "prediction_samples.csv"
        ), "adaptive decisions: actionability/telemetry mismatch")
        score = _number(
            row,
            "admission_score" if has_typed_score else "calibrated_probability",
            file_name,
        )
        if has_typed_score:
            scorer = stage_scorers.get(_stage_name(offset))
            _require(isinstance(scorer, dict),
                     "adaptive decisions: stage scorer identity is absent")
            for row_key, scorer_key in (
                ("score_name", "score_name"),
                ("score_kind", "score_kind"),
                ("score_model_id", "model_id"),
                ("score_source_model_sha256", "source_model_sha256"),
                ("score_target_provenance_sha256", "target_provenance_sha256"),
                ("score_feature_contract_sha256", "feature_contract_sha256"),
                ("score_combiner_sha256", "combiner_sha256"),
            ):
                _require(row.get(row_key) == str(scorer.get(scorer_key, "")),
                         "adaptive decisions: score provenance mismatch")
            primary_probability = _optional_number(
                row, "primary_miss_probability", file_name
            )
            tail_probability = _optional_number(
                row, "completed_tail_probability", file_name
            )
            _require(
                primary_probability is not None and
                0 <= primary_probability <= 1 and
                (tail_probability is None or 0 <= tail_probability <= 1),
                "adaptive decisions: invalid head probability",
            )
            if scorer.get("score_kind") == "calibrated_primary_miss_probability":
                _require(tail_probability is None and _close(score, primary_probability),
                         "adaptive decisions: invalid calibrated probability score")
            else:
                _require(
                    scorer.get("score_kind") ==
                        "weighted_head_probability_admission_score"
                    and tail_probability is not None
                    and _close(
                        score,
                        (primary_probability + 0.2 * tail_probability) / 1.2,
                    ),
                    "adaptive decisions: invalid weighted admission score",
                )
        estimated = _number(row, "estimated_airtime_us", file_name)
        admission = (
            _number(row, "admission_airtime_us", file_name)
            if has_admission_airtime
            else estimated
        )
        reference = _number(row, "reference_airtime_us", file_name)
        shadow = _number(row, "shadow_price", file_name)
        dual_shadow = (
            _number(row, "dual_shadow_price", file_name)
            if has_dual_shadow else shadow
        )
        normalized = _number(row, "normalized_cost", file_name)
        utility = _adaptive_utility(row)
        row_fraction = _number(row, "airtime_budget_fraction", file_name)
        row_capacity = _number(row, "bucket_capacity_us", file_name)
        balance = _signed_number(row, "bucket_balance_us", file_name)
        initial_capacity = _number(row, "initial_bucket_capacity_us", file_name)
        reserved = _number(row, "reserved_airtime_us", file_name)
        available = _signed_number(row, "available_airtime_us", file_name)
        measured = _number(row, "measured_airtime_total_us", file_name)
        _require(0 <= score <= 1 and 0 <= shadow <= 1 and
                 0 <= dual_shadow <= 1 and reference > 0,
                 "adaptive decisions: score, price, or reference out of bounds")
        expected_shadow = offset_prices.get(offset, dual_shadow)
        _require(_close(shadow, expected_shadow),
                 "adaptive decisions: effective shadow-price mismatch")
        if has_shadow_source:
            expected_source = "offset_override" if offset in offset_prices else "global_dual"
            _require(row.get("shadow_price_source") == expected_source,
                     "adaptive decisions: shadow-price source mismatch")
        _require(_close(row_fraction, fraction) and _close(row_capacity, capacity) and
                 _close(initial_capacity, initial_capacity_config),
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
        if expected_reference is not None:
            _require(_close(reference, expected_reference),
                     "adaptive decisions: reference airtime estimator mismatch")

        decision = row.get("decision")
        _require(decision in allowed_decisions,
                 "adaptive decisions: unknown decision")
        launched = _flag(row, "secondary_launched", file_name)
        _require(launched == (decision == "action"),
                 "adaptive decisions: action/launch mismatch")
        restriction_applies = (
            offset in restricted_offsets and
            prediction.get("frame_type") != "I_FRAME" and
            frame_id not in action_estimates
        )
        _require(not restriction_applies or decision == "frame_type_restricted",
                 "adaptive decisions: frame-type restriction was bypassed")

        selected_count = 0
        selected_indices: list[int] = []
        frame_packet_count = 0
        if has_packet_selection_audit or primary_deficit:
            frame_packet_count = _integer(
                prediction, "frame_packet_count", "prediction_samples.csv"
            )
        if has_packet_selection_audit:
            logged_frame_packet_count = _integer(row, "frame_packet_count", file_name)
            _require(logged_frame_packet_count == frame_packet_count,
                     "adaptive decisions: frame packet count mismatch")
            selected_count = _integer(row, "secondary_packet_count", file_name)
            index_text = row.get("secondary_packet_indices", "")
            index_tokens = [] if not index_text else index_text.split(";")
            _require(all(re.fullmatch(r"[0-9]+", token) for token in index_tokens),
                     "adaptive decisions: malformed selected packet indexes")
            selected_indices = [int(token) for token in index_tokens]
            selected_indices_for_cost = selected_indices
            _require(len(selected_indices) == selected_count and
                     len(selected_indices) == len(set(selected_indices)) and
                     all(0 <= index < frame_packet_count for index in selected_indices),
                     "adaptive decisions: invalid selected packet indexes")

        if primary_deficit:
            _require(frame_packet_count == _integer(
                prediction, "frame_packet_count", "prediction_samples.csv"
            ), "adaptive deficit decisions: frame packet count mismatch")
            prediction_acked = prediction.get("frame_packets_tx_succeeded", "")
            logged_acked = row.get("primary_acked_packets", "")
            _require(logged_acked != "" and logged_acked == prediction_acked,
                     "adaptive deficit decisions: ACK count mismatch")
            acked_count = int(logged_acked)
            acked_text = row.get("primary_acked_packet_indices", "")
            acked_tokens = [] if not acked_text else acked_text.split(";")
            _require(all(re.fullmatch(r"[0-9]+", token) for token in acked_tokens),
                     "adaptive deficit decisions: malformed ACKed packet indexes")
            acked_indices = [int(token) for token in acked_tokens]
            _require(len(acked_indices) == acked_count and
                     len(acked_indices) == len(set(acked_indices)) and
                     all(0 <= index < frame_packet_count for index in acked_indices) and
                     acked_indices == sorted(acked_indices),
                     "adaptive deficit decisions: invalid ACKed packet indexes")
            order = row.get("secondary_packet_order")
            descriptor_absent = (
                decision in {"already_resolved", "no_primary_deficit"} or
                (decision == "frame_type_restricted" and selected_count == 0)
            )
            if descriptor_absent:
                _require(selected_count == 0 and order == "none",
                         "adaptive deficit decisions: resolved row retains a packet set")
                if decision == "no_primary_deficit":
                    _require(acked_indices == list(range(frame_packet_count)),
                             "adaptive deficit decisions: zero deficit lacks all ACKs")
            else:
                expected_indices = sorted(
                    set(range(frame_packet_count)) - set(acked_indices),
                    reverse=True,
                )
                _require(selected_count == frame_packet_count - acked_count and
                         selected_indices == expected_indices and
                         order == "primary_unacknowledged_reverse",
                         "adaptive deficit decisions: selected packet set is inconsistent")
        elif has_packet_selection_audit:
            descriptor_absent = decision == "already_resolved"
            if descriptor_absent:
                _require(selected_count == 0 and
                         row.get("secondary_packet_order") == "none",
                         "adaptive decisions: resolved row retains a full-copy packet set")
            else:
                _require(selected_count == frame_packet_count and
                         selected_indices == list(range(frame_packet_count)) and
                         row.get("secondary_packet_order") == "full_forward",
                         "adaptive decisions: full-copy packet set is inconsistent")

        if has_packet_cost_audit:
            configured_packet_cost = config.get(
                "configured_admission_packet_cost", "launched_packet_set"
            )
            _require(
                row.get("configured_admission_packet_cost") == configured_packet_cost
                and row.get("effective_admission_packet_cost") == effective_packet_cost,
                "adaptive decisions: packet-cost mode mismatch",
            )
            admission_packet_count = _integer(
                row, "admission_packet_count", file_name
            )
            expected_admission_packet_count = (
                frame_packet_count
                if admission > 0 and effective_packet_cost == "whole_copy"
                else selected_count
            )
            _require(admission_packet_count == expected_admission_packet_count,
                     "adaptive decisions: admission packet count mismatch")

        actual_descriptor_present = (
            decision != "already_resolved"
            and not (primary_deficit and selected_count == 0)
        )
        admission_descriptor_present = (
            decision != "already_resolved"
            and (
                effective_packet_cost == "whole_copy"
                or actual_descriptor_present
            )
        )
        _require(
            (estimated > 0) == actual_descriptor_present
            and (admission > 0) == admission_descriptor_present,
            "adaptive decisions: descriptor/cost presence mismatch",
        )

        expected_actual_nominal: float | None = None
        expected_admission_nominal: float | None = None
        if exact_estimator and estimated > 0:
            if primary_deficit:
                _require(selected_indices_for_cost is not None,
                         "adaptive deficit estimator: packet set is absent")
                expected_actual_nominal = _adaptive_primary_deficit_cost(
                    frames_by_id[frame_id],
                    selected_indices_for_cost,
                    stream_config,
                    safety,
                )
            else:
                expected_actual_nominal = expected_whole_copy_costs[frame_id]
            _require(estimated >= expected_actual_nominal or
                     _close(estimated, expected_actual_nominal),
                     "adaptive decisions: reservation below nominal estimator")
            if uses_retry_inflation:
                if not saw_retry_inflated_descriptor:
                    _require(_close(estimated, expected_actual_nominal),
                             "adaptive decisions: initial retry estimate mismatch")
                saw_retry_inflated_descriptor = True
        if exact_estimator and admission > 0:
            expected_admission_nominal = (
                expected_whole_copy_costs[frame_id]
                if effective_packet_cost == "whole_copy"
                else expected_actual_nominal
            )
            _require(expected_admission_nominal is not None,
                     "adaptive decisions: admission estimator packet set is absent")
            if uses_retry_inflation:
                _require(admission >= expected_admission_nominal or
                         _close(admission, expected_admission_nominal),
                         "adaptive decisions: admission below nominal estimator")
                if expected_actual_nominal is not None:
                    _require(
                        _close(
                            admission / expected_admission_nominal,
                            estimated / expected_actual_nominal,
                        ),
                        "adaptive decisions: admission/reservation inflation mismatch",
                    )
            else:
                _require(_close(admission, expected_admission_nominal),
                         "adaptive decisions: nominal admission estimator mismatch")

        if admission > 0:
            _require(_close(normalized, admission / reference) and
                     math.isfinite(utility) and
                     _close(utility, score - shadow * normalized),
                     "adaptive decisions: cost or utility arithmetic mismatch")
        else:
            _require(_close(admission, 0) and _close(normalized, 0) and
                     math.isnan(utility),
                     "adaptive decisions: absent descriptor must have zero cost and NaN utility")

        if decision == "action":
            _require(actionable and utility > 0 and available + 1e-9 >= estimated and
                     estimated > 0 and frame_id not in action_estimates,
                     "adaptive decisions: invalid action predicate")
            action_estimates[frame_id] = estimated
            if exact_estimator:
                _require(expected_actual_nominal is not None,
                         "adaptive decisions: action nominal estimator is absent")
                if action_nominal_airtimes is not None:
                    action_nominal_airtimes[frame_id] = expected_actual_nominal
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
        elif decision == "no_primary_deficit":
            _require(primary_deficit and estimated == 0 and not launched,
                     "adaptive decisions: invalid zero-deficit predicate")
        elif decision == "already_resolved":
            _require(frame_id in action_estimates and estimated == 0,
                     "adaptive decisions: invalid already-resolved predicate")
        elif decision == "frame_type_restricted":
            _require(offset in restricted_offsets and
                     prediction.get("frame_type") != "I_FRAME" and
                     frame_id not in action_estimates and not launched,
                     "adaptive decisions: invalid frame-type restriction")
        else:
            _require(actionable and estimated > 0 and utility > 0 and
                     available + 1e-9 >= estimated,
                     "adaptive decisions: invalid launch-rejected predicate")

        if offset == 0:
            if last_t0_time is None:
                _require(_close(dual_shadow, initial_price),
                         "adaptive decisions: initial shadow price mismatch")
            else:
                elapsed_us = (sample_time - last_t0_time) / 1000.0
                measured_delta = measured - last_t0_measured
                expected_dual_shadow = min(
                    1.0,
                    max(
                        0.0,
                        last_t0_dual_shadow +
                        dual_step * (measured_delta - fraction * elapsed_us) / reference,
                    ),
                )
                _require(_close(dual_shadow, expected_dual_shadow),
                         "adaptive decisions: shadow-price recurrence mismatch")
            last_t0_time = sample_time
            last_t0_measured = measured
            last_t0_dual_shadow = dual_shadow
        else:
            _require(last_t0_time is not None and
                     _close(dual_shadow, last_t0_dual_shadow),
                     "adaptive decisions: shadow price changed outside T0")

    return action_estimates


def _validate_randomized_intervention(
    run_dir: Path,
    config: dict[str, Any],
    run_id: str,
    frames: list[dict[str, str]],
    duplicated_frame_ids: set[int],
) -> tuple[dict[int, float], dict[int, float]]:
    """Reconstruct the randomized assignment and execution ledgers exactly."""
    object_name = "randomizedIntervention"
    randomized = config.get(object_name)
    _require(isinstance(randomized, dict),
             "resolved_config.json: missing randomizedIntervention object")
    _require(set(randomized) == RANDOMIZED_CONFIG_KEYS,
             "resolved_config.json: randomizedIntervention schema mismatch")
    _require(config.get("policy") == "randomized_full_copy_exploration" and
             config.get("topology") == "dual_interface",
             "resolved_config.json: randomized intervention requires dual_interface")
    _require(isinstance(randomized.get("csv_schema_version"), int) and
             not isinstance(randomized.get("csv_schema_version"), bool) and
             randomized.get("csv_schema_version") == RANDOMIZED_CSV_SCHEMA_VERSION and
             randomized.get("assignment_algorithm") == ALGORITHM_ID and
             randomized.get("randomization_consumes_ns3_rng") is False and
             randomized.get("stages") == ["T2", "T4"] and
             randomized.get("stage_offsets_us") == [RANDOMIZED_T2_OFFSET_US,
                                                       RANDOMIZED_T4_OFFSET_US] and
             randomized.get("primary_path") == 1 and
             randomized.get("primary_copy_id") == 0 and
             randomized.get("secondary_path") == 0 and
             randomized.get("secondary_copy_id") == 1 and
             randomized.get("common_eligibility_rule") ==
             RANDOMIZED_COMMON_ELIGIBILITY_RULE and
             randomized.get("intervention") == "canonical_full_secondary_copy" and
             randomized.get("token_gate_enabled") is False and
             randomized.get("cost_estimator_id") == RANDOMIZED_COST_ESTIMATOR,
             "resolved_config.json: invalid randomized intervention provenance")
    for key, expected in (
        ("primary_path", 1), ("primary_copy_id", 0),
        ("secondary_path", 0), ("secondary_copy_id", 1),
    ):
        value = randomized.get(key)
        _require(isinstance(value, int) and not isinstance(value, bool) and
                 value == expected,
                 "resolved_config.json: invalid randomized intervention provenance")

    salt = randomized.get("assignment_salt")
    seed = config.get("seed")
    run = config.get("run")
    for key, value in (("assignment_salt", salt), ("seed", seed), ("run", run)):
        _require(isinstance(value, int) and not isinstance(value, bool) and
                 0 <= value < 2**64,
                 f"resolved_config.json: randomized {key} is not uint64")

    probabilities = randomized.get("arm_probabilities")
    _require(isinstance(probabilities, dict) and
             set(probabilities) == {"FULL_COPY_T2", "FULL_COPY_T4", "CONTROL"},
             "resolved_config.json: invalid randomized arm probabilities")
    t2_probability = _config_number(
        probabilities, "FULL_COPY_T2", "randomizedIntervention.arm_probabilities"
    )
    t4_probability = _config_number(
        probabilities, "FULL_COPY_T4", "randomizedIntervention.arm_probabilities"
    )
    control_probability = _config_number(
        probabilities, "CONTROL", "randomizedIntervention.arm_probabilities"
    )
    _require(t2_probability > 0 and t4_probability > 0 and
             control_probability > 0 and
             _close(control_probability, (1.0 - t2_probability) - t4_probability),
             "resolved_config.json: randomized arm probabilities do not partition one")

    window_start = randomized.get("assignment_window_start_ns")
    window_stop = randomized.get("assignment_window_stop_ns")
    stop_guard_us = randomized.get("assignment_stop_guard_us")
    _require(all(isinstance(value, int) and not isinstance(value, bool)
                 for value in (window_start, window_stop, stop_guard_us)) and
             window_start >= 0 and stop_guard_us > 0 and
             window_stop > window_start + RANDOMIZED_T4_OFFSET_US * 1000,
             "resolved_config.json: invalid randomized assignment window")
    meter = config.get("secondaryAirtimeMeter")
    _require(isinstance(meter, dict) and meter.get("enabled") is True,
             "resolved_config.json: randomized intervention requires airtime meter")
    meter_start = meter.get("measurement_start_ns")
    meter_stop = meter.get("measurement_stop_ns")
    _require(isinstance(meter_start, int) and not isinstance(meter_start, bool) and
             isinstance(meter_stop, int) and not isinstance(meter_stop, bool) and
             window_start == meter_start and
             window_stop == meter_stop - stop_guard_us * 1000,
             "resolved_config.json: randomized assignment window provenance mismatch")
    warmup_s = config.get("warmup_s")
    duration_s = config.get("duration_s")
    _require(isinstance(warmup_s, (int, float)) and not isinstance(warmup_s, bool) and
             isinstance(duration_s, (int, float)) and not isinstance(duration_s, bool) and
             math.isfinite(float(warmup_s)) and math.isfinite(float(duration_s)) and
             float(warmup_s) >= 0 and float(duration_s) > 0,
             "resolved_config.json: invalid randomized run interval")
    expected_start = math.floor(float(warmup_s) * 1e9 + 0.5)
    expected_stop = math.floor((float(warmup_s) + float(duration_s)) * 1e9 + 0.5)
    _require(meter_start == expected_start and meter_stop == expected_stop,
             "resolved_config.json: randomized meter window differs from run interval")
    prediction = config.get("predictionTelemetry")
    _require(isinstance(prediction, dict) and prediction.get("enabled") is True and
             prediction.get("sample_offsets_us") == [0, RANDOMIZED_T2_OFFSET_US,
                                                       RANDOMIZED_T4_OFFSET_US] and
             prediction.get("polling_interval_us") == 1000 and
             prediction.get("polling_report_delay_us") == 1000 and
             prediction.get("event_log_enabled") is False and
             prediction.get("oracle_features_enabled") is False,
             "resolved_config.json: invalid randomized paired prediction configuration")
    wifi = config.get("wifi")
    stream = config.get("stream")
    _require(isinstance(wifi, dict) and wifi.get("guard_interval") == "800ns" and
             all(isinstance(wifi.get(key), int) and
                 not isinstance(wifi.get(key), bool) and wifi.get(key) == expected
                 for key, expected in (
                     ("max_ampdu_size_bytes", 65535), ("txop_limit_us", 0),
                     ("rts_cts_threshold_bytes", 4692480),
                 )),
             "resolved_config.json: invalid randomized one-PPDU Wi-Fi provenance")
    queue_max_delay_ms = wifi.get("queue_max_delay_ms")
    _require(isinstance(queue_max_delay_ms, int) and
             not isinstance(queue_max_delay_ms, bool) and queue_max_delay_ms > 0,
             "resolved_config.json: invalid randomized queue delay")
    _require(isinstance(stream, dict) and stream.get("source") == "synthetic" and
             stream.get("emission_mode") == "burst",
             "resolved_config.json: randomized intervention requires burst synthetic input")
    frame_size = stream.get("frame_size_bytes")
    payload_size = stream.get("payload_size_bytes")
    keyframe_multiplier = stream.get("keyframe_size_multiplier")
    deadline_us = stream.get("deadline_us")
    _require(isinstance(frame_size, int) and not isinstance(frame_size, bool) and
             frame_size > 0 and
             isinstance(payload_size, int) and not isinstance(payload_size, bool) and
             payload_size > 0 and
             isinstance(keyframe_multiplier, (int, float)) and
             not isinstance(keyframe_multiplier, bool) and
             math.isfinite(float(keyframe_multiplier)) and
             isinstance(deadline_us, int) and not isinstance(deadline_us, bool) and
             deadline_us > RANDOMIZED_T4_OFFSET_US,
             "resolved_config.json: invalid randomized bounded synthetic profile")
    largest_frame_float = frame_size * float(keyframe_multiplier)
    _require(math.isfinite(largest_frame_float) and
             1 <= largest_frame_float <= 2**32 - 1,
             "resolved_config.json: randomized synthetic frame bound is invalid")
    largest_frame_bytes = math.floor(largest_frame_float + 0.5)
    largest_frame_packets = 1 + (largest_frame_bytes - 1) // payload_size
    largest_aggregate_bytes = largest_frame_bytes + largest_frame_packets * (
        ADAPTIVE_ESTIMATOR_STREAMING_HEADER_BYTES +
        ADAPTIVE_ESTIMATOR_MAC_SERVICE_OVERHEAD_BYTES +
        ADAPTIVE_ESTIMATOR_ADDITIONAL_BYTES_PER_PACKET
    )
    _require(largest_aggregate_bytes <= wifi["max_ampdu_size_bytes"],
             "resolved_config.json: randomized frame exceeds one-PPDU estimator domain")
    minimum_stop_guard_us = (
        queue_max_delay_ms * 1000 + 1000 + deadline_us - RANDOMIZED_T4_OFFSET_US
    )
    _require(minimum_stop_guard_us <= stop_guard_us <= (2**63 - 1) // 1000 and
             float(duration_s) * 1e6 > stop_guard_us + RANDOMIZED_T4_OFFSET_US,
             "resolved_config.json: randomized stop guard does not cover settlement")

    assignments_path = run_dir / "randomized_intervention_assignments.csv"
    executions_path = run_dir / "randomized_intervention_executions.csv"
    _require(assignments_path.is_file(),
             "missing core file: randomized_intervention_assignments.csv")
    _require(executions_path.is_file(),
             "missing core file: randomized_intervention_executions.csv")
    assignments = _csv(assignments_path, RANDOMIZED_ASSIGNMENT_COLUMNS)
    executions = _csv(executions_path, RANDOMIZED_EXECUTION_COLUMNS)
    _require(assignments and executions,
             "randomized intervention ledgers contain no rows")
    _require(all(set(row) == RANDOMIZED_ASSIGNMENT_COLUMNS and
                 all(value is not None for value in row.values())
                 for row in assignments),
             "randomized intervention assignments: CSV schema mismatch")
    _require(all(set(row) == RANDOMIZED_EXECUTION_COLUMNS and
                 all(value is not None for value in row.values())
                 for row in executions),
             "randomized intervention executions: CSV schema mismatch")

    frame_ids = [_integer(frame, "frame_id", "frames.csv") for frame in frames]
    _require(len(frame_ids) == len(set(frame_ids)) and
             all(frame_id < 2**64 for frame_id in frame_ids),
             "frames.csv: invalid randomized frame IDs")
    frame_by_id = dict(zip(frame_ids, frames))
    expected_frame_ids = set(frame_by_id)
    _require(all(frame.get("policy") == "randomized_full_copy_exploration" and
                 frame.get("primary_link") == "1"
                 for frame in frames),
             "frames.csv: randomized policy/primary path mismatch")
    _require(len(assignments) == len(frames) and len(executions) == len(frames),
             "randomized intervention ledgers: frame cardinality mismatch")

    def index_rows(rows: list[dict[str, str]], file_name: str) -> dict[int, dict[str, str]]:
        indexed: dict[int, dict[str, str]] = {}
        for row in rows:
            frame_id = _integer(row, "frame_id", file_name)
            _require(frame_id in expected_frame_ids,
                     f"{file_name}: unknown frame")
            _require(frame_id not in indexed,
                     f"{file_name}: duplicate frame")
            indexed[frame_id] = row
        _require(set(indexed) == expected_frame_ids,
                 f"{file_name}: frame coverage mismatch")
        return indexed

    assignment_by_frame = index_rows(
        assignments, "randomized_intervention_assignments.csv"
    )
    execution_by_frame = index_rows(
        executions, "randomized_intervention_executions.csv"
    )

    samples = _csv(run_dir / "prediction_samples.csv", RANDOMIZED_PREDICTION_COLUMNS)
    sample_by_key: dict[tuple[int, int, int, int], dict[str, str]] = {}
    for sample in samples:
        offset = _integer(sample, "sample_offset_us", "prediction_samples.csv")
        if offset not in {RANDOMIZED_T2_OFFSET_US, RANDOMIZED_T4_OFFSET_US}:
            continue
        frame_id = _integer(sample, "frame_id", "prediction_samples.csv")
        path_id = _integer(sample, "path_id", "prediction_samples.csv")
        copy_id = _integer(sample, "copy_id", "prediction_samples.csv")
        key = (frame_id, offset, path_id, copy_id)
        _require(key not in sample_by_key,
                 "prediction_samples.csv: duplicate randomized paired sample")
        sample_by_key[key] = sample
    expected_sample_keys = {
        (frame_id, offset, path_id, copy_id)
        for frame_id in expected_frame_ids
        for offset in (RANDOMIZED_T2_OFFSET_US, RANDOMIZED_T4_OFFSET_US)
        for path_id, copy_id in ((1, 0), (0, 1))
    }
    _require(set(sample_by_key) == expected_sample_keys,
             "prediction_samples.csv: randomized paired sample coverage mismatch")

    def paired_samples(
        frame_id: int, offset: int
    ) -> tuple[dict[str, str], dict[str, str]]:
        primary = sample_by_key[(frame_id, offset, 1, 0)]
        secondary = sample_by_key[(frame_id, offset, 0, 1)]
        _require(primary["run_id"] == secondary["run_id"] == run_id,
                 "prediction_samples.csv: randomized paired run_id mismatch")
        for field in (
            "frame_id", "sample_stage", "sample_offset_us", "sample_time_ns",
            "generation_time_ns", "deadline_time_ns", "frame_size_bytes",
            "frame_packet_count", "frame_type",
        ):
            _require(primary[field] == secondary[field],
                     f"prediction_samples.csv: randomized paired {field} mismatch")
        _require(primary["sample_stage"] == _stage_name(offset),
                 "prediction_samples.csv: randomized stage mismatch")
        return primary, secondary

    def validate_untreated_secondary(sample: dict[str, str]) -> None:
        file_name = "prediction_samples.csv"
        packet_count = _integer(sample, "frame_packet_count", file_name)
        _require(_integer(sample, "packets_submitted", file_name) == 0 and
                 _integer(sample, "application_socket_packet_bytes_submitted", file_name) == 0 and
                 _integer(sample, "packets_remaining_to_submit", file_name) == packet_count and
                 not _flag(sample, "sender_mac_complete", file_name) and
                 _flag(sample, "actionable", file_name),
                 f"{file_name}: hypothetical secondary contains application progress")
        for field in (
            "frame_packets_mac_enqueued", "frame_packets_mac_dequeued",
            "frame_packets_tx_succeeded", "frame_mpdu_attempt_failures",
            "frame_packets_terminally_dropped", "frame_packets_currently_queued",
            "frame_mac_service_bytes_currently_queued",
        ):
            value = _optional_integer(sample, field, file_name)
            _require(value in {None, 0},
                     f"{file_name}: hypothetical secondary contains MAC progress")

    def validate_assignment_provenance(
        row: dict[str, str], frame_id: int, file_name: str
    ) -> None:
        assignment = assign_frame(
            salt, seed, run, frame_id, t2_probability, t4_probability
        )
        _require(row["run_id"] == run_id and
                 _integer(row, "schema_version", file_name) ==
                 RANDOMIZED_CSV_SCHEMA_VERSION and
                 _integer(row, "assignment_seed", file_name) == seed and
                 _integer(row, "assignment_run", file_name) == run and
                 _integer(row, "assignment_salt", file_name) == salt and
                 row["assignment_algorithm"] == ALGORITHM_ID and
                 row["assigned_arm"] == assignment.arm.value,
                 f"{file_name}: assignment provenance mismatch")
        raw_draw = _integer(row, "raw_draw", file_name)
        unit_draw = _number(row, "unit_draw", file_name)
        _require(raw_draw < 2**64 and raw_draw == assignment.raw_draw and
                 0 <= unit_draw < 1 and _close(unit_draw, assignment.unit_draw),
                 f"{file_name}: deterministic assignment draw mismatch")
        _require(_close(_number(row, "t2_probability", file_name), t2_probability) and
                 _close(_number(row, "t4_probability", file_name), t4_probability) and
                 _close(_number(row, "control_probability", file_name),
                        control_probability) and
                 _close(_number(row, "propensity", file_name),
                        assignment.arm_probability),
                 f"{file_name}: assignment probability mismatch")

    def validate_snapshot_evidence(
        row: dict[str, str],
        primary: dict[str, str],
        secondary: dict[str, str],
        file_name: str,
    ) -> None:
        _require(row["primary_sample_time_ns"] == primary["sample_time_ns"] and
                 row["secondary_sample_time_ns"] == secondary["sample_time_ns"] and
                 row["primary_feature_watermark_time_ns"] ==
                 primary["latest_feature_event_time_ns"] and
                 row["primary_feature_watermark_sequence"] ==
                 primary["latest_feature_event_sequence"] and
                 row["secondary_feature_watermark_time_ns"] ==
                 secondary["latest_feature_event_time_ns"] and
                 row["secondary_feature_watermark_sequence"] ==
                 secondary["latest_feature_event_sequence"] and
                 row["generation_time_ns"] == primary["generation_time_ns"] and
                 row["deadline_time_ns"] == primary["deadline_time_ns"],
                 f"{file_name}: paired telemetry evidence mismatch")

    def validate_descriptor(
        row: dict[str, str],
        frame: dict[str, str],
        available_key: str,
        file_name: str,
    ) -> tuple[bool, float, float]:
        available = _flag(row, available_key, file_name)
        packet_count = _integer(row, "secondary_packet_count", file_name)
        expected_service_bytes = _integer(
            row, "secondary_expected_mac_service_bytes", file_name
        )
        safety_factor = _number(row, "cost_safety_factor", file_name)
        nominal = _number(row, "nominal_airtime_us", file_name)
        estimated = _number(row, "estimated_airtime_us", file_name)
        _require(row["cost_estimator"] == RANDOMIZED_COST_ESTIMATOR and
                 _close(safety_factor, RANDOMIZED_COST_SAFETY_FACTOR),
                 f"{file_name}: randomized cost provenance mismatch")
        if not available:
            _require(packet_count == 0 and row["secondary_packet_indices"] == "" and
                     expected_service_bytes == 0 and nominal == 0 and estimated == 0,
                     f"{file_name}: unavailable descriptor has cost evidence")
            return False, nominal, estimated
        frame_size = _integer(frame, "frame_size_bytes", "frames.csv")
        frame_packets = _integer(frame, "packet_count", "frames.csv")
        expected_indices = ";".join(str(index) for index in range(frame_packets))
        expected_service = frame_size + frame_packets * (
            ADAPTIVE_ESTIMATOR_STREAMING_HEADER_BYTES +
            ADAPTIVE_ESTIMATOR_MAC_SERVICE_OVERHEAD_BYTES
        )
        expected_nominal = _adaptive_nominal_airtime_us(
            frame_size, frame_packets, 1.0
        )
        _require(packet_count == frame_packets and
                 row["secondary_packet_indices"] == expected_indices and
                 expected_service_bytes == expected_service and
                 _close(nominal, expected_nominal) and
                 _close(estimated, RANDOMIZED_COST_SAFETY_FACTOR * expected_nominal),
                 f"{file_name}: randomized descriptor/cost arithmetic mismatch")
        return True, nominal, estimated

    launched_frame_ids: set[int] = set()
    action_estimates: dict[int, float] = {}
    action_nominal_airtimes: dict[int, float] = {}
    launched_at_t2: set[int] = set()
    for frame_id in sorted(expected_frame_ids):
        frame = frame_by_id[frame_id]
        assignment_row = assignment_by_frame[frame_id]
        execution_row = execution_by_frame[frame_id]
        t2_primary, t2_secondary = paired_samples(frame_id, RANDOMIZED_T2_OFFSET_US)
        t4_primary, t4_secondary = paired_samples(frame_id, RANDOMIZED_T4_OFFSET_US)
        validate_untreated_secondary(t2_secondary)
        validate_assignment_provenance(
            assignment_row, frame_id, "randomized_intervention_assignments.csv"
        )
        validate_snapshot_evidence(
            assignment_row, t2_primary, t2_secondary,
            "randomized_intervention_assignments.csv",
        )
        _require(assignment_row["prospective_t4_time_ns"] ==
                 str(_integer(t2_primary, "generation_time_ns", "prediction_samples.csv") +
                     RANDOMIZED_T4_OFFSET_US * 1000) and
                 assignment_row["frame_size_bytes"] == t2_primary["frame_size_bytes"] and
                 assignment_row["frame_packet_count"] ==
                 t2_primary["frame_packet_count"] and
                 assignment_row["frame_type"] == t2_primary["frame_type"],
                 "randomized intervention assignments: immutable metadata mismatch")
        descriptor_available, nominal, estimated = validate_descriptor(
            assignment_row,
            frame,
            "descriptor_available",
            "randomized_intervention_assignments.csv",
        )
        t2_actionable = _flag(t2_primary, "actionable", "prediction_samples.csv")
        t2_sample_time = _integer(
            t2_primary, "sample_time_ns", "prediction_samples.csv"
        )
        prospective_t4 = _integer(
            assignment_row, "prospective_t4_time_ns",
            "randomized_intervention_assignments.csv",
        )
        if t2_sample_time < window_start or prospective_t4 >= window_stop:
            expected_eligibility = (False, "outside_assignment_window")
        elif not t2_actionable:
            expected_eligibility = (False, "primary_not_actionable_t2")
        elif not descriptor_available:
            expected_eligibility = (False, "delayed_copy_unavailable_t2")
        else:
            expected_eligibility = (True, "eligible")
        eligible = _flag(
            assignment_row, "eligible_t2", "randomized_intervention_assignments.csv"
        )
        _require((eligible, assignment_row["eligibility_reason"]) == expected_eligibility,
                 "randomized intervention assignments: eligibility priority mismatch")

        validate_assignment_provenance(
            execution_row, frame_id, "randomized_intervention_executions.csv"
        )
        _require(execution_row["eligible_t2"] == assignment_row["eligible_t2"] and
                 execution_row["eligibility_reason"] ==
                 assignment_row["eligibility_reason"],
                 "randomized intervention executions: assignment outcome changed")
        arm = assignment_row["assigned_arm"]
        expected_stage = "T4" if eligible and arm == "FULL_COPY_T4" else "T2"
        _require(execution_row["execution_stage"] == expected_stage,
                 "randomized intervention executions: stage mismatch")
        execution_primary, execution_secondary = (
            (t4_primary, t4_secondary) if expected_stage == "T4"
            else (t2_primary, t2_secondary)
        )
        validate_snapshot_evidence(
            execution_row,
            execution_primary,
            execution_secondary,
            "randomized_intervention_executions.csv",
        )
        descriptor_at_assignment = _flag(
            execution_row,
            "descriptor_available_at_assignment",
            "randomized_intervention_executions.csv",
        )
        _require(descriptor_at_assignment == descriptor_available,
                 "randomized intervention executions: assignment descriptor flag changed")
        execution_descriptor, execution_nominal, execution_estimated = validate_descriptor(
            execution_row,
            frame,
            "descriptor_available_at_assignment",
            "randomized_intervention_executions.csv",
        )
        _require(execution_descriptor == descriptor_available and
                 _close(execution_nominal, nominal) and
                 _close(execution_estimated, estimated) and
                 execution_row["secondary_packet_count"] ==
                 assignment_row["secondary_packet_count"] and
                 execution_row["secondary_packet_indices"] ==
                 assignment_row["secondary_packet_indices"] and
                 execution_row["secondary_expected_mac_service_bytes"] ==
                 assignment_row["secondary_expected_mac_service_bytes"],
                 "randomized intervention executions: descriptor evidence changed")

        file_name = "randomized_intervention_executions.csv"
        descriptor_at_execution = _flag(
            execution_row, "descriptor_available_at_execution", file_name
        )
        primary_actionable = _flag(execution_row, "primary_actionable", file_name)
        attempted = _flag(execution_row, "attempted", file_name)
        launched = _flag(execution_row, "launched", file_name)
        noncompliance = _flag(execution_row, "noncompliance", file_name)
        expected_primary_actionable = _flag(
            execution_primary, "actionable", "prediction_samples.csv"
        )
        _require(primary_actionable == expected_primary_actionable,
                 "randomized intervention executions: primary actionability mismatch")

        if not eligible:
            expected_execution = (
                descriptor_available, False, False, False,
                "not_exposed_ineligible_t2",
            )
        elif arm == "CONTROL":
            expected_execution = (True, False, False, False, "control_no_launch")
        elif arm == "FULL_COPY_T2":
            expected_execution = (
                True, True, launched, not launched,
                "launched_t2" if launched else "launch_rejected_t2",
            )
        elif not primary_actionable:
            # The controller queries the canonical descriptor before checking
            # T4 actionability.  A frame can therefore become MAC-complete
            # while its delayed-copy descriptor remains observable; either
            # descriptor value is factual and neither is noncompliance.
            expected_execution = (
                descriptor_at_execution,
                False,
                False,
                False,
                "primary_not_actionable_t4",
            )
        elif not descriptor_at_execution:
            expected_execution = (
                False, False, False, True, "assigned_t4_not_launched"
            )
        else:
            expected_execution = (
                True, True, launched, not launched,
                "launched_t4" if launched else "launch_rejected_t4",
            )
        _require(
            (descriptor_at_execution, attempted, launched, noncompliance,
             execution_row["status"]) == expected_execution,
            "randomized intervention executions: status/compliance semantics mismatch",
        )
        if launched:
            _require(frame_id not in launched_frame_ids,
                     "randomized intervention executions: frame launched twice")
            launched_frame_ids.add(frame_id)
            action_estimates[frame_id] = estimated
            action_nominal_airtimes[frame_id] = nominal
            if expected_stage == "T2":
                launched_at_t2.add(frame_id)

    for frame_id in expected_frame_ids - launched_at_t2:
        validate_untreated_secondary(
            sample_by_key[(frame_id, RANDOMIZED_T4_OFFSET_US, 0, 1)]
        )
    _require(launched_frame_ids == duplicated_frame_ids,
             "randomized intervention executions: launches do not match duplicated frames")
    return action_estimates, action_nominal_airtimes


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
    frames: list[dict[str, str]] | None = None,
    stream_config: dict[str, Any] | None = None,
    action_nominal_airtimes: dict[int, float] | None = None,
) -> None:
    """Reconcile secondary PHY events, reservations, and the run budget."""
    _require(SECONDARY_AIRTIME_SUMMARY_KEYS <= summary.keys(),
             "secondary_airtime_summary.json: missing fields")
    _require(policy in {
        "selective_duplication", "adaptive_airtime_duplication",
        "adaptive_deficit_duplication", "randomized_full_copy_exploration",
        "full_duplication",
    }, "secondary airtime meter enabled for an unsupported policy")
    expected_settlement_nominals = dict(action_nominal_airtimes or {})
    if policy == "adaptive_airtime_duplication" and adaptive_config is not None:
        _, has_cost_metadata, _ = _adaptive_admission_cost_metadata(
            adaptive_config, "adaptiveAirtimeDuplication", "full_forward"
        )
        if _requires_exact_adaptive_estimator(
            adaptive_config, has_cost_metadata
        ):
            _require(frames is not None and isinstance(stream_config, dict),
                     "adaptive estimator: frame or stream metadata is absent")
            _, whole_copy_nominals = _adaptive_whole_copy_costs(
                frames,
                stream_config,
                _config_number(
                    adaptive_config,
                    "cost_safety_factor",
                    "adaptiveAirtimeDuplication",
                ),
            )
            expected_settlement_nominals = {
                frame_id: whole_copy_nominals[frame_id]
                for frame_id in action_estimates
            }
    if expected_settlement_nominals:
        _require(set(expected_settlement_nominals) == set(action_estimates),
                 "secondary airtime settlements: nominal action set mismatch")
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
        if expected_settlement_nominals:
            _require(frame_id in expected_settlement_nominals and _close(
                nominal, expected_settlement_nominals[frame_id]
            ), "secondary airtime settlements: nominal estimator mismatch")
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

    reservation_policy = policy in {
        "adaptive_airtime_duplication", "adaptive_deficit_duplication",
        "randomized_full_copy_exploration",
    }
    if reservation_policy:
        if policy != "randomized_full_copy_exploration":
            _require(adaptive_config is not None,
                     "adaptive secondary airtime validation lacks controller config")
        _require(set(settlement_by_frame) == set(action_estimates),
                 "secondary airtime settlements do not match reserved actions")
        _require(observed_event_frames <= set(action_estimates),
                 "secondary airtime events do not match reserved actions")
        _require(_close(estimate_total, sum(action_estimates.values())),
                 "secondary airtime summary: action estimates do not sum")
        measured_total = sum(float(item["measured"]) for item in settlement_by_frame.values())
        _require(_close(measured_total, tagged_total),
                 "secondary airtime settlements: measured airtime does not sum")
        for frame_id, estimate in action_estimates.items():
            settlement = settlement_by_frame[frame_id]
            measured = float(settlement["measured"])
            released = float(settlement["released"])
            expected_release = max(0.0, estimate - measured)
            # These three values are written independently with setprecision(12).
            # Scale the absolute tolerance to the operands so cancellation near
            # zero does not turn harmless decimal serialization into a failure.
            serialization_tolerance = max(
                1e-9,
                1e-11 * max(abs(estimate), abs(measured), 1.0),
            )
            _require(math.isclose(
                released,
                expected_release,
                rel_tol=1e-9,
                abs_tol=serialization_tolerance,
            ),
                     "secondary airtime settlements: released reservation mismatch")
        expected_ratio = tagged_total / estimate_total if estimate_total else 0.0
        _require(_close(ratio, expected_ratio),
                 "secondary airtime summary: estimate ratio mismatch")
        if policy == "randomized_full_copy_exploration":
            _require(_close(maximum_debt, 0.0),
                     "secondary airtime summary: randomized policy has budget debt")
            for key in (
                "budget_fraction", "initial_bucket_capacity_us", "finite_run_budget_us",
                "budget_excess_us",
            ):
                _require(summary.get(key) is None,
                         f"secondary_airtime_summary.json: {key} must be null")
        else:
            assert adaptive_config is not None
            object_name = (
                "adaptiveDeficitDuplication"
                if policy == "adaptive_deficit_duplication"
                else "adaptiveAirtimeDuplication"
            )
            fraction = _config_number(adaptive_config, "budget_fraction", object_name)
            capacity = _config_number(
                adaptive_config, "initial_bucket_capacity_us", object_name
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
        "adaptive_airtime_duplication", "adaptive_deficit_duplication",
        "randomized_full_copy_exploration",
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
    randomized_policy = config["policy"] == "randomized_full_copy_exploration"
    _require(not randomized_policy or event_enabled is False,
             "randomized paired-link telemetry requires disabled raw event logging")
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
    copies_per_frame = 2 if randomized_policy else 1
    _require(len(samples) == len(frames) * len(offsets) * copies_per_frame,
             "prediction_samples.csv: receiver-independent cardinality mismatch")

    selected_path = 0 if config["policy"] == "fixed_link_0" else 1
    expected_identities = (
        {(1, 0), (0, 1)} if randomized_policy else {(selected_path, 0)}
    )
    frames_by_id = {int(row["frame_id"]): row for row in frames}
    sample_keys: set[tuple[int, int, int, int]] = set()
    observed_order: list[tuple[int, int, int, int, int]] = []
    by_frame_copy: dict[tuple[int, int, int], list[dict[str, str]]] = {}
    paired_samples: dict[tuple[int, int], list[dict[str, str]]] = {}
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
        _require((path_id, copy_id) in expected_identities,
                 f"{file_name}: path/copy isolation failed")
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
        by_frame_copy.setdefault((frame_id, path_id, copy_id), []).append(row)
        paired_samples.setdefault((frame_id, offset), []).append(row)

    _require(observed_order == sorted(observed_order),
             "prediction_samples.csv: rows are not deterministically ordered")
    _require(set(by_frame_copy) == {
        (frame_id, path_id, copy_id)
        for frame_id in frames_by_id
        for path_id, copy_id in expected_identities
    }, "prediction_samples.csv: frame-copy coverage mismatch")
    for (_frame_id, _path_id, _copy_id), rows in by_frame_copy.items():
        _require({int(row["sample_offset_us"]) for row in rows} == set(offsets),
                 "prediction_samples.csv: frame copy is missing a configured stage")
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

    expected_pair_size = len(expected_identities)
    _require(set(paired_samples) == {
        (frame_id, offset) for frame_id in frames_by_id for offset in offsets
    }, "prediction_samples.csv: frame-stage coverage mismatch")
    for rows in paired_samples.values():
        _require(len(rows) == expected_pair_size and {
            (int(row["path_id"]), int(row["copy_id"])) for row in rows
        } == expected_identities,
                 "prediction_samples.csv: incomplete paired path/copy sample")
        if randomized_policy:
            primary = next(row for row in rows if row["path_id"] == "1")
            secondary = next(row for row in rows if row["path_id"] == "0")
            for field in (
                "frame_id", "sample_stage", "sample_offset_us", "sample_time_ns",
                "generation_time_ns", "deadline_time_ns", "frame_age_us",
                "deadline_slack_us", "frame_size_bytes", "frame_packet_count",
                "frame_type",
            ):
                _require(primary[field] == secondary[field],
                         f"prediction_samples.csv: paired {field} mismatch")

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
    _require(config.get("topology") in {
        "single_link", "dual_interface", "mlo_str", "mlo_emlsr",
    },
             "resolved_config.json: invalid topology")
    _require(isinstance(config.get("stream"), dict), "resolved_config.json: missing stream")
    wifi = config.get("wifi", {})
    max_inflights = int(wifi.get("sta_max_inflights", 1))
    _require(1 <= max_inflights <= 15,
             "resolved_config.json: invalid STA max inflights")
    _require(max_inflights == 1 or
             (config["topology"] == "mlo_str" and wifi.get("block_ack_enabled") is True),
             "resolved_config.json: multiple inflights require MLO Block Ack")
    mlo_runtime: dict[str, Any] | None = None
    mlo_runtime_path = run_dir / "mlo_runtime.json"
    if config["topology"] == "mlo_emlsr":
        _require(max_inflights == 1,
                 "resolved_config.json: EMLSR requires one maximum inflight")
        _require(wifi.get("block_ack_enabled") is True and
                 wifi.get("static_association") is True and
                 wifi.get("tid_to_link_mapping_ul") == "0 0,1" and
                 wifi.get("str_mode") == "not_applicable" and
                 wifi.get("multi_link_mode") == "EMLSR" and
                 wifi.get("application_socket_count") == 1 and
                 wifi.get("application_duplication") is False,
                 "resolved_config.json: invalid EMLSR MLD/application setup")
        expected_emlsr = {
            "activated": True,
            "profile": "advanced_sta_ap_fixed_aux_v4",
            "manager": "ns3::AdvancedEmlsrManager",
            "ap_manager": "ns3::AdvancedApEmlsrManager",
            "link_ids": [0, 1],
            "main_phy_id": 1,
            "padding_delay_us": 128,
            "transition_delay_us": 128,
            "transition_timeout_us": 0,
            "medium_sync_duration_us": 5472,
            "msd_ofdm_ed_threshold_dbm": -72,
            "msd_max_n_txops": 1,
            "channel_switch_delay_us": 100,
            "switch_aux_phy": False,
            "aux_phy_tx_capable": False,
            "aux_phy_channel_width_mhz": 20,
            "aux_phy_max_modulation_class": "OFDM",
            "put_aux_phy_to_sleep": False,
            "in_device_interference": False,
            "use_notified_mac_header": True,
            "reset_cam_state": False,
            "allow_ul_txop_in_rx": False,
            "interrupt_switch": False,
            "use_aux_phy_cca": False,
            "switch_main_phy_back_delay_us": 5000,
            "keep_main_phy_after_dl_txop": False,
            "check_access_on_main_phy_link": True,
            "min_ac_to_skip_check_access": "AC_BK",
            "ap_use_notified_mac_header": True,
            "ap_early_switch_to_listening": False,
            "ap_wait_trans_delay_on_psdu_rx_error": True,
            "ap_update_cw_after_failed_icf": True,
            "ap_report_failed_icf": True,
            "cam_generate_backoff_without_tx": False,
            "cam_proactive_backoff": False,
            "cam_reset_backoff_threshold_us": 0,
            "cam_n_slots_left": 0,
            "cam_n_slots_left_min_delay_us": 25,
            "notify_mac_header_rx_end": True,
            "main_phy_frequency_ranges": [
                "WIFI_SPECTRUM_2_4_GHZ", "WIFI_SPECTRUM_5_GHZ",
            ],
        }
        _require(wifi.get("emlsr") == expected_emlsr,
                 "resolved_config.json: invalid practical EMLSR profile")
        _require(mlo_runtime_path.is_file(), "missing core file: mlo_runtime.json")
        mlo_runtime = _json(mlo_runtime_path)
        expected_runtime = {
            "mode": "EMLSR",
            "profile": "advanced_sta_ap_fixed_aux_v4",
            "station_emlsr_activated": True,
            "ap_emlsr_activated": True,
            "emlsr_manager": "ns3::AdvancedEmlsrManager",
            "ap_emlsr_manager": "ns3::AdvancedApEmlsrManager",
            "emlsr_link_ids": [0, 1],
            "ap_emlsr_enabled_per_link": [True, True],
            "main_phy_id": 1,
            "initial_main_phy_link_id": 1,
            "initial_main_phy_band": "5 GHz",
            "padding_delay_us": 128,
            "transition_delay_us": 128,
            "transition_timeout_us": 0,
            "medium_sync_duration_us": 5472,
            "msd_ofdm_ed_threshold_dbm": -72,
            "msd_max_n_txops": 1,
            "channel_switch_delay_us": 100,
            "switch_aux_phy": False,
            "aux_phy_tx_capable": False,
            "aux_phy_channel_width_mhz": 20,
            "aux_phy_max_modulation_class": "OFDM",
            "put_aux_phy_to_sleep": False,
            "in_device_interference": False,
            "use_notified_mac_header": True,
            "reset_cam_state": False,
            "allow_ul_txop_in_rx": False,
            "interrupt_switch": False,
            "use_aux_phy_cca": False,
            "switch_main_phy_back_delay_us": 5000,
            "keep_main_phy_after_dl_txop": False,
            "check_access_on_main_phy_link": True,
            "min_ac_to_skip_check_access": "AC_BK",
            "ap_use_notified_mac_header": True,
            "ap_early_switch_to_listening": False,
            "ap_wait_trans_delay_on_psdu_rx_error": True,
            "ap_update_cw_after_failed_icf": True,
            "ap_report_failed_icf": True,
            "all_phy_settings_match_profile": True,
            "all_cam_settings_match_profile": True,
            "notify_mac_header_rx_end": True,
            "main_phy_frequency_ranges": [
                "WIFI_SPECTRUM_2_4_GHZ", "WIFI_SPECTRUM_5_GHZ",
            ],
        }
        _require(all(mlo_runtime.get(key) == value
                     for key, value in expected_runtime.items()),
                 "mlo_runtime.json: practical EMLSR profile mismatch")
    else:
        _require(not mlo_runtime_path.exists(),
                 "mlo_runtime.json exists for a non-EMLSR topology")
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
    action_nominal_airtimes: dict[int, float] = {}
    observed_budget_debt_us = 0.0
    decision_samples: dict[tuple[int, int], dict[str, str]] = {}
    if config["policy"] in {
        "selective_duplication", "adaptive_airtime_duplication",
        "adaptive_deficit_duplication",
    }:
        decision_samples = _prediction_decision_samples(
            run_dir, run_id, expected_path=1
        )
    if config["policy"] == "selective_duplication":
        selective_config = config.get("selectiveDuplication")
        _require(isinstance(selective_config, dict),
                 "resolved_config.json: missing selectiveDuplication object")
        offsets = _strict_integer_list(
            selective_config.get("decision_offsets_us"),
            "selectiveDuplication.decision_offsets_us",
            positive=False,
        )
        _require(
            selective_config.get("model_id") in {
                "commodity_polling_1ms_genuine_v1",
                "commodity_polling_1ms_legacy_frame_delayed_v1",
                PRIMARY_T0_MODEL_ID,
            } and
            _prediction_target_provenance_valid(selective_config) and
            isinstance(selective_config.get("source_model_sha256"), str) and
            re.fullmatch(r"[0-9a-f]{64}", selective_config["source_model_sha256"])
            is not None and
            selective_config.get("feature_set") == "F0+F1-degraded" and
            selective_config.get("degradation_profile") == "polling_1ms" and
            selective_config.get("calibration") == "platt" and
            offsets[0] == 0 and
            set(offsets) <= {0, 1000, 2000, 4000} and
            selective_config.get("stages") == [_stage_name(offset) for offset in offsets],
            "resolved_config.json: invalid selective predictor provenance",
        )
        _require(_prediction_model_offsets_valid(selective_config, offsets),
                 "resolved_config.json: primary T0 predictor only supports offset 0")
        selective_path = run_dir / "selective_duplication_decisions.csv"
        _require(selective_path.is_file(),
                 "missing core file: selective_duplication_decisions.csv")
        selective = _csv(selective_path, SELECTIVE_DECISION_COLUMNS)
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
            prediction = decision_samples.get((frame_id, offset))
            _require(prediction is not None,
                     "selective decisions: predictor sample is absent")
            _require(
                row["path_id"] == prediction["path_id"] and
                row["copy_id"] == prediction["copy_id"] and
                row["sample_stage"] == prediction["sample_stage"] ==
                _stage_name(offset) and
                row["sample_time_ns"] == prediction["sample_time_ns"] and
                row["deadline_time_ns"] == prediction["deadline_time_ns"] and
                row["actionable"] == prediction["actionable"],
                "selective decisions: predictor telemetry mismatch",
            )
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
    elif config["policy"] in {
        "adaptive_airtime_duplication", "adaptive_deficit_duplication",
    }:
        primary_deficit = config["policy"] == "adaptive_deficit_duplication"
        config_key = (
            "adaptiveDeficitDuplication" if primary_deficit
            else "adaptiveAirtimeDuplication"
        )
        adaptive_config = config.get(config_key)
        _require(isinstance(adaptive_config, dict),
                 f"resolved_config.json: missing {config_key} object")
        expected_selection = (
            "primary_unacknowledged_reverse" if primary_deficit else "full_forward"
        )
        _validate_adaptive_config(adaptive_config, expected_selection)
        adaptive_path = run_dir / "adaptive_airtime_decisions.csv"
        _require(adaptive_path.is_file(),
                 "missing core file: adaptive_airtime_decisions.csv")
        required_columns = ADAPTIVE_DECISION_COLUMNS | (
            DEFICIT_DECISION_COLUMNS if primary_deficit else set()
        )
        adaptive = _csv(adaptive_path, required_columns)
        action_estimates = _validate_adaptive_decisions(
            adaptive,
            adaptive_config,
            frames,
            decision_samples,
            run_id,
            config["stream"],
            action_nominal_airtimes,
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
    elif config["policy"] == "randomized_full_copy_exploration":
        _require(all(key not in config for key in (
            "selectiveDuplication", "adaptiveAirtimeDuplication",
            "adaptiveDeficitDuplication",
        )), "resolved_config.json: predictor controller object exists for randomized policy")
        _require(not (run_dir / "selective_duplication_decisions.csv").exists(),
                 "selective decision output exists for randomized policy")
        _require(not (run_dir / "adaptive_airtime_decisions.csv").exists(),
                 "adaptive decision output exists for randomized policy")
        action_estimates, action_nominal_airtimes = _validate_randomized_intervention(
            run_dir, config, run_id, frames, duplicated_frame_ids
        )
    else:
        _require(not (run_dir / "selective_duplication_decisions.csv").exists(),
                 "selective decision output exists for a non-selective policy")
        _require(not (run_dir / "adaptive_airtime_decisions.csv").exists(),
                 "adaptive decision output exists for a non-adaptive policy")

    if config["policy"] != "randomized_full_copy_exploration":
        _require("randomizedIntervention" not in config,
                 "resolved_config.json: randomized object exists for another policy")
        _require(not (run_dir / "randomized_intervention_assignments.csv").exists(),
                 "randomized assignment output exists for another policy")
        _require(not (run_dir / "randomized_intervention_executions.csv").exists(),
                 "randomized execution output exists for another policy")

    meter = config.get("secondaryAirtimeMeter")
    if isinstance(meter, dict) and meter.get("enabled") is True:
        _require(wifi.get("max_amsdu_size_bytes") == 0 and
                 wifi.get("fragmentation_threshold_bytes") == 65535,
                 "resolved_config.json: secondary airtime metering requires "
                 "disabled A-MSDU and fragmentation")
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
            frames,
            config["stream"],
            action_nominal_airtimes,
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
    if config["topology"] in {"mlo_str", "mlo_emlsr"}:
        _require(sorted(link_ids) == [0, 1],
                 "link_intervals.csv: native MLO requires links 0 and 1")
        _require(len({row["device_id"] for row in mac}) == 1,
                 "mac_summary.csv: native MLO links do not share one device")
    if config["topology"] == "mlo_emlsr":
        _require(mlo_runtime is not None,
                 "mlo_runtime.json: EMLSR runtime metadata is absent")
        ordered_links = sorted(links, key=lambda row: int(row["link_id"]))
        successful_mpdus = [
            _integer(row, "successful_mpdus", "link_intervals.csv")
            for row in ordered_links
        ]
        phy_tx_time_us = [
            _integer(row, "phy_tx_time_us", "link_intervals.csv")
            for row in ordered_links
        ]
        _require(mlo_runtime.get("successful_mpdus_per_link") == successful_mpdus and
                 mlo_runtime.get("phy_tx_time_us_per_link") == phy_tx_time_us,
                 "mlo_runtime.json: per-link activity differs from link_intervals.csv")
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
