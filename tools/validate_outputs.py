#!/usr/bin/env python3
"""Validate one wifi-streaming run directory."""

from __future__ import annotations

import argparse
import bisect
import copy
import csv
import hashlib
import importlib.util
import json
import lzma
import math
import pickle
import re
import struct
from collections import Counter
from fractions import Fraction
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
MECHANISM_T2_POLICIES = {
    "mechanism_full_copy_t2",
    "mechanism_oracle_repair_t2",
    "mechanism_systematic_fec_t2",
}
MECHANISM_ACTION_BY_POLICY = {
    "mechanism_full_copy_t2": "FULL_COPY_T2",
    "mechanism_oracle_repair_t2": "ORACLE_EVENTUAL_MISSING_REPAIR_T2",
    "mechanism_systematic_fec_t2": "IDEAL_SYSTEMATIC_REPAIR_T2",
}
MECHANISM_SNAPSHOT_COLUMNS = {
    "schema_version", "run_id", "frame_id", "path_id", "copy_id",
    "sample_time_ns", "source_packet_count", "frame_packets_tx_succeeded",
    "frame_packets_pending_primary", "frame_packets_currently_queued",
    "frame_mac_service_bytes_currently_queued", "mac_queue_packets",
    "mac_queue_service_bytes", "packets_ahead_of_frame",
    "mac_service_bytes_ahead_of_frame", "primary_ack_deficit_count",
    "primary_ack_deficit_packet_indices",
}
MECHANISM_ACTION_COLUMNS = {
    "schema_version", "run_id", "frame_id", "generation_time_ns", "action",
    "requested", "launched", "reason", "source_packet_count",
    "action_packet_count", "action_packet_indices", "expected_mac_service_bytes",
    "nominal_airtime_us", "action_time_us",
}
FRAME_PACKET_OUTCOME_COLUMNS = {
    "run_id", "frame_id", "source_packet_count",
    "received_source_packet_indices", "missing_source_packet_indices",
    "copy_0_source_packet_indices", "copy_1_source_packet_indices",
    "link_0_source_packet_indices", "link_1_source_packet_indices",
    "received_coded_repair_indices",
}
SECONDARY_AIRTIME_EVENT_COLUMNS = {
    "run_id", "time_ns", "path_id", "ppdu_duration_us", "tagged_mpdu_bytes",
    "frame_ids", "mixed_ppdu", "cumulative_tagged_airtime_us",
}
SECONDARY_AIRTIME_EVENT_V2_ORDERED_COLUMNS = (
    "run_id",
    "time_ns",
    "path_id",
    "ppdu_duration_us",
    "ppdu_duration_binary64_bits",
    "tagged_mpdu_bytes",
    "frame_ids",
    "frame_tagged_mpdu_bytes",
    "frame_allocated_airtime_binary64_bits",
    "mixed_ppdu",
    "cumulative_tagged_airtime_us",
)
SECONDARY_AIRTIME_EVENT_V2_COLUMNS = set(
    SECONDARY_AIRTIME_EVENT_V2_ORDERED_COLUMNS
)
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
# WifiMpdu::GetSize() adds the frozen QoS data MAC header to each MAC-service
# packet.  Generalized P frames may have one shorter final packet.
PAIRED_VALUE_T2_WIFI_MAC_HEADER_BYTES = 30
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

PAIRED_VALUE_T2_POLICY = "paired_value_duplication_t2"
PAIRED_VALUE_T2_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAIRED_VALUE_T2_CONTRACT_ID = "paired-value-duplication-t2-runtime-v1"
PAIRED_VALUE_T2_CONTRACT_SHA256 = (
    "b9b9caf6cf49e73cb0669107576a17790f59bda4875c43f676caa426393dbf41"
)
PAIRED_VALUE_T2_CONTRACT_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/paired-value-duplication-t2-runtime-v1.json"
)
PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_ID = (
    "paired-value-duplication-t2-score-aware-emergency-v2"
)
PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_SHA256 = (
    "bdc5b2a944475d1cc31749100e333a2eb2059e106eaf86d918855b721ab3fcda"
)
PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/"
    "paired-value-duplication-t2-score-aware-emergency-v2.json"
)
PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_ID = (
    "paired-value-duplication-t2-full-horizon-carryover-v3"
)
PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_SHA256 = (
    "16ccbbfc19ac5c6b824c65b5f00fd0a8792610ea9239e9277390f51eda83f9d8"
)
PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/"
    "paired-value-duplication-t2-full-horizon-carryover-v3.json"
)
PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_ID = (
    "paired-value-duplication-t2-remaining-refill-borrowing-v4"
)
PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_SHA256 = (
    "0b5d31861c862e1b4fb31231936ecd144958939308b21566e97405a29de0d9dd"
)
PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/"
    "paired-value-duplication-t2-remaining-refill-borrowing-v4.json"
)
PAIRED_VALUE_T2_COST_FREE_CONTRACT_ID = (
    "paired-value-duplication-t2-cost-free-score-aware-v5"
)
PAIRED_VALUE_T2_COST_FREE_CONTRACT_SHA256 = (
    "b7fb00982ae090fe1142b39adf0ad6d26d253741dd5059ed95637dd86047ba96"
)
PAIRED_VALUE_T2_COST_FREE_CONTRACT_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/"
    "paired-value-duplication-t2-cost-free-score-aware-v5.json"
)
PAIRED_VALUE_T2_NEUTRAL_SOURCE_PATH = (
    Path(__file__).resolve().parent / "analyze_primary_tail_t4_campaign.py"
)
PAIRED_VALUE_T2_NEUTRAL_SOURCE_SHA256 = (
    "667c43df8a5f9dc57a22647eef6dc8fcad02d19c7dc187a17227bf5cb1b02d47"
)
PAIRED_VALUE_T2_NEUTRAL_ENVIRONMENT_SHA256 = (
    "5d1774e3b38f27908de3d845953cad825e3f4207d738996952315e63097382dc"
)
PAIRED_VALUE_T2_DUAL_INTERFACE_WIFI_SHA256 = (
    "26698328ca7dde07a6ad283e05f84ad8406de4b1f50d0d8863da500f14140c90"
)
PAIRED_VALUE_T2_SHARED_WIFI_SHA256 = (
    "7e98f6f5877a44565191ac23aa8118f732d36218b1511f6333438b0ee59e8864"
)
PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE = "canonical_v1"
PAIRED_TEMPORAL_T2_GENERALIZATION_FRAME_PROFILE = (
    "environment_generalization_v1"
)
PAIRED_TEMPORAL_T2_GENERALIZATION_ENVIRONMENT = (
    "held_out_environment_generalization_v1"
)
PAIRED_TEMPORAL_T2_GENERALIZATION_CONTRACT_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/environment-generalization-v1.json"
)
PAIRED_TEMPORAL_T2_GENERALIZATION_CONTRACT_SHA256 = (
    "d74f3826e0c624f3dfb91c9acee5934389d67aa6b94b39e493549c4d8ea659aa"
)
PAIRED_TEMPORAL_T2_GENERALIZATION_SCENARIOS_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/environment-generalization-scenarios-v1.json"
)
PAIRED_TEMPORAL_T2_GENERALIZATION_SCENARIOS_SHA256 = (
    "ed7cb32fc3fd7c08ddac296f9c7d7d3c532066305ca50e7c62a0971f9fd6d593"
)
PAIRED_VALUE_T2_MODEL_ARTIFACT_SHA256 = (
    "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8"
)
PAIRED_VALUE_T2_FEATURE_NAMES_SHA256 = (
    "a00ebbb9807f99972f2cd009d1b2a20bf0b001cee123ac60d5121b2b1c07209e"
)
PAIRED_VALUE_T2_SCORE_THRESHOLD_BITS = 0x38BBC0E5
PAIRED_VALUE_T2_SCORE_THRESHOLD = struct.unpack(
    ">f", PAIRED_VALUE_T2_SCORE_THRESHOLD_BITS.to_bytes(4, "big")
)[0]
PAIRED_VALUE_T2_LOG_SMEARING_FACTOR = 0.087899462834571604
PAIRED_VALUE_T2_LOG_COST_CAP = 13.815511557963774
PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US = 1e-9
# This is an operational guard for one solver representation, not part of the
# evidence tolerance.  Each independent component and presolve alternative
# receives the full budget so concurrent validation load cannot consume the
# budget of a later, otherwise feasible representation.
PAIRED_VALUE_T2_MILP_ATTEMPT_TIME_LIMIT_S = 60.0
PAIRED_VALUE_T2_DECISION_START_NS = 1_000_000_000
PAIRED_VALUE_T2_DECISION_STOP_NS = 60_466_000_000
PAIRED_VALUE_T2_MEASUREMENT_STOP_NS = 61_000_000_000
PAIRED_VALUE_T2_GUARD_FRACTION = 0.006
PAIRED_VALUE_T2_GUARD_CAPACITY_US = 60_000.0
PAIRED_VALUE_T2_FULL_HORIZON_GUARD_CAPACITY_US = 360_000.0
PAIRED_VALUE_T2_GUARD_INITIAL_CREDIT_US = 12_000.0
PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD_BITS = 0x391D4952
PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD = struct.unpack(
    ">f", PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD_BITS.to_bytes(4, "big")
)[0]
PAIRED_VALUE_T2_EMERGENCY_MAXIMUM_DEBT_US = 60_000.0
PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD_BITS = 0x3E3F68CF
PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD = struct.unpack(
    ">f", PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD_BITS.to_bytes(4, "big")
)[0]
PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD_BITS = 0x3E9D2AC5
PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD = struct.unpack(
    ">f",
    PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD_BITS.to_bytes(4, "big"),
)[0]

PAIRED_VALUE_T2_DECISION_COLUMNS = (
    "schema_version", "run_id", "frame_id", "policy", "decision_status",
    "primary_path_id", "primary_copy_id", "secondary_path_id", "secondary_copy_id",
    "sample_stage", "sample_offset_us", "generation_time_ns", "deadline_time_ns",
    "primary_sample_time_ns", "secondary_sample_time_ns",
    "primary_feature_watermark_time_ns", "primary_feature_watermark_sequence",
    "frame_type", "frame_size_bytes", "frame_packet_count", "primary_actionable",
    "decision_window_start_ns", "decision_window_stop_ns", "inside_decision_window",
    "history_ready", "current_poll_capture_time_ns", "current_poll_available_time_ns",
    "lag1_frame_id", "lag1_poll_capture_time_ns", "lag3_frame_id",
    "lag3_poll_capture_time_ns", "lag8_frame_id", "lag8_poll_capture_time_ns",
    "feature_evaluated", "model_spec_id", "model_artifact_sha256", "feature_family",
    "feature_count", "feature_adapter_id", "ordered_feature_names_sha256", "ranker",
    "frame_gate", "score_adapter_id", "score_threshold_float32",
    "primary_bad12_logit", "primary_bad12_probability", "treated_bad12_logit",
    "treated_bad12_probability", "predicted_log_airtime",
    "predicted_secondary_airtime_us", "nonnegative_bad12_value",
    "value_per_cost_score_float32", "passes_score_threshold", "descriptor_checked",
    "descriptor_available", "descriptor_frame_packet_count", "descriptor_packet_count",
    "descriptor_packet_indices", "descriptor_expected_mac_service_bytes",
    "descriptor_deadline_time_ns", "canonical_cost_estimator_id", "cost_safety_factor",
    "canonical_nominal_airtime_us", "canonical_reserved_airtime_us", "guard_fraction",
    "guard_max_horizon_us", "guard_initial_horizon_us", "guard_capacity_us",
    "guard_initial_credit_us", "guard_balance_before_us", "meter_reserved_before_us",
    "guard_available_before_us", "guard_debt_before_us", "guard_admission_considered",
    "guard_admitted", "launch_attempted", "secondary_launched",
    "guard_balance_after_us", "meter_reserved_after_us", "guard_available_after_us",
    "guard_debt_after_us", "learned_cost_token_accounting",
)
PAIRED_VALUE_T2_SCORE_AWARE_DECISION_SUFFIX = (
    "admission_profile_id", "strict_guard_admitted",
    "emergency_score_threshold_float32", "passes_emergency_score_threshold",
    "emergency_admission_considered", "emergency_maximum_debt_us",
    "emergency_admitted", "admission_tier",
)
PAIRED_VALUE_T2_REMAINING_REFILL_DECISION_SUFFIX = (
    "remaining_refill_credit_us", "remaining_refill_admission_considered",
    "remaining_refill_admitted",
)
PAIRED_VALUE_T2_COST_FREE_DECISION_SUFFIX = ("policy_score_float32",)
PAIRED_VALUE_T2_STATUSES = (
    "outside_decision_window", "history_warmup", "frame_type_restricted",
    "not_actionable", "descriptor_unavailable", "below_score_threshold",
    "airtime_guard_rejected", "launch_rejected", "action",
)
PAIRED_VALUE_T2_MODEL_METADATA = {
    "policy": PAIRED_VALUE_T2_POLICY,
    "sample_stage": "T2",
    "sample_offset_us": "2000",
    "model_spec_id": "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1",
    "model_artifact_sha256": PAIRED_VALUE_T2_MODEL_ARTIFACT_SHA256,
    "feature_family": "primary_compact_physics_temporal",
    "feature_count": "246",
    "feature_adapter_id": "finite_numeric_float32_then_float64_one_hot_v1",
    "ordered_feature_names_sha256": PAIRED_VALUE_T2_FEATURE_NAMES_SHA256,
    "ranker": "legacy_bad12_value_per_cost",
    "frame_gate": "p_frames_only",
    "score_adapter_id": "final_candidate_float32_threshold_ge_v1",
    "learned_cost_token_accounting": "0",
}
PAIRED_VALUE_T2_COST_FREE_MODEL_METADATA = {
    **PAIRED_VALUE_T2_MODEL_METADATA,
    "ranker": "legacy_bad12_value",
}
PAIRED_VALUE_T2_CONFIG = {
    "csv_schema_version": 1,
    "summary_schema_version": 1,
    "runtime_contract_id": PAIRED_VALUE_T2_CONTRACT_ID,
    "runtime_contract_sha256": PAIRED_VALUE_T2_CONTRACT_SHA256,
    "primary_path": 1,
    "primary_copy_id": 0,
    "secondary_path": 0,
    "secondary_copy_id": 1,
    "stage": "T2",
    "sample_offset_us": 2000,
    "measurement_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
    "measurement_stop_ns": PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
    "decision_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
    "decision_stop_ns": PAIRED_VALUE_T2_DECISION_STOP_NS,
    "decision_stop_guard_us": 534000,
    "delayed_secondary_prediction_tracking_enabled": True,
    "receiver_hold_for_delayed_secondary": True,
    "action": "canonical_full_secondary_copy",
    "cost_estimator_id": RANDOMIZED_COST_ESTIMATOR,
    "cost_safety_factor": 1.25,
    "budget_fraction": PAIRED_VALUE_T2_GUARD_FRACTION,
    "budget_max_horizon_us": 10_000_000,
    "budget_initial_horizon_us": 2_000_000,
}
PAIRED_VALUE_T2_SCORE_AWARE_CONFIG = {
    **PAIRED_VALUE_T2_CONFIG,
    "csv_schema_version": 2,
    "summary_schema_version": 2,
    "runtime_contract_id": PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_ID,
    "runtime_contract_sha256": PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_SHA256,
    "admission_profile_id": "score_aware_emergency_v2",
    # resolved_config.json uses the common 12-significant-digit writer; the
    # decision and controller-summary evidence retain max_digits10.
    "emergency_score_threshold_float32": 0.000150000007125,
    "emergency_score_threshold_float32_bits_hex": "0x391d4952",
    "emergency_maximum_debt_us": 60_000,
}
PAIRED_VALUE_T2_FULL_HORIZON_CONFIG = {
    **PAIRED_VALUE_T2_SCORE_AWARE_CONFIG,
    "runtime_contract_id": PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_ID,
    "runtime_contract_sha256": PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_SHA256,
    "admission_profile_id": "score_aware_full_horizon_v3",
    "budget_max_horizon_us": 60_000_000,
}
PAIRED_VALUE_T2_REMAINING_REFILL_CONFIG = {
    **PAIRED_VALUE_T2_FULL_HORIZON_CONFIG,
    "csv_schema_version": 3,
    "summary_schema_version": 3,
    "runtime_contract_id": PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_ID,
    "runtime_contract_sha256": PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_SHA256,
    "admission_profile_id": "score_aware_remaining_refill_v4",
    "remaining_refill_borrowing_enabled": True,
    "remaining_refill_repayment_stop_ns": PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
}
PAIRED_VALUE_T2_COST_FREE_CONFIG = {
    **PAIRED_VALUE_T2_SCORE_AWARE_CONFIG,
    "csv_schema_version": 4,
    "summary_schema_version": 4,
    "runtime_contract_id": PAIRED_VALUE_T2_COST_FREE_CONTRACT_ID,
    "runtime_contract_sha256": PAIRED_VALUE_T2_COST_FREE_CONTRACT_SHA256,
    "admission_profile_id": "cost_free_score_aware_v5",
    "emergency_score_threshold_float32": 0.306966930628,
    "emergency_score_threshold_float32_bits_hex": "0x3e9d2ac5",
}
PAIRED_VALUE_T2_PREDICTION_CONFIG = {
    "enabled": True,
    "sample_offsets_us": [0, 2000],
    "history_windows_us": [1000, 5000, 20000],
    "polling_interval_us": 1000,
    "polling_report_delay_us": 1000,
    "polling_schema_version": 1,
    "event_log_enabled": False,
    "oracle_features_enabled": False,
    "telemetry_schema_version": 3,
    "event_schema_version": 2,
    "feature_support_mask_version": 2,
}
PAIRED_VALUE_T2_METER_CONFIG_V1 = {
    "enabled": True,
    "path_id": 0,
    "copy_id": 1,
    "definition": "secondary_sender_phy_tx_airtime",
    "measurement_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
    "measurement_stop_ns": PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
}
PAIRED_VALUE_T2_METER_CONFIG = {
    **PAIRED_VALUE_T2_METER_CONFIG_V1,
    "event_schema_version": 2,
}
PAIRED_VALUE_T2_ENVIRONMENT_KEYS = (
    "duration_s", "warmup_s", "measurement_start_s", "measurement_stop_s",
    "stream", "propagation", "background",
)
PAIRED_VALUE_T2_SHARED_WIFI_KEYS = (
    "standard", "station_manager", "data_mode", "control_mode", "guard_interval",
    "channel_settings", "frequency_ranges", "data_modes_per_link", "queue_max_packets",
    "queue_max_delay_ms", "max_ampdu_size_bytes", "max_amsdu_size_bytes",
    "sta_max_inflights", "ul_ofdma_enabled", "ul_ofdma_scope",
    "ul_ofdma_access_interval_ms", "ul_ofdma_bsrp_enabled", "ul_ofdma_max_stations",
    "ul_ofdma_psdu_size_bytes", "block_ack_enabled", "frame_retry_limit",
    "rts_cts_threshold_bytes", "fragmentation_threshold_bytes", "access_category",
    "txop_limit_us", "application_duplication",
)
PAIRED_VALUE_T2_TOPOLOGY_WIFI_KEYS = (
    "static_association", "tid_to_link_mapping_ul", "str_mode", "multi_link_mode",
    "application_socket_count", "emlsr",
)
PAIRED_VALUE_T2_SOURCE_FILES = {
    "tools/build_randomized_intervention_dataset.py": (
        "f365274a0a82a01cf23b390c52401bbaa6eaf5390c2c737a098e2a227c185cea"
    ),
    "tools/build_randomized_temporal_dataset.py": (
        "7ca3bfc117d318ccb311fa66869ebcd247b87e3e473f4cc6e631d3077fd798d3"
    ),
    "tools/train_randomized_value.py": (
        "118efb2c10ea8a0fd4e99f382ee2a9686f9d7f291041ad3f811b58dcdbace911"
    ),
    "tools/train_temporal_t2_value.py": (
        "ffe024b88dd7b70bab34873ac59ba7abb748db5af564be8526fb205ec94ddfa9"
    ),
    "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json": (
        "c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f"
    ),
    "results/randomized_full_copy_exploration_collection_v1/"
    "temporal_t2_primary_only_two_objective_v1/artifact_manifest.json": (
        "b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f"
    ),
    "results/randomized_full_copy_exploration_collection_v1/"
    "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_models.pkl": (
        PAIRED_VALUE_T2_MODEL_ARTIFACT_SHA256
    ),
    "results/randomized_full_copy_exploration_collection_v1/"
    "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_policy_candidates.csv": (
        "7cbd5c622838df0a2f752c3bf9f4c54f333f7d280a9240cb80eda19efb1c28bb"
    ),
    "results/randomized_full_copy_exploration_collection_v1/"
    "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_training_metrics.json": (
        "35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014"
    ),
    "contrib/wifi-streaming/model/canonical-secondary-airtime-estimator.cc": (
        "67d61c6da75e676752fede0af3eafeccc43048597d947b7af3215a33afbfab31"
    ),
    "contrib/wifi-streaming/model/canonical-secondary-airtime-estimator.h": (
        "a40842c0dec03e949b723190049ca158eee73f96ce35a241add88e6be972167b"
    ),
}

_PAIRED_VALUE_T2_MODEL_REPLAY_CONTEXT: dict[str, Any] | None = None

DISTRIBUTIONAL_SHADOW_T2_POLICY = "distributional_shadow_duplication_t2"
DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID = "temporal-t2-shadow-borrow-runtime-v1"
DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256 = (
    "33b16c62848d0b724d347b791650e805c0fe2611eaf44ac4079b93cb59b5f4fa"
)
DISTRIBUTIONAL_SHADOW_T2_CONTRACT_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/temporal-t2-shadow-borrow-runtime-v1.json"
)
DISTRIBUTIONAL_SHADOW_T2_MODEL_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/"
    "temporal-t2-shadow-borrow-runtime-model-v1.json.xz"
)
DISTRIBUTIONAL_SHADOW_T2_REFERENCE_PATH = (
    PAIRED_VALUE_T2_REPOSITORY_ROOT
    / "experiments/model-selection/"
    "temporal-t2-shadow-borrow-runtime-reference-v1.json.xz"
)
DISTRIBUTIONAL_SHADOW_T2_MODEL_XZ_SHA256 = (
    "03e9e36f6dbec6457a25768571cb71a4dd860e737c406a92ddf2de00024a08a6"
)
DISTRIBUTIONAL_SHADOW_T2_REFERENCE_XZ_SHA256 = (
    "f73ca45c059653448d1006f4250ec114538aaa645482a11140849025436b5502"
)
DISTRIBUTIONAL_SHADOW_T2_SOURCE_MODEL_SHA256 = (
    "e9d5f0ebc822f8956ef3ed06fc8cb1d776961d0fba5ebeebcdd1e181b5be0071"
)
DISTRIBUTIONAL_SHADOW_T2_SOURCE_REFERENCE_SHA256 = (
    "a4d2ac57e35e79bb173d09e1ca6f0237e06438c001c1685a6d4af9ff13f44acf"
)
DISTRIBUTIONAL_SHADOW_T2_PORTABLE_MODEL_SHA256 = (
    "8023d41495cd93df78a68fdad45f2dc588369ba23102ae038a400e8c3d5d5aac"
)
DISTRIBUTIONAL_SHADOW_T2_DEPLOYMENT_REFERENCE_SHA256 = (
    "493c0624082a7cb363bcb7bfe3af0930cb6a2d84e09ce17e1d7eef8dd7d7f316"
)
DISTRIBUTIONAL_SHADOW_T2_FEATURE_CONTRACT_SHA256 = (
    "1f8dce2aad4c21d5cdf66a25a87d7bdeaf8eb3befbc6fbc1922062b77c5d9d96"
)
DISTRIBUTIONAL_SHADOW_T2_EXPORTER_SHA256 = (
    "1a0e9a89ca0edad2f12c1bccb383e246de8e1b8c1f578b8d47283d4b38e18a21"
)
DISTRIBUTIONAL_SHADOW_T2_FEATURE_ADAPTER = (
    "parse or derive in the frozen order, quantize each finite numeric or missing "
    "NaN to binary32, then widen exactly to binary64"
)
DISTRIBUTIONAL_SHADOW_T2_CANONICAL_RESERVATION_US = 1983.760667318285
DISTRIBUTIONAL_SHADOW_T2_POSITIVE_BALANCE_CAPACITY_US = 360_000.0
DISTRIBUTIONAL_SHADOW_T2_INITIAL_CREDIT_US = 12_000.0
DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_WIDTH_US = 5_000_000
DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT = 12
DISTRIBUTIONAL_SHADOW_T2_REGIME_COUNT = 3
DISTRIBUTIONAL_SHADOW_T2_STATUSES = (
    "outside_decision_window",
    "history_warmup",
    "not_actionable",
    "frame_type_restricted",
    "descriptor_unavailable",
    "nonpositive_reward",
    "opportunity_price_rejected",
    "horizon_credit_rejected",
    "launch_rejected",
    "action",
)
DISTRIBUTIONAL_SHADOW_T2_DECISION_COLUMNS = (
    "schema_version", "run_id", "frame_id", "policy", "decision_status",
    "primary_path_id", "primary_copy_id", "secondary_path_id", "secondary_copy_id",
    "sample_stage", "sample_offset_us", "generation_time_ns", "deadline_time_ns",
    "primary_sample_time_ns", "secondary_sample_time_ns",
    "primary_feature_watermark_time_ns", "primary_feature_watermark_sequence",
    "secondary_feature_watermark_time_ns", "secondary_feature_watermark_sequence",
    "frame_type", "frame_size_bytes", "frame_packet_count", "primary_actionable",
    "decision_window_start_ns", "decision_window_stop_ns", "inside_decision_window",
    "history_ready", "primary_current_poll_capture_time_ns",
    "primary_current_poll_available_time_ns", "secondary_current_poll_capture_time_ns",
    "secondary_current_poll_available_time_ns", "primary_lag1_frame_id",
    "primary_lag1_poll_capture_time_ns", "primary_lag3_frame_id",
    "primary_lag3_poll_capture_time_ns", "primary_lag8_frame_id",
    "primary_lag8_poll_capture_time_ns", "secondary_lag1_frame_id",
    "secondary_lag1_poll_capture_time_ns", "secondary_lag3_frame_id",
    "secondary_lag3_poll_capture_time_ns", "secondary_lag8_frame_id",
    "secondary_lag8_poll_capture_time_ns", "congestion_updated",
    "current_primary_busy20ms", "running_primary_busy20ms",
    "congestion_observation_count", "time_bin", "congestion_regime",
    "feature_evaluated", "model_spec_id", "selected_variant", "feature_family",
    "feature_count", "feature_adapter_id", "runtime_contract_id",
    "runtime_contract_sha256", "control_logits", "control_probabilities", "control_cdf",
    "full_copy_logits", "full_copy_probabilities", "full_copy_cdf",
    "deadline_rescue_reward", "tail18_cdf_gain", "reward_density_per_us",
    "opportunity_cost_per_us", "passes_opportunity_price", "earlier_unsettled_launches",
    "secondary_state_action_dirty", "descriptor_checked", "descriptor_available",
    "descriptor_frame_packet_count", "descriptor_packet_count",
    "descriptor_packet_indices", "descriptor_expected_mac_service_bytes",
    "descriptor_deadline_time_ns", "canonical_cost_estimator_id", "cost_safety_factor",
    "canonical_nominal_airtime_us", "canonical_reserved_airtime_us",
    "credit_accounting_id", "budget_fraction", "positive_balance_capacity_us",
    "initial_credit_us", "repayment_stop_ns", "ledger_balance_before_us",
    "ledger_debt_before_us", "ledger_remaining_refill_before_us",
    "ledger_repayable_before_us", "ledger_debited_before_us",
    "horizon_admission_considered", "horizon_admitted", "launch_attempted",
    "secondary_launched", "ledger_balance_after_us", "ledger_debt_after_us",
    "ledger_debited_after_us", "meter_reserved_before_us", "meter_reserved_after_us",
    "measured_settlement_refunds_ledger",
)
DISTRIBUTIONAL_SHADOW_T2_CONFIG = {
    "csv_schema_version": 1,
    "summary_schema_version": 1,
    "runtime_contract_id": DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID,
    "runtime_contract_sha256": DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256,
    "primary_path": 1,
    "primary_copy_id": 0,
    "secondary_path": 0,
    "secondary_copy_id": 1,
    "stage": "T2",
    "sample_offset_us": 2000,
    "measurement_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
    "measurement_stop_ns": PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
    "decision_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
    "decision_stop_ns": PAIRED_VALUE_T2_DECISION_STOP_NS,
    "decision_stop_guard_us": 534000,
    "delayed_secondary_prediction_tracking_enabled": True,
    "receiver_hold_for_delayed_secondary": True,
    "action": "canonical_full_secondary_copy",
    "predictor_variant": "primary_secondary_hgb64",
    "predictor_model_spec_id": "hgb64_depth3_7leaf_multiclass_v1",
    "predictor_feature_count": 308,
    "deadline_reward_and_tail_gain_are_separate": True,
    "shadow_reference": "congestion_tertile_5s",
    "cost_estimator_id": RANDOMIZED_COST_ESTIMATOR,
    "cost_safety_factor": 1.25,
    "canonical_p_frame_reservation_us": 1983.76066732,
    "budget_fraction": PAIRED_VALUE_T2_GUARD_FRACTION,
    "positive_balance_capacity_us": 360000,
    "initial_credit_us": 12000,
    "negative_balance_allowed_when_repayable": True,
    "accepted_reservation_is_permanent": True,
    "measured_settlement_refunds_ledger": False,
}
DISTRIBUTIONAL_SHADOW_T2_SOURCE_FILES = {
    "tools/export_temporal_t2_shadow_runtime_v1.py":
        DISTRIBUTIONAL_SHADOW_T2_EXPORTER_SHA256,
    "contrib/wifi-streaming/model/temporal-t2-distribution-model-data-v1.cc":
        "b4b375b825edb789a4addf4a7e617764d8c3881a7ca91e048ed3974579f821d2",
    "contrib/wifi-streaming/model/temporal-t2-distribution-model-data-v1.h":
        "3099f3d72419706936b9012bec7d24294bd3912e5787a5cc10301c0b631941fd",
    "contrib/wifi-streaming/model/temporal-t2-distribution-model-evaluator.cc":
        "6a844b62fc4813208b4efc9a9ca1ea069cde1dc8f0fc0033283a4358647eeaa7",
    "contrib/wifi-streaming/model/temporal-t2-distribution-model-evaluator.h":
        "c11fe01089073629f194d2e7ed18ed683d0a7ffdb00553e4f651effa16a3711e",
    "contrib/wifi-streaming/model/temporal-t2-distribution-predictor.cc":
        "5ab8e06ddf63c0e929ef04dd1a2b49b7feda39395a93686019e900efa0cfafa9",
    "contrib/wifi-streaming/model/temporal-t2-distribution-predictor.h":
        "be0ee6462fd9f44ccf56cabfedbbc7d457839aeb37f2ecfc2189bf05718c1552",
    "contrib/wifi-streaming/model/distributional-shadow-t2-controller.cc":
        "67023428d7941d03fa4370f375bb7c3f99dfb911181a7db6b9c4c358b8c14a72",
    "contrib/wifi-streaming/model/distributional-shadow-t2-controller.h":
        "c20dd68094f22519c8d03b49d4b8506baab761b8d2ce462bd74f8e621bf38b8b",
    "contrib/wifi-streaming/model/permanent-airtime-credit-ledger.cc":
        "fbbd9f5d13f5b99ab03ccfae9a685b3f70e2aea59e71ffdd36fd6a33c9a525e9",
    "contrib/wifi-streaming/model/permanent-airtime-credit-ledger.h":
        "f79e7c888c825f9f9d1c22ede142c8ac523e97bf32ca309234dee61a2b870727",
    "contrib/wifi-streaming/model/canonical-secondary-airtime-estimator.cc":
        "67d61c6da75e676752fede0af3eafeccc43048597d947b7af3215a33afbfab31",
    "contrib/wifi-streaming/model/canonical-secondary-airtime-estimator.h":
        "a40842c0dec03e949b723190049ca158eee73f96ce35a241add88e6be972167b",
}

_DISTRIBUTIONAL_SHADOW_T2_MODEL_REPLAY_CONTEXT: dict[str, Any] | None = None


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


def _csv(
    path: Path,
    required: set[str],
    *,
    ordered_columns: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """Read a CSV with required columns and an optional exact ordered schema."""
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            _require(reader.fieldnames is not None, f"{path.name}: missing header")
            if ordered_columns is None:
                _require(required <= set(reader.fieldnames), f"{path.name}: missing columns")
            else:
                _require(reader.fieldnames == list(ordered_columns),
                         f"{path.name}: header differs from frozen ordered schema")
            rows = list(reader)
    except OSError as error:
        raise ValidationError(f"{path.name}: cannot read: {error}") from error
    if ordered_columns is None:
        _require(all(None not in row for row in rows),
                 f"{path.name}: row has more values than the header")
    else:
        _require(all(set(row) == set(ordered_columns) and
                     None not in row and
                     all(value is not None for value in row.values()) for row in rows),
                 f"{path.name}: row width differs from frozen schema")
    return rows


def _sha256_file(path: Path, name: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValidationError(f"{name}: cannot verify source closure: {error}") from error


def _canonical_json_sha256(value: Any, name: str) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{name}: cannot canonicalize: {error}") from error
    return hashlib.sha256(encoded).hexdigest()


def _float32(value: float, name: str) -> float:
    _require(math.isfinite(value), f"{name}: non-finite float32 value")
    try:
        result = struct.unpack(">f", struct.pack(">f", value))[0]
    except OverflowError as error:
        raise ValidationError(f"{name}: float32 overflow") from error
    _require(math.isfinite(result), f"{name}: float32 overflow")
    return result


def _paired_close(actual: float, expected: float) -> bool:
    return (
        math.isfinite(actual)
        and math.isfinite(expected)
        and abs(actual - expected) <= PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
    )


def _paired_meter_quantization_us(text: str) -> float:
    """Return half of one 12-significant-digit meter CSV unit."""
    value = float(text)
    if value == 0:
        return 0.0
    exponent = math.floor(math.log10(abs(value))) - 11
    return 0.5 * (10.0 ** exponent)


def _paired_meter_close(actual: float, expected: float) -> bool:
    """Compare a 12-significant-digit meter value to an exact reconstruction."""
    return (
        math.isfinite(actual)
        and math.isfinite(expected)
        and actual == float(format(expected, ".12g"))
    )


def _paired_meter_sum_close(actual: float, serialized_values: list[str]) -> bool:
    """Compare an exact total with the bounded sum of 12-digit meter rows."""
    expected = sum(float(value) for value in serialized_values)
    tolerance = (
        PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
        + sum(_paired_meter_quantization_us(value) for value in serialized_values)
    )
    return math.isfinite(actual) and abs(actual - expected) <= tolerance


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


def _index_list(
    row: dict[str, str], key: str, file_name: str, *, upper_bound: int | None = None
) -> list[int]:
    """Parse one canonical, semicolon-delimited packet-index list."""
    text = row.get(key, "")
    tokens = [] if text == "" else text.split(";")
    _require(
        all(re.fullmatch(r"[0-9]+", token) for token in tokens),
        f"{file_name}: malformed {key}",
    )
    values = [int(token) for token in tokens]
    _require(
        values == sorted(set(values)),
        f"{file_name}: {key} is not a unique ascending index set",
    )
    if upper_bound is not None:
        _require(
            all(value < upper_bound for value in values),
            f"{file_name}: {key} contains an out-of-range index",
        )
    return values


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


def _canonical_full_copy_descriptor(
    frame: dict[str, str],
) -> tuple[int, str, int, float]:
    """Return exact packet, service-byte, and nominal whole-copy evidence."""
    frame_size = _integer(frame, "frame_size_bytes", "frames.csv")
    packet_count = _integer(frame, "packet_count", "frames.csv")
    packet_indices = ";".join(str(index) for index in range(packet_count))
    expected_service_bytes = frame_size + packet_count * (
        ADAPTIVE_ESTIMATOR_STREAMING_HEADER_BYTES
        + ADAPTIVE_ESTIMATOR_MAC_SERVICE_OVERHEAD_BYTES
    )
    nominal_airtime_us = _adaptive_nominal_airtime_us(
        frame_size, packet_count, 1.0
    )
    return packet_count, packet_indices, expected_service_bytes, nominal_airtime_us


def _canonical_full_copy_mpdu_profile(
    frame: dict[str, str],
) -> tuple[int, int, int]:
    """Return full-MPDU bytes, final-MPDU bytes, and exact packet count."""
    frame_size = _integer(frame, "frame_size_bytes", "frames.csv")
    packet_count = _integer(frame, "packet_count", "frames.csv")
    payload_bytes = PRIMARY_T0_ESTIMATOR_PAYLOAD_BYTES
    _require(
        packet_count == 1 + (frame_size - 1) // payload_bytes,
        "frames.csv: canonical full-copy packet count differs",
    )
    final_payload_bytes = frame_size - payload_bytes * (packet_count - 1)
    _require(
        0 < final_payload_bytes <= payload_bytes,
        "frames.csv: canonical full-copy final payload is invalid",
    )
    per_packet_overhead = (
        ADAPTIVE_ESTIMATOR_STREAMING_HEADER_BYTES
        + ADAPTIVE_ESTIMATOR_MAC_SERVICE_OVERHEAD_BYTES
        + PAIRED_VALUE_T2_WIFI_MAC_HEADER_BYTES
    )
    return (
        payload_bytes + per_packet_overhead,
        final_payload_bytes + per_packet_overhead,
        packet_count,
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
        frame_packets, expected_indices, expected_service, expected_nominal = (
            _canonical_full_copy_descriptor(frame)
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


def _validate_paired_value_t2_source_files() -> None:
    """Verify every repository file used by independent paired-value replay."""
    repository_root = PAIRED_VALUE_T2_REPOSITORY_ROOT.resolve()
    for relative_path, expected_sha256 in PAIRED_VALUE_T2_SOURCE_FILES.items():
        source_path = PAIRED_VALUE_T2_REPOSITORY_ROOT / relative_path
        _require(source_path.is_file() and not source_path.is_symlink() and
                 source_path.resolve().is_relative_to(repository_root),
                 f"paired-value runtime contract: invalid source path {relative_path}")
        _require(_sha256_file(source_path, relative_path) == expected_sha256,
                 f"paired-value runtime contract: source drifted {relative_path}")


def _validate_paired_temporal_t2_environment(
    config: dict[str, Any], label: str
) -> None:
    """Validate the canonical or held-out temporal-T2 environment envelope."""

    frame_profile = config.get(
        "pairedTemporalT2FrameProfile",
        PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE,
    )
    _require(
        frame_profile
        in {
            PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE,
            PAIRED_TEMPORAL_T2_GENERALIZATION_FRAME_PROFILE,
        },
        f"resolved_config.json: {label} frame profile is unsupported",
    )
    missing = [key for key in PAIRED_VALUE_T2_ENVIRONMENT_KEYS if key not in config]
    _require(
        not missing,
        f"resolved_config.json: {label} environment fields are missing",
    )
    wifi = config.get("wifi")
    _require(
        isinstance(wifi, dict),
        f"resolved_config.json: {label} Wi-Fi config is missing",
    )
    shared_wifi = {
        key: wifi.get(key, "__MISSING__") for key in PAIRED_VALUE_T2_SHARED_WIFI_KEYS
    }
    _require(
        _canonical_json_sha256(shared_wifi, f"{label} shared target Wi-Fi")
        == PAIRED_VALUE_T2_SHARED_WIFI_SHA256,
        f"resolved_config.json: {label} shared target Wi-Fi differs",
    )

    if frame_profile == PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE:
        _require(
            config.get("environment") == "unchanged_neutral_mixed4x4",
            f"resolved_config.json: {label} environment identity mismatch",
        )
        _require(
            _sha256_file(PAIRED_VALUE_T2_NEUTRAL_SOURCE_PATH, "neutral environment source")
            == PAIRED_VALUE_T2_NEUTRAL_SOURCE_SHA256,
            f"{label} runtime: neutral environment source drifted",
        )
        environment = {
            **{key: config[key] for key in PAIRED_VALUE_T2_ENVIRONMENT_KEYS},
            "shared_target_wifi": shared_wifi,
        }
        environment = copy.deepcopy(environment)
        obss = environment.get("background", {}).get("obss")
        if isinstance(obss, dict):
            obss.pop("bsses", None)
        _require(
            _canonical_json_sha256(environment, f"{label} neutral environment")
            == PAIRED_VALUE_T2_NEUTRAL_ENVIRONMENT_SHA256,
            f"resolved_config.json: {label} neutral environment projection differs",
        )
        return

    _require(
        config.get("environment") == PAIRED_TEMPORAL_T2_GENERALIZATION_ENVIRONMENT,
        f"resolved_config.json: {label} generalization environment identity mismatch",
    )
    for path, expected, source_label in (
        (
            PAIRED_TEMPORAL_T2_GENERALIZATION_CONTRACT_PATH,
            PAIRED_TEMPORAL_T2_GENERALIZATION_CONTRACT_SHA256,
            "environment-generalization contract",
        ),
        (
            PAIRED_TEMPORAL_T2_GENERALIZATION_SCENARIOS_PATH,
            PAIRED_TEMPORAL_T2_GENERALIZATION_SCENARIOS_SHA256,
            "environment-generalization scenario catalog",
        ),
    ):
        _require(
            _sha256_file(path, source_label) == expected,
            f"{label} runtime: {source_label} drifted",
        )
    _require(
        config.get("duration_s") == 60
        and config.get("warmup_s") == 1
        and config.get("measurement_start_s") == 1
        and config.get("measurement_stop_s") == 61,
        f"resolved_config.json: {label} generalization measurement window differs",
    )
    stream = config.get("stream")
    _require(
        isinstance(stream, dict),
        f"resolved_config.json: {label} generalization stream is missing",
    )
    fps = stream.get("fps") if isinstance(stream, dict) else None
    deadline_by_fps = {24: 41667, 30: 33333, 45: 22222, 60: 16667}
    frame_size = stream.get("frame_size_bytes") if isinstance(stream, dict) else None
    gop_length = stream.get("gop_length") if isinstance(stream, dict) else None
    keyframe_multiplier = (
        stream.get("keyframe_size_multiplier") if isinstance(stream, dict) else None
    )
    _require(
        stream.get("source") == "synthetic"
        and stream.get("trace_file") == ""
        and stream.get("payload_size_bytes") == 1200
        and stream.get("emission_mode") == "burst"
        and isinstance(fps, (int, float))
        and not isinstance(fps, bool)
        and float(fps).is_integer()
        and int(fps) in deadline_by_fps
        and stream.get("deadline_us") == deadline_by_fps[int(fps)]
        and isinstance(frame_size, int)
        and not isinstance(frame_size, bool)
        and 6000 <= frame_size <= 14000
        and frame_size % 100 == 0
        and isinstance(gop_length, int)
        and not isinstance(gop_length, bool)
        and gop_length in {30, 60, 90, 120}
        and isinstance(keyframe_multiplier, (int, float))
        and not isinstance(keyframe_multiplier, bool)
        and math.isfinite(float(keyframe_multiplier))
        and 2.0 <= float(keyframe_multiplier) <= 4.0,
        f"resolved_config.json: {label} generalization stream is outside the frozen domain",
    )
    _require(
        isinstance(config.get("propagation"), dict)
        and isinstance(config.get("background"), dict),
        f"resolved_config.json: {label} generalization environment is incomplete",
    )


def _validate_paired_value_t2_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the frozen resolved configuration and source closure."""
    paired = config.get("pairedValueDuplicationT2")
    _require(isinstance(paired, dict),
             "resolved_config.json: pairedValueDuplicationT2 is missing")
    runtime_id = paired.get("runtime_contract_id")
    if runtime_id == PAIRED_VALUE_T2_CONTRACT_ID:
        profile = {
            "score_aware": False,
            "remaining_refill": False,
            "cost_free": False,
            "decision_schema_version": 1,
            "summary_schema_version": 1,
            "runtime_contract_id": PAIRED_VALUE_T2_CONTRACT_ID,
            "runtime_contract_sha256": PAIRED_VALUE_T2_CONTRACT_SHA256,
            "runtime_contract_path": PAIRED_VALUE_T2_CONTRACT_PATH,
            "resolved_config": PAIRED_VALUE_T2_CONFIG,
            "decision_columns": PAIRED_VALUE_T2_DECISION_COLUMNS,
            "admission_profile_id": None,
            "guard_max_horizon_us": 10_000_000,
            "guard_capacity_us": int(PAIRED_VALUE_T2_GUARD_CAPACITY_US),
            "model_metadata": PAIRED_VALUE_T2_MODEL_METADATA,
            "score_threshold": PAIRED_VALUE_T2_SCORE_THRESHOLD,
            "score_threshold_bits": PAIRED_VALUE_T2_SCORE_THRESHOLD_BITS,
            "emergency_score_threshold":
                PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD,
            "emergency_score_threshold_bits":
                PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD_BITS,
        }
    elif runtime_id == PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_ID:
        profile = {
            "score_aware": True,
            "remaining_refill": False,
            "decision_schema_version": 2,
            "summary_schema_version": 2,
            "runtime_contract_id": PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_ID,
            "runtime_contract_sha256": PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_SHA256,
            "runtime_contract_path": PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_PATH,
            "resolved_config": PAIRED_VALUE_T2_SCORE_AWARE_CONFIG,
            "decision_columns": (
                PAIRED_VALUE_T2_DECISION_COLUMNS
                + PAIRED_VALUE_T2_SCORE_AWARE_DECISION_SUFFIX
            ),
            "admission_profile_id": "score_aware_emergency_v2",
            "guard_max_horizon_us": 10_000_000,
            "guard_capacity_us": int(PAIRED_VALUE_T2_GUARD_CAPACITY_US),
        }
    elif runtime_id == PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_ID:
        profile = {
            "score_aware": True,
            "remaining_refill": False,
            "decision_schema_version": 2,
            "summary_schema_version": 2,
            "runtime_contract_id": PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_ID,
            "runtime_contract_sha256": PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_SHA256,
            "runtime_contract_path": PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_PATH,
            "resolved_config": PAIRED_VALUE_T2_FULL_HORIZON_CONFIG,
            "decision_columns": (
                PAIRED_VALUE_T2_DECISION_COLUMNS
                + PAIRED_VALUE_T2_SCORE_AWARE_DECISION_SUFFIX
            ),
            "admission_profile_id": "score_aware_full_horizon_v3",
            "guard_max_horizon_us": 60_000_000,
            "guard_capacity_us": int(
                PAIRED_VALUE_T2_FULL_HORIZON_GUARD_CAPACITY_US
            ),
        }
    elif runtime_id == PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_ID:
        profile = {
            "score_aware": True,
            "remaining_refill": True,
            "decision_schema_version": 3,
            "summary_schema_version": 3,
            "runtime_contract_id": PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_ID,
            "runtime_contract_sha256":
                PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_SHA256,
            "runtime_contract_path": PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_PATH,
            "resolved_config": PAIRED_VALUE_T2_REMAINING_REFILL_CONFIG,
            "decision_columns": (
                PAIRED_VALUE_T2_DECISION_COLUMNS
                + PAIRED_VALUE_T2_SCORE_AWARE_DECISION_SUFFIX
                + PAIRED_VALUE_T2_REMAINING_REFILL_DECISION_SUFFIX
            ),
            "admission_profile_id": "score_aware_remaining_refill_v4",
            "guard_max_horizon_us": 60_000_000,
            "guard_capacity_us": int(
                PAIRED_VALUE_T2_FULL_HORIZON_GUARD_CAPACITY_US
            ),
        }
    elif runtime_id == PAIRED_VALUE_T2_COST_FREE_CONTRACT_ID:
        profile = {
            "score_aware": True,
            "remaining_refill": False,
            "cost_free": True,
            "decision_schema_version": 4,
            "summary_schema_version": 4,
            "runtime_contract_id": PAIRED_VALUE_T2_COST_FREE_CONTRACT_ID,
            "runtime_contract_sha256": PAIRED_VALUE_T2_COST_FREE_CONTRACT_SHA256,
            "runtime_contract_path": PAIRED_VALUE_T2_COST_FREE_CONTRACT_PATH,
            "resolved_config": PAIRED_VALUE_T2_COST_FREE_CONFIG,
            "decision_columns": (
                PAIRED_VALUE_T2_DECISION_COLUMNS
                + PAIRED_VALUE_T2_SCORE_AWARE_DECISION_SUFFIX
                + PAIRED_VALUE_T2_COST_FREE_DECISION_SUFFIX
            ),
            "admission_profile_id": "cost_free_score_aware_v5",
            "guard_max_horizon_us": 10_000_000,
            "guard_capacity_us": int(PAIRED_VALUE_T2_GUARD_CAPACITY_US),
        }
    else:
        raise ValidationError(
            "resolved_config.json: unsupported paired-value runtime contract"
        )
    profile.setdefault("cost_free", False)
    profile["model_metadata"] = (
        PAIRED_VALUE_T2_COST_FREE_MODEL_METADATA
        if profile["cost_free"]
        else PAIRED_VALUE_T2_MODEL_METADATA
    )
    profile["score_threshold"] = (
        PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD
        if profile["cost_free"]
        else PAIRED_VALUE_T2_SCORE_THRESHOLD
    )
    profile["score_threshold_bits"] = (
        PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD_BITS
        if profile["cost_free"]
        else PAIRED_VALUE_T2_SCORE_THRESHOLD_BITS
    )
    profile["emergency_score_threshold"] = (
        PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD
        if profile["cost_free"]
        else PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD
    )
    profile["emergency_score_threshold_bits"] = (
        PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD_BITS
        if profile["cost_free"]
        else PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD_BITS
    )
    _require(
        _sha256_file(profile["runtime_contract_path"], "paired-value runtime contract")
        == profile["runtime_contract_sha256"],
        "paired-value runtime contract: committed bytes differ from frozen SHA-256",
    )
    _validate_paired_value_t2_source_files()
    _require(config.get("topology") == "dual_interface" and
             config.get("policy") == PAIRED_VALUE_T2_POLICY,
             "resolved_config.json: paired-value policy/topology mismatch")
    _validate_paired_temporal_t2_environment(config, "paired-value")

    _require(isinstance(paired, dict) and
             _canonical_json_sha256(paired, "pairedValueDuplicationT2") ==
             _canonical_json_sha256(profile["resolved_config"],
                                    "frozen paired config"),
             "resolved_config.json: pairedValueDuplicationT2 differs from contract")
    if profile["score_aware"]:
        configured_emergency_threshold = paired.get(
            "emergency_score_threshold_float32"
        )
        _require(
            isinstance(configured_emergency_threshold, (int, float))
            and not isinstance(configured_emergency_threshold, bool)
            and struct.pack(
                ">f",
                _float32(
                    float(configured_emergency_threshold),
                    "paired-value resolved emergency threshold",
                ),
            ) == profile["emergency_score_threshold_bits"].to_bytes(4, "big"),
            "resolved_config.json: emergency float32 threshold differs",
        )
    prediction = config.get("predictionTelemetry")
    _require(isinstance(prediction, dict) and
             _canonical_json_sha256(prediction, "predictionTelemetry") ==
             _canonical_json_sha256(PAIRED_VALUE_T2_PREDICTION_CONFIG,
                                    "frozen prediction config"),
             "resolved_config.json: paired-value predictionTelemetry differs from contract")
    meter = config.get("secondaryAirtimeMeter")
    expected_meter = (
        PAIRED_VALUE_T2_METER_CONFIG
        if isinstance(meter, dict) and meter.get("event_schema_version") == 2
        else PAIRED_VALUE_T2_METER_CONFIG_V1
    )
    _require(isinstance(meter, dict) and
             _canonical_json_sha256(meter, "secondaryAirtimeMeter") ==
             _canonical_json_sha256(expected_meter, "frozen meter config"),
             "resolved_config.json: paired-value secondaryAirtimeMeter differs from contract")
    _require(all(key not in config for key in (
        "selectiveDuplication", "adaptiveAirtimeDuplication",
        "adaptiveDeficitDuplication", "randomizedIntervention",
    )), "resolved_config.json: another controller object exists for paired-value policy")

    wifi = config.get("wifi")
    _require(isinstance(wifi, dict), "resolved_config.json: paired-value wifi is missing")
    topology_wifi = {
        key: wifi.get(key, "__MISSING__")
        for key in PAIRED_VALUE_T2_TOPOLOGY_WIFI_KEYS
    }
    _require(
        _canonical_json_sha256(topology_wifi, "paired-value topology wifi")
        == PAIRED_VALUE_T2_DUAL_INTERFACE_WIFI_SHA256,
        "resolved_config.json: paired-value topology Wi-Fi projection differs",
    )
    return profile


def _validate_distributional_shadow_t2_source_files() -> None:
    """Verify the source closure of the compiled distributional runtime."""
    repository_root = PAIRED_VALUE_T2_REPOSITORY_ROOT.resolve()
    for relative_path, expected_sha256 in (
        DISTRIBUTIONAL_SHADOW_T2_SOURCE_FILES.items()
    ):
        source_path = PAIRED_VALUE_T2_REPOSITORY_ROOT / relative_path
        _require(
            source_path.is_file()
            and not source_path.is_symlink()
            and source_path.resolve().is_relative_to(repository_root),
            f"distributional-shadow runtime: invalid source path {relative_path}",
        )
        _require(
            _sha256_file(source_path, relative_path) == expected_sha256,
            f"distributional-shadow runtime: source drifted {relative_path}",
        )


def _validate_distributional_shadow_t2_config(config: dict[str, Any]) -> None:
    """Validate the frozen controller, telemetry, and neutral environment."""
    _require(
        _sha256_file(
            DISTRIBUTIONAL_SHADOW_T2_CONTRACT_PATH,
            "distributional-shadow runtime contract",
        )
        == DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256,
        "distributional-shadow runtime contract: committed bytes differ",
    )
    _validate_distributional_shadow_t2_source_files()
    controller = config.get("distributionalShadowDuplicationT2")
    _require(
        config.get("topology") == "dual_interface"
        and config.get("policy") == DISTRIBUTIONAL_SHADOW_T2_POLICY
        and config.get("environment")
        in {
            "unchanged_neutral_mixed4x4",
            PAIRED_TEMPORAL_T2_GENERALIZATION_ENVIRONMENT,
        },
        "resolved_config.json: distributional-shadow identity differs",
    )
    _validate_paired_temporal_t2_environment(config, "distributional-shadow")
    _require(
        isinstance(controller, dict)
        and _canonical_json_sha256(controller, "distributionalShadowDuplicationT2")
        == _canonical_json_sha256(
            DISTRIBUTIONAL_SHADOW_T2_CONFIG,
            "frozen distributional-shadow config",
        ),
        "resolved_config.json: distributionalShadowDuplicationT2 differs",
    )
    prediction = config.get("predictionTelemetry")
    meter = config.get("secondaryAirtimeMeter")
    expected_meter = (
        PAIRED_VALUE_T2_METER_CONFIG
        if isinstance(meter, dict) and meter.get("event_schema_version") == 2
        else PAIRED_VALUE_T2_METER_CONFIG_V1
    )
    _require(
        isinstance(prediction, dict)
        and _canonical_json_sha256(prediction, "predictionTelemetry")
        == _canonical_json_sha256(
            PAIRED_VALUE_T2_PREDICTION_CONFIG,
            "frozen distributional prediction config",
        ),
        "resolved_config.json: distributional predictionTelemetry differs",
    )
    _require(
        isinstance(meter, dict)
        and _canonical_json_sha256(meter, "secondaryAirtimeMeter")
        == _canonical_json_sha256(
            expected_meter,
            "frozen distributional meter config",
        ),
        "resolved_config.json: distributional secondaryAirtimeMeter differs",
    )
    _require(
        all(
            key not in config
            for key in (
                "pairedValueDuplicationT2",
                "selectiveDuplication",
                "adaptiveAirtimeDuplication",
                "adaptiveDeficitDuplication",
                "randomizedIntervention",
            )
        ),
        "resolved_config.json: another controller object exists for "
        "distributional-shadow policy",
    )

    wifi = config.get("wifi")
    _require(
        isinstance(wifi, dict),
        "resolved_config.json: distributional neutral Wi-Fi config is missing",
    )
    topology_wifi = {
        key: wifi.get(key, "__MISSING__")
        for key in PAIRED_VALUE_T2_TOPOLOGY_WIFI_KEYS
    }
    _require(
        _canonical_json_sha256(topology_wifi, "distributional topology Wi-Fi")
        == PAIRED_VALUE_T2_DUAL_INTERFACE_WIFI_SHA256,
        "resolved_config.json: distributional topology Wi-Fi differs",
    )


def _distributional_shadow_t2_xz_json(
    path: Path,
    compressed_sha256: str,
    decompressed_sha256: str,
    label: str,
) -> dict[str, Any]:
    """Load one hash-pinned compressed portable replay artifact."""
    _require(
        path.is_file()
        and not path.is_symlink()
        and _sha256_file(path, label) == compressed_sha256,
        f"distributional-shadow replay: compressed {label} differs",
    )
    try:
        with lzma.open(path, "rb") as source:
            encoded = source.read()
    except (OSError, lzma.LZMAError) as error:
        raise ValidationError(
            f"distributional-shadow replay: cannot decompress {label}: {error}"
        ) from error
    _require(
        hashlib.sha256(encoded).hexdigest() == decompressed_sha256,
        f"distributional-shadow replay: decompressed {label} differs",
    )
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError(
            f"distributional-shadow replay: invalid {label}: {error}"
        ) from error
    _require(
        isinstance(value, dict),
        f"distributional-shadow replay: {label} is not an object",
    )
    return value


def _distributional_shadow_t2_secondary_feature_names() -> tuple[str, ...]:
    names = ["x_secondary_mac_queue_packets", "x_secondary_mac_queue_service_bytes"]
    for lag in (None, 1, 3, 8):
        prefix = "x_secondary" if lag is None else f"x_secondary_lag{lag}"
        for window in ("1ms", "5ms", "20ms"):
            for state in ("tx", "rx", "busy", "idle", "other"):
                names.append(f"{prefix}_phy_{state}_fraction_{window}")
    return tuple(names)


def _distributional_shadow_t2_model_replay_context() -> dict[str, Any]:
    """Load and semantically close the portable model and shadow curves."""
    global _DISTRIBUTIONAL_SHADOW_T2_MODEL_REPLAY_CONTEXT
    if _DISTRIBUTIONAL_SHADOW_T2_MODEL_REPLAY_CONTEXT is not None:
        return _DISTRIBUTIONAL_SHADOW_T2_MODEL_REPLAY_CONTEXT

    _validate_distributional_shadow_t2_source_files()
    model = _distributional_shadow_t2_xz_json(
        DISTRIBUTIONAL_SHADOW_T2_MODEL_PATH,
        DISTRIBUTIONAL_SHADOW_T2_MODEL_XZ_SHA256,
        DISTRIBUTIONAL_SHADOW_T2_SOURCE_MODEL_SHA256,
        "portable model",
    )
    reference = _distributional_shadow_t2_xz_json(
        DISTRIBUTIONAL_SHADOW_T2_REFERENCE_PATH,
        DISTRIBUTIONAL_SHADOW_T2_REFERENCE_XZ_SHA256,
        DISTRIBUTIONAL_SHADOW_T2_SOURCE_REFERENCE_SHA256,
        "shadow reference",
    )
    _require(
        _canonical_json_sha256(model, "distributional portable model")
        == DISTRIBUTIONAL_SHADOW_T2_PORTABLE_MODEL_SHA256
        and _canonical_json_sha256(reference, "distributional shadow reference")
        == DISTRIBUTIONAL_SHADOW_T2_DEPLOYMENT_REFERENCE_SHA256,
        "distributional-shadow replay: canonical component digest differs",
    )
    primary_context = _paired_value_t2_model_replay_context()
    feature_names = tuple(model.get("feature_names", ()))
    expected_names = (
        tuple(primary_context["feature_names"])
        + _distributional_shadow_t2_secondary_feature_names()
    )
    feature_contract = {
        "feature_family": "primary_compact_physics_temporal_plus_passive_secondary",
        "feature_names": list(feature_names),
        "adapter": DISTRIBUTIONAL_SHADOW_T2_FEATURE_ADAPTER,
    }
    _require(
        model.get("model_schema_version") == 1
        and model.get("runtime_contract_id") == DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID
        and model.get("runtime_contract_sha256")
        == DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256
        and model.get("selected_variant") == "primary_secondary_hgb64"
        and model.get("feature_family")
        == "primary_compact_physics_temporal_plus_passive_secondary"
        and feature_names == expected_names
        and len(feature_names) == 308
        and model.get("thresholds_us") == [12000, 18000, 24000, 30000, 33333]
        and _canonical_json_sha256(feature_contract, "distributional feature contract")
        == DISTRIBUTIONAL_SHADOW_T2_FEATURE_CONTRACT_SHA256,
        "distributional-shadow replay: model or feature contract differs",
    )
    heads = model.get("heads")
    _require(
        isinstance(heads, dict) and set(heads) == {"control", "full_copy_t2"},
        "distributional-shadow replay: model heads differ",
    )
    for name, training_count in (("control", 66_759), ("full_copy_t2", 6_641)):
        head = heads[name]
        imputer = head.get("imputer") if isinstance(head, dict) else None
        _require(
            isinstance(head, dict)
            and isinstance(imputer, dict)
            and len(imputer.get("medians", ())) == 308
            and all(
                isinstance(index, int) and not isinstance(index, bool) and 0 <= index < 308
                for index in imputer.get("missing_indicator_raw_features", ())
            )
            and len(head.get("baseline", ())) == 6
            and len(head.get("trees", ())) == 384
            and isinstance(head.get("nodes"), list)
            and head.get("training_count") == training_count
            and head.get("dirichlet_alpha_per_class") == 0.5,
            f"distributional-shadow replay: {name} head differs",
        )

    bins = reference.get("bins")
    _require(
        reference.get("reference_schema_version") == 1
        and reference.get("runtime_contract_id") == DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID
        and reference.get("runtime_contract_sha256")
        == DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256
        and reference.get("selected_variant") == "primary_secondary_hgb64"
        and reference.get("canonical_p_frame_reservation_us")
        == "1983.760667318285"
        and reference.get("frame_gate") == "p_frames_only"
        and reference.get("objective") == "deadline_rescue"
        and reference.get("time_bin_count") == DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT
        and reference.get("time_bin_width_us")
        == DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_WIDTH_US
        and isinstance(bins, list)
        and len(bins) == DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT,
        "distributional-shadow replay: shadow-reference metadata differs",
    )
    for time_bin, bin_row in enumerate(bins):
        cutpoints = bin_row.get("congestion_cutpoints")
        curves = bin_row.get("congestion_tertile")
        _require(
            bin_row.get("time_bin") == time_bin
            and bin_row.get("start_us")
            == time_bin * DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_WIDTH_US
            and bin_row.get("stop_us")
            == (time_bin + 1) * DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_WIDTH_US
            and isinstance(cutpoints, list)
            and len(cutpoints) == 2
            and all(isinstance(value, (int, float)) for value in cutpoints)
            and 0 <= float(cutpoints[0]) < float(cutpoints[1]) <= 1
            and isinstance(curves, list)
            and len(curves) == DISTRIBUTIONAL_SHADOW_T2_REGIME_COUNT,
            "distributional-shadow replay: congestion bin differs",
        )
        for regime, curve in enumerate(curves):
            densities = curve.get("density_descending")
            _require(
                curve.get("regime") == regime
                and isinstance(curve.get("training_run_count"), int)
                and curve["training_run_count"] > 0
                and isinstance(densities, list)
                and densities
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    and float(value) > 0
                    for value in densities
                )
                and all(
                    float(left) >= float(right)
                    for left, right in zip(densities, densities[1:])
                ),
                "distributional-shadow replay: opportunity curve differs",
            )

    _DISTRIBUTIONAL_SHADOW_T2_MODEL_REPLAY_CONTEXT = {
        "numpy": primary_context["numpy"],
        "model": model,
        "reference": reference,
        "feature_names": feature_names,
    }
    return _DISTRIBUTIONAL_SHADOW_T2_MODEL_REPLAY_CONTEXT


def _distributional_shadow_t2_evaluate_head(
    head: dict[str, Any], raw_features: Any
) -> dict[str, list[float]]:
    """Evaluate one portable six-class tree head in the compiled order."""
    context = _distributional_shadow_t2_model_replay_context()
    np = context["numpy"]
    try:
        raw = np.asarray(raw_features, dtype=np.float64)
        _require(
            raw.shape == (308,) and not np.any(np.isinf(raw)),
            "distributional-shadow replay: feature vector differs",
        )
        with np.errstate(over="ignore", invalid="ignore"):
            quantized = raw.astype(np.float32).astype(np.float64)
        _require(
            not np.any(np.isinf(quantized)),
            "distributional-shadow replay: feature overflows binary32",
        )
        imputer = head["imputer"]
        medians = imputer["medians"]
        indicators = imputer["missing_indicator_raw_features"]
        transformed = [
            float(medians[index]) if math.isnan(float(value)) else float(value)
            for index, value in enumerate(quantized)
        ]
        transformed.extend(
            1.0 if math.isnan(float(quantized[index])) else 0.0
            for index in indicators
        )
        logits = [float(value) for value in head["baseline"]]
        nodes = head["nodes"]
        for offset, count, class_index, _iteration in head["trees"]:
            index = 0
            while True:
                _require(
                    0 <= index < count and offset + index < len(nodes),
                    "distributional-shadow replay: tree child differs",
                )
                value, threshold, feature, left, right, missing_left, leaf = (
                    nodes[offset + index]
                )
                if leaf:
                    logits[class_index] += float(value)
                    break
                observed = transformed[feature]
                if math.isnan(observed):
                    index = left if missing_left else right
                else:
                    index = left if observed <= float(threshold) else right
        maximum = max(logits)
        softmax: list[float] = []
        denominator = 0.0
        for logit in logits:
            probability = math.exp(logit - maximum)
            softmax.append(probability)
            denominator += probability
        training_count = int(head["training_count"])
        alpha = float(head["dirichlet_alpha_per_class"])
        smoothed_denominator = training_count + alpha * len(logits)
        probabilities: list[float] = []
        cdf: list[float] = []
        cumulative = 0.0
        for probability in softmax:
            smoothed = (
                training_count * probability / denominator + alpha
            ) / smoothed_denominator
            probabilities.append(smoothed)
            cumulative += smoothed
            if len(cdf) < 5:
                cdf.append(cumulative)
    except (KeyError, TypeError, ValueError, OverflowError, IndexError) as error:
        raise ValidationError(
            f"distributional-shadow replay: portable evaluation failed: {error}"
        ) from error
    return {"logits": logits, "probabilities": probabilities, "cdf": cdf}


def _distributional_shadow_t2_model_result(raw_features: Any) -> dict[str, Any]:
    """Evaluate both completion heads and derive the two separate benefits."""
    heads = _distributional_shadow_t2_model_replay_context()["model"]["heads"]
    control = _distributional_shadow_t2_evaluate_head(heads["control"], raw_features)
    full_copy = _distributional_shadow_t2_evaluate_head(
        heads["full_copy_t2"], raw_features
    )
    return {
        "control": control,
        "full_copy": full_copy,
        "deadline_rescue_reward": max(
            full_copy["cdf"][-1] - control["cdf"][-1], 0.0
        ),
        "tail18_cdf_gain": full_copy["cdf"][1] - control["cdf"][1],
    }


def _distributional_shadow_t2_opportunity_cost(
    time_bin: int, regime: int, repayable_credit_us: float
) -> float:
    """Replay the exact marginal-density lookup using binary64 operands."""
    context = _distributional_shadow_t2_model_replay_context()
    _require(
        0 <= time_bin < DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT
        and 0 <= regime < DISTRIBUTIONAL_SHADOW_T2_REGIME_COUNT
        and math.isfinite(repayable_credit_us)
        and 0 <= repayable_credit_us <= 372000.0 + PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
        "distributional-shadow replay: invalid opportunity-price state",
    )
    curve = context["reference"]["bins"][time_bin]["congestion_tertile"][regime]
    densities = curve["density_descending"]
    training_runs = int(curve["training_run_count"])
    repayable_numerator, repayable_denominator = repayable_credit_us.as_integer_ratio()
    cost_numerator, cost_denominator = (
        DISTRIBUTIONAL_SHADOW_T2_CANONICAL_RESERVATION_US.as_integer_ratio()
    )
    low = 0
    high = len(densities)
    while low < high:
        middle = (low + high) // 2
        affordable = (
            cost_numerator * (middle + 1) * repayable_denominator
            <= repayable_numerator * training_runs * cost_denominator
        )
        if affordable:
            low = middle + 1
        else:
            high = middle
    if low == 0:
        return math.inf
    if low >= len(densities):
        return 0.0
    return float(densities[low - 1])


def _paired_value_t2_model_replay_context() -> dict[str, Any]:
    """Load the exact canonical model and hash-pinned feature builders once."""
    global _PAIRED_VALUE_T2_MODEL_REPLAY_CONTEXT
    if _PAIRED_VALUE_T2_MODEL_REPLAY_CONTEXT is not None:
        return _PAIRED_VALUE_T2_MODEL_REPLAY_CONTEXT

    _validate_paired_value_t2_source_files()
    module_paths = {
        "build_randomized_intervention_dataset": (
            PAIRED_VALUE_T2_REPOSITORY_ROOT
            / "tools/build_randomized_intervention_dataset.py"
        ),
        "build_randomized_temporal_dataset": (
            PAIRED_VALUE_T2_REPOSITORY_ROOT
            / "tools/build_randomized_temporal_dataset.py"
        ),
        "train_temporal_t2_value": (
            PAIRED_VALUE_T2_REPOSITORY_ROOT / "tools/train_temporal_t2_value.py"
        ),
    }
    modules: dict[str, Any] = {}
    try:
        for name, expected_path in module_paths.items():
            spec = importlib.util.find_spec(name)
            _require(spec is not None and spec.origin is not None and
                     Path(spec.origin).resolve() == expected_path.resolve(),
                     f"paired-value model replay: module path differs for {name}")
            module = importlib.import_module(name)
            _require(Path(module.__file__).resolve() == expected_path.resolve(),
                     f"paired-value model replay: imported path differs for {name}")
            modules[name] = module
        import numpy as np
    except (ImportError, AttributeError, OSError) as error:
        raise ValidationError(
            f"paired-value model replay: cannot load exact feature software: {error}"
        ) from error

    model_path = (
        PAIRED_VALUE_T2_REPOSITORY_ROOT
        / "results/randomized_full_copy_exploration_collection_v1/"
        "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_models.pkl"
    )
    try:
        with model_path.open("rb") as source:
            bundle = pickle.load(source)
        family = bundle["feature_families"]["primary_compact_physics_temporal"]
        feature_names = tuple(family["ordered_feature_names"])
        heads = family["heads"]
        primary_head = heads["bad_tail_12000us:primary_need"]
        treated_head = heads["bad_tail_12000us:treated_bad"]
        cost_head = heads["log_cost_given_launch"]
        smearing_factor = float(heads["log_cost_smearing_factor"])
        threshold = float(bundle["selected_policy"]["score_threshold"])
    except (OSError, pickle.UnpicklingError, KeyError, TypeError, ValueError) as error:
        raise ValidationError(
            f"paired-value model replay: canonical model bundle differs: {error}"
        ) from error

    base = modules["build_randomized_intervention_dataset"]
    temporal = modules["build_randomized_temporal_dataset"]
    trainer = modules["train_temporal_t2_value"]
    encoded_base_names = tuple(
        name for name in base.FEATURE_COLUMNS if name != "x_f0_frame_type"
    ) + ("x_f0_frame_type=I_FRAME", "x_f0_frame_type=P_FRAME")
    primary_base_names = tuple(
        name for name in encoded_base_names if not name.startswith("x_secondary_")
    )
    expected_names = (
        primary_base_names
        + tuple(trainer.COMPACT_PRIMARY_PHYSICS_NAMES)
        + tuple(trainer.PRIMARY_TEMPORAL_COLUMNS)
    )
    serialized_names = json.dumps(
        list(feature_names), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    _require(
        len(feature_names) == 246
        and feature_names == expected_names
        and hashlib.sha256(serialized_names).hexdigest()
        == PAIRED_VALUE_T2_FEATURE_NAMES_SHA256,
        "paired-value model replay: exact ordered feature contract differs",
    )
    _require(
        struct.pack(">f", _float32(threshold, "paired-value model replay"))
        == PAIRED_VALUE_T2_SCORE_THRESHOLD_BITS.to_bytes(4, "big")
        and math.isfinite(smearing_factor)
        and smearing_factor > 0
        and math.log(smearing_factor) == PAIRED_VALUE_T2_LOG_SMEARING_FACTOR
        and trainer.PREDICTED_COST_CAP_US == 1_000_000.0,
        "paired-value model replay: frozen score or cost adapter differs",
    )
    for name, model, methods in (
        ("primary", primary_head, ("decision_function", "predict_proba")),
        ("treated", treated_head, ("decision_function", "predict_proba")),
        ("cost", cost_head, ("predict",)),
    ):
        _require(all(callable(getattr(model, method, None)) for method in methods),
                 f"paired-value model replay: {name} head differs")

    _PAIRED_VALUE_T2_MODEL_REPLAY_CONTEXT = {
        "numpy": np,
        "base": base,
        "temporal": temporal,
        "trainer": trainer,
        "feature_names": feature_names,
        "primary_head": primary_head,
        "treated_head": treated_head,
        "cost_head": cost_head,
        "smearing_factor": smearing_factor,
        "threshold": threshold,
    }
    return _PAIRED_VALUE_T2_MODEL_REPLAY_CONTEXT


def _paired_value_t2_feature_vector(
    frame_id: int,
    primary: dict[str, str],
    current_poll: dict[str, str],
    lag_polls: dict[int, dict[str, str]],
) -> Any:
    """Rebuild one exact primary-only 246-feature vector without decision data."""
    context = _paired_value_t2_model_replay_context()
    np = context["numpy"]
    base = context["base"]
    temporal = context["temporal"]
    trainer = context["trainer"]
    names = context["feature_names"]
    source = f"paired-value model replay frame {frame_id}"

    raw: dict[str, str] = {}
    try:
        raw.update((f"x_f0_{field}", primary[field]) for field in base.F0_FIELDS)
        raw.update(
            (f"x_primary_{field}", primary[field])
            for field in base.PRIMARY_CURRENT_FIELDS
        )
        raw.update(
            (f"x_primary_{field}", current_poll[field])
            for field in base.PRIMARY_ROLLING_FIELDS
        )
        base_values: list[float] = []
        for name in names[:68]:
            if name == "x_f0_frame_type=I_FRAME":
                value = float(primary["frame_type"] == "I_FRAME")
            elif name == "x_f0_frame_type=P_FRAME":
                value = float(primary["frame_type"] == "P_FRAME")
            else:
                value = trainer._float32_numeric(raw[name], name, source)
            base_values.append(value)

        base_matrix = np.asarray(base_values, dtype=np.float64).reshape(1, -1)
        compact = trainer._compact_primary_physics(base_matrix, names[:68])[0]
        current_capture = int(current_poll["capture_time_ns"])
        current = temporal.Endpoint(
            frame_id,
            int(primary["generation_time_ns"]),
            int(primary["sample_time_ns"]),
            current_capture,
            primary,
            primary,
            current_poll,
            current_poll,
        )
        temporal_values: dict[str, str] = temporal._radio_features(
            "x_primary_delayed_current", current_poll, current_capture, source
        )
        for lag in (1, 3, 8):
            lag_poll = lag_polls[lag]
            lagged = temporal.Endpoint(
                frame_id - lag,
                0,
                0,
                int(lag_poll["capture_time_ns"]),
                primary,
                primary,
                lag_poll,
                lag_poll,
            )
            temporal_values.update(temporal._lag_features(current, lagged, lag, source))
        selected_temporal = [
            trainer._float32_numeric(temporal_values[name], name, source)
            for name in names[75:]
        ]
        vector = np.asarray(
            [*base_values, *compact.tolist(), *selected_temporal], dtype=np.float64
        )
        with np.errstate(over="ignore", invalid="ignore"):
            vector = vector.astype(np.float32).astype(np.float64)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValidationError(f"{source}: feature reconstruction failed: {error}") from error
    _require(vector.shape == (246,) and not np.any(np.isinf(vector)),
             f"{source}: reconstructed feature vector differs")
    return vector


def _paired_value_t2_ordered_cost_replay(
    features: Any,
) -> tuple[float, float, int]:
    """Replay the compiled ridge head and retain its roundoff scale.

    The returned absolute sum bounds the magnitude of the intercept and every
    accumulated ridge term.  It lets the independent sklearn cross-check use
    a forward-error bound instead of assuming that two valid reduction orders
    differ by a fixed number of ULPs of the potentially cancelled result.
    """
    context = _paired_value_t2_model_replay_context()
    cost_head = context["cost_head"]
    try:
        imputer = cost_head.named_steps["impute"]
        scaler = cost_head.named_steps["scale"]
        regressor = cost_head.named_steps["regressor"]
        medians = imputer.statistics_
        indicator_features = imputer.indicator_.features_
        means = scaler.mean_
        scales = scaler.scale_
        coefficients = regressor.coef_
        transformed = [
            float(medians[index]) if math.isnan(float(value)) else float(value)
            for index, value in enumerate(features)
        ]
        transformed.extend(
            1.0 if math.isnan(float(features[index])) else 0.0
            for index in indicator_features
        )
        _require(
            len(features) == 246
            and len(medians) == 246
            and len(transformed) == len(means) == len(scales) == len(coefficients)
            and len(transformed) == 262,
            "paired-value model replay: canonical cost transform differs",
        )
        predicted_log = float(regressor.intercept_)
        absolute_sum = abs(predicted_log)
        for index, value in enumerate(transformed):
            scale = float(scales[index])
            _require(scale > 0 and math.isfinite(scale),
                     "paired-value model replay: canonical cost scale differs")
            # Keep these operations separate and ordered.  This is the exact
            # expression evaluated by TemporalT2ValueModelEvaluator::EvaluateCost.
            standardized = (value - float(means[index])) / scale
            term = float(coefficients[index]) * standardized
            predicted_log += term
            absolute_sum += abs(term)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValidationError(
            f"paired-value model replay: canonical cost head differs: {error}"
        ) from error
    _require(math.isfinite(predicted_log) and math.isfinite(absolute_sum),
             "paired-value model replay: canonical cost result is non-finite")
    return predicted_log, absolute_sum, len(transformed) + 1


def _paired_value_t2_ordered_cost_log(features: Any) -> float:
    """Replay the compiled ridge head's explicit scalar accumulation order."""
    return _paired_value_t2_ordered_cost_replay(features)[0]


def _paired_value_t2_cost_reductions_close(
    ordered: float,
    vectorized: float,
    absolute_sum: float,
    term_count: int,
) -> bool:
    """Compare scalar and vector ridge reductions with a forward-error bound."""
    if not (
        math.isfinite(ordered)
        and math.isfinite(vectorized)
        and math.isfinite(absolute_sum)
        and absolute_sum >= 0
        and term_count > 0
    ):
        return False
    # Both paths standardize, multiply, and reduce binary64 operands.  Eight
    # rounding operations per term conservatively cover both evaluation paths.
    unit_roundoff = 2.0**-53
    operation_count = 8 * term_count
    scaled_roundoff = operation_count * unit_roundoff
    if scaled_roundoff >= 1.0:
        return False
    gamma = scaled_roundoff / (1.0 - scaled_roundoff)
    tolerance = gamma * absolute_sum
    return abs(ordered - vectorized) <= max(
        tolerance,
        16 * max(math.ulp(ordered), math.ulp(vectorized)),
    )


def _validate_paired_value_t2_model_replays(
    records: list[dict[str, Any]], profile: dict[str, Any]
) -> None:
    """Batch-replay and compare every evaluated row with the canonical model."""
    if not records:
        return
    context = _paired_value_t2_model_replay_context()
    np = context["numpy"]
    matrix = np.vstack([record["features"] for record in records])
    try:
        primary_logits = context["primary_head"].decision_function(matrix)
        primary_probabilities = context["primary_head"].predict_proba(matrix)[:, 1]
        treated_logits = context["treated_head"].decision_function(matrix)
        treated_probabilities = context["treated_head"].predict_proba(matrix)[:, 1]
        predicted_logs = context["cost_head"].predict(matrix)
    except (TypeError, ValueError, FloatingPointError) as error:
        raise ValidationError(
            f"paired-value model replay: canonical evaluation failed: {error}"
        ) from error

    for index, record in enumerate(records):
        primary_probability = float(primary_probabilities[index])
        treated_probability = float(treated_probabilities[index])
        sklearn_predicted_log = float(predicted_logs[index])
        predicted_log, absolute_sum, term_count = (
            _paired_value_t2_ordered_cost_replay(record["features"])
        )
        # The pinned sklearn pipeline evaluates the same ridge expression with
        # a vector reduction.  Require semantic agreement, but compare output
        # evidence with the compiled evaluator's explicit reduction order.
        _require(
            _paired_value_t2_cost_reductions_close(
                predicted_log,
                sklearn_predicted_log,
                absolute_sum,
                term_count,
            ),
            "paired-value model replay: ordered and sklearn cost heads diverge",
        )
        adjusted_log = min(
            max(predicted_log + math.log(context["smearing_factor"]), 0.0),
            math.log1p(1_000_000.0),
        )
        predicted_cost = max(math.expm1(adjusted_log), 1.0)
        nonnegative_value = max(primary_probability - treated_probability, 0.0)
        score = float(np.float32(nonnegative_value / predicted_cost))
        policy_score = (
            float(np.float32(nonnegative_value))
            if profile["cost_free"]
            else score
        )
        expected = {
            "primary_bad12_logit": float(primary_logits[index]),
            "primary_bad12_probability": primary_probability,
            "treated_bad12_logit": float(treated_logits[index]),
            "treated_bad12_probability": treated_probability,
            "predicted_log_airtime": predicted_log,
            "predicted_secondary_airtime_us": predicted_cost,
            "nonnegative_bad12_value": nonnegative_value,
            "value_per_cost_score_float32": score,
        }
        observed = record["values"]
        # The generated tree evaluator and sklearn classifiers, and the C++
        # and Python libm paths, retain deterministic last-bit differences.
        # Use one explicit bound per diagnostic; do not reuse the validator's
        # generic relative-tolerance helper.
        probability_tolerance = 2e-16
        evaluator_tolerances = {
            "primary_bad12_logit": 1e-15,
            "primary_bad12_probability": probability_tolerance,
            "treated_bad12_logit": 1e-15,
            "treated_bad12_probability": probability_tolerance,
            "predicted_log_airtime": 0.0,
            "predicted_secondary_airtime_us": max(3e-11, predicted_cost * 2e-14),
            # This value subtracts the two independently rounded
            # probabilities above, so its absolute error bound is their sum.
            "nonnegative_bad12_value": 2 * probability_tolerance,
            "value_per_cost_score_float32": 0.0,
        }
        _require(
            all(
                abs(observed[key] - value) <= evaluator_tolerances[key]
                for key, value in expected.items()
            ),
            "paired_value_t2_decisions.csv: canonical model replay differs for "
            f"frame {record['frame_id']}",
        )
        _require(
            record["policy_score"] == policy_score,
            "paired_value_t2_decisions.csv: active model score differs for "
            f"frame {record['frame_id']}",
        )
        expected_pass = policy_score >= profile["score_threshold"]
        _require(record["passes"] == expected_pass,
                 "paired_value_t2_decisions.csv: canonical model gate differs for "
                 f"frame {record['frame_id']}")


def _paired_sigmoid(logit: float) -> float:
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    exponential = math.exp(logit)
    return exponential / (1.0 + exponential)


def _validate_paired_primary_report(
    row: dict[str, str],
    *,
    previous_counters: dict[str, int] | None = None,
) -> dict[str, int]:
    """Validate the primary polling values consumed by the temporal adapter."""
    file_name = "prediction_polling_samples.csv"
    _require(row.get("feature_support_mask") == "0x3ffffffffdffff",
             f"{file_name}: paired-value primary support mask differs")
    latest_time = _optional_integer(row, "latest_feature_event_time_ns", file_name)
    latest_sequence = _integer(row, "latest_feature_event_sequence", file_name)
    _require((latest_sequence == 0) == (latest_time is None),
             f"{file_name}: paired-value watermark absence mismatch")
    capture_time = _integer(row, "capture_time_ns", file_name)
    if latest_time is not None:
        _require(latest_time <= capture_time,
                 f"{file_name}: paired-value polling watermark is future")

    cumulative_fields = (
        "mpdu_tx_attempts_total", "mpdu_positive_acks_total",
        "mpdu_tx_attempt_failures_total", "mpdu_retries_total",
        "mpdu_terminal_drops_total", "mpdu_retry_limit_drops_total",
        "mpdu_lifetime_drops_total", "mpdu_queue_drops_total", "ppdu_tx_count_total",
    )
    counters = {field: _integer(row, field, file_name) for field in cumulative_fields}
    if previous_counters is not None:
        _require(all(counters[field] >= previous_counters[field]
                     for field in cumulative_fields),
                 f"{file_name}: paired-value primary cumulative counter decreased")

    for window in (1000, 5000, 20000):
        label = _window_label(window)
        attempts = _integer(row, f"mpdu_attempts_{label}", file_name)
        retries = _integer(row, f"mpdu_retries_{label}", file_name)
        positive_acks = _integer(row, f"mpdu_positive_acks_{label}", file_name)
        _integer(row, f"mpdu_attempt_failures_{label}", file_name)
        _integer(row, f"acknowledged_mac_service_bytes_{label}", file_name)
        _require(retries <= attempts,
                 f"{file_name}: paired-value retries exceed attempts")
        retry_ratio = _optional_number(row, f"mpdu_retry_ratio_{label}", file_name)
        if attempts == 0:
            _require(retry_ratio is None,
                     f"{file_name}: paired-value zero-attempt retry ratio is populated")
        else:
            _require(retry_ratio is not None and _close(retry_ratio, retries / attempts),
                     f"{file_name}: paired-value retry ratio differs")
        ack_latencies = [
            _optional_number(row, f"{prefix}_{label}_us", file_name)
            for prefix in (
                "mpdu_queue_to_ack_mean", "mpdu_queue_to_ack_p95",
                "mpdu_first_attempt_to_ack_mean", "mpdu_first_attempt_to_ack_p95",
            )
        ]
        _require((positive_acks == 0 and all(value is None for value in ack_latencies)) or
                 (positive_acks > 0 and all(value is not None for value in ack_latencies)),
                 f"{file_name}: paired-value ACK latency nullability differs")
        coverage = _number(row, f"history_coverage_{label}_us", file_name)
        _require(_close(coverage, float(window)),
                 f"{file_name}: paired-value polling history is not fully covered")
        fractions = [
            _optional_number(row, f"phy_{state}_fraction_{label}", file_name)
            for state in ("tx", "rx", "busy", "idle", "other")
        ]
        _require(all(value is not None and 0 <= value <= 1 for value in fractions),
                 f"{file_name}: paired-value PHY fraction is missing or out of bounds")
        _require(abs(sum(value for value in fractions if value is not None) - 1.0) <= 2e-6,
                 f"{file_name}: paired-value PHY fractions do not sum to one")
    return counters


def _paired_value_t2_telemetry(
    run_dir: Path,
    run_id: str,
    frames_by_id: dict[int, dict[str, str]],
) -> tuple[dict[int, dict[str, str]], dict[int, dict[str, str]]]:
    """Return independently checked primary T2 samples and polling reports."""
    rolling_columns = {
        _rolling_column(prefix, _window_label(window))
        for prefix in PREDICTION_ROLLING_PREFIXES
        for window in (1000, 5000, 20000)
    }
    samples = _csv(run_dir / "prediction_samples.csv",
                   PREDICTION_BASE_COLUMNS | rolling_columns)
    polling = _csv(run_dir / "prediction_polling_samples.csv",
                   PREDICTION_POLLING_BASE_COLUMNS | rolling_columns)
    t2_samples = [row for row in samples if row.get("sample_offset_us") == "2000"]
    _require(len(t2_samples) == 2 * len(frames_by_id),
             "prediction_samples.csv: paired-value T2 endpoint cardinality mismatch")
    pairs: dict[int, dict[tuple[int, int], dict[str, str]]] = {}
    for row in t2_samples:
        frame_id = _integer(row, "frame_id", "prediction_samples.csv")
        key = (
            _integer(row, "path_id", "prediction_samples.csv"),
            _integer(row, "copy_id", "prediction_samples.csv"),
        )
        _require(frame_id in frames_by_id and key in {(1, 0), (0, 1)},
                 "prediction_samples.csv: paired-value T2 endpoint identity mismatch")
        _require(key not in pairs.setdefault(frame_id, {}),
                 "prediction_samples.csv: duplicate paired-value T2 endpoint")
        pairs[frame_id][key] = row
    _require(set(pairs) == set(frames_by_id) and
             all(set(pair) == {(1, 0), (0, 1)} for pair in pairs.values()),
             "prediction_samples.csv: incomplete paired-value T2 endpoint pair")

    immutable = (
        "run_id", "telemetry_schema_version", "frame_id", "sample_stage",
        "sample_offset_us", "sample_time_ns", "generation_time_ns", "deadline_time_ns",
        "frame_age_us", "deadline_slack_us", "frame_size_bytes", "frame_packet_count",
        "frame_type",
    )
    primary_samples: dict[int, dict[str, str]] = {}
    last_sample_watermark_time = 0
    last_sample_watermark_sequence = 0
    for frame_id in sorted(pairs):
        primary = pairs[frame_id][(1, 0)]
        secondary = pairs[frame_id][(0, 1)]
        _require(all(primary[field] == secondary[field] for field in immutable),
                 "prediction_samples.csv: paired-value immutable endpoint mismatch")
        _require(primary["run_id"] == run_id and primary["sample_stage"] == "T2" and
                 primary["telemetry_schema_version"] == "3",
                 "prediction_samples.csv: paired-value primary identity mismatch")
        _require(primary["feature_support_mask"] == "0x3ffffffffdffff",
                 "prediction_samples.csv: paired-value primary support mask differs")
        latest_time = _optional_integer(
            primary, "latest_feature_event_time_ns", "prediction_samples.csv"
        )
        latest_sequence = _integer(
            primary, "latest_feature_event_sequence", "prediction_samples.csv"
        )
        _require((latest_sequence == 0) == (latest_time is None),
                 "prediction_samples.csv: paired-value primary watermark absence mismatch")
        watermark_time = latest_time or 0
        _require(watermark_time >= last_sample_watermark_time and
                 latest_sequence >= last_sample_watermark_sequence,
                 "prediction_samples.csv: paired-value primary watermark reordered")
        last_sample_watermark_time = watermark_time
        last_sample_watermark_sequence = latest_sequence

        packet_count = _integer(secondary, "frame_packet_count", "prediction_samples.csv")
        _require(_integer(secondary, "packets_submitted", "prediction_samples.csv") == 0 and
                 _integer(secondary, "application_socket_packet_bytes_submitted",
                          "prediction_samples.csv") == 0 and
                 _integer(secondary, "packets_remaining_to_submit",
                          "prediction_samples.csv") == packet_count and
                 not _flag(secondary, "sender_mac_complete", "prediction_samples.csv") and
                 _flag(secondary, "actionable", "prediction_samples.csv"),
                 "prediction_samples.csv: hypothetical secondary is not untreated")
        for field in (
            "frame_packets_mac_enqueued", "frame_packets_mac_dequeued",
            "frame_packets_tx_succeeded", "frame_mpdu_attempt_failures",
            "frame_packets_terminally_dropped", "frame_packets_currently_queued",
            "frame_mac_service_bytes_currently_queued",
        ):
            value = _optional_integer(secondary, field, "prediction_samples.csv")
            _require(value is None or value == 0,
                     "prediction_samples.csv: hypothetical secondary has MAC progress")
        primary_samples[frame_id] = primary

    t2_polling = [row for row in polling if row.get("sample_offset_us") == "2000" and
                  row.get("path_id") == "1" and row.get("copy_id") == "0"]
    _require(len(t2_polling) == len(frames_by_id),
             "prediction_polling_samples.csv: paired-value primary T2 cardinality mismatch")
    primary_polling: dict[int, dict[str, str]] = {}
    previous_counters: dict[str, int] | None = None
    last_report_watermark_time = 0
    last_report_watermark_sequence = 0
    last_capture = -1
    for row in sorted(t2_polling, key=lambda item: _integer(
        item, "capture_time_ns", "prediction_polling_samples.csv"
    )):
        file_name = "prediction_polling_samples.csv"
        frame_id = _integer(row, "frame_id", file_name)
        _require(frame_id in frames_by_id and frame_id not in primary_polling and
                 row["run_id"] == run_id and row["sample_stage"] == "T2" and
                 row["polling_schema_version"] == "1" and
                 _flag(row, "report_available", file_name),
                 f"{file_name}: paired-value primary report identity mismatch")
        capture = _integer(row, "capture_time_ns", file_name)
        available = _integer(row, "available_time_ns", file_name)
        sample_time = _integer(primary_samples[frame_id], "sample_time_ns",
                               "prediction_samples.csv")
        _require(capture % 1_000_000 == 0 and available == capture + 1_000_000 and
                 available <= sample_time and
                 1_000_000 <= sample_time - capture < 2_000_000,
                 f"{file_name}: paired-value delayed report timing mismatch")
        counters = _validate_paired_primary_report(
            row, previous_counters=previous_counters
        )
        previous_counters = counters
        latest_time = _optional_integer(row, "latest_feature_event_time_ns", file_name) or 0
        latest_sequence = _integer(row, "latest_feature_event_sequence", file_name)
        _require(capture >= last_capture and latest_time >= last_report_watermark_time and
                 latest_sequence >= last_report_watermark_sequence,
                 f"{file_name}: paired-value report order or watermark regressed")
        last_capture = capture
        last_report_watermark_time = latest_time
        last_report_watermark_sequence = latest_sequence
        primary_polling[frame_id] = row
    return primary_samples, primary_polling


def _distributional_shadow_t2_telemetry(
    run_dir: Path,
    run_id: str,
    frames_by_id: dict[int, dict[str, str]],
) -> tuple[
    dict[int, dict[str, str]],
    dict[int, dict[str, str]],
    dict[int, dict[str, str]],
    dict[int, dict[str, str]],
]:
    """Return independently checked primary and passive-secondary T2 state."""
    primary_samples, primary_polling = _paired_value_t2_telemetry(
        run_dir, run_id, frames_by_id
    )
    rolling_columns = {
        _rolling_column(prefix, _window_label(window))
        for prefix in PREDICTION_ROLLING_PREFIXES
        for window in (1000, 5000, 20000)
    }
    samples = _csv(
        run_dir / "prediction_samples.csv",
        PREDICTION_BASE_COLUMNS | rolling_columns,
    )
    polling = _csv(
        run_dir / "prediction_polling_samples.csv",
        PREDICTION_POLLING_BASE_COLUMNS | rolling_columns,
    )
    secondary_rows = [
        row
        for row in samples
        if row.get("sample_offset_us") == "2000"
        and row.get("path_id") == "0"
        and row.get("copy_id") == "1"
    ]
    _require(
        len(secondary_rows) == len(frames_by_id),
        "prediction_samples.csv: distributional secondary T2 cardinality differs",
    )
    secondary_samples: dict[int, dict[str, str]] = {}
    last_watermark_time = 0
    last_watermark_sequence = 0
    for row in secondary_rows:
        file_name = "prediction_samples.csv"
        frame_id = _integer(row, "frame_id", file_name)
        _require(
            frame_id in frames_by_id
            and frame_id not in secondary_samples
            and row["run_id"] == run_id
            and row["sample_stage"] == "T2"
            and row["telemetry_schema_version"] == "3"
            and row["feature_support_mask"] == "0x3ffffffffdffff",
            f"{file_name}: distributional secondary identity differs",
        )
        latest_time = _optional_integer(
            row, "latest_feature_event_time_ns", file_name
        )
        latest_sequence = _integer(
            row, "latest_feature_event_sequence", file_name
        )
        _require(
            (latest_sequence == 0) == (latest_time is None)
            and (latest_time or 0) >= last_watermark_time
            and latest_sequence >= last_watermark_sequence,
            f"{file_name}: distributional secondary watermark regressed",
        )
        last_watermark_time = latest_time or 0
        last_watermark_sequence = latest_sequence
        secondary_samples[frame_id] = row

    secondary_poll_rows = [
        row
        for row in polling
        if row.get("sample_offset_us") == "2000"
        and row.get("path_id") == "0"
        and row.get("copy_id") == "1"
    ]
    _require(
        len(secondary_poll_rows) == len(frames_by_id),
        "prediction_polling_samples.csv: distributional secondary cardinality differs",
    )
    secondary_polling: dict[int, dict[str, str]] = {}
    previous_counters: dict[str, int] | None = None
    last_capture = -1
    last_report_watermark_time = 0
    last_report_watermark_sequence = 0
    for row in sorted(
        secondary_poll_rows,
        key=lambda item: _integer(
            item, "capture_time_ns", "prediction_polling_samples.csv"
        ),
    ):
        file_name = "prediction_polling_samples.csv"
        frame_id = _integer(row, "frame_id", file_name)
        _require(
            frame_id in frames_by_id
            and frame_id not in secondary_polling
            and row["run_id"] == run_id
            and row["sample_stage"] == "T2"
            and row["polling_schema_version"] == "1"
            and _flag(row, "report_available", file_name),
            f"{file_name}: distributional secondary report identity differs",
        )
        capture = _integer(row, "capture_time_ns", file_name)
        available = _integer(row, "available_time_ns", file_name)
        sample_time = _integer(
            secondary_samples[frame_id], "sample_time_ns", "prediction_samples.csv"
        )
        _require(
            capture % 1_000_000 == 0
            and available == capture + 1_000_000
            and available <= sample_time
            and 1_000_000 <= sample_time - capture < 2_000_000,
            f"{file_name}: distributional secondary report timing differs",
        )
        counters = _validate_paired_primary_report(
            row, previous_counters=previous_counters
        )
        previous_counters = counters
        latest_time = _optional_integer(
            row, "latest_feature_event_time_ns", file_name
        ) or 0
        latest_sequence = _integer(
            row, "latest_feature_event_sequence", file_name
        )
        _require(
            capture >= last_capture
            and latest_time >= last_report_watermark_time
            and latest_sequence >= last_report_watermark_sequence,
            f"{file_name}: distributional secondary report order regressed",
        )
        last_capture = capture
        last_report_watermark_time = latest_time
        last_report_watermark_sequence = latest_sequence
        secondary_polling[frame_id] = row
    return primary_samples, primary_polling, secondary_samples, secondary_polling


def _distributional_shadow_t2_feature_vector(
    frame_id: int,
    primary: dict[str, str],
    primary_poll: dict[str, str],
    primary_lag_polls: dict[int, dict[str, str]],
    secondary: dict[str, str],
    secondary_poll: dict[str, str],
    secondary_lag_polls: dict[int, dict[str, str]],
) -> Any:
    """Rebuild the exact 308-feature binary32 adapter from raw telemetry."""
    context = _distributional_shadow_t2_model_replay_context()
    np = context["numpy"]
    primary_features = _paired_value_t2_feature_vector(
        frame_id, primary, primary_poll, primary_lag_polls
    )
    values: list[float] = []
    for field in ("mac_queue_packets", "mac_queue_service_bytes"):
        observed = _optional_number(secondary, field, "prediction_samples.csv")
        values.append(math.nan if observed is None else observed)

    def append_fractions(report: dict[str, str]) -> None:
        for window in (1000, 5000, 20000):
            label = _window_label(window)
            for state in ("tx", "rx", "busy", "idle", "other"):
                observed = _optional_number(
                    report,
                    f"phy_{state}_fraction_{label}",
                    "prediction_polling_samples.csv",
                )
                values.append(math.nan if observed is None else observed)

    append_fractions(secondary_poll)
    for lag in (1, 3, 8):
        append_fractions(secondary_lag_polls[lag])
    try:
        vector = np.concatenate(
            (primary_features, np.asarray(values, dtype=np.float64))
        )
        with np.errstate(over="ignore", invalid="ignore"):
            vector = vector.astype(np.float32).astype(np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError(
            f"distributional-shadow frame {frame_id}: feature reconstruction failed: "
            f"{error}"
        ) from error
    _require(
        vector.shape == (308,) and not np.any(np.isinf(vector)),
        f"distributional-shadow frame {frame_id}: feature vector differs",
    )
    return vector


def _validate_paired_value_t2_decisions(
    run_dir: Path,
    run_id: str,
    frames: list[dict[str, str]],
    policy_decisions: list[dict[str, str]],
    duplicated_frame_ids: set[int],
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and reconstruct every frozen paired-value decision."""
    if profile is None:
        profile = {
            "score_aware": False,
            "remaining_refill": False,
            "cost_free": False,
            "decision_schema_version": 1,
            "summary_schema_version": 1,
            "runtime_contract_id": PAIRED_VALUE_T2_CONTRACT_ID,
            "runtime_contract_sha256": PAIRED_VALUE_T2_CONTRACT_SHA256,
            "decision_columns": PAIRED_VALUE_T2_DECISION_COLUMNS,
            "admission_profile_id": None,
            "guard_max_horizon_us": 10_000_000,
            "guard_capacity_us": int(PAIRED_VALUE_T2_GUARD_CAPACITY_US),
            "model_metadata": PAIRED_VALUE_T2_MODEL_METADATA,
            "score_threshold": PAIRED_VALUE_T2_SCORE_THRESHOLD,
            "score_threshold_bits": PAIRED_VALUE_T2_SCORE_THRESHOLD_BITS,
            "emergency_score_threshold":
                PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD,
            "emergency_score_threshold_bits":
                PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD_BITS,
        }
    file_name = "paired_value_t2_decisions.csv"
    rows = _csv(
        run_dir / file_name,
        set(profile["decision_columns"]),
        ordered_columns=profile["decision_columns"],
    )
    frame_ids = [_integer(frame, "frame_id", "frames.csv") for frame in frames]
    expected_frame_ids = list(range(len(frames)))
    _require(set(frame_ids) == set(expected_frame_ids),
             "frames.csv: paired-value frame IDs must start at zero and be contiguous")
    _require(len(rows) == len(frames) and
             [_integer(row, "frame_id", file_name) for row in rows] == expected_frame_ids,
             f"{file_name}: rows must exactly match generated frames in frame order")
    frames_by_id = {int(frame["frame_id"]): frame for frame in frames}
    primary_samples, primary_polling = _paired_value_t2_telemetry(
        run_dir, run_id, frames_by_id
    )

    status_counts: Counter[str] = Counter()
    action_frames: set[int] = set()
    action_estimates: dict[int, float] = {}
    action_nominals: dict[int, float] = {}
    action_byte_quanta: dict[int, int] = {}
    action_mpdu_profiles: dict[int, tuple[int, int, int]] = {}
    learned_evaluated = 0.0
    learned_launched = 0.0
    nominal_launched = 0.0
    reserved_launched = 0.0
    score_passed = 0
    launch_attempted_count = 0
    strict_guard_admitted_count = 0
    emergency_score_passed_count = 0
    emergency_admission_considered_count = 0
    emergency_admitted_count = 0
    remaining_refill_admission_considered_count = 0
    remaining_refill_admitted_count = 0
    maximum_observed_debt = 0.0
    previous_meter_reserved_after: float | None = None
    model_replay_records: list[dict[str, Any]] = []

    model_numeric_columns = (
        "primary_bad12_logit", "primary_bad12_probability", "treated_bad12_logit",
        "treated_bad12_probability", "predicted_log_airtime",
        "predicted_secondary_airtime_us", "nonnegative_bad12_value",
        "value_per_cost_score_float32",
    )
    for row in rows:
        frame_id = _integer(row, "frame_id", file_name)
        frame = frames_by_id[frame_id]
        primary = primary_samples[frame_id]
        poll = primary_polling[frame_id]
        _require(_integer(row, "schema_version", file_name) ==
                 profile["decision_schema_version"] and
                 row["run_id"] == run_id and
                 all(row[key] == value
                     for key, value in profile["model_metadata"].items()),
                 f"{file_name}: fixed schema, policy, or model metadata differs")
        threshold = _number(row, "score_threshold_float32", file_name)
        _require(struct.pack(">f", _float32(threshold, file_name)) ==
                 profile["score_threshold_bits"].to_bytes(4, "big"),
                 f"{file_name}: frozen float32 threshold differs")
        _require(row["primary_path_id"] == "1" and row["primary_copy_id"] == "0" and
                 row["secondary_path_id"] == "0" and row["secondary_copy_id"] == "1",
                 f"{file_name}: paired endpoint identity differs")

        generation_ns = _integer(row, "generation_time_ns", file_name)
        deadline_ns = _integer(row, "deadline_time_ns", file_name)
        sample_ns = _integer(row, "primary_sample_time_ns", file_name)
        _require(generation_ns // 1000 == int(frame["generation_time_us"]) and
                 deadline_ns == generation_ns + int(frame["deadline_us"]) * 1000 and
                 sample_ns == generation_ns + 2_000_000 and
                 _integer(row, "secondary_sample_time_ns", file_name) == sample_ns,
                 f"{file_name}: paired decision timestamps differ")
        _require(row["generation_time_ns"] == primary["generation_time_ns"] and
                 row["deadline_time_ns"] == primary["deadline_time_ns"] and
                 row["primary_sample_time_ns"] == primary["sample_time_ns"] and
                 row["frame_type"] == frame["frame_type"] == primary["frame_type"] and
                 row["frame_size_bytes"] == frame["frame_size_bytes"] ==
                 primary["frame_size_bytes"] and
                 row["frame_packet_count"] == frame["packet_count"] ==
                 primary["frame_packet_count"] and
                 row["primary_actionable"] == primary["actionable"],
                 f"{file_name}: decision evidence differs from primary telemetry")
        _require(row["primary_feature_watermark_time_ns"] ==
                 primary["latest_feature_event_time_ns"] and
                 row["primary_feature_watermark_sequence"] ==
                 primary["latest_feature_event_sequence"],
                 f"{file_name}: primary watermark evidence differs")

        _require(row["current_poll_capture_time_ns"] == poll["capture_time_ns"] and
                 row["current_poll_available_time_ns"] == poll["available_time_ns"],
                 f"{file_name}: current delayed polling evidence differs")
        history_ready = frame_id >= 8
        _require(_flag(row, "history_ready", file_name) == history_ready,
                 f"{file_name}: exact history-ready evidence differs")
        for lag in (1, 3, 8):
            lag_frame_key = f"lag{lag}_frame_id"
            lag_capture_key = f"lag{lag}_poll_capture_time_ns"
            if frame_id >= lag:
                expected_frame = frame_id - lag
                _require(row[lag_frame_key] == str(expected_frame) and
                         row[lag_capture_key] == primary_polling[expected_frame]["capture_time_ns"],
                         f"{file_name}: exact lag-{lag} evidence differs")
            else:
                _require(row[lag_frame_key] == "" and row[lag_capture_key] == "",
                         f"{file_name}: absent lag-{lag} must be null")

        inside = PAIRED_VALUE_T2_DECISION_START_NS <= sample_ns < \
            PAIRED_VALUE_T2_DECISION_STOP_NS
        actionable = _flag(row, "primary_actionable", file_name)
        _require(row["decision_window_start_ns"] ==
                 str(PAIRED_VALUE_T2_DECISION_START_NS) and
                 row["decision_window_stop_ns"] == str(PAIRED_VALUE_T2_DECISION_STOP_NS) and
                 _flag(row, "inside_decision_window", file_name) == inside,
                 f"{file_name}: decision-window evidence differs")
        descriptor_should_be_checked = (
            inside and history_ready and row["frame_type"] == "P_FRAME" and actionable
        )
        descriptor_checked = _flag(row, "descriptor_checked", file_name)
        descriptor_available = _flag(row, "descriptor_available", file_name)
        _require(descriptor_checked == descriptor_should_be_checked and
                 (descriptor_checked or not descriptor_available),
                 f"{file_name}: descriptor gate order differs")

        _require(row["canonical_cost_estimator_id"] == RANDOMIZED_COST_ESTIMATOR and
                 _paired_close(_number(row, "cost_safety_factor", file_name), 1.25),
                 f"{file_name}: canonical estimator metadata differs")
        nominal: float | None = None
        reserved: float | None = None
        mpdu_bytes: int | None = None
        mpdu_profile: tuple[int, int, int] | None = None
        descriptor_fields = (
            "descriptor_frame_packet_count", "descriptor_packet_count",
            "descriptor_packet_indices", "descriptor_expected_mac_service_bytes",
            "descriptor_deadline_time_ns", "canonical_nominal_airtime_us",
            "canonical_reserved_airtime_us",
        )
        if descriptor_available:
            packet_count, indices, expected_service_bytes, expected_nominal = (
                _canonical_full_copy_descriptor(frame)
            )
            _require(row["descriptor_frame_packet_count"] == str(packet_count) and
                     row["descriptor_packet_count"] == str(packet_count) and
                     row["descriptor_packet_indices"] == indices and
                     row["descriptor_expected_mac_service_bytes"] ==
                     str(expected_service_bytes) and
                     row["descriptor_deadline_time_ns"] == str(deadline_ns),
                     f"{file_name}: canonical descriptor evidence differs")
            mpdu_profile = _canonical_full_copy_mpdu_profile(frame)
            if mpdu_profile[0] == mpdu_profile[1]:
                mpdu_bytes = mpdu_profile[0]
            nominal = _number(row, "canonical_nominal_airtime_us", file_name)
            reserved = _number(row, "canonical_reserved_airtime_us", file_name)
            _require(_paired_close(nominal, expected_nominal) and
                     _paired_close(reserved, 1.25 * expected_nominal),
                     f"{file_name}: canonical descriptor cost differs")
        else:
            _require(all(row[field] == "" for field in descriptor_fields),
                     f"{file_name}: unavailable descriptor has populated evidence")

        feature_evaluated = _flag(row, "feature_evaluated", file_name)
        _require(feature_evaluated == descriptor_available,
                 f"{file_name}: model evaluation bypassed ordered gates")
        passes: bool | None
        observed_score: float | None = None
        policy_score: float | None = None
        predicted_cost: float | None = None
        if feature_evaluated:
            values = {key: _signed_number(row, key, file_name)
                      for key in model_numeric_columns}
            primary_probability = values["primary_bad12_probability"]
            treated_probability = values["treated_bad12_probability"]
            predicted_log_cost = values["predicted_log_airtime"]
            predicted_cost = values["predicted_secondary_airtime_us"]
            nonnegative_value = values["nonnegative_bad12_value"]
            adjusted_log_cost = min(
                max(predicted_log_cost + PAIRED_VALUE_T2_LOG_SMEARING_FACTOR, 0.0),
                PAIRED_VALUE_T2_LOG_COST_CAP,
            )
            reconstructed_cost = max(math.expm1(adjusted_log_cost), 1.0)
            _require(0 <= primary_probability <= 1 and 0 <= treated_probability <= 1 and
                     _close(primary_probability,
                            _paired_sigmoid(values["primary_bad12_logit"])) and
                     _close(treated_probability,
                            _paired_sigmoid(values["treated_bad12_logit"])) and
                     1 <= predicted_cost <= 1_000_000 and
                     _close(predicted_cost, reconstructed_cost) and
                     _close(nonnegative_value,
                            max(primary_probability - treated_probability, 0.0)),
                     f"{file_name}: frozen model diagnostics do not reconcile")
            expected_score = _float32(nonnegative_value / predicted_cost, file_name)
            observed_score = values["value_per_cost_score_float32"]
            _require(_float32(observed_score, file_name) == observed_score and
                     struct.pack(">f", observed_score) == struct.pack(">f", expected_score),
                     f"{file_name}: value-per-cost score is not exact float32")
            policy_score = (
                _float32(nonnegative_value, file_name)
                if profile["cost_free"]
                else observed_score
            )
            if profile["cost_free"]:
                serialized_policy_score = _signed_number(
                    row, "policy_score_float32", file_name
                )
                _require(
                    _float32(serialized_policy_score, file_name)
                    == serialized_policy_score
                    and struct.pack(">f", serialized_policy_score)
                    == struct.pack(">f", policy_score),
                    f"{file_name}: cost-free policy score is not exact float32",
                )
            passes = _flag(row, "passes_score_threshold", file_name)
            _require(passes == (policy_score >= profile["score_threshold"]),
                     f"{file_name}: float32 score-threshold result differs")
            model_replay_records.append({
                "frame_id": frame_id,
                "features": _paired_value_t2_feature_vector(
                    frame_id,
                    primary,
                    poll,
                    {
                        lag: primary_polling[frame_id - lag]
                        for lag in (1, 3, 8)
                    },
                ),
                "values": values,
                "passes": passes,
                "policy_score": policy_score,
            })
            learned_evaluated += predicted_cost
            if passes:
                score_passed += 1
        else:
            _require(all(row[key] == "" for key in model_numeric_columns) and
                     row["passes_score_threshold"] == "",
                     f"{file_name}: unevaluated model diagnostics must be null")
            if profile["cost_free"]:
                _require(row["policy_score_float32"] == "",
                         f"{file_name}: unevaluated policy score must be null")
            passes = None
            policy_score = None

        _require(_paired_close(_number(row, "guard_fraction", file_name), 0.006) and
                 _integer(row, "guard_max_horizon_us", file_name) ==
                 profile["guard_max_horizon_us"] and
                 _integer(row, "guard_initial_horizon_us", file_name) == 2_000_000 and
                 _paired_close(_number(row, "guard_capacity_us", file_name),
                               profile["guard_capacity_us"]) and
                 _paired_close(_number(row, "guard_initial_credit_us", file_name), 12_000),
                 f"{file_name}: frozen guard metadata differs")
        balance_before = _signed_number(row, "guard_balance_before_us", file_name)
        reserved_before = _number(row, "meter_reserved_before_us", file_name)
        available_before = _signed_number(row, "guard_available_before_us", file_name)
        debt_before = _number(row, "guard_debt_before_us", file_name)
        balance_after = _signed_number(row, "guard_balance_after_us", file_name)
        reserved_after = _number(row, "meter_reserved_after_us", file_name)
        available_after = _signed_number(row, "guard_available_after_us", file_name)
        debt_after = _number(row, "guard_debt_after_us", file_name)
        if previous_meter_reserved_after is None:
            _require(_paired_close(reserved_before, 0.0),
                     f"{file_name}: initial meter reservation is nonzero")
        else:
            _require(reserved_before <= previous_meter_reserved_after +
                     PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
                     f"{file_name}: meter reservation increased between decisions")
        _require(_paired_close(available_before, balance_before - reserved_before) and
                 _paired_close(available_after, balance_after - reserved_after) and
                 _paired_close(debt_before, max(0.0, -balance_before)) and
                 _paired_close(debt_after, max(0.0, -balance_after)) and
                 _paired_close(balance_before, balance_after),
                 f"{file_name}: guard accounting arithmetic differs")
        maximum_observed_debt = max(maximum_observed_debt, debt_before, debt_after)
        considered = _flag(row, "guard_admission_considered", file_name)
        admitted = _flag(row, "guard_admitted", file_name)
        launch_attempted = _flag(row, "launch_attempted", file_name)
        launched = _flag(row, "secondary_launched", file_name)
        _require(considered == (passes is True),
                 f"{file_name}: guard admission gate order differs")
        strict_admitted = bool(considered and reserved is not None and
                               reserved <= available_before)
        emergency_admitted = False
        remaining_refill_admitted = False
        if profile["score_aware"]:
            serialized_emergency_threshold = _number(
                row, "emergency_score_threshold_float32", file_name
            )
            _require(
                row["admission_profile_id"] == profile["admission_profile_id"]
                and struct.pack(
                    ">f", _float32(serialized_emergency_threshold, file_name)
                ) == profile["emergency_score_threshold_bits"].to_bytes(4, "big")
                and _paired_close(
                    _number(row, "emergency_maximum_debt_us", file_name),
                    PAIRED_VALUE_T2_EMERGENCY_MAXIMUM_DEBT_US,
                ),
                f"{file_name}: emergency admission metadata differs",
            )
            expected_emergency_score_pass = bool(
                considered
                and not strict_admitted
                and policy_score is not None
                and policy_score >= profile["emergency_score_threshold"]
            )
            observed_emergency_score_pass = _flag(
                row, "passes_emergency_score_threshold", file_name
            )
            emergency_considered = _flag(
                row, "emergency_admission_considered", file_name
            )
            emergency_admitted = bool(
                expected_emergency_score_pass
                and reserved is not None
                and reserved <= available_before + PAIRED_VALUE_T2_EMERGENCY_MAXIMUM_DEBT_US
            )
            observed_emergency_admitted = _flag(
                row, "emergency_admitted", file_name
            )
            _require(
                _flag(row, "strict_guard_admitted", file_name) == strict_admitted
                and observed_emergency_score_pass == expected_emergency_score_pass
                and emergency_considered == expected_emergency_score_pass
                and observed_emergency_admitted == emergency_admitted,
                f"{file_name}: emergency admission differs from reconstructed decision",
            )
            strict_guard_admitted_count += int(strict_admitted)
            emergency_score_passed_count += int(expected_emergency_score_pass)
            emergency_admission_considered_count += int(emergency_considered)
            emergency_admitted_count += int(emergency_admitted)
        if profile["remaining_refill"]:
            last_refill_ns = max(
                PAIRED_VALUE_T2_DECISION_START_NS,
                min(sample_ns, PAIRED_VALUE_T2_MEASUREMENT_STOP_NS),
            )
            expected_remaining_refill_us = PAIRED_VALUE_T2_GUARD_FRACTION * (
                (PAIRED_VALUE_T2_MEASUREMENT_STOP_NS - last_refill_ns) / 1000.0
            )
            observed_remaining_refill_us = _number(
                row, "remaining_refill_credit_us", file_name
            )
            remaining_refill_considered = bool(
                considered and not strict_admitted and not emergency_admitted
            )
            remaining_refill_admitted = bool(
                remaining_refill_considered
                and reserved is not None
                and reserved <= available_before + expected_remaining_refill_us
            )
            _require(
                _paired_close(
                    observed_remaining_refill_us, expected_remaining_refill_us
                )
                and _flag(
                    row, "remaining_refill_admission_considered", file_name
                ) == remaining_refill_considered
                and _flag(row, "remaining_refill_admitted", file_name)
                == remaining_refill_admitted,
                f"{file_name}: remaining-refill admission differs from reconstructed "
                "decision",
            )
            remaining_refill_admission_considered_count += int(
                remaining_refill_considered
            )
            remaining_refill_admitted_count += int(remaining_refill_admitted)
        if profile["score_aware"]:
            expected_tier = (
                "strict" if strict_admitted
                else "emergency" if emergency_admitted
                else "remaining_refill" if remaining_refill_admitted
                else "none"
            )
            _require(
                row["admission_tier"] == expected_tier,
                f"{file_name}: admission tier differs from reconstructed decision",
            )
        expected_admitted = (
            strict_admitted or emergency_admitted or remaining_refill_admitted
        )
        _require(admitted == expected_admitted and launch_attempted == admitted and
                 (launch_attempted or not launched),
                 f"{file_name}: guard or launch flags differ from reconstructed decision")
        if launch_attempted:
            launch_attempted_count += 1
        if launched:
            assert (nominal is not None and reserved is not None and
                    predicted_cost is not None and mpdu_profile is not None)
            _require(_paired_close(reserved_after, reserved_before + reserved),
                     f"{file_name}: action reservation does not reconcile")
            action_frames.add(frame_id)
            action_estimates[frame_id] = reserved
            action_nominals[frame_id] = nominal
            action_mpdu_profiles[frame_id] = mpdu_profile
            if mpdu_bytes is not None:
                action_byte_quanta[frame_id] = mpdu_bytes
            learned_launched += predicted_cost
            nominal_launched += nominal
            reserved_launched += reserved
        else:
            _require(_paired_close(reserved_after, reserved_before),
                     f"{file_name}: non-action changed meter reservation")
        previous_meter_reserved_after = reserved_after

        if not inside:
            expected_status = "outside_decision_window"
        elif not history_ready:
            expected_status = "history_warmup"
        elif row["frame_type"] != "P_FRAME":
            expected_status = "frame_type_restricted"
        elif not actionable:
            expected_status = "not_actionable"
        elif not descriptor_available:
            expected_status = "descriptor_unavailable"
        elif not passes:
            expected_status = "below_score_threshold"
        elif not admitted:
            expected_status = "airtime_guard_rejected"
        elif not launched:
            expected_status = "launch_rejected"
        else:
            expected_status = "action"
        _require(row["decision_status"] == expected_status and
                 row["decision_status"] in PAIRED_VALUE_T2_STATUSES,
                 f"{file_name}: decision status differs from ordered gates")
        status_counts[expected_status] += 1

    _validate_paired_value_t2_model_replays(model_replay_records, profile)
    _require(
        not profile["remaining_refill"]
        or remaining_refill_admission_considered_count
        == remaining_refill_admitted_count
        + status_counts["airtime_guard_rejected"],
        f"{file_name}: remaining-refill consideration count does not reconcile",
    )
    _require(action_frames == duplicated_frame_ids,
             f"{file_name}: actions do not match duplicated frames")
    policy_action_frames = {
        _integer(row, "frame_id", "policy_decisions.csv")
        for row in policy_decisions
        if _flag(row, "duplicated", "policy_decisions.csv")
    }
    _require(policy_action_frames == action_frames and
             all(row["policy"] == PAIRED_VALUE_T2_POLICY and row["primary_link"] == "1"
                 for row in policy_decisions) and
             all((row["secondary_link"] == "0") ==
                 _flag(row, "duplicated", "policy_decisions.csv")
                 for row in policy_decisions),
             "policy_decisions.csv: paired-value launch evidence differs")
    _require(all(frame["policy"] == PAIRED_VALUE_T2_POLICY and
                 frame["primary_link"] == "1" for frame in frames),
             "frames.csv: paired-value policy/path identity differs")
    return {
        "rows": rows,
        "status_counts": status_counts,
        "action_frames": action_frames,
        "action_estimates": action_estimates,
        "action_nominals": action_nominals,
        "action_byte_quanta": action_byte_quanta,
        "action_mpdu_profiles": action_mpdu_profiles,
        "learned_evaluated": learned_evaluated,
        "learned_launched": learned_launched,
        "nominal_launched": nominal_launched,
        "reserved_launched": reserved_launched,
        "score_passed": score_passed,
        "launch_attempted": launch_attempted_count,
        "strict_guard_admitted": strict_guard_admitted_count,
        "emergency_score_passed": emergency_score_passed_count,
        "emergency_admission_considered": emergency_admission_considered_count,
        "emergency_admitted": emergency_admitted_count,
        "remaining_refill_admission_considered":
            remaining_refill_admission_considered_count,
        "remaining_refill_admitted": remaining_refill_admitted_count,
        "maximum_observed_debt": maximum_observed_debt,
        "profile": profile,
    }


def _distributional_shadow_t2_array(
    row: dict[str, str], key: str, size: int, file_name: str
) -> list[float]:
    tokens = row.get(key, "").split(";")
    _require(
        len(tokens) == size and all(token != "" for token in tokens),
        f"{file_name}: {key} has the wrong array width",
    )
    try:
        values = [float(token) for token in tokens]
    except ValueError as error:
        raise ValidationError(f"{file_name}: {key} has an invalid value") from error
    _require(
        all(math.isfinite(value) for value in values),
        f"{file_name}: {key} contains a non-finite value",
    )
    return values


def _distributional_shadow_t2_extended_nonnegative(
    row: dict[str, str], key: str, file_name: str
) -> float:
    try:
        value = float(row[key])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"{file_name}: invalid extended number {key}") from error
    _require(
        not math.isnan(value) and value >= 0,
        f"{file_name}: invalid nonnegative extended number {key}",
    )
    return value


def _distributional_shadow_t2_values_close(
    observed: list[float], expected: list[float], tolerance: float = 5e-14
) -> bool:
    return len(observed) == len(expected) and all(
        abs(left - right) <= max(
            tolerance,
            16 * max(math.ulp(left), math.ulp(right)),
        )
        for left, right in zip(observed, expected)
    )


def _distributional_shadow_t2_expected_unsettled(
    sample_time_ns: int,
    feature_evaluated: bool,
    action_times: dict[int, int],
    settlement_times: dict[int, int],
) -> int:
    """Reconstruct the controller's scored-only unsettled-action diagnostic."""
    if not feature_evaluated:
        return 0
    return sum(
        launch_time < sample_time_ns <= settlement_times[frame_id]
        for frame_id, launch_time in action_times.items()
    )


def _distributional_shadow_t2_descriptor_cost_matches_profile(
    nominal: float,
    reserved: float,
    expected_nominal: float,
    frame_profile: str,
) -> bool:
    """Check dynamic descriptor arithmetic and the canonical-profile anchor."""
    return (
        _paired_close(nominal, expected_nominal)
        and _paired_close(reserved, 1.25 * expected_nominal)
        and (
            frame_profile == PAIRED_TEMPORAL_T2_GENERALIZATION_FRAME_PROFILE
            or (
                frame_profile == PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE
                and _paired_close(
                    reserved,
                    DISTRIBUTIONAL_SHADOW_T2_CANONICAL_RESERVATION_US,
                )
            )
        )
    )


def _validate_distributional_shadow_t2_decisions(
    run_dir: Path,
    run_id: str,
    frames: list[dict[str, str]],
    policy_decisions: list[dict[str, str]],
    duplicated_frame_ids: set[int],
    frame_profile: str,
) -> dict[str, Any]:
    """Independently replay every distributional-shadow decision and ledger debit."""
    _require(
        frame_profile
        in {
            PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE,
            PAIRED_TEMPORAL_T2_GENERALIZATION_FRAME_PROFILE,
        },
        "distributional_shadow_t2_decisions.csv: unsupported frame profile",
    )
    file_name = "distributional_shadow_t2_decisions.csv"
    rows = _csv(
        run_dir / file_name,
        set(DISTRIBUTIONAL_SHADOW_T2_DECISION_COLUMNS),
        ordered_columns=DISTRIBUTIONAL_SHADOW_T2_DECISION_COLUMNS,
    )
    frames_by_id = {_integer(row, "frame_id", "frames.csv"): row for row in frames}
    _require(
        len(rows) == len(frames_by_id)
        and {_integer(row, "frame_id", file_name) for row in rows}
        == set(frames_by_id),
        f"{file_name}: frame cardinality differs",
    )
    (
        primary_samples,
        primary_polling,
        secondary_samples,
        secondary_polling,
    ) = _distributional_shadow_t2_telemetry(run_dir, run_id, frames_by_id)
    context = _distributional_shadow_t2_model_replay_context()
    reference_bins = context["reference"]["bins"]

    status_counts: Counter[str] = Counter()
    action_frames: set[int] = set()
    action_estimates: dict[int, float] = {}
    action_nominals: dict[int, float] = {}
    action_byte_quanta: dict[int, int] = {}
    action_mpdu_profiles: dict[int, tuple[int, int, int]] = {}
    action_times: dict[int, int] = {}
    actions_by_time_bin = [0] * DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT
    reservation_by_time_bin = [0.0] * DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT
    actions_by_regime = [0] * DISTRIBUTIONAL_SHADOW_T2_REGIME_COUNT
    reservation_by_regime = [0.0] * DISTRIBUTIONAL_SHADOW_T2_REGIME_COUNT
    positive_rewards: list[float] = []
    finite_opportunity_costs: list[float] = []
    infinite_opportunity_costs = 0
    predicted_reward_launched = 0.0
    tail18_gain_launched = 0.0
    nominal_launched = 0.0
    reserved_launched = 0.0
    feature_evaluated_count = 0
    positive_reward_count = 0
    opportunity_passed_count = 0
    horizon_considered_count = 0
    horizon_admitted_count = 0
    launch_attempted_count = 0

    ledger_balance = DISTRIBUTIONAL_SHADOW_T2_INITIAL_CREDIT_US
    ledger_last_ns = PAIRED_VALUE_T2_DECISION_START_NS
    ledger_minimum_balance = ledger_balance
    ledger_generated_refill = 0.0
    ledger_discarded_refill = 0.0
    ledger_debited = 0.0
    ledger_debit_count = 0
    previous_meter_reserved_after: float | None = None
    congestion_sum = 0.0
    congestion_count = 0

    model_metadata = {
        "model_spec_id": "hgb64_depth3_7leaf_multiclass_v1",
        "selected_variant": "primary_secondary_hgb64",
        "feature_family": "primary_compact_physics_temporal_plus_passive_secondary",
        "feature_count": "308",
        "feature_adapter_id": DISTRIBUTIONAL_SHADOW_T2_FEATURE_ADAPTER,
        "runtime_contract_id": DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID,
        "runtime_contract_sha256": DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256,
    }
    model_columns = (
        "control_logits",
        "control_probabilities",
        "control_cdf",
        "full_copy_logits",
        "full_copy_probabilities",
        "full_copy_cdf",
        "deadline_rescue_reward",
        "tail18_cdf_gain",
        "reward_density_per_us",
        "opportunity_cost_per_us",
        "passes_opportunity_price",
    )

    for row in rows:
        frame_id = _integer(row, "frame_id", file_name)
        frame = frames_by_id[frame_id]
        primary = primary_samples[frame_id]
        secondary = secondary_samples[frame_id]
        primary_poll = primary_polling[frame_id]
        secondary_poll = secondary_polling[frame_id]
        _require(
            _integer(row, "schema_version", file_name) == 1
            and row["run_id"] == run_id
            and row["policy"] == DISTRIBUTIONAL_SHADOW_T2_POLICY
            and all(row[key] == value for key, value in model_metadata.items())
            and row["primary_path_id"] == "1"
            and row["primary_copy_id"] == "0"
            and row["secondary_path_id"] == "0"
            and row["secondary_copy_id"] == "1"
            and row["sample_stage"] == "T2"
            and row["sample_offset_us"] == "2000",
            f"{file_name}: frozen identity or model metadata differs",
        )

        generation_ns = _integer(row, "generation_time_ns", file_name)
        deadline_ns = _integer(row, "deadline_time_ns", file_name)
        sample_ns = _integer(row, "primary_sample_time_ns", file_name)
        _require(
            generation_ns // 1000 == int(frame["generation_time_us"])
            and deadline_ns == generation_ns + int(frame["deadline_us"]) * 1000
            and sample_ns == generation_ns + 2_000_000
            and _integer(row, "secondary_sample_time_ns", file_name) == sample_ns
            and row["generation_time_ns"] == primary["generation_time_ns"]
            == secondary["generation_time_ns"]
            and row["deadline_time_ns"] == primary["deadline_time_ns"]
            == secondary["deadline_time_ns"]
            and row["primary_sample_time_ns"] == primary["sample_time_ns"]
            and row["secondary_sample_time_ns"] == secondary["sample_time_ns"]
            and row["frame_type"] == frame["frame_type"] == primary["frame_type"]
            == secondary["frame_type"]
            and row["frame_size_bytes"] == frame["frame_size_bytes"]
            == primary["frame_size_bytes"] == secondary["frame_size_bytes"]
            and row["frame_packet_count"] == frame["packet_count"]
            == primary["frame_packet_count"] == secondary["frame_packet_count"]
            and row["primary_actionable"] == primary["actionable"],
            f"{file_name}: paired immutable telemetry differs",
        )
        _require(
            row["primary_feature_watermark_time_ns"]
            == primary["latest_feature_event_time_ns"]
            and row["primary_feature_watermark_sequence"]
            == primary["latest_feature_event_sequence"]
            and row["secondary_feature_watermark_time_ns"]
            == secondary["latest_feature_event_time_ns"]
            and row["secondary_feature_watermark_sequence"]
            == secondary["latest_feature_event_sequence"],
            f"{file_name}: paired watermark evidence differs",
        )
        _require(
            row["primary_current_poll_capture_time_ns"]
            == primary_poll["capture_time_ns"]
            and row["primary_current_poll_available_time_ns"]
            == primary_poll["available_time_ns"]
            and row["secondary_current_poll_capture_time_ns"]
            == secondary_poll["capture_time_ns"]
            and row["secondary_current_poll_available_time_ns"]
            == secondary_poll["available_time_ns"],
            f"{file_name}: current delayed polling evidence differs",
        )

        history_ready = frame_id >= 8
        _require(
            _flag(row, "history_ready", file_name) == history_ready,
            f"{file_name}: history-ready evidence differs",
        )
        for endpoint, reports in (
            ("primary", primary_polling),
            ("secondary", secondary_polling),
        ):
            for lag in (1, 3, 8):
                frame_key = f"{endpoint}_lag{lag}_frame_id"
                capture_key = f"{endpoint}_lag{lag}_poll_capture_time_ns"
                if frame_id >= lag:
                    expected_frame = frame_id - lag
                    _require(
                        row[frame_key] == str(expected_frame)
                        and row[capture_key]
                        == reports[expected_frame]["capture_time_ns"],
                        f"{file_name}: {endpoint} lag-{lag} evidence differs",
                    )
                else:
                    _require(
                        row[frame_key] == "" and row[capture_key] == "",
                        f"{file_name}: absent {endpoint} lag-{lag} is populated",
                    )

        inside = (
            PAIRED_VALUE_T2_DECISION_START_NS
            <= sample_ns
            < PAIRED_VALUE_T2_DECISION_STOP_NS
        )
        actionable = _flag(row, "primary_actionable", file_name)
        _require(
            row["decision_window_start_ns"] == str(PAIRED_VALUE_T2_DECISION_START_NS)
            and row["decision_window_stop_ns"] == str(PAIRED_VALUE_T2_DECISION_STOP_NS)
            and _flag(row, "inside_decision_window", file_name) == inside,
            f"{file_name}: decision-window evidence differs",
        )

        _require(
            sample_ns >= ledger_last_ns
            and sample_ns <= PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
            f"{file_name}: ledger time regressed",
        )
        elapsed_us = (sample_ns - ledger_last_ns) / 1000.0
        generated_us = PAIRED_VALUE_T2_GUARD_FRACTION * elapsed_us
        uncapped_us = ledger_balance + generated_us
        new_balance = min(
            DISTRIBUTIONAL_SHADOW_T2_POSITIVE_BALANCE_CAPACITY_US, uncapped_us
        )
        ledger_discarded_refill += max(0.0, uncapped_us - new_balance)
        ledger_generated_refill += generated_us
        ledger_balance = new_balance
        ledger_last_ns = sample_ns
        remaining_refill = PAIRED_VALUE_T2_GUARD_FRACTION * (
            (PAIRED_VALUE_T2_MEASUREMENT_STOP_NS - sample_ns) / 1000.0
        )
        repayable = ledger_balance + remaining_refill
        _require(
            row["credit_accounting_id"]
            == "permanent_canonical_reservation_borrow_repay_v1"
            and _paired_close(
                _number(row, "budget_fraction", file_name),
                PAIRED_VALUE_T2_GUARD_FRACTION,
            )
            and _paired_close(
                _number(row, "positive_balance_capacity_us", file_name),
                DISTRIBUTIONAL_SHADOW_T2_POSITIVE_BALANCE_CAPACITY_US,
            )
            and _paired_close(
                _number(row, "initial_credit_us", file_name),
                DISTRIBUTIONAL_SHADOW_T2_INITIAL_CREDIT_US,
            )
            and _integer(row, "repayment_stop_ns", file_name)
            == PAIRED_VALUE_T2_MEASUREMENT_STOP_NS
            and _paired_close(
                _signed_number(row, "ledger_balance_before_us", file_name),
                ledger_balance,
            )
            and _paired_close(
                _number(row, "ledger_debt_before_us", file_name),
                max(0.0, -ledger_balance),
            )
            and _paired_close(
                _number(row, "ledger_remaining_refill_before_us", file_name),
                remaining_refill,
            )
            and _paired_close(
                _number(row, "ledger_repayable_before_us", file_name), repayable
            )
            and _paired_close(
                _number(row, "ledger_debited_before_us", file_name), ledger_debited
            )
            and not _flag(row, "measured_settlement_refunds_ledger", file_name),
            f"{file_name}: permanent ledger snapshot differs",
        )

        congestion_should_update = inside and history_ready and actionable
        congestion_updated = _flag(row, "congestion_updated", file_name)
        _require(
            congestion_updated == congestion_should_update,
            f"{file_name}: congestion update gate differs",
        )
        time_bin: int | None = None
        regime: int | None = None
        if congestion_updated:
            current_busy = _number(
                primary_poll, "phy_busy_fraction_20ms", "prediction_polling_samples.csv"
            )
            congestion_sum += current_busy
            congestion_count += 1
            running_busy = congestion_sum / congestion_count
            time_bin = min(
                (sample_ns - PAIRED_VALUE_T2_DECISION_START_NS)
                // (DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_WIDTH_US * 1000),
                DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT - 1,
            )
            low, high = (
                float(value)
                for value in reference_bins[time_bin]["congestion_cutpoints"]
            )
            regime = 0 if running_busy < low else 1 if running_busy < high else 2
            _require(
                _paired_close(
                    _number(row, "current_primary_busy20ms", file_name), current_busy
                )
                and _paired_close(
                    _number(row, "running_primary_busy20ms", file_name), running_busy
                )
                and _integer(row, "congestion_observation_count", file_name)
                == congestion_count
                and _integer(row, "time_bin", file_name) == time_bin
                and _integer(row, "congestion_regime", file_name) == regime,
                f"{file_name}: congestion state differs",
            )
        else:
            _require(
                all(
                    row[key] == ""
                    for key in (
                        "current_primary_busy20ms",
                        "running_primary_busy20ms",
                        "congestion_observation_count",
                        "time_bin",
                        "congestion_regime",
                    )
                ),
                f"{file_name}: skipped congestion state is populated",
            )

        descriptor_should_be_checked = (
            congestion_should_update and row["frame_type"] == "P_FRAME"
        )
        descriptor_checked = _flag(row, "descriptor_checked", file_name)
        descriptor_available = _flag(row, "descriptor_available", file_name)
        _require(
            descriptor_checked == descriptor_should_be_checked
            and (descriptor_checked or not descriptor_available),
            f"{file_name}: descriptor gate order differs",
        )
        _require(
            row["canonical_cost_estimator_id"] == RANDOMIZED_COST_ESTIMATOR
            and _paired_close(_number(row, "cost_safety_factor", file_name), 1.25),
            f"{file_name}: canonical estimator metadata differs",
        )
        descriptor_fields = (
            "descriptor_frame_packet_count",
            "descriptor_packet_count",
            "descriptor_packet_indices",
            "descriptor_expected_mac_service_bytes",
            "descriptor_deadline_time_ns",
            "canonical_nominal_airtime_us",
            "canonical_reserved_airtime_us",
        )
        nominal: float | None = None
        reserved: float | None = None
        mpdu_bytes: int | None = None
        mpdu_profile: tuple[int, int, int] | None = None
        if descriptor_available:
            packet_count, indices, service_bytes, expected_nominal = (
                _canonical_full_copy_descriptor(frame)
            )
            nominal = _number(row, "canonical_nominal_airtime_us", file_name)
            reserved = _number(row, "canonical_reserved_airtime_us", file_name)
            _require(
                row["descriptor_frame_packet_count"] == str(packet_count)
                and row["descriptor_packet_count"] == str(packet_count)
                and row["descriptor_packet_indices"] == indices
                and row["descriptor_expected_mac_service_bytes"] == str(service_bytes)
                and row["descriptor_deadline_time_ns"] == str(deadline_ns)
                and _distributional_shadow_t2_descriptor_cost_matches_profile(
                    nominal,
                    reserved,
                    expected_nominal,
                    frame_profile,
                ),
                f"{file_name}: canonical descriptor or reservation differs",
            )
            mpdu_profile = _canonical_full_copy_mpdu_profile(frame)
            if mpdu_profile[0] == mpdu_profile[1]:
                mpdu_bytes = mpdu_profile[0]
        else:
            _require(
                all(row[field] == "" for field in descriptor_fields),
                f"{file_name}: unavailable descriptor has populated evidence",
            )

        feature_evaluated = _flag(row, "feature_evaluated", file_name)
        _require(
            feature_evaluated == descriptor_available,
            f"{file_name}: model evaluation bypassed ordered gates",
        )
        reward: float | None = None
        tail18_gain: float | None = None
        reward_density: float | None = None
        opportunity_cost: float | None = None
        passes_opportunity = False
        if feature_evaluated:
            assert time_bin is not None and regime is not None and reserved is not None
            feature_evaluated_count += 1
            features = _distributional_shadow_t2_feature_vector(
                frame_id,
                primary,
                primary_poll,
                {lag: primary_polling[frame_id - lag] for lag in (1, 3, 8)},
                secondary,
                secondary_poll,
                {lag: secondary_polling[frame_id - lag] for lag in (1, 3, 8)},
            )
            expected_model = _distributional_shadow_t2_model_result(features)
            observed_arrays = {
                "control_logits": _distributional_shadow_t2_array(
                    row, "control_logits", 6, file_name
                ),
                "control_probabilities": _distributional_shadow_t2_array(
                    row, "control_probabilities", 6, file_name
                ),
                "control_cdf": _distributional_shadow_t2_array(
                    row, "control_cdf", 5, file_name
                ),
                "full_copy_logits": _distributional_shadow_t2_array(
                    row, "full_copy_logits", 6, file_name
                ),
                "full_copy_probabilities": _distributional_shadow_t2_array(
                    row, "full_copy_probabilities", 6, file_name
                ),
                "full_copy_cdf": _distributional_shadow_t2_array(
                    row, "full_copy_cdf", 5, file_name
                ),
            }
            expected_arrays = {
                "control_logits": expected_model["control"]["logits"],
                "control_probabilities": expected_model["control"]["probabilities"],
                "control_cdf": expected_model["control"]["cdf"],
                "full_copy_logits": expected_model["full_copy"]["logits"],
                "full_copy_probabilities": expected_model["full_copy"]["probabilities"],
                "full_copy_cdf": expected_model["full_copy"]["cdf"],
            }
            _require(
                all(
                    _distributional_shadow_t2_values_close(
                        observed_arrays[key], expected_arrays[key]
                    )
                    for key in expected_arrays
                ),
                f"{file_name}: portable model replay differs for frame {frame_id}",
            )
            reward = _number(row, "deadline_rescue_reward", file_name)
            tail18_gain = _signed_number(row, "tail18_cdf_gain", file_name)
            reward_density = _number(row, "reward_density_per_us", file_name)
            _require(
                abs(reward - expected_model["deadline_rescue_reward"]) <= 5e-14
                and abs(tail18_gain - expected_model["tail18_cdf_gain"]) <= 5e-14
                and _paired_close(reward_density, reward / reserved),
                f"{file_name}: derived distributional reward differs",
            )
            if reward > 0:
                positive_reward_count += 1
                positive_rewards.append(reward)
                opportunity_cost = _distributional_shadow_t2_opportunity_cost(
                    time_bin, regime, repayable
                )
                observed_opportunity = _distributional_shadow_t2_extended_nonnegative(
                    row, "opportunity_cost_per_us", file_name
                )
                _require(
                    (math.isinf(opportunity_cost) and math.isinf(observed_opportunity))
                    or (
                        math.isfinite(opportunity_cost)
                        and observed_opportunity == opportunity_cost
                    ),
                    f"{file_name}: shadow-price replay differs",
                )
                if math.isfinite(opportunity_cost):
                    finite_opportunity_costs.append(opportunity_cost)
                else:
                    infinite_opportunity_costs += 1
                passes_opportunity = reward_density >= opportunity_cost
                _require(
                    _flag(row, "passes_opportunity_price", file_name)
                    == passes_opportunity,
                    f"{file_name}: opportunity-price gate differs",
                )
                opportunity_passed_count += int(passes_opportunity)
            else:
                _require(
                    row["opportunity_cost_per_us"] == ""
                    and not _flag(row, "passes_opportunity_price", file_name),
                    f"{file_name}: nonpositive reward reached the shadow gate",
                )
        else:
            _require(
                all(row[key] == "" for key in model_columns),
                f"{file_name}: skipped model evidence is populated",
            )

        considered = _flag(row, "horizon_admission_considered", file_name)
        admitted = _flag(row, "horizon_admitted", file_name)
        launch_attempted = _flag(row, "launch_attempted", file_name)
        launched = _flag(row, "secondary_launched", file_name)
        expected_considered = bool(feature_evaluated and reward is not None and reward > 0
                                   and passes_opportunity)
        expected_admitted = bool(
            expected_considered and reserved is not None and reserved <= repayable
        )
        _require(
            considered == expected_considered
            and admitted == expected_admitted
            and launch_attempted == admitted
            and (launch_attempted or not launched),
            f"{file_name}: horizon or launch gate differs",
        )
        horizon_considered_count += int(considered)
        horizon_admitted_count += int(admitted)
        launch_attempted_count += int(launch_attempted)

        meter_before = _number(row, "meter_reserved_before_us", file_name)
        meter_after = _number(row, "meter_reserved_after_us", file_name)
        if previous_meter_reserved_after is None:
            _require(
                _paired_close(meter_before, 0.0),
                f"{file_name}: initial meter reservation is nonzero",
            )
        else:
            _require(
                meter_before
                <= previous_meter_reserved_after
                + PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
                f"{file_name}: meter reservation increased between decisions",
            )
        if launched:
            assert (
                nominal is not None
                and reserved is not None
                and mpdu_profile is not None
                and reward is not None
                and tail18_gain is not None
                and time_bin is not None
                and regime is not None
            )
            ledger_balance -= reserved
            ledger_minimum_balance = min(ledger_minimum_balance, ledger_balance)
            ledger_debited += reserved
            ledger_debit_count += 1
            _require(
                ledger_balance >= -remaining_refill
                and _paired_close(meter_after, meter_before + reserved),
                f"{file_name}: action debit or meter reservation differs",
            )
            action_frames.add(frame_id)
            action_estimates[frame_id] = reserved
            action_nominals[frame_id] = nominal
            action_mpdu_profiles[frame_id] = mpdu_profile
            if mpdu_bytes is not None:
                action_byte_quanta[frame_id] = mpdu_bytes
            action_times[frame_id] = sample_ns
            nominal_launched += nominal
            reserved_launched += reserved
            predicted_reward_launched += reward
            tail18_gain_launched += tail18_gain
            actions_by_time_bin[time_bin] += 1
            reservation_by_time_bin[time_bin] += reserved
            actions_by_regime[regime] += 1
            reservation_by_regime[regime] += reserved
        else:
            _require(
                _paired_close(meter_after, meter_before),
                f"{file_name}: rejection changed the meter reservation",
            )
        previous_meter_reserved_after = meter_after
        _require(
            _paired_close(
                _signed_number(row, "ledger_balance_after_us", file_name),
                ledger_balance,
            )
            and _paired_close(
                _number(row, "ledger_debt_after_us", file_name),
                max(0.0, -ledger_balance),
            )
            and _paired_close(
                _number(row, "ledger_debited_after_us", file_name), ledger_debited
            ),
            f"{file_name}: post-decision ledger differs",
        )

        if not inside:
            expected_status = "outside_decision_window"
        elif not history_ready:
            expected_status = "history_warmup"
        elif not actionable:
            expected_status = "not_actionable"
        elif row["frame_type"] != "P_FRAME":
            expected_status = "frame_type_restricted"
        elif not descriptor_available:
            expected_status = "descriptor_unavailable"
        elif reward is not None and not (reward > 0):
            expected_status = "nonpositive_reward"
        elif not passes_opportunity:
            expected_status = "opportunity_price_rejected"
        elif not admitted:
            expected_status = "horizon_credit_rejected"
        elif not launched:
            expected_status = "launch_rejected"
        else:
            expected_status = "action"
        _require(
            row["decision_status"] == expected_status
            and expected_status in DISTRIBUTIONAL_SHADOW_T2_STATUSES,
            f"{file_name}: ordered decision status differs",
        )
        status_counts[expected_status] += 1

    settlement_rows = _csv(
        run_dir / "secondary_airtime_settlements.csv",
        SECONDARY_AIRTIME_SETTLEMENT_COLUMNS,
    )
    settlement_times = {
        _integer(row, "frame_id", "secondary_airtime_settlements.csv"):
        _integer(row, "settlement_time_ns", "secondary_airtime_settlements.csv")
        for row in settlement_rows
    }
    _require(
        set(settlement_times) == action_frames,
        f"{file_name}: settlement/action identity differs",
    )
    action_dirty_count = 0
    for row in rows:
        sample_ns = _integer(row, "primary_sample_time_ns", file_name)
        feature_evaluated = _flag(row, "feature_evaluated", file_name)
        earlier_unsettled = _distributional_shadow_t2_expected_unsettled(
            sample_ns,
            feature_evaluated,
            action_times,
            settlement_times,
        )
        _require(
            _integer(row, "earlier_unsettled_launches", file_name)
            == earlier_unsettled
            and _flag(row, "secondary_state_action_dirty", file_name)
            == (earlier_unsettled != 0),
            f"{file_name}: action-dirty secondary diagnostic differs",
        )
        action_dirty_count += int(feature_evaluated and earlier_unsettled != 0)

    _require(
        action_frames == duplicated_frame_ids,
        f"{file_name}: launches do not match duplicated frames",
    )
    policy_action_frames = {
        _integer(row, "frame_id", "policy_decisions.csv")
        for row in policy_decisions
        if _flag(row, "duplicated", "policy_decisions.csv")
    }
    _require(
        policy_action_frames == action_frames
        and all(
            row["policy"] == DISTRIBUTIONAL_SHADOW_T2_POLICY
            and row["primary_link"] == "1"
            and (row["secondary_link"] == "0")
            == _flag(row, "duplicated", "policy_decisions.csv")
            for row in policy_decisions
        )
        and all(
            frame["policy"] == DISTRIBUTIONAL_SHADOW_T2_POLICY
            and frame["primary_link"] == "1"
            for frame in frames
        ),
        "distributional-shadow final frame/action evidence differs",
    )
    _require(
        len(rows) == sum(status_counts[status] for status in DISTRIBUTIONAL_SHADOW_T2_STATUSES)
        and feature_evaluated_count
        == status_counts["nonpositive_reward"]
        + status_counts["opportunity_price_rejected"]
        + status_counts["horizon_credit_rejected"]
        + status_counts["launch_rejected"]
        + status_counts["action"]
        and positive_reward_count
        == status_counts["opportunity_price_rejected"]
        + status_counts["horizon_credit_rejected"]
        + status_counts["launch_rejected"]
        + status_counts["action"]
        and opportunity_passed_count
        == status_counts["horizon_credit_rejected"]
        + status_counts["launch_rejected"]
        + status_counts["action"]
        and horizon_considered_count == opportunity_passed_count
        and horizon_admitted_count
        == status_counts["launch_rejected"] + status_counts["action"]
        and launch_attempted_count == horizon_admitted_count
        and len(action_frames) == status_counts["action"],
        f"{file_name}: reconstructed counts do not reconcile",
    )

    balance_after_last_decision = ledger_balance
    debt_after_last_decision = max(0.0, -ledger_balance)
    remaining_after_last_decision = PAIRED_VALUE_T2_GUARD_FRACTION * (
        (PAIRED_VALUE_T2_MEASUREMENT_STOP_NS - ledger_last_ns) / 1000.0
    )
    repayable_after_last_decision = ledger_balance + remaining_after_last_decision
    final_uncapped = ledger_balance + remaining_after_last_decision
    final_balance = min(
        DISTRIBUTIONAL_SHADOW_T2_POSITIVE_BALANCE_CAPACITY_US, final_uncapped
    )
    final_discarded_refill = ledger_discarded_refill + max(
        0.0,
        final_uncapped - DISTRIBUTIONAL_SHADOW_T2_POSITIVE_BALANCE_CAPACITY_US,
    )
    final_generated_refill = ledger_generated_refill + remaining_after_last_decision
    _require(
        final_balance >= 0,
        f"{file_name}: ledger does not repay by measurement stop",
    )
    return {
        "rows": rows,
        "status_counts": status_counts,
        "action_frames": action_frames,
        "action_estimates": action_estimates,
        "action_nominals": action_nominals,
        "action_byte_quanta": action_byte_quanta,
        "action_mpdu_profiles": action_mpdu_profiles,
        "maximum_observed_debt": max(0.0, -ledger_minimum_balance),
        "feature_evaluated": feature_evaluated_count,
        "positive_reward": positive_reward_count,
        "opportunity_passed": opportunity_passed_count,
        "horizon_considered": horizon_considered_count,
        "horizon_admitted": horizon_admitted_count,
        "launch_attempted": launch_attempted_count,
        "congestion_observations": congestion_count,
        "action_dirty_scored": action_dirty_count,
        "positive_rewards": positive_rewards,
        "finite_opportunity_costs": finite_opportunity_costs,
        "infinite_opportunity_costs": infinite_opportunity_costs,
        "predicted_reward_launched": predicted_reward_launched,
        "tail18_gain_launched": tail18_gain_launched,
        "nominal_launched": nominal_launched,
        "reserved_launched": reserved_launched,
        "actions_by_time_bin": actions_by_time_bin,
        "reservation_by_time_bin": reservation_by_time_bin,
        "actions_by_regime": actions_by_regime,
        "reservation_by_regime": reservation_by_regime,
        "ledger": {
            "balance_after_last_decision_us": balance_after_last_decision,
            "debt_after_last_decision_us": debt_after_last_decision,
            "remaining_refill_after_last_decision_us": remaining_after_last_decision,
            "repayable_after_last_decision_us": repayable_after_last_decision,
            "minimum_balance_us": ledger_minimum_balance,
            "maximum_debt_us": max(0.0, -ledger_minimum_balance),
            "permanent_debited_us": ledger_debited,
            "permanent_debit_count": ledger_debit_count,
            "generated_refill_before_finalize_us": ledger_generated_refill,
            "discarded_refill_before_finalize_us": ledger_discarded_refill,
            "generated_refill_at_stop_us": final_generated_refill,
            "discarded_refill_at_stop_us": final_discarded_refill,
            "final_balance_us": final_balance,
            "repayment_closed": True,
        },
    }


def _replay_paired_value_t2_guard(
    decision_rows: list[dict[str, str]],
    events: list[dict[str, str]],
    profile: dict[str, Any],
) -> None:
    """Reconstruct measured-airtime guard balance from independent PHY events."""
    decisions_by_time: dict[int, list[dict[str, str]]] = {}
    events_by_time: dict[int, list[dict[str, str]]] = {}
    for row in decision_rows:
        decisions_by_time.setdefault(
            _integer(row, "primary_sample_time_ns", "paired_value_t2_decisions.csv"), []
        ).append(row)
    for row in events:
        events_by_time.setdefault(
            _integer(row, "time_ns", "secondary_airtime_events.csv"), []
        ).append(row)

    balance = PAIRED_VALUE_T2_GUARD_INITIAL_CREDIT_US
    last_refill_ns = PAIRED_VALUE_T2_DECISION_START_NS
    for time_ns in sorted(set(decisions_by_time) | set(events_by_time)):
        if time_ns >= PAIRED_VALUE_T2_DECISION_START_NS:
            refill_time = min(time_ns, PAIRED_VALUE_T2_MEASUREMENT_STOP_NS)
            _require(refill_time >= last_refill_ns,
                     "paired-value guard replay: causal time regressed")
            balance = min(
                profile["guard_capacity_us"],
                balance + PAIRED_VALUE_T2_GUARD_FRACTION *
                ((refill_time - last_refill_ns) / 1000.0),
            )
            last_refill_ns = refill_time
        event_debit = sum(
            _number(row, "ppdu_duration_us", "secondary_airtime_events.csv")
            for row in events_by_time.get(time_ns, [])
        )
        for row in decisions_by_time.get(time_ns, []):
            observed = _signed_number(
                row, "guard_balance_before_us", "paired_value_t2_decisions.csv"
            )
            # Independent files do not preserve callback order for equal-time
            # events.  A decision may therefore expose the balance immediately
            # before or after all same-time PPDU callbacks, but no value between.
            valid = _paired_close(observed, balance)
            if event_debit:
                valid = valid or _paired_close(observed, balance - event_debit)
            _require(valid,
                     "paired_value_t2_decisions.csv: guard balance differs from PHY replay")
        balance -= event_debit


def _paired_value_t2_event_frames(row: dict[str, str]) -> tuple[int, ...]:
    """Parse the distinct frame IDs attributed to one secondary PPDU."""
    tokens = row.get("frame_ids", "").split(";")
    _require(tokens and all(re.fullmatch(r"[0-9]+", token) for token in tokens),
             "secondary airtime events: invalid frame_ids")
    frame_ids = tuple(int(token) for token in tokens)
    _require(len(frame_ids) == len(set(frame_ids)) and
             frame_ids == tuple(sorted(frame_ids)),
             "secondary airtime events: frame IDs are repeated or not canonical")
    return frame_ids


def _secondary_airtime_binary64(text: str, field: str) -> tuple[int, float]:
    """Decode one exact binary64 bit pattern from a V2 event row."""
    _require(
        isinstance(text, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)", text),
        f"secondary airtime events: invalid {field}",
    )
    try:
        bits = int(text)
    except (TypeError, ValueError) as error:
        raise ValidationError(
            f"secondary airtime events: invalid {field}"
        ) from error
    _require(
        0 <= bits < 2**64,
        f"secondary airtime events: invalid {field}",
    )
    value = struct.unpack(">d", bits.to_bytes(8, "big"))[0]
    _require(
        math.isfinite(value) and value > 0,
        f"secondary airtime events: nonpositive {field}",
    )
    return bits, value


def _secondary_airtime_v2_event_evidence(
    row: dict[str, str],
    frame_ids: tuple[int, ...],
    tagged_bytes: int,
    serialized_duration: float,
) -> tuple[dict[int, int], dict[int, float], float]:
    """Validate and decode an exact per-frame V2 PPDU allocation."""
    duration_bits, exact_duration = _secondary_airtime_binary64(
        row.get("ppdu_duration_binary64_bits", ""),
        "ppdu_duration_binary64_bits",
    )
    _require(
        serialized_duration == float(format(exact_duration, ".12g")),
        "secondary airtime events: duration bits differ from serialized duration",
    )
    byte_tokens = row.get("frame_tagged_mpdu_bytes", "").split(";")
    allocation_tokens = row.get(
        "frame_allocated_airtime_binary64_bits", ""
    ).split(";")
    _require(
        len(byte_tokens) == len(frame_ids)
        and len(allocation_tokens) == len(frame_ids)
        and all(re.fullmatch(r"[0-9]+", token) for token in byte_tokens)
        and all(re.fullmatch(r"[0-9]+", token) for token in allocation_tokens),
        "secondary airtime events: V2 frame evidence width differs",
    )
    frame_bytes = {
        frame_id: int(token)
        for frame_id, token in zip(frame_ids, byte_tokens, strict=True)
    }
    _require(
        all(value > 0 for value in frame_bytes.values())
        and sum(frame_bytes.values()) == tagged_bytes,
        "secondary airtime events: V2 frame bytes do not reconcile",
    )
    allocation_bits: dict[int, int] = {}
    frame_allocations: dict[int, float] = {}
    for frame_id, token in zip(frame_ids, allocation_tokens, strict=True):
        bits, value = _secondary_airtime_binary64(
            token,
            "frame_allocated_airtime_binary64_bits",
        )
        allocation_bits[frame_id] = bits
        frame_allocations[frame_id] = value

    reconstructed_sum = 0.0
    for index, frame_id in enumerate(frame_ids):
        expected = (
            exact_duration
            * float(frame_bytes[frame_id])
            / float(tagged_bytes)
        )
        if index + 1 == len(frame_ids):
            expected = exact_duration - reconstructed_sum
        reconstructed_sum += expected
        expected_bits = struct.unpack(">Q", struct.pack(">d", expected))[0]
        _require(
            expected_bits == allocation_bits[frame_id],
            "secondary airtime events: V2 allocation differs from byte split",
        )
    _require(
        struct.unpack(">Q", struct.pack(">d", exact_duration))[0]
        == duration_bits,
        "secondary airtime events: V2 duration is not bit stable",
    )
    return frame_bytes, frame_allocations, exact_duration


def _paired_value_t2_direct_meter_checkpoints(
    checkpoints: list[dict[str, Any]],
    event_records: list[dict[str, Any]],
    settlement_records: dict[int, dict[str, Any]],
    action_estimates: dict[int, float],
) -> None:
    """Replay V2 meter state directly from exact per-frame allocations."""
    measured_by_frame = {frame_id: 0.0 for frame_id in settlement_records}
    allocation_times: dict[int, list[int]] = {
        frame_id: [] for frame_id in settlement_records
    }
    allocation_prefixes: dict[int, list[float]] = {
        frame_id: [0.0] for frame_id in settlement_records
    }
    for event in event_records:
        allocations = event["frame_allocations"]
        for frame_id in event["frame_ids"]:
            allocated = allocations[frame_id]
            measured_by_frame[frame_id] += allocated
            allocation_times[frame_id].append(int(event["time"]))
            allocation_prefixes[frame_id].append(
                allocation_prefixes[frame_id][-1] + allocated
            )
    for frame_id, measured in measured_by_frame.items():
        record = settlement_records[frame_id]
        observed = float(record["measured"])
        tolerance = (
            PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
            + _paired_meter_quantization_us(str(record["measured_text"]))
        )
        _require(
            abs(observed - measured) <= tolerance,
            "paired-value meter V2 settlement differs from exact event replay",
        )

    for checkpoint in checkpoints:
        expected = 0.0
        for frame_id in checkpoint["active_frames"]:
            visible_count = bisect.bisect_left(
                allocation_times[frame_id], int(checkpoint["time"])
            )
            allocated = allocation_prefixes[frame_id][visible_count]
            expected += max(action_estimates[frame_id] - allocated, 0.0)
        _require(
            _paired_close(float(checkpoint["observed"]), expected),
            "paired-value meter V2 checkpoint differs from exact event replay",
        )


def _validate_paired_value_t2_meter_checkpoints(
    decision_rows: list[dict[str, str]],
    event_records: list[dict[str, Any]],
    settlement_records: dict[int, dict[str, Any]],
    launches: dict[int, int],
    action_estimates: dict[int, float],
    action_byte_quanta: dict[int, int] | None = None,
    action_mpdu_profiles: dict[int, tuple[int, int, int]] | None = None,
) -> None:
    """Prove one latent PPDU split can satisfy every meter checkpoint.

    The V1 event schema records each PPDU's total tagged bytes and participating
    frame IDs, but not the per-frame byte split used by the meter.  Consequently
    an arbitrary feasible final-marginal max-flow is not an exact replay of an
    intermediate reservation checkpoint.  This mixed-integer feasibility model
    keeps the byte split latent and jointly constrains all event totals, final
    frame settlements, and decision-time outstanding reservations.
    """
    decision_file = "paired_value_t2_decisions.csv"
    checkpoints: list[dict[str, Any]] = []
    for row in decision_rows:
        time_ns = _integer(row, "primary_sample_time_ns", decision_file)
        active_frames = tuple(sorted(
            frame_id
            for frame_id, launch_time in launches.items()
            if launch_time < time_ns <= int(settlement_records[frame_id]["time"])
        ))
        observed_before = _number(row, "meter_reserved_before_us", decision_file)
        if active_frames:
            checkpoints.append({
                "time": time_ns,
                "observed": observed_before,
                "active_frames": active_frames,
            })
        else:
            _require(_paired_close(observed_before, 0.0),
                     "paired-value decision has a reservation without an active frame")

    if not event_records:
        _require(
            all(_paired_close(float(record["measured"]), 0.0)
                for record in settlement_records.values()),
            "secondary airtime settlement is positive without a PPDU event",
        )
        return

    v2_events = ["frame_allocations" in event for event in event_records]
    _require(
        all(v2_events) or not any(v2_events),
        "secondary airtime events: mixed V1 and V2 evidence",
    )
    if all(v2_events):
        _paired_value_t2_direct_meter_checkpoints(
            checkpoints,
            event_records,
            settlement_records,
            action_estimates,
        )
        return

    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError as error:
        raise ValidationError(
            f"paired-value meter replay: cannot load feasibility solver: {error}"
        ) from error

    variable_mpdu_mode = False
    if action_mpdu_profiles is not None:
        _require(
            set(action_mpdu_profiles) == set(launches)
            and all(
                full_bytes > 0
                and final_bytes > 0
                and packet_count > 0
                for full_bytes, final_bytes, packet_count
                in action_mpdu_profiles.values()
            ),
            "paired-value meter replay: action MPDU profile differs",
        )
        variable_mpdu_mode = any(
            full_bytes != final_bytes
            for full_bytes, final_bytes, _ in action_mpdu_profiles.values()
        )

    byte_quantum = 1
    if action_byte_quanta is not None:
        _require(
            set(action_byte_quanta) == set(launches)
            and len(set(action_byte_quanta.values())) == 1,
            "paired-value meter replay: MPDU byte quantum differs across actions",
        )
        byte_quantum = next(iter(action_byte_quanta.values()))
        _require(
            byte_quantum > 0
            and all(
                int(event["tagged_bytes"]) % byte_quantum == 0
                and int(event["tagged_bytes"]) // byte_quantum
                >= len(event["frame_ids"])
                for event in event_records
            ),
            "paired-value meter replay: tagged bytes violate the MPDU quantum",
        )
    _require(
        not variable_mpdu_mode or action_byte_quanta is None,
        "paired-value meter replay: variable MPDUs also declare one byte quantum",
    )

    # Events with the same serialized duration, tagged-byte total, frame set,
    # and checkpoint visibility are observationally indistinguishable.  Model
    # their per-frame allocation-unit totals as one group.  Any positive-
    # integer group allocation can be decomposed into positive per-event
    # allocations by a transportation construction, so this preserves the
    # feasible evidence set while removing repeated PPDU variables.
    event_group_index: dict[tuple[Any, ...], int] = {}
    event_groups: list[dict[str, Any]] = []
    for event in event_records:
        visibility = tuple(
            int(event["time"]) < int(checkpoint["time"])
            for checkpoint in checkpoints
        )
        key = (
            (event["index"],)
            if variable_mpdu_mode
            else (
                event["duration_text"],
                event["tagged_bytes"],
                event["frame_ids"],
                visibility,
            )
        )
        group_index = event_group_index.get(key)
        if group_index is None:
            group_index = len(event_groups)
            event_group_index[key] = group_index
            event_groups.append({
                "count": 1,
                "duration": event["duration"],
                "duration_text": event["duration_text"],
                "tagged_bytes": event["tagged_bytes"],
                "frame_ids": event["frame_ids"],
                "visibility": visibility,
            })
        else:
            event_groups[group_index]["count"] += 1

    # A single-frame group has no latent allocation.  For a shared group,
    # retain only the first n - 1 integer allocation totals and derive the
    # final total as count * tagged_units minus their sum.
    if variable_mpdu_mode:
        byte_edges = [
            (group_index, frame_id, packet_kind)
            for group_index, group in enumerate(event_groups)
            for frame_id in group["frame_ids"]
            for packet_kind in ("full", "final")
        ]
    else:
        byte_edges = [
            (group_index, frame_id)
            for group_index, group in enumerate(event_groups)
            for frame_id in group["frame_ids"][:-1]
        ]
    byte_index = {
        edge: index for index, edge in enumerate(byte_edges)
    }
    checkpoint_pairs = [
        (checkpoint_index, frame_id)
        for checkpoint_index, checkpoint in enumerate(checkpoints)
        for frame_id in checkpoint["active_frames"]
    ]
    byte_count = len(byte_edges)
    pair_count = len(checkpoint_pairs)
    variable_count = byte_count + 2 * pair_count
    remaining_start = byte_count
    unsaturated_start = byte_count + pair_count

    rows: list[Any] = []
    lower_constraints: list[float] = []
    upper_constraints: list[float] = []
    integer_solver_rows: dict[int, dict[int, Fraction]] = {}

    def add_constraint(coefficients: Any, lower: float, upper: float) -> None:
        rows.append(coefficients)
        lower_constraints.append(lower)
        upper_constraints.append(upper)

    variable_lower = np.zeros(variable_count, dtype=np.float64)
    variable_upper = np.full(variable_count, np.inf, dtype=np.float64)
    if variable_mpdu_mode:
        assert action_mpdu_profiles is not None
        for edge_index, (_, frame_id, packet_kind) in enumerate(byte_edges):
            _, _, packet_count = action_mpdu_profiles[frame_id]
            variable_upper[edge_index] = float(
                packet_count - 1 if packet_kind == "full" else 1
            )
        for group_index, group in enumerate(event_groups):
            _require(
                group["count"] == 1,
                "paired-value meter replay: variable-MPDU events were grouped",
            )
            byte_total = np.zeros(variable_count, dtype=np.float64)
            for frame_id in group["frame_ids"]:
                full_bytes, final_bytes, packet_count = action_mpdu_profiles[
                    frame_id
                ]
                packet_total = np.zeros(variable_count, dtype=np.float64)
                packet_total[byte_index[(group_index, frame_id, "full")]] = 1.0
                packet_total[byte_index[(group_index, frame_id, "final")]] = 1.0
                add_constraint(packet_total, 1.0, float(packet_count))
                byte_total[
                    byte_index[(group_index, frame_id, "full")]
                ] = float(full_bytes)
                byte_total[
                    byte_index[(group_index, frame_id, "final")]
                ] = float(final_bytes)
            tagged_bytes = float(group["tagged_bytes"])
            add_constraint(byte_total, tagged_bytes, tagged_bytes)
    else:
        for edge_index, (group_index, _) in enumerate(byte_edges):
            group = event_groups[group_index]
            count = int(group["count"])
            frame_count = len(group["frame_ids"])
            tagged_units = int(group["tagged_bytes"]) // byte_quantum
            variable_lower[edge_index] = float(count)
            variable_upper[edge_index] = float(
                count * (tagged_units - frame_count + 1)
            )

        for group_index, group in enumerate(event_groups):
            if len(group["frame_ids"]) == 1:
                continue
            coefficients = np.zeros(variable_count, dtype=np.float64)
            for frame_id in group["frame_ids"][:-1]:
                coefficients[byte_index[(group_index, frame_id)]] = 1.0
            # Each retained total leaves one allocation unit per contributing
            # event for its frame.  This does the same for the derived final frame.
            add_constraint(
                coefficients,
                -np.inf,
                float(
                    group["count"]
                    * (int(group["tagged_bytes"]) // byte_quantum - 1)
                ),
            )

    def allocation_expression(group_index: int, frame_id: int) -> tuple[Any, float, float]:
        """Return grouped C++ allocation coefficients, constant, and error."""
        group = event_groups[group_index]
        duration = float(group["duration"])
        frames = group["frame_ids"]
        count = int(group["count"])
        coefficients = np.zeros(variable_count, dtype=np.float64)
        constant = 0.0
        if variable_mpdu_mode:
            assert action_mpdu_profiles is not None
            full_bytes, final_bytes, _ = action_mpdu_profiles[frame_id]
            tagged_bytes = float(group["tagged_bytes"])
            coefficients[
                byte_index[(group_index, frame_id, "full")]
            ] = duration * full_bytes / tagged_bytes
            coefficients[
                byte_index[(group_index, frame_id, "final")]
            ] = duration * final_bytes / tagged_bytes
        else:
            tagged_units = float(int(group["tagged_bytes"]) // byte_quantum)
            if frame_id != frames[-1]:
                coefficients[byte_index[(group_index, frame_id)]] = \
                    duration / tagged_units
            else:
                constant = count * duration
                for previous_frame in frames[:-1]:
                    coefficients[byte_index[(group_index, previous_frame)]] -= \
                        duration / tagged_units
        # The serialized duration hides at most half a 12-digit unit.  Cover
        # that scale error plus the ordered binary64 multiply/sum residual.
        uncertainty = (
            count * (
                _paired_meter_quantization_us(str(group["duration_text"]))
                + (len(frames) + 2) * math.ulp(duration)
            )
        )
        return coefficients, constant, uncertainty

    def rational_allocation_expression(
        group_index: int,
        frame_id: int,
    ) -> dict[int, Fraction]:
        """Return exact center coefficients from serialized event durations."""
        group = event_groups[group_index]
        frames = group["frame_ids"]
        coefficients: dict[int, Fraction] = {}
        if variable_mpdu_mode:
            assert action_mpdu_profiles is not None
            full_bytes, final_bytes, _ = action_mpdu_profiles[frame_id]
            rate = Fraction(str(group["duration_text"])) / int(
                group["tagged_bytes"]
            )
            coefficients[
                byte_index[(group_index, frame_id, "full")]
            ] = rate * full_bytes
            coefficients[
                byte_index[(group_index, frame_id, "final")]
            ] = rate * final_bytes
        else:
            tagged_units = int(group["tagged_bytes"]) // byte_quantum
            rate = Fraction(str(group["duration_text"])) / tagged_units
            if frame_id != frames[-1]:
                coefficients[byte_index[(group_index, frame_id)]] = rate
            else:
                for previous_frame in frames[:-1]:
                    coefficients[byte_index[(group_index, previous_frame)]] = -rate
        return coefficients

    for frame_id, settlement in settlement_records.items():
        coefficients = np.zeros(variable_count, dtype=np.float64)
        constant = 0.0
        allocation_uncertainty = 0.0
        for group_index, group in enumerate(event_groups):
            if frame_id in group["frame_ids"]:
                event_coefficients, event_constant, event_uncertainty = \
                    allocation_expression(group_index, frame_id)
                coefficients += event_coefficients
                constant += event_constant
                allocation_uncertainty += event_uncertainty
        measured = float(settlement["measured"])
        quantization = _paired_meter_quantization_us(
            str(settlement["measured_text"])
        )
        row_index = len(rows)
        add_constraint(
            coefficients,
            measured - quantization - allocation_uncertainty - constant,
            measured + quantization + allocation_uncertainty - constant,
        )
        rational_coefficients: dict[int, Fraction] = {}
        for group_index, group in enumerate(event_groups):
            if frame_id not in group["frame_ids"]:
                continue
            for variable_index, value in rational_allocation_expression(
                group_index, frame_id
            ).items():
                rational_coefficients[variable_index] = (
                    rational_coefficients.get(variable_index, Fraction()) + value
                )
        rational_coefficients = {
            variable_index: value
            for variable_index, value in rational_coefficients.items()
            if value
        }
        # Variable-tail profiles combine event rates with unrelated byte
        # denominators, so their exact common lattice can exceed binary64.
        # Keep those rows in their original scale and independently replay the
        # rounded integer witness against the 1e-9 us envelope below.
        if rational_coefficients and not variable_mpdu_mode:
            integer_solver_rows[row_index] = rational_coefficients

    pairs_by_checkpoint: dict[int, list[int]] = {}
    pairs_by_frame: dict[int, list[int]] = {}
    for pair_index, (checkpoint_index, frame_id) in enumerate(checkpoint_pairs):
        pairs_by_checkpoint.setdefault(checkpoint_index, []).append(pair_index)
        pairs_by_frame.setdefault(frame_id, []).append(pair_index)
        checkpoint = checkpoints[checkpoint_index]
        cumulative = np.zeros(variable_count, dtype=np.float64)
        cumulative_constant = 0.0
        cumulative_uncertainty = 0.0
        for group_index, group in enumerate(event_groups):
            if (group["visibility"][checkpoint_index]
                    and frame_id in group["frame_ids"]):
                event_coefficients, event_constant, event_uncertainty = \
                    allocation_expression(group_index, frame_id)
                cumulative += event_coefficients
                cumulative_constant += event_constant
                cumulative_uncertainty += event_uncertainty

        remaining_index = remaining_start + pair_index
        unsaturated_index = unsaturated_start + pair_index
        reservation = action_estimates[frame_id]
        measured = float(settlement_records[frame_id]["measured"])
        measured_quantization = _paired_meter_quantization_us(
            str(settlement_records[frame_id]["measured_text"])
        )
        big_m = (
            max(reservation, measured + measured_quantization)
            + PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
        )
        variable_upper[remaining_index] = reservation
        variable_upper[unsaturated_index] = 1.0

        # remaining = max(reservation - cumulative allocation, 0).
        coefficients = cumulative.copy()
        coefficients[remaining_index] += 1.0
        add_constraint(
            coefficients,
            reservation - cumulative_constant - cumulative_uncertainty,
            np.inf,
        )

        coefficients = cumulative.copy()
        coefficients[remaining_index] += 1.0
        coefficients[unsaturated_index] += big_m
        add_constraint(
            coefficients,
            -np.inf,
            reservation + big_m - cumulative_constant + cumulative_uncertainty,
        )

        coefficients = np.zeros(variable_count, dtype=np.float64)
        coefficients[remaining_index] = 1.0
        coefficients[unsaturated_index] = -reservation
        add_constraint(coefficients, -np.inf, 0.0)

        coefficients = cumulative.copy()
        coefficients[unsaturated_index] += big_m
        add_constraint(
            coefficients,
            -np.inf,
            reservation + big_m - cumulative_constant + cumulative_uncertainty,
        )

        coefficients = cumulative.copy()
        coefficients[unsaturated_index] += reservation
        add_constraint(
            coefficients,
            reservation - cumulative_constant - cumulative_uncertainty,
            np.inf,
        )

    for checkpoint_index, checkpoint in enumerate(checkpoints):
        coefficients = np.zeros(variable_count, dtype=np.float64)
        for pair_index in pairs_by_checkpoint[checkpoint_index]:
            coefficients[remaining_start + pair_index] = 1.0
        observed = float(checkpoint["observed"])
        add_constraint(
            coefficients,
            observed - PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
            observed + PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
        )

    # A frame may move from unsaturated to saturated, never back again.
    for pair_indices in pairs_by_frame.values():
        for previous, current in zip(pair_indices, pair_indices[1:]):
            coefficients = np.zeros(variable_count, dtype=np.float64)
            coefficients[unsaturated_start + current] = 1.0
            coefficients[unsaturated_start + previous] = -1.0
            add_constraint(coefficients, -np.inf, 0.0)

    matrix = np.asarray(rows, dtype=np.float64)
    integrality = np.zeros(variable_count, dtype=np.uint8)
    integrality[:byte_count] = 1
    integrality[unsaturated_start:] = 1
    if variable_count == 0:
        lower_array = np.asarray(lower_constraints, dtype=np.float64)
        upper_array = np.asarray(upper_constraints, dtype=np.float64)
        tolerance = PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
        _require(
            bool(np.all(lower_array <= tolerance))
            and bool(np.all(upper_array >= -tolerance)),
            "paired-value fixed event allocation violates the accounting envelope",
        )
        return
    lower_array = np.asarray(lower_constraints, dtype=np.float64)
    upper_array = np.asarray(upper_constraints, dtype=np.float64)
    solver_matrix = matrix.copy()
    solver_lower = lower_array.copy()
    solver_upper = upper_array.copy()
    # A binary64 MILP cannot reliably distinguish the serialized settlement
    # envelopes (around 1e-9 us) from feasibility tolerance when the byte
    # coefficients are around 1e-1 us.  Convert those rows to equivalent
    # integer lattices using the exact decimal event durations.  Bounds are
    # rounded inward to attainable integer activities.  The eventual witness
    # is still replayed against the original floating-point rows below.
    for row_index, rational_coefficients in integer_solver_rows.items():
        denominator = math.lcm(*(
            coefficient.denominator
            for coefficient in rational_coefficients.values()
        ))
        integer_coefficients = {
            variable_index: int(coefficient * denominator)
            for variable_index, coefficient in rational_coefficients.items()
        }
        divisor = math.gcd(*(
            abs(coefficient) for coefficient in integer_coefficients.values()
        ))
        scaled_lower = math.nextafter(
            lower_array[row_index] * denominator, -math.inf
        )
        scaled_upper = math.nextafter(
            upper_array[row_index] * denominator, math.inf
        )
        attainable_lower = math.ceil(scaled_lower / divisor)
        attainable_upper = math.floor(scaled_upper / divisor)
        _require(
            attainable_lower <= attainable_upper,
            "paired-value settlement has no feasible event allocation",
        )
        normalized = np.zeros(variable_count, dtype=np.float64)
        for variable_index, coefficient in integer_coefficients.items():
            normalized[variable_index] = coefficient // divisor
        maximum_activity = max(
            abs(attainable_lower),
            abs(attainable_upper),
            *(abs(int(value)) for value in normalized),
        )
        _require(
            maximum_activity <= 2**53,
            "paired-value settlement integer normalization exceeds binary64",
        )
        solver_matrix[row_index] = normalized
        solver_lower[row_index] = float(attainable_lower)
        solver_upper[row_index] = float(attainable_upper)
    # Every nonzero row joins all variables that it touches.  Solve the
    # resulting independent components separately: PPDU/frame clusters do not
    # share latent byte totals, and forcing them into one MILP made HiGHS 1.2
    # spend its time on irrelevant cross-products.  Disable presolve because
    # that version can incorrectly call exact integer-lattice rows infeasible.
    parent = list(range(variable_count))

    def find_root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(left: int, right: int) -> None:
        left_root = find_root(left)
        right_root = find_root(right)
        if left_root != right_root:
            parent[right_root] = left_root

    row_variables: list[Any] = []
    for row in solver_matrix:
        indices = np.flatnonzero(row)
        row_variables.append(indices)
        for index in indices[1:]:
            join(int(indices[0]), int(index))

    component_variables: dict[int, list[int]] = {}
    for variable_index in range(variable_count):
        component_variables.setdefault(find_root(variable_index), []).append(
            variable_index
        )
    component_rows: dict[int, list[int]] = {
        root: [] for root in component_variables
    }
    tolerance = PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
    for row_index, indices in enumerate(row_variables):
        if len(indices) == 0:
            _require(
                solver_lower[row_index] <= tolerance
                and solver_upper[row_index] >= -tolerance,
                "paired-value fixed event allocation violates the accounting envelope",
            )
            continue
        component_rows[find_root(int(indices[0]))].append(row_index)

    witness = np.zeros(variable_count, dtype=np.float64)
    try:
        for root, variable_indices_list in component_variables.items():
            variable_indices = np.asarray(variable_indices_list, dtype=np.int64)
            row_indices = np.asarray(component_rows[root], dtype=np.int64)
            if len(row_indices) == 0:
                witness[variable_indices] = variable_lower[variable_indices]
                continue
            component_matrix = solver_matrix[np.ix_(
                row_indices, variable_indices
            )].copy()
            component_lower = solver_lower[row_indices].copy()
            component_upper = solver_upper[row_indices].copy()
            # Keep coefficients near unity and finite right-hand sides near
            # 1e5.  Integer-lattice rows retain a unit step well above HiGHS'
            # primal tolerance for the frozen paired-T2 evidence ranges.
            for component_row in range(len(row_indices)):
                scale = max(
                    float(np.max(np.abs(component_matrix[component_row]))),
                    1.0,
                )
                if math.isfinite(component_lower[component_row]):
                    scale = max(
                        scale, abs(component_lower[component_row]) / 100_000.0
                    )
                if math.isfinite(component_upper[component_row]):
                    scale = max(
                        scale, abs(component_upper[component_row]) / 100_000.0
                    )
                component_matrix[component_row] /= scale
                if math.isfinite(component_lower[component_row]):
                    component_lower[component_row] /= scale
                if math.isfinite(component_upper[component_row]):
                    component_upper[component_row] /= scale
            def solve_component(*, presolve: bool) -> Any:
                """Solve one representation with an independent time budget."""
                return milp(
                    np.zeros(len(variable_indices), dtype=np.float64),
                    integrality=integrality[variable_indices],
                    bounds=Bounds(
                        variable_lower[variable_indices],
                        variable_upper[variable_indices],
                    ),
                    constraints=LinearConstraint(
                        component_matrix,
                        component_lower,
                        component_upper,
                    ),
                    options={
                        "presolve": presolve,
                        "time_limit": PAIRED_VALUE_T2_MILP_ATTEMPT_TIME_LIMIT_S,
                        "mip_rel_gap": 0.0,
                    },
                )

            result = solve_component(presolve=False)
            if not result.success or result.x is None:
                # HiGHS 1.2 has returned a false infeasible result for a
                # feasible 186-variable component with presolve disabled.
                # Other exact-lattice fixtures require presolve to remain
                # disabled.  Try both representations and accept only a
                # witness that passes the independent exact replay below.
                result = solve_component(presolve=True)
            _require(
                result.success and result.x is not None,
                "paired-value reservation checkpoints have no feasible "
                "event allocation",
            )
            witness[variable_indices] = result.x
    except (TypeError, ValueError, FloatingPointError) as error:
        raise ValidationError(
            f"paired-value meter replay: feasibility solver failed: {error}"
        ) from error
    integer_indices = np.flatnonzero(integrality)
    rounded = np.rint(witness[integer_indices])
    # HiGHS may return an integer-feasible solution with visible postsolve
    # representation error (observed at 2.6e-7 bytes with SciPy 1.11.4).  The
    # solver representation is not evidence; the rounded integer assignment
    # is.  Round it unconditionally, then fail closed by independently checking
    # every variable bound and linear constraint below against the validator's
    # 1e-9-us accounting envelope.
    witness[integer_indices] = rounded
    # Reconstruct the continuous and binary checkpoint variables from the
    # integer byte witness.  This removes the MIP solver's harmless continuous
    # row slack before independently checking the declared 1e-9 envelopes.
    for pair_index, (checkpoint_index, frame_id) in enumerate(checkpoint_pairs):
        checkpoint = checkpoints[checkpoint_index]
        cumulative = 0.0
        for group_index, group in enumerate(event_groups):
            if (group["visibility"][checkpoint_index]
                    and frame_id in group["frame_ids"]):
                coefficients, constant, _ = allocation_expression(
                    group_index, frame_id
                )
                cumulative += float(coefficients @ witness) + constant
        reservation = action_estimates[frame_id]
        witness[remaining_start + pair_index] = max(reservation - cumulative, 0.0)
        witness[unsaturated_start + pair_index] = float(cumulative <= reservation)
    activities = matrix @ witness
    tolerance = PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
    lower_violation = float(np.max(np.maximum(lower_array - activities, 0.0)))
    upper_violation = float(np.max(np.maximum(activities - upper_array, 0.0)))
    bound_violation = float(max(
        np.max(np.maximum(variable_lower - witness, 0.0)),
        np.max(np.maximum(witness - variable_upper, 0.0)),
    ))
    _require(
        bound_violation <= tolerance
        and lower_violation <= tolerance
        and upper_violation <= tolerance,
        "paired-value meter solver witness violates the accounting envelope "
        f"(bounds={bound_violation:.17g}, lower={lower_violation:.17g}, "
        f"upper={upper_violation:.17g})",
    )


def _replay_paired_value_t2_meter(
    decision_rows: list[dict[str, str]],
    events: list[dict[str, str]],
    settlements: list[dict[str, str]],
    action_estimates: dict[int, float],
    action_nominals: dict[int, float],
    action_byte_quanta: dict[int, int] | None = None,
    action_mpdu_profiles: dict[int, tuple[int, int, int]] | None = None,
) -> None:
    """Reconstruct paired action causality and outstanding meter reservations."""
    decision_file = "paired_value_t2_decisions.csv"
    event_file = "secondary_airtime_events.csv"
    settlement_file = "secondary_airtime_settlements.csv"
    launches = {
        _integer(row, "frame_id", decision_file):
        _integer(row, "primary_sample_time_ns", decision_file)
        for row in decision_rows
        if _flag(row, "secondary_launched", decision_file)
    }
    _require(set(launches) == set(action_estimates) == set(action_nominals),
             "paired-value meter replay: action evidence differs")
    if action_byte_quanta is not None:
        _require(
            set(action_byte_quanta) == set(launches)
            and len(set(action_byte_quanta.values())) == 1
            and all(value > 0 for value in action_byte_quanta.values()),
            "paired-value meter replay: MPDU byte quantum differs across actions",
        )
    if action_mpdu_profiles is not None:
        _require(
            set(action_mpdu_profiles) == set(launches)
            and all(
                full_bytes > 0
                and final_bytes > 0
                and packet_count > 0
                for full_bytes, final_bytes, packet_count
                in action_mpdu_profiles.values()
            ),
            "paired-value meter replay: action MPDU profile differs",
        )

    settlement_records: dict[int, dict[str, Any]] = {}
    for row in settlements:
        frame_id = _integer(row, "frame_id", settlement_file)
        _require(frame_id in launches and frame_id not in settlement_records,
                 "secondary airtime settlement references an unlaunched frame")
        settlement_time = _integer(row, "settlement_time_ns", settlement_file)
        _require(launches[frame_id] <= settlement_time <=
                 PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
                 "secondary airtime settlement precedes its action launch")
        nominal = _number(row, "nominal_airtime_us", settlement_file)
        # The meter CSV is written with 12 significant digits.  Compare its
        # canonical rendering instead of adding a magnitude-scaled tolerance.
        expected_nominal = float(format(action_nominals[frame_id], ".12g"))
        _require(nominal == expected_nominal,
                 "secondary airtime settlement nominal differs from its action")
        settlement_records[frame_id] = {
            "time": settlement_time,
            "released": _number(row, "released_airtime_us", settlement_file),
            "measured": _number(row, "measured_airtime_us", settlement_file),
            "released_text": row["released_airtime_us"],
            "measured_text": row["measured_airtime_us"],
        }
    _require(set(settlement_records) == set(launches),
             "secondary airtime settlements do not match paired-value actions")

    event_records: list[dict[str, Any]] = []
    previous_event_time = -1
    for event_index, row in enumerate(events):
        event_time = _integer(row, "time_ns", event_file)
        _require(
            event_time >= previous_event_time,
            "secondary airtime events: rows are not chronological",
        )
        previous_event_time = event_time
        frame_ids = _paired_value_t2_event_frames(row)
        duration = _number(row, "ppdu_duration_us", event_file)
        tagged_bytes = _integer(row, "tagged_mpdu_bytes", event_file)
        _require(duration == float(format(duration, ".12g")),
                 "secondary airtime event duration is not canonical 12-digit output")
        _require(tagged_bytes >= len(frame_ids),
                 "secondary airtime event cannot assign one byte to every frame")
        for frame_id in frame_ids:
            _require(frame_id in launches,
                     "secondary airtime event references an unlaunched frame")
            _require(launches[frame_id] <= event_time <=
                     settlement_records[frame_id]["time"],
                     "secondary airtime event is outside its frame's active interval")
        event_record = {
            "index": event_index,
            "time": event_time,
            "duration": duration,
            "duration_text": row["ppdu_duration_us"],
            "tagged_bytes": tagged_bytes,
            "frame_ids": frame_ids,
        }
        v2_fields = (
            "ppdu_duration_binary64_bits",
            "frame_tagged_mpdu_bytes",
            "frame_allocated_airtime_binary64_bits",
        )
        present_v2_fields = [field in row for field in v2_fields]
        _require(
            all(present_v2_fields) or not any(present_v2_fields),
            "secondary airtime events: incomplete V2 event schema",
        )
        if all(present_v2_fields):
            frame_bytes, frame_allocations, exact_duration = (
                _secondary_airtime_v2_event_evidence(
                    row,
                    frame_ids,
                    tagged_bytes,
                    duration,
                )
            )
            if action_byte_quanta is not None:
                _require(
                    all(
                        value % action_byte_quanta[frame_id] == 0
                        for frame_id, value in frame_bytes.items()
                    ),
                    "paired-value meter replay: V2 frame bytes violate the MPDU quantum",
                )
            if action_mpdu_profiles is not None:
                for frame_id, value in frame_bytes.items():
                    full_bytes, final_bytes, packet_count = action_mpdu_profiles[
                        frame_id
                    ]
                    representable = False
                    for final_count in (0, 1):
                        remainder = value - final_count * final_bytes
                        if remainder < 0 or remainder % full_bytes:
                            continue
                        full_count = remainder // full_bytes
                        if (
                            full_count <= packet_count - 1
                            and 1 <= full_count + final_count <= packet_count
                        ):
                            representable = True
                    _require(
                        representable,
                        "paired-value meter replay: V2 frame bytes violate the MPDU profile",
                    )
            event_record.update({
                "duration": exact_duration,
                "frame_bytes": frame_bytes,
                "frame_allocations": frame_allocations,
            })
        event_records.append(event_record)

    _validate_paired_value_t2_meter_checkpoints(
        decision_rows,
        event_records,
        settlement_records,
        launches,
        action_estimates,
        action_byte_quanta,
        action_mpdu_profiles,
    )
    for frame_id, record in settlement_records.items():
        measured_text = str(record["measured_text"])
        released_text = str(record["released_text"])
        released = float(record["released"])
        measured = float(record["measured"])
        _require(
            released == float(format(released, ".12g"))
            and measured == float(format(measured, ".12g"))
            and abs(max(action_estimates[frame_id] - measured, 0.0) - released)
            <= PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
            + _paired_meter_quantization_us(measured_text)
            + _paired_meter_quantization_us(released_text),
            "secondary airtime settlement release differs from replay",
        )


def _validate_paired_value_t2_summary(
    run_dir: Path,
    run_id: str,
    frame_count: int,
    evidence: dict[str, Any],
    events: list[dict[str, str]],
    settlements: list[dict[str, str]],
    meter_summary: dict[str, Any],
) -> None:
    """Validate exact controller summary keys and reconstruct every total."""
    profile = evidence["profile"]
    summary = _json(run_dir / "paired_value_t2_summary.json")
    top_keys = {
        "schema_version", "run_id", "policy", "runtime_contract_id",
        "runtime_contract_sha256", "source_artifacts", "model", "telemetry",
        "decision_window", "budget_guard", "counts", "airtime", "integrity",
    }
    _require(set(summary) == top_keys,
             "paired_value_t2_summary.json: top-level key set differs")
    _require(isinstance(summary.get("schema_version"), int) and
             not isinstance(summary.get("schema_version"), bool) and
             summary.get("schema_version") == profile["summary_schema_version"] and
             summary.get("run_id") == run_id and
             summary.get("policy") == PAIRED_VALUE_T2_POLICY and
             summary.get("runtime_contract_id") == profile["runtime_contract_id"] and
             summary.get("runtime_contract_sha256") ==
             profile["runtime_contract_sha256"],
             "paired_value_t2_summary.json: scalar identity differs")
    expected_sources = {
        "frozen_selection": {
            "path": "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json",
            "sha256": "c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f",
        },
        "canonical_fit_manifest": {
            "path": (
                "results/randomized_full_copy_exploration_collection_v1/"
                "temporal_t2_primary_only_two_objective_v1/artifact_manifest.json"
            ),
            "sha256": "b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f",
        },
        "canonical_model_pickle": {
            "path": (
                "results/randomized_full_copy_exploration_collection_v1/"
                "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_models.pkl"
            ),
            "sha256": PAIRED_VALUE_T2_MODEL_ARTIFACT_SHA256,
        },
        "canonical_candidates": {
            "path": (
                "results/randomized_full_copy_exploration_collection_v1/"
                "temporal_t2_primary_only_two_objective_v1/"
                "temporal_t2_value_policy_candidates.csv"
            ),
            "sha256": "7cbd5c622838df0a2f752c3bf9f4c54f333f7d280a9240cb80eda19efb1c28bb",
        },
        "canonical_metrics": {
            "path": (
                "results/randomized_full_copy_exploration_collection_v1/"
                "temporal_t2_primary_only_two_objective_v1/"
                "temporal_t2_value_training_metrics.json"
            ),
            "sha256": "35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014",
        },
    }
    _require(summary.get("source_artifacts") == expected_sources,
             "paired_value_t2_summary.json: source artifact closure differs")
    expected_model = {
        "model_spec_id": "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1",
        "artifact_sha256": PAIRED_VALUE_T2_MODEL_ARTIFACT_SHA256,
        "feature_family": "primary_compact_physics_temporal",
        "feature_count": 246,
        "feature_adapter_id": "finite_numeric_float32_then_float64_one_hot_v1",
        "ordered_feature_names_sha256": PAIRED_VALUE_T2_FEATURE_NAMES_SHA256,
        "ranker": profile["model_metadata"]["ranker"],
        "frame_gate": "p_frames_only",
        "score_adapter_id": "final_candidate_float32_threshold_ge_v1",
        "score_threshold_float32": profile["score_threshold"],
        "score_threshold_float32_bits_hex": (
            "0x3e3f68cf" if profile["cost_free"] else "0x38bbc0e5"
        ),
    }
    model = summary.get("model")
    _require(isinstance(model, dict) and set(model) == set(expected_model),
             "paired_value_t2_summary.json: model key set differs")
    _require(isinstance(model.get("feature_count"), int) and
             not isinstance(model.get("feature_count"), bool),
             "paired_value_t2_summary.json: model.feature_count is not an integer")
    for key, expected in expected_model.items():
        if key == "score_threshold_float32":
            value = model.get(key)
            _require(isinstance(value, (int, float)) and not isinstance(value, bool) and
                     struct.pack(">f", _float32(float(value), "paired summary model")) ==
                     profile["score_threshold_bits"].to_bytes(4, "big"),
                     "paired_value_t2_summary.json: model threshold differs")
        else:
            _require(model.get(key) == expected,
                     f"paired_value_t2_summary.json: model.{key} differs")

    expected_telemetry = {
        "telemetry_schema_version": 3,
        "polling_schema_version": 1,
        "feature_support_mask_version": 2,
        "primary_required_support_mask_hex": "0x3ffffffffdffff",
        "sample_offsets_us": [0, 2000],
        "history_windows_us": [1000, 5000, 20000],
        "polling_interval_us": 1000,
        "polling_report_delay_us": 1000,
        "raw_prediction_event_log_enabled": False,
        "oracle_features_enabled": False,
    }
    expected_window = {
        "measurement_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
        "measurement_stop_ns": PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
        "decision_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
        "decision_stop_ns": PAIRED_VALUE_T2_DECISION_STOP_NS,
        "interval_semantics": "half_open",
        "decision_stop_guard_us": 534000,
    }
    expected_guard = {
        "canonical_estimator_id": RANDOMIZED_COST_ESTIMATOR,
        "cost_safety_factor": 1.25,
        "fraction": PAIRED_VALUE_T2_GUARD_FRACTION,
        "max_horizon_us": profile["guard_max_horizon_us"],
        "initial_horizon_us": 2_000_000,
        "capacity_us": profile["guard_capacity_us"],
        "initial_credit_us": 12_000,
        "initialization_time_ns": PAIRED_VALUE_T2_DECISION_START_NS,
        "accounting_absolute_tolerance_us": PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
    }
    if profile["score_aware"]:
        expected_guard.update({
            "admission_profile_id": profile["admission_profile_id"],
            "emergency_score_threshold_float32":
                profile["emergency_score_threshold"],
            "emergency_score_threshold_float32_bits_hex": (
                "0x3e9d2ac5" if profile["cost_free"] else "0x391d4952"
            ),
            "emergency_maximum_debt_us": 60_000,
        })
    if profile["remaining_refill"]:
        expected_guard.update({
            "remaining_refill_borrowing_enabled": True,
            "remaining_refill_repayment_stop_ns":
                PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
            "remaining_refill_credit_formula": (
                "fraction * (repayment_stop_ns - "
                "last_causal_guard_refill_ns) / 1000"
            ),
        })
    for key, expected in (
        ("telemetry", expected_telemetry),
        ("decision_window", expected_window),
        ("budget_guard", expected_guard),
    ):
        actual = summary.get(key)
        _require(isinstance(actual, dict) and
                 _canonical_json_sha256(actual, f"paired summary {key}") ==
                 _canonical_json_sha256(expected, f"expected paired summary {key}"),
                 f"paired_value_t2_summary.json: {key} differs")

    count_keys = {
        "generated_frames", "paired_t2_frames", *PAIRED_VALUE_T2_STATUSES[:-1],
        "feature_evaluated", "score_threshold_passed", "launch_attempted",
        "secondary_launched", "secondary_settled",
    }
    if profile["score_aware"]:
        count_keys.update({
            "strict_guard_admitted",
            "emergency_score_threshold_passed",
            "emergency_admission_considered",
            "emergency_admitted",
        })
    if profile["remaining_refill"]:
        count_keys.update({
            "remaining_refill_admission_considered",
            "remaining_refill_admitted",
        })
    # ACTION is serialized as secondary_launched, not as a separate count key.
    counts = summary.get("counts")
    _require(isinstance(counts, dict) and set(counts) == count_keys,
             "paired_value_t2_summary.json: counts key set differs")
    _require(all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                 for value in counts.values()),
             "paired_value_t2_summary.json: count is not a nonnegative integer")
    status_counts: Counter[str] = evidence["status_counts"]
    expected_counts = {
        "generated_frames": frame_count,
        "paired_t2_frames": frame_count,
        "outside_decision_window": status_counts["outside_decision_window"],
        "history_warmup": status_counts["history_warmup"],
        "frame_type_restricted": status_counts["frame_type_restricted"],
        "not_actionable": status_counts["not_actionable"],
        "descriptor_unavailable": status_counts["descriptor_unavailable"],
        "feature_evaluated": sum(
            _flag(row, "feature_evaluated", "paired_value_t2_decisions.csv")
            for row in evidence["rows"]
        ),
        "below_score_threshold": status_counts["below_score_threshold"],
        "score_threshold_passed": evidence["score_passed"],
        "airtime_guard_rejected": status_counts["airtime_guard_rejected"],
        "launch_attempted": evidence["launch_attempted"],
        "launch_rejected": status_counts["launch_rejected"],
        "secondary_launched": len(evidence["action_frames"]),
        "secondary_settled": len(settlements),
    }
    if profile["score_aware"]:
        expected_counts.update({
            "strict_guard_admitted": evidence["strict_guard_admitted"],
            "emergency_score_threshold_passed": evidence["emergency_score_passed"],
            "emergency_admission_considered":
                evidence["emergency_admission_considered"],
            "emergency_admitted": evidence["emergency_admitted"],
        })
    if profile["remaining_refill"]:
        expected_counts.update({
            "remaining_refill_admission_considered":
                evidence["remaining_refill_admission_considered"],
            "remaining_refill_admitted": evidence["remaining_refill_admitted"],
        })
    _require(counts == expected_counts,
             "paired_value_t2_summary.json: counts differ from reconstructed rows")
    _require(frame_count == sum(status_counts[status]
                                for status in PAIRED_VALUE_T2_STATUSES) and
             expected_counts["feature_evaluated"] ==
             status_counts["below_score_threshold"] + evidence["score_passed"] and
             evidence["score_passed"] ==
             status_counts["airtime_guard_rejected"] +
             status_counts["launch_rejected"] + status_counts["action"] and
             evidence["launch_attempted"] ==
             status_counts["launch_rejected"] + status_counts["action"] and
             (not profile["score_aware"] or
              evidence["strict_guard_admitted"] + evidence["emergency_admitted"] +
              evidence["remaining_refill_admitted"] ==
              evidence["launch_attempted"]) and
             len(evidence["action_frames"]) == status_counts["action"] == len(settlements),
             "paired_value_t2_summary.json: reconstructed counts do not reconcile")

    airtime_keys = {
        "learned_predicted_cost_sum_evaluated_us",
        "learned_predicted_cost_sum_launched_us",
        "canonical_nominal_launched_sum_us",
        "canonical_reserved_launched_sum_us",
        "measured_secondary_airtime_debited_us",
    }
    airtime = summary.get("airtime")
    _require(isinstance(airtime, dict) and set(airtime) == airtime_keys,
             "paired_value_t2_summary.json: airtime key set differs")
    measured_total = sum(_number(
        row, "ppdu_duration_us", "secondary_airtime_events.csv"
    ) for row in events)
    serialized_event_durations = [row["ppdu_duration_us"] for row in events]
    for key, expected in {
        "learned_predicted_cost_sum_evaluated_us": evidence["learned_evaluated"],
        "learned_predicted_cost_sum_launched_us": evidence["learned_launched"],
        "canonical_nominal_launched_sum_us": evidence["nominal_launched"],
        "canonical_reserved_launched_sum_us": evidence["reserved_launched"],
        "measured_secondary_airtime_debited_us": measured_total,
    }.items():
        value = airtime.get(key)
        matches = (
            _paired_meter_sum_close(float(value), serialized_event_durations)
            if key == "measured_secondary_airtime_debited_us" and
            isinstance(value, (int, float)) and not isinstance(value, bool)
            else isinstance(value, (int, float)) and not isinstance(value, bool) and
            _paired_close(float(value), expected)
        )
        _require(isinstance(value, (int, float)) and not isinstance(value, bool) and
                 math.isfinite(float(value)) and float(value) >= 0 and
                 matches,
                 f"paired_value_t2_summary.json: airtime.{key} differs")
    _require(_paired_meter_close(
                 _summary_number(meter_summary, "estimated_action_airtime_us"),
                 float(airtime["canonical_reserved_launched_sum_us"]),
             ) and
             _paired_meter_close(
                 _summary_number(meter_summary, "tagged_secondary_tx_airtime_us"),
                 float(airtime["measured_secondary_airtime_debited_us"]),
             ),
             "paired_value_t2_summary.json: airtime differs from meter summary")

    expected_integrity = {
        "pending_pair_empty": True,
        "generated_equals_paired": True,
        "status_counts_reconcile": True,
        "launches_equal_settlements": True,
        "launched_frame_ids_equal_duplicated_frame_ids": True,
        "meter_reserved_final_within_tolerance": True,
        "meter_reserved_final_normalized_us": 0,
        "learned_cost_used_for_token_accounting": False,
    }
    if profile["remaining_refill"]:
        expected_integrity[
            "strict_plus_emergency_plus_remaining_refill_admitted_equals_"
            "launch_attempted"
        ] = True
    elif profile["score_aware"]:
        expected_integrity[
            "strict_plus_emergency_admitted_equals_launch_attempted"
        ] = True
    integrity = summary.get("integrity")
    _require(isinstance(integrity, dict) and
             set(integrity) == set(expected_integrity) | {"meter_reserved_final_raw_us"} and
             all(integrity.get(key) == value for key, value in expected_integrity.items()),
             "paired_value_t2_summary.json: integrity evidence differs")
    _require(all(isinstance(integrity.get(key), bool)
                 for key in expected_integrity
                 if key != "meter_reserved_final_normalized_us") and
             isinstance(integrity.get("meter_reserved_final_normalized_us"), int) and
             not isinstance(integrity.get("meter_reserved_final_normalized_us"), bool),
             "paired_value_t2_summary.json: integrity value type differs")
    raw_reserved = integrity.get("meter_reserved_final_raw_us")
    _require(isinstance(raw_reserved, (int, float)) and not isinstance(raw_reserved, bool) and
             math.isfinite(float(raw_reserved)) and
             abs(float(raw_reserved)) <= PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
             "paired_value_t2_summary.json: final raw reservation exceeds tolerance")
    settlement_ids = {
        _integer(row, "frame_id", "secondary_airtime_settlements.csv")
        for row in settlements
    }
    _require(settlement_ids == evidence["action_frames"],
             "paired_value_t2_summary.json: settlements do not match actions")
    _replay_paired_value_t2_meter(
        evidence["rows"],
        events,
        settlements,
        evidence["action_estimates"],
        evidence["action_nominals"],
        evidence["action_byte_quanta"] or None,
        evidence["action_mpdu_profiles"],
    )
    _replay_paired_value_t2_guard(evidence["rows"], events, profile)


def _distributional_shadow_t2_distribution(values: list[float]) -> dict[str, Any]:
    """Reconstruct the controller's nearest-rank diagnostic distribution."""
    if not values:
        return {
            "finite_count": 0,
            "minimum": None,
            "p50": None,
            "p90": None,
            "p99": None,
            "maximum": None,
            "mean": None,
        }
    ordered = sorted(values)

    def nearest_rank(probability: float) -> float:
        rank = math.ceil(probability * len(ordered))
        return ordered[max(0, min(rank - 1, len(ordered) - 1))]

    return {
        "finite_count": len(ordered),
        "minimum": ordered[0],
        "p50": nearest_rank(0.50),
        "p90": nearest_rank(0.90),
        "p99": nearest_rank(0.99),
        "maximum": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def _validate_distributional_shadow_t2_summary(
    run_dir: Path,
    run_id: str,
    frame_count: int,
    evidence: dict[str, Any],
    events: list[dict[str, str]],
    settlements: list[dict[str, str]],
    meter_summary: dict[str, Any],
) -> None:
    """Reconstruct every distributional controller summary field."""
    file_name = "distributional_shadow_t2_summary.json"
    summary = _json(run_dir / file_name)
    expected_top_keys = {
        "schema_version",
        "run_id",
        "policy",
        "runtime_contract_id",
        "runtime_contract_sha256",
        "evidence_status",
        "source_artifacts",
        "model",
        "telemetry",
        "decision_window",
        "allocator",
        "counts",
        "allocation_by_time_bin",
        "allocation_by_congestion_regime",
        "prediction_diagnostics",
        "ledger",
        "airtime",
        "integrity",
    }
    _require(
        set(summary) == expected_top_keys
        and summary.get("schema_version") == 1
        and summary.get("run_id") == run_id
        and summary.get("policy") == DISTRIBUTIONAL_SHADOW_T2_POLICY
        and summary.get("runtime_contract_id") == DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID
        and summary.get("runtime_contract_sha256")
        == DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256
        and summary.get("evidence_status")
        == "deployment_refit_and_in_sample_construction_sanity",
        f"{file_name}: top-level identity differs",
    )
    expected_sources = {
        "training_git_commit": "49c74528289e7dfd8881cbe1f65ea9293abe3ca6",
        "source_model_pickle_sha256": (
            "60c181eb75faafde57f65a63f71a31cee99050a54afa53edb162a9ac7a1ec6e0"
        ),
        "source_model_json_sha256": DISTRIBUTIONAL_SHADOW_T2_SOURCE_MODEL_SHA256,
        "source_reference_json_sha256": (
            DISTRIBUTIONAL_SHADOW_T2_SOURCE_REFERENCE_SHA256
        ),
        "source_metrics_sha256": (
            "ffa32fdb852f4296a9f548666a946b9da7048d475f648679deb6bcc72cf8c9ab"
        ),
        "source_manifest_sha256": (
            "20207dacbbb44dc638c3674d61c56596e1e2092920e3a95fb391cb0dd6d05b89"
        ),
        "exporter_sha256": DISTRIBUTIONAL_SHADOW_T2_EXPORTER_SHA256,
        "portable_model_sha256": DISTRIBUTIONAL_SHADOW_T2_PORTABLE_MODEL_SHA256,
        "deployment_reference_sha256": (
            DISTRIBUTIONAL_SHADOW_T2_DEPLOYMENT_REFERENCE_SHA256
        ),
        "feature_contract_sha256": DISTRIBUTIONAL_SHADOW_T2_FEATURE_CONTRACT_SHA256,
    }
    expected_model = {
        "selected_variant": "primary_secondary_hgb64",
        "model_spec_id": "hgb64_depth3_7leaf_multiclass_v1",
        "feature_family": "primary_compact_physics_temporal_plus_passive_secondary",
        "feature_count": 308,
        "feature_adapter_id": DISTRIBUTIONAL_SHADOW_T2_FEATURE_ADAPTER,
        "objective": "deadline_rescue",
        "frame_gate": "p_frames_only",
    }
    expected_telemetry = {
        "telemetry_schema_version": 3,
        "polling_schema_version": 1,
        "feature_support_mask_version": 2,
        "required_support_mask_hex": "0x3ffffffffdffff",
        "sample_offsets_us": [0, 2000],
        "history_windows_us": [1000, 5000, 20000],
        "polling_interval_us": 1000,
        "polling_report_delay_us": 1000,
        "raw_prediction_event_log_enabled": False,
        "oracle_features_enabled": False,
    }
    expected_window = {
        "measurement_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
        "measurement_stop_ns": PAIRED_VALUE_T2_MEASUREMENT_STOP_NS,
        "decision_start_ns": PAIRED_VALUE_T2_DECISION_START_NS,
        "decision_stop_ns": PAIRED_VALUE_T2_DECISION_STOP_NS,
        "interval_semantics": "half_open",
    }
    expected_allocator = {
        "shadow_reference_id": "full_refit_congestion_tertile_5s_finite_horizon_v1",
        "congestion_signal_id": "causal_running_mean_primary_phy_busy_fraction_20ms",
        "time_bin_width_us": DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_WIDTH_US,
        "time_bin_count": DISTRIBUTIONAL_SHADOW_T2_TIME_BIN_COUNT,
        "congestion_regime_count": DISTRIBUTIONAL_SHADOW_T2_REGIME_COUNT,
        "canonical_estimator_id": RANDOMIZED_COST_ESTIMATOR,
        "canonical_p_frame_reservation_us": (
            DISTRIBUTIONAL_SHADOW_T2_CANONICAL_RESERVATION_US
        ),
        "cost_safety_factor": 1.25,
        "credit_accounting_id": "permanent_canonical_reservation_borrow_repay_v1",
        "refill_fraction": PAIRED_VALUE_T2_GUARD_FRACTION,
        "positive_balance_capacity_us": (
            DISTRIBUTIONAL_SHADOW_T2_POSITIVE_BALANCE_CAPACITY_US
        ),
        "initial_credit_us": DISTRIBUTIONAL_SHADOW_T2_INITIAL_CREDIT_US,
        "maximum_generated_credit_us": 372000,
        "negative_balance_allowed_when_repayable": True,
        "accepted_reservation_is_permanent": True,
        "measured_settlement_refunds_ledger": False,
    }
    for key, expected in (
        ("source_artifacts", expected_sources),
        ("model", expected_model),
        ("telemetry", expected_telemetry),
        ("decision_window", expected_window),
        ("allocator", expected_allocator),
    ):
        _require(
            summary.get(key) == expected,
            f"{file_name}: {key} differs",
        )

    status_counts: Counter[str] = evidence["status_counts"]
    expected_counts = {
        "generated_frames": frame_count,
        "paired_t2_frames": frame_count,
        "outside_decision_window": status_counts["outside_decision_window"],
        "history_warmup": status_counts["history_warmup"],
        "not_actionable": status_counts["not_actionable"],
        "frame_type_restricted": status_counts["frame_type_restricted"],
        "descriptor_unavailable": status_counts["descriptor_unavailable"],
        "feature_evaluated": evidence["feature_evaluated"],
        "nonpositive_reward": status_counts["nonpositive_reward"],
        "positive_reward": evidence["positive_reward"],
        "opportunity_price_rejected": status_counts["opportunity_price_rejected"],
        "opportunity_price_passed": evidence["opportunity_passed"],
        "horizon_credit_rejected": status_counts["horizon_credit_rejected"],
        "horizon_admission_considered": evidence["horizon_considered"],
        "horizon_admitted": evidence["horizon_admitted"],
        "launch_attempted": evidence["launch_attempted"],
        "launch_rejected": status_counts["launch_rejected"],
        "secondary_launched": len(evidence["action_frames"]),
        "secondary_settled": len(settlements),
        "congestion_observations": evidence["congestion_observations"],
        "action_dirty_scored_decisions": evidence["action_dirty_scored"],
    }
    _require(
        summary.get("counts") == expected_counts,
        f"{file_name}: counts differ from replay",
    )

    def validate_allocation(
        key: str,
        expected_actions: list[int],
        expected_reservations: list[float],
    ) -> None:
        actual = summary.get(key)
        _require(
            isinstance(actual, dict)
            and set(actual) == {"actions", "canonical_reservation_us"}
            and actual.get("actions") == expected_actions
            and isinstance(actual.get("canonical_reservation_us"), list)
            and len(actual["canonical_reservation_us"])
            == len(expected_reservations)
            and all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and _paired_close(float(value), expected)
                for value, expected in zip(
                    actual["canonical_reservation_us"], expected_reservations
                )
            ),
            f"{file_name}: {key} differs",
        )

    validate_allocation(
        "allocation_by_time_bin",
        evidence["actions_by_time_bin"],
        evidence["reservation_by_time_bin"],
    )
    validate_allocation(
        "allocation_by_congestion_regime",
        evidence["actions_by_regime"],
        evidence["reservation_by_regime"],
    )

    diagnostics = summary.get("prediction_diagnostics")
    expected_positive = _distributional_shadow_t2_distribution(
        evidence["positive_rewards"]
    )
    expected_opportunity = _distributional_shadow_t2_distribution(
        evidence["finite_opportunity_costs"]
    )
    _require(
        isinstance(diagnostics, dict)
        and set(diagnostics)
        == {
            "positive_deadline_rescue_reward",
            "finite_opportunity_cost_per_us",
            "infinite_opportunity_cost_count",
            "predicted_deadline_rescue_sum_launched",
            "predicted_tail18_cdf_gain_sum_launched",
        },
        f"{file_name}: prediction diagnostic keys differ",
    )

    def validate_distribution(actual: Any, expected: dict[str, Any], label: str) -> None:
        _require(
            isinstance(actual, dict) and set(actual) == set(expected),
            f"{file_name}: {label} structure differs",
        )
        for key, expected_value in expected.items():
            actual_value = actual.get(key)
            if expected_value is None or key == "finite_count":
                _require(
                    actual_value == expected_value,
                    f"{file_name}: {label}.{key} differs",
                )
            else:
                _require(
                    isinstance(actual_value, (int, float))
                    and not isinstance(actual_value, bool)
                    and _paired_close(float(actual_value), float(expected_value)),
                    f"{file_name}: {label}.{key} differs",
                )

    validate_distribution(
        diagnostics["positive_deadline_rescue_reward"],
        expected_positive,
        "positive reward distribution",
    )
    validate_distribution(
        diagnostics["finite_opportunity_cost_per_us"],
        expected_opportunity,
        "opportunity-cost distribution",
    )
    _require(
        diagnostics["infinite_opportunity_cost_count"]
        == evidence["infinite_opportunity_costs"]
        and _paired_close(
            float(diagnostics["predicted_deadline_rescue_sum_launched"]),
            evidence["predicted_reward_launched"],
        )
        and _paired_close(
            float(diagnostics["predicted_tail18_cdf_gain_sum_launched"]),
            evidence["tail18_gain_launched"],
        ),
        f"{file_name}: prediction totals differ",
    )

    ledger = summary.get("ledger")
    expected_ledger = evidence["ledger"]
    _require(
        isinstance(ledger, dict) and set(ledger) == set(expected_ledger),
        f"{file_name}: ledger structure differs",
    )
    for key, expected in expected_ledger.items():
        actual = ledger.get(key)
        if isinstance(expected, bool) or isinstance(expected, int):
            _require(actual == expected, f"{file_name}: ledger.{key} differs")
        else:
            _require(
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and _paired_close(float(actual), expected),
                f"{file_name}: ledger.{key} differs",
            )

    measured_airtime = _summary_number(
        meter_summary, "tagged_secondary_tx_airtime_us"
    )
    estimated_airtime = _summary_number(
        meter_summary, "estimated_action_airtime_us"
    )
    airtime = summary.get("airtime")
    expected_airtime = {
        "canonical_nominal_launched_sum_us": evidence["nominal_launched"],
        "canonical_reserved_launched_sum_us": evidence["reserved_launched"],
        "measured_secondary_airtime_us": measured_airtime,
        "meter_estimated_action_airtime_us": estimated_airtime,
    }
    _require(
        isinstance(airtime, dict) and set(airtime) == set(expected_airtime),
        f"{file_name}: airtime structure differs",
    )
    for key, expected in expected_airtime.items():
        actual = airtime.get(key)
        meter_serialized = key in {
            "measured_secondary_airtime_us",
            "meter_estimated_action_airtime_us",
        }
        _require(
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and (
                _paired_meter_close(expected, float(actual))
                if meter_serialized
                else _paired_close(float(actual), expected)
            ),
            f"{file_name}: airtime.{key} differs",
        )

    expected_integrity = {
        "pending_pair_empty": True,
        "generated_equals_paired": True,
        "status_counts_reconcile": True,
        "launches_equal_settlements": True,
        "launched_frame_ids_equal_duplicated_frame_ids": True,
        "ledger_debits_equal_actions": True,
        "ledger_debits_equal_canonical_reservation_sum": True,
        "ledger_finalized_at_repayment_stop": True,
        "meter_reserved_final_within_tolerance": True,
        "meter_reserved_final_normalized_us": 0,
        "measured_settlement_refunds_ledger": False,
    }
    integrity = summary.get("integrity")
    _require(
        isinstance(integrity, dict)
        and set(integrity) == set(expected_integrity) | {"meter_reserved_final_raw_us"}
        and all(integrity.get(key) == value for key, value in expected_integrity.items()),
        f"{file_name}: integrity evidence differs",
    )
    raw_reserved = integrity.get("meter_reserved_final_raw_us")
    _require(
        isinstance(raw_reserved, (int, float))
        and not isinstance(raw_reserved, bool)
        and math.isfinite(float(raw_reserved))
        and abs(float(raw_reserved)) <= PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US,
        f"{file_name}: final meter reservation exceeds tolerance",
    )
    settlement_ids = {
        _integer(row, "frame_id", "secondary_airtime_settlements.csv")
        for row in settlements
    }
    _require(
        settlement_ids == evidence["action_frames"],
        f"{file_name}: settlements do not match actions",
    )
    _replay_paired_value_t2_meter(
        evidence["rows"],
        events,
        settlements,
        evidence["action_estimates"],
        evidence["action_nominals"],
        evidence["action_byte_quanta"] or None,
        evidence["action_mpdu_profiles"],
    )


def _paired_value_t2_event_maximum_debt(
    events: list[dict[str, str]],
    measurement_start_ns: int,
    guard_capacity_us: float,
) -> float:
    """Replay the V2 measured-airtime guard at every PHY event."""
    _require(
        measurement_start_ns >= 0
        and math.isfinite(guard_capacity_us)
        and guard_capacity_us >= PAIRED_VALUE_T2_GUARD_INITIAL_CREDIT_US,
        "paired-value event debt replay: invalid guard configuration",
    )
    balance_us = PAIRED_VALUE_T2_GUARD_INITIAL_CREDIT_US
    last_refill_ns = measurement_start_ns
    maximum_debt_us = 0.0
    for row in events:
        event_time_ns = _integer(
            row, "time_ns", "secondary_airtime_events.csv"
        )
        duration_us = _number(
            row, "ppdu_duration_us", "secondary_airtime_events.csv"
        )
        _require(
            event_time_ns >= last_refill_ns and duration_us > 0,
            "paired-value event debt replay: invalid event order or duration",
        )
        elapsed_us = (event_time_ns - last_refill_ns) / 1000.0
        balance_us = min(
            guard_capacity_us,
            balance_us + PAIRED_VALUE_T2_GUARD_FRACTION * elapsed_us,
        )
        # The meter splits one unmixed PPDU among its tagged frames at this
        # timestamp.  The final callback subtracts the complete PPDU duration,
        # which is also the deepest debt reached within the event.
        balance_us -= duration_us
        maximum_debt_us = max(maximum_debt_us, max(0.0, -balance_us))
        last_refill_ns = event_time_ns
    _require(
        math.isfinite(balance_us) and math.isfinite(maximum_debt_us),
        "paired-value event debt replay: non-finite balance",
    )
    return maximum_debt_us


def _validate_mechanism_experiment(
    run_dir: Path,
    config: dict[str, Any],
    run_id: str,
    frames: list[dict[str, str]],
    duplicated_frame_ids: set[int],
) -> tuple[dict[int, float], dict[int, float]]:
    """Validate the frozen paired-T2 mechanism telemetry and action ledger."""
    policy = config["policy"]
    settings = config.get("policy_settings")
    _require(
        config.get("topology") == "dual_interface"
        and isinstance(settings, dict)
        and settings.get("mechanism_telemetry_enabled") is True
        and settings.get("mechanism_systematic_repair_divisor") == 8,
        "resolved_config.json: invalid mechanism experiment settings",
    )
    expected_action = MECHANISM_ACTION_BY_POLICY.get(policy, "OBSERVE")
    _require(
        policy in MECHANISM_T2_POLICIES | {"fixed_link_1", "full_duplication"}
        and settings.get("mechanism_action") == expected_action,
        "resolved_config.json: mechanism action/policy mismatch",
    )
    oracle_file = settings.get("mechanism_oracle_packet_outcome_file")
    _require(
        isinstance(oracle_file, str)
        and bool(oracle_file) == (policy == "mechanism_oracle_repair_t2"),
        "resolved_config.json: invalid mechanism oracle source",
    )

    snapshots_path = run_dir / "mechanism_t2_snapshots.csv"
    actions_path = run_dir / "mechanism_t2_actions.csv"
    outcomes_path = run_dir / "frame_packet_outcomes.csv"
    for path in (snapshots_path, actions_path, outcomes_path):
        _require(path.is_file(), f"missing mechanism file: {path.name}")
    snapshots = _csv(snapshots_path, MECHANISM_SNAPSHOT_COLUMNS)
    actions = _csv(actions_path, MECHANISM_ACTION_COLUMNS)
    outcomes = _csv(outcomes_path, FRAME_PACKET_OUTCOME_COLUMNS)
    _require(
        len(snapshots) == 2 * len(frames)
        and len(actions) == len(frames)
        and len(outcomes) == len(frames),
        "mechanism telemetry: frame cardinality mismatch",
    )
    frames_by_id = {_integer(row, "frame_id", "frames.csv"): row for row in frames}

    snapshot_pairs: dict[int, dict[tuple[int, int], dict[str, str]]] = {}
    optional_snapshot_fields = (
        "frame_packets_tx_succeeded",
        "frame_packets_pending_primary",
        "frame_packets_currently_queued",
        "frame_mac_service_bytes_currently_queued",
        "mac_queue_packets",
        "mac_queue_service_bytes",
        "packets_ahead_of_frame",
        "mac_service_bytes_ahead_of_frame",
    )
    for row in snapshots:
        file_name = snapshots_path.name
        _require(
            row["run_id"] == run_id
            and _integer(row, "schema_version", file_name) == 1,
            f"{file_name}: identity mismatch",
        )
        frame_id = _integer(row, "frame_id", file_name)
        _require(frame_id in frames_by_id, f"{file_name}: unknown frame")
        frame = frames_by_id[frame_id]
        packet_count = _integer(frame, "packet_count", "frames.csv")
        _require(
            _integer(row, "source_packet_count", file_name) == packet_count
            and _integer(row, "sample_time_ns", file_name) // 1000
            == _integer(frame, "generation_time_us", "frames.csv") + 2000,
            f"{file_name}: source count or T2 timestamp mismatch",
        )
        identity = (
            _integer(row, "path_id", file_name),
            _integer(row, "copy_id", file_name),
        )
        _require(
            identity in {(1, 0), (0, 1)},
            f"{file_name}: unexpected path/copy identity",
        )
        for field in optional_snapshot_fields:
            _optional_integer(row, field, file_name)
        deficit = _index_list(
            row,
            "primary_ack_deficit_packet_indices",
            file_name,
            upper_bound=packet_count,
        )
        _require(
            len(deficit) == _integer(row, "primary_ack_deficit_count", file_name),
            f"{file_name}: ACK-deficit count mismatch",
        )
        pair = snapshot_pairs.setdefault(frame_id, {})
        _require(identity not in pair, f"{file_name}: duplicate frame/path row")
        pair[identity] = row
    _require(set(snapshot_pairs) == set(frames_by_id),
             "mechanism snapshots: frame coverage mismatch")
    for frame_id, pair in snapshot_pairs.items():
        _require(
            set(pair) == {(1, 0), (0, 1)}
            and pair[(1, 0)]["primary_ack_deficit_count"]
            == pair[(0, 1)]["primary_ack_deficit_count"]
            and pair[(1, 0)]["primary_ack_deficit_packet_indices"]
            == pair[(0, 1)]["primary_ack_deficit_packet_indices"],
            f"mechanism snapshots: incomplete or inconsistent pair for frame {frame_id}",
        )

    stream = config.get("stream", {})
    payload_size = stream.get("payload_size_bytes")
    _require(
        isinstance(payload_size, int) and not isinstance(payload_size, bool)
        and payload_size > 0,
        "resolved_config.json: mechanism payload size is invalid",
    )
    action_estimates: dict[int, float] = {}
    action_nominals: dict[int, float] = {}
    seen_actions: set[int] = set()
    for row in actions:
        file_name = actions_path.name
        _require(
            row["run_id"] == run_id
            and _integer(row, "schema_version", file_name) == 1
            and row["action"] == expected_action
            and bool(row["reason"]),
            f"{file_name}: identity or provenance mismatch",
        )
        frame_id = _integer(row, "frame_id", file_name)
        _require(
            frame_id in frames_by_id and frame_id not in seen_actions,
            f"{file_name}: duplicate or unknown frame",
        )
        seen_actions.add(frame_id)
        frame = frames_by_id[frame_id]
        packet_count = _integer(frame, "packet_count", "frames.csv")
        generation_us = _integer(frame, "generation_time_us", "frames.csv")
        generation_ns = _integer(row, "generation_time_ns", file_name)
        _require(
            generation_ns // 1000 == generation_us
            and _integer(row, "source_packet_count", file_name) == packet_count
            and _integer(row, "action_time_us", file_name) == generation_us + 2000,
            f"{file_name}: frame metadata or T2 action time mismatch",
        )
        requested = _flag(row, "requested", file_name)
        launched = _flag(row, "launched", file_name)
        _require(requested == launched,
                 f"{file_name}: a requested mechanism action failed to launch")
        descriptor_fields = (
            "action_packet_count", "action_packet_indices",
            "expected_mac_service_bytes", "nominal_airtime_us",
        )
        if not requested:
            _require(
                all(row.get(field, "") == "" for field in descriptor_fields),
                f"{file_name}: inactive row retains a descriptor",
            )
            continue
        selected_count = _integer(row, "action_packet_count", file_name)
        selected = _index_list(row, "action_packet_indices", file_name)
        _require(
            selected_count > 0 and len(selected) == selected_count,
            f"{file_name}: selected packet count mismatch",
        )
        if policy == "mechanism_full_copy_t2":
            _require(
                selected == list(range(packet_count)),
                f"{file_name}: full-copy packet set differs",
            )
            application_bytes = _integer(frame, "frame_size_bytes", "frames.csv")
        elif policy == "mechanism_systematic_fec_t2":
            repair_count = (packet_count + 7) // 8
            _require(
                selected == list(range(packet_count, packet_count + repair_count)),
                f"{file_name}: systematic repair set differs",
            )
            application_bytes = repair_count * payload_size
        else:
            _require(
                policy == "mechanism_oracle_repair_t2"
                and all(index < packet_count for index in selected),
                f"{file_name}: oracle repair packet set is invalid",
            )
            frame_size = _integer(frame, "frame_size_bytes", "frames.csv")
            final_payload = frame_size - payload_size * (packet_count - 1)
            _require(0 < final_payload <= payload_size,
                     f"{file_name}: invalid final source-packet payload")
            application_bytes = sum(
                final_payload if index == packet_count - 1 else payload_size
                for index in selected
            )
        expected_service_bytes = application_bytes + selected_count * (
            ADAPTIVE_ESTIMATOR_STREAMING_HEADER_BYTES
            + ADAPTIVE_ESTIMATOR_MAC_SERVICE_OVERHEAD_BYTES
        )
        nominal = _adaptive_nominal_airtime_us(
            application_bytes, selected_count, 1.0
        )
        logged_nominal = _number(row, "nominal_airtime_us", file_name)
        _require(
            _integer(row, "expected_mac_service_bytes", file_name)
            == expected_service_bytes
            and logged_nominal == float(format(nominal, ".12g")),
            f"{file_name}: descriptor bytes or canonical airtime differs",
        )
        action_estimates[frame_id] = logged_nominal
        action_nominals[frame_id] = logged_nominal
    _require(seen_actions == set(frames_by_id),
             "mechanism actions: frame coverage mismatch")

    seen_outcomes: set[int] = set()
    for row in outcomes:
        file_name = outcomes_path.name
        _require(row["run_id"] == run_id, f"{file_name}: run_id mismatch")
        frame_id = _integer(row, "frame_id", file_name)
        _require(
            frame_id in frames_by_id and frame_id not in seen_outcomes,
            f"{file_name}: duplicate or unknown frame",
        )
        seen_outcomes.add(frame_id)
        packet_count = _integer(frames_by_id[frame_id], "packet_count", "frames.csv")
        _require(
            _integer(row, "source_packet_count", file_name) == packet_count,
            f"{file_name}: source packet count mismatch",
        )
        received = set(_index_list(
            row, "received_source_packet_indices", file_name, upper_bound=packet_count
        ))
        missing = set(_index_list(
            row, "missing_source_packet_indices", file_name, upper_bound=packet_count
        ))
        copy0 = set(_index_list(
            row, "copy_0_source_packet_indices", file_name, upper_bound=packet_count
        ))
        copy1 = set(_index_list(
            row, "copy_1_source_packet_indices", file_name, upper_bound=packet_count
        ))
        link0 = set(_index_list(
            row, "link_0_source_packet_indices", file_name, upper_bound=packet_count
        ))
        link1 = set(_index_list(
            row, "link_1_source_packet_indices", file_name, upper_bound=packet_count
        ))
        coded = _index_list(row, "received_coded_repair_indices", file_name)
        _require(
            received | missing == set(range(packet_count))
            and not received & missing
            and received == copy0 | copy1 == link0 | link1
            and copy0 == link1
            and copy1 == link0,
            f"{file_name}: source packet sets do not reconcile",
        )
        if policy == "mechanism_systematic_fec_t2":
            repair_count = (packet_count + 7) // 8
            _require(
                all(packet_count <= index < packet_count + repair_count for index in coded),
                f"{file_name}: coded repair index is out of range",
            )
        else:
            _require(not coded, f"{file_name}: coded symbols exist for a non-FEC arm")
    _require(seen_outcomes == set(frames_by_id),
             "frame packet outcomes: frame coverage mismatch")

    launched_frames = set(action_estimates)
    if policy in MECHANISM_T2_POLICIES:
        _require(
            launched_frames == duplicated_frame_ids,
            "mechanism actions do not match duplicated frames",
        )
    elif policy == "fixed_link_1":
        _require(not duplicated_frame_ids,
                 "single-link mechanism observation duplicated a frame")
    else:
        _require(duplicated_frame_ids == set(frames_by_id),
                 "full-copy T0 mechanism observation omitted a frame")
    return action_estimates, action_nominals


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
    paired_guard_capacity_us: float | None = None,
) -> None:
    """Reconcile secondary PHY events, reservations, and the run budget."""
    exact_policy = policy in {
        PAIRED_VALUE_T2_POLICY,
        DISTRIBUTIONAL_SHADOW_T2_POLICY,
    }
    accounting_close = _paired_close if exact_policy else _close
    meter_close = _paired_meter_close if exact_policy else _close
    _require(SECONDARY_AIRTIME_SUMMARY_KEYS <= summary.keys(),
             "secondary_airtime_summary.json: missing fields")
    _require(policy in {
        "selective_duplication", "adaptive_airtime_duplication",
        "adaptive_deficit_duplication", "randomized_full_copy_exploration",
        PAIRED_VALUE_T2_POLICY, DISTRIBUTIONAL_SHADOW_T2_POLICY, "full_duplication",
        *MECHANISM_T2_POLICIES,
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
    event_schema_version = meter_config.get("event_schema_version", 1)
    _require(
        event_schema_version in {1, 2}
        and summary.get("event_schema_version", 1) == event_schema_version,
        "secondary airtime meter event schema differs",
    )
    start_ns = meter_config.get("measurement_start_ns")
    stop_ns = meter_config.get("measurement_stop_ns")
    _require(isinstance(start_ns, int) and not isinstance(start_ns, bool) and start_ns >= 0 and
             isinstance(stop_ns, int) and not isinstance(stop_ns, bool) and stop_ns > start_ns,
             "resolved_config.json: invalid secondary airtime measurement window")
    _require(_summary_integer(summary, "measurement_start_ns") == start_ns and
             _summary_integer(summary, "measurement_stop_ns") == stop_ns,
             "secondary airtime summary: measurement window mismatch")
    duration_us = (stop_ns - start_ns) / 1000.0
    _require(meter_close(
                 _summary_number(summary, "measurement_duration_us"), duration_us),
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
        if event_schema_version == 2:
            _require(
                frame_ids == sorted(frame_ids),
                "secondary airtime events: V2 frame IDs are not canonical",
            )
            _secondary_airtime_v2_event_evidence(
                row,
                tuple(frame_ids),
                tagged_bytes,
                duration,
            )
        else:
            _require(
                not any(
                    field in row
                    for field in (
                        "ppdu_duration_binary64_bits",
                        "frame_tagged_mpdu_bytes",
                        "frame_allocated_airtime_binary64_bits",
                    )
                ),
                "secondary airtime events: V2 columns use a V1 configuration",
            )
        observed_event_frames.update(frame_ids)
        mixed = _flag(row, "mixed_ppdu", "secondary_airtime_events.csv")
        mixed_count += mixed
        _require(not mixed, "secondary airtime meter observed mixed PPDUs")
        running_total += duration
        cumulative = _number(
            row, "cumulative_tagged_airtime_us", "secondary_airtime_events.csv"
        )
        _require(meter_close(cumulative, running_total),
                 "secondary airtime events: cumulative airtime mismatch")

    tagged_total = _summary_number(summary, "tagged_secondary_tx_airtime_us")
    _require(_summary_integer(summary, "tagged_ppdu_count") == len(events) and
             _summary_integer(summary, "mixed_ppdu_count") == mixed_count and
             meter_close(tagged_total, running_total),
             "secondary airtime events do not reconcile with summary")
    _require(meter_close(
        _summary_number(summary, "tagged_secondary_tx_airtime_fraction"),
        running_total / duration_us,
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
            expected_nominal = expected_settlement_nominals.get(frame_id)
            if exact_policy and expected_nominal is not None:
                nominal_matches = nominal == float(format(expected_nominal, ".12g"))
            else:
                nominal_matches = expected_nominal is not None and accounting_close(
                    nominal, expected_nominal
                )
            _require(nominal_matches,
                     "secondary airtime settlements: nominal estimator mismatch")
        settlement_by_frame[frame_id] = {
            "released": released, "measured": measured, "fallback": fallback,
        }

    _require(_summary_integer(summary, "forced_reservation_settlements") == fallback_count,
             "secondary airtime summary: fallback settlement count mismatch")
    estimate_total = _summary_number(summary, "estimated_action_airtime_us")
    ratio = _summary_number(summary, "actual_to_estimated_airtime_ratio")
    maximum_debt = _summary_number(summary, "maximum_budget_debt_us")
    if policy == PAIRED_VALUE_T2_POLICY:
        _require(
            paired_guard_capacity_us is not None,
            "secondary airtime summary: paired guard capacity is absent",
        )
        event_maximum_debt_us = _paired_value_t2_event_maximum_debt(
            events,
            start_ns,
            paired_guard_capacity_us,
        )
        _require(
            _paired_meter_close(maximum_debt, event_maximum_debt_us),
            "secondary airtime summary: maximum debt differs from exact replay",
        )
        serialized_debt_tolerance_us = (
            PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
            + _paired_meter_quantization_us(format(maximum_debt, ".12g"))
        )
        _require(
            observed_budget_debt_us
            <= maximum_debt + serialized_debt_tolerance_us,
            "secondary airtime summary: maximum debt misses a decision snapshot",
        )
    elif policy == DISTRIBUTIONAL_SHADOW_T2_POLICY:
        _require(
            _paired_meter_close(maximum_debt, observed_budget_debt_us),
            "secondary airtime summary: maximum debt differs from exact replay",
        )
    else:
        _require(
            maximum_debt + 1e-9 >= observed_budget_debt_us,
            "secondary airtime summary: maximum debt misses an observed deficit",
        )

    reservation_policy = policy in {
        "adaptive_airtime_duplication", "adaptive_deficit_duplication",
        "randomized_full_copy_exploration", PAIRED_VALUE_T2_POLICY,
        DISTRIBUTIONAL_SHADOW_T2_POLICY, *MECHANISM_T2_POLICIES,
    }
    if reservation_policy:
        if policy not in {
            "randomized_full_copy_exploration",
            PAIRED_VALUE_T2_POLICY,
            DISTRIBUTIONAL_SHADOW_T2_POLICY,
            *MECHANISM_T2_POLICIES,
        }:
            _require(adaptive_config is not None,
                     "adaptive secondary airtime validation lacks controller config")
        _require(set(settlement_by_frame) == set(action_estimates),
                 "secondary airtime settlements do not match reserved actions")
        _require(observed_event_frames <= set(action_estimates),
                 "secondary airtime events do not match reserved actions")
        exact_estimate_total = sum(action_estimates.values())
        _require(meter_close(estimate_total, exact_estimate_total),
                 "secondary airtime summary: action estimates do not sum")
        measured_total = sum(float(item["measured"]) for item in settlement_by_frame.values())
        measured_matches = (
            _paired_meter_sum_close(
                tagged_total,
                [row["measured_airtime_us"] for row in settlements],
            )
            if exact_policy
            else accounting_close(measured_total, tagged_total)
        )
        _require(measured_matches,
                 "secondary airtime settlements: measured airtime does not sum")
        for frame_id, estimate in action_estimates.items():
            settlement = settlement_by_frame[frame_id]
            measured = float(settlement["measured"])
            released = float(settlement["released"])
            expected_release = max(0.0, estimate - measured)
            if exact_policy:
                settlement_row = next(
                    row for row in settlements
                    if _integer(row, "frame_id", "secondary_airtime_settlements.csv")
                    == frame_id
                )
                release_matches = (
                    measured == float(format(measured, ".12g"))
                    and released == float(format(released, ".12g"))
                    and abs(max(estimate - measured, 0.0) - released)
                    <= PAIRED_VALUE_T2_ACCOUNTING_TOLERANCE_US
                    + _paired_meter_quantization_us(
                        settlement_row["measured_airtime_us"]
                    )
                    + _paired_meter_quantization_us(
                        settlement_row["released_airtime_us"]
                    )
                )
            else:
                # These three values are written independently with
                # setprecision(12). Preserve the legacy policies' envelope.
                serialization_tolerance = max(
                    1e-9,
                    1e-11 * max(abs(estimate), abs(measured), 1.0),
                )
                release_matches = math.isclose(
                    released,
                    expected_release,
                    rel_tol=1e-9,
                    abs_tol=serialization_tolerance,
                )
            _require(release_matches,
                     "secondary airtime settlements: released reservation mismatch")
        expected_ratio = running_total / exact_estimate_total if exact_estimate_total else 0.0
        _require(meter_close(ratio, expected_ratio),
                 "secondary airtime summary: estimate ratio mismatch")
        if policy == "randomized_full_copy_exploration" or policy in MECHANISM_T2_POLICIES:
            _require(accounting_close(maximum_debt, 0.0),
                     "secondary airtime summary: non-budgeted policy has budget debt")
            for key in (
                "budget_fraction", "initial_bucket_capacity_us", "finite_run_budget_us",
                "budget_excess_us",
            ):
                _require(summary.get(key) is None,
                         f"secondary_airtime_summary.json: {key} must be null")
        elif exact_policy:
            fraction = PAIRED_VALUE_T2_GUARD_FRACTION
            capacity = PAIRED_VALUE_T2_GUARD_INITIAL_CREDIT_US
            finite_budget = fraction * duration_us + capacity
            _require(meter_close(
                         _summary_number(summary, "budget_fraction"), fraction) and
                     meter_close(
                         _summary_number(summary, "initial_bucket_capacity_us"), capacity) and
                     meter_close(
                         _summary_number(summary, "finite_run_budget_us"), finite_budget) and
                     meter_close(
                         _summary_number(summary, "budget_excess_us"),
                         max(0.0, running_total - finite_budget)),
                     "secondary airtime summary: paired-value finite-run budget mismatch")
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
            _require(accounting_close(
                         _summary_number(summary, "budget_fraction"), fraction) and
                     accounting_close(
                         _summary_number(summary, "initial_bucket_capacity_us"), capacity) and
                     accounting_close(
                         _summary_number(summary, "finite_run_budget_us"), finite_budget) and
                     accounting_close(
                         _summary_number(summary, "budget_excess_us"),
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
        "randomized_full_copy_exploration", PAIRED_VALUE_T2_POLICY,
        DISTRIBUTIONAL_SHADOW_T2_POLICY, "full_duplication", *MECHANISM_T2_POLICIES,
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
    mechanism_policy = bool(
        config.get("policy_settings", {}).get("mechanism_telemetry_enabled", False)
    )
    paired_endpoint_policy = randomized_policy or config["policy"] in {
        PAIRED_VALUE_T2_POLICY,
        DISTRIBUTIONAL_SHADOW_T2_POLICY,
    } or mechanism_policy
    _require(not paired_endpoint_policy or event_enabled is False,
             "paired-link telemetry requires disabled raw event logging")
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
    copies_per_frame = 2 if paired_endpoint_policy else 1
    _require(len(samples) == len(frames) * len(offsets) * copies_per_frame,
             "prediction_samples.csv: receiver-independent cardinality mismatch")

    selected_path = 0 if config["policy"] == "fixed_link_0" else 1
    expected_identities = (
        {(1, 0), (0, 1)} if paired_endpoint_policy else {(selected_path, 0)}
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
        if paired_endpoint_policy:
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
    paired_value_profile: dict[str, Any] | None = None
    if config.get("policy") == PAIRED_VALUE_T2_POLICY:
        paired_value_profile = _validate_paired_value_t2_config(config)
    elif config.get("policy") == DISTRIBUTIONAL_SHADOW_T2_POLICY:
        _validate_distributional_shadow_t2_config(config)
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
    paired_value_evidence: dict[str, Any] | None = None
    distributional_shadow_evidence: dict[str, Any] | None = None
    mechanism_enabled = bool(
        config.get("policy_settings", {}).get("mechanism_telemetry_enabled", False)
    )
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
    elif config["policy"] == PAIRED_VALUE_T2_POLICY:
        assert paired_value_profile is not None
        decisions_path = run_dir / "paired_value_t2_decisions.csv"
        controller_summary_path = run_dir / "paired_value_t2_summary.json"
        _require(decisions_path.is_file(),
                 "missing core file: paired_value_t2_decisions.csv")
        _require(controller_summary_path.is_file(),
                 "missing core file: paired_value_t2_summary.json")
        paired_value_evidence = _validate_paired_value_t2_decisions(
            run_dir,
            run_id,
            frames,
            decisions,
            duplicated_frame_ids,
            paired_value_profile,
        )
        action_estimates = paired_value_evidence["action_estimates"]
        action_nominal_airtimes = paired_value_evidence["action_nominals"]
        observed_budget_debt_us = paired_value_evidence["maximum_observed_debt"]
        for forbidden in (
            "selective_duplication_decisions.csv",
            "adaptive_airtime_decisions.csv",
            "randomized_intervention_assignments.csv",
            "randomized_intervention_executions.csv",
        ):
            _require(not (run_dir / forbidden).exists(),
                     f"{forbidden} exists for paired-value policy")
    elif config["policy"] == DISTRIBUTIONAL_SHADOW_T2_POLICY:
        decisions_path = run_dir / "distributional_shadow_t2_decisions.csv"
        controller_summary_path = run_dir / "distributional_shadow_t2_summary.json"
        _require(
            decisions_path.is_file(),
            "missing core file: distributional_shadow_t2_decisions.csv",
        )
        _require(
            controller_summary_path.is_file(),
            "missing core file: distributional_shadow_t2_summary.json",
        )
        distributional_shadow_evidence = (
            _validate_distributional_shadow_t2_decisions(
                run_dir,
                run_id,
                frames,
                decisions,
                duplicated_frame_ids,
                config.get(
                    "pairedTemporalT2FrameProfile",
                    PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE,
                ),
            )
        )
        action_estimates = distributional_shadow_evidence["action_estimates"]
        action_nominal_airtimes = distributional_shadow_evidence["action_nominals"]
        observed_budget_debt_us = distributional_shadow_evidence[
            "maximum_observed_debt"
        ]
        for forbidden in (
            "selective_duplication_decisions.csv",
            "adaptive_airtime_decisions.csv",
            "randomized_intervention_assignments.csv",
            "randomized_intervention_executions.csv",
            "paired_value_t2_decisions.csv",
            "paired_value_t2_summary.json",
        ):
            _require(
                not (run_dir / forbidden).exists(),
                f"{forbidden} exists for distributional-shadow policy",
            )
    elif mechanism_enabled:
        action_estimates, action_nominal_airtimes = _validate_mechanism_experiment(
            run_dir,
            config,
            run_id,
            frames,
            duplicated_frame_ids,
        )
        for forbidden in (
            "selective_duplication_decisions.csv",
            "adaptive_airtime_decisions.csv",
            "randomized_intervention_assignments.csv",
            "randomized_intervention_executions.csv",
        ):
            _require(
                not (run_dir / forbidden).exists(),
                f"{forbidden} exists for a mechanism experiment arm",
            )
    else:
        _require(not (run_dir / "selective_duplication_decisions.csv").exists(),
                 "selective decision output exists for a non-selective policy")
        _require(not (run_dir / "adaptive_airtime_decisions.csv").exists(),
                 "adaptive decision output exists for a non-adaptive policy")

    if not mechanism_enabled:
        for forbidden in (
            "mechanism_t2_snapshots.csv",
            "mechanism_t2_actions.csv",
            "frame_packet_outcomes.csv",
        ):
            _require(
                not (run_dir / forbidden).exists(),
                f"{forbidden} exists outside a mechanism experiment arm",
            )

    if config["policy"] != PAIRED_VALUE_T2_POLICY:
        _require("pairedValueDuplicationT2" not in config,
                 "resolved_config.json: paired-value object exists for another policy")
        _require(not (run_dir / "paired_value_t2_decisions.csv").exists(),
                 "paired-value decisions exist for another policy")
        _require(not (run_dir / "paired_value_t2_summary.json").exists(),
                 "paired-value summary exists for another policy")

    if config["policy"] != DISTRIBUTIONAL_SHADOW_T2_POLICY:
        _require(
            "distributionalShadowDuplicationT2" not in config,
            "resolved_config.json: distributional-shadow object exists for another policy",
        )
        _require(
            not (run_dir / "distributional_shadow_t2_decisions.csv").exists(),
            "distributional-shadow decisions exist for another policy",
        )
        _require(
            not (run_dir / "distributional_shadow_t2_summary.json").exists(),
            "distributional-shadow summary exists for another policy",
        )

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
        event_schema_version = meter.get("event_schema_version", 1)
        _require(
            event_schema_version in {1, 2},
            "resolved_config.json: unsupported secondary airtime event schema",
        )
        events = _csv(
            events_path,
            (
                SECONDARY_AIRTIME_EVENT_V2_COLUMNS
                if event_schema_version == 2
                else SECONDARY_AIRTIME_EVENT_COLUMNS
            ),
            ordered_columns=(
                SECONDARY_AIRTIME_EVENT_V2_ORDERED_COLUMNS
                if event_schema_version == 2
                else None
            ),
        )
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
            (
                paired_value_profile["guard_capacity_us"]
                if paired_value_profile is not None
                else None
            ),
        )
        if paired_value_evidence is not None:
            _validate_paired_value_t2_summary(
                run_dir,
                run_id,
                total,
                paired_value_evidence,
                events,
                settlements,
                meter_summary,
            )
        if distributional_shadow_evidence is not None:
            _validate_distributional_shadow_t2_summary(
                run_dir,
                run_id,
                total,
                distributional_shadow_evidence,
                events,
                settlements,
                meter_summary,
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
