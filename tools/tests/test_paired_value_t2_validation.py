#!/usr/bin/env python3
"""Focused fail-closed tests for paired temporal-T2 run validation."""

from __future__ import annotations

import copy
import csv
import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from validate_outputs import (  # noqa: E402
    PAIRED_VALUE_T2_CONFIG,
    PAIRED_VALUE_T2_COST_FREE_CONFIG,
    PAIRED_VALUE_T2_COST_FREE_CONTRACT_ID,
    PAIRED_VALUE_T2_COST_FREE_CONTRACT_SHA256,
    PAIRED_VALUE_T2_COST_FREE_DECISION_SUFFIX,
    PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD,
    PAIRED_VALUE_T2_COST_FREE_MODEL_METADATA,
    PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD,
    PAIRED_VALUE_T2_DECISION_COLUMNS,
    PAIRED_VALUE_T2_EMERGENCY_MAXIMUM_DEBT_US,
    PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD,
    PAIRED_VALUE_T2_FULL_HORIZON_CONFIG,
    PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_ID,
    PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_SHA256,
    PAIRED_VALUE_T2_FULL_HORIZON_GUARD_CAPACITY_US,
    PAIRED_VALUE_T2_LOG_SMEARING_FACTOR,
    PAIRED_VALUE_T2_MODEL_ARTIFACT_SHA256,
    PAIRED_VALUE_T2_MODEL_METADATA,
    PAIRED_VALUE_T2_PREDICTION_CONFIG,
    PAIRED_VALUE_T2_REMAINING_REFILL_CONFIG,
    PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_ID,
    PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_SHA256,
    PAIRED_VALUE_T2_REMAINING_REFILL_DECISION_SUFFIX,
    PAIRED_VALUE_T2_SCORE_THRESHOLD,
    PAIRED_VALUE_T2_SCORE_AWARE_CONFIG,
    PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_ID,
    PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_SHA256,
    PAIRED_VALUE_T2_SCORE_AWARE_DECISION_SUFFIX,
    PREDICTION_BASE_COLUMNS,
    PREDICTION_POLLING_BASE_COLUMNS,
    PREDICTION_ROLLING_PREFIXES,
    ValidationError,
    _adaptive_nominal_airtime_us,
    _csv,
    _paired_value_t2_cost_reductions_close,
    _paired_value_t2_event_maximum_debt,
    _paired_meter_sum_close,
    _replay_paired_value_t2_meter,
    _rolling_column,
    _validate_paired_value_t2_config,
    _validate_paired_value_t2_decisions,
    _validate_paired_value_t2_summary,
    _validate_secondary_airtime,
    _window_label,
)
from analyze_primary_tail_t4_campaign import (  # noqa: E402
    DECLARED_NEUTRAL_ENVIRONMENT,
    DECLARED_TOPOLOGY_WIFI,
)


RUN_ID = "paired-value-test"


def write_csv(path: Path, columns: list[str] | tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", value))[0]


class PairedValueT2Fixture:
    def __init__(self, root: Path, *, action: bool = False) -> None:
        self.root = root
        self.action = action
        self.decision_columns = PAIRED_VALUE_T2_DECISION_COLUMNS
        self.decision_profile: dict[str, object] | None = None
        self.frames = self._frames()
        self.policy_decisions = self._policy_decisions()
        self.samples, self.polling, captures = self._telemetry()
        self.decisions = self._decisions(captures)
        self.settlements: list[dict[str, str]] = []
        self.events: list[dict[str, str]] = []
        self._write_inputs()

    def _frames(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for frame_id in range(9):
            generation_us = 1_000_000 + frame_id * 33_333
            frame_type = "I_FRAME" if frame_id == 0 else "P_FRAME"
            size = 48_000 if frame_id == 0 else 12_000
            count = 40 if frame_id == 0 else 10
            rows.append({
                "run_id": RUN_ID,
                "frame_id": str(frame_id),
                "generation_time_us": str(generation_us),
                "frame_size_bytes": str(size),
                "packet_count": str(count),
                "frame_type": frame_type,
                "deadline_us": "33333",
                "policy": "paired_value_duplication_t2",
                "primary_link": "1",
                "duplicated": "0",
            })
        return rows

    def _policy_decisions(self) -> list[dict[str, str]]:
        rows = []
        for frame in self.frames:
            launched = self.action and frame["frame_id"] == "8"
            frame["duplicated"] = "1" if launched else "0"
            rows.append({
                "run_id": RUN_ID,
                "frame_id": frame["frame_id"],
                "policy": "paired_value_duplication_t2",
                "primary_link": "1",
                "duplicated": "1" if launched else "0",
                "secondary_link": "0" if launched else "",
            })
        return rows

    def _telemetry(self) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[int, int]]:
        rolling = {
            _rolling_column(prefix, _window_label(window))
            for prefix in PREDICTION_ROLLING_PREFIXES
            for window in (1000, 5000, 20000)
        }
        sample_columns = sorted(PREDICTION_BASE_COLUMNS | rolling)
        polling_columns = sorted(PREDICTION_POLLING_BASE_COLUMNS | rolling)
        samples: list[dict[str, str]] = []
        reports: list[dict[str, str]] = []
        captures: dict[int, int] = {}
        for frame in self.frames:
            frame_id = int(frame["frame_id"])
            generation_ns = int(frame["generation_time_us"]) * 1000
            sample_ns = generation_ns + 2_000_000
            deadline_ns = generation_ns + 33_333_000
            for path_id, copy_id in ((0, 1), (1, 0)):
                row = {column: "" for column in sample_columns}
                row.update({
                    "telemetry_schema_version": "3",
                    "run_id": RUN_ID,
                    "frame_id": str(frame_id),
                    "path_id": str(path_id),
                    "copy_id": str(copy_id),
                    "sample_stage": "T2",
                    "sample_offset_us": "2000",
                    "sample_time_ns": str(sample_ns),
                    "latest_feature_event_time_ns": "",
                    "latest_feature_event_sequence": "0",
                    "generation_time_ns": str(generation_ns),
                    "deadline_time_ns": str(deadline_ns),
                    "frame_age_us": "2000",
                    "deadline_slack_us": "31333",
                    "sender_mac_complete": "0",
                    "actionable": "1",
                    "frame_size_bytes": frame["frame_size_bytes"],
                    "frame_packet_count": frame["packet_count"],
                    "frame_type": frame["frame_type"],
                    "packets_submitted": "0",
                    "application_socket_packet_bytes_submitted": "0",
                    "packets_remaining_to_submit": frame["packet_count"],
                    "feature_support_mask": "0x3ffffffffdffff",
                })
                for field in (
                    "frame_packets_mac_enqueued", "frame_packets_mac_dequeued",
                    "frame_packets_tx_succeeded", "frame_mpdu_attempt_failures",
                    "frame_packets_terminally_dropped", "frame_packets_currently_queued",
                    "frame_mac_service_bytes_currently_queued",
                ):
                    row[field] = "0"
                samples.append(row)

            capture_ns = ((sample_ns - 1_000_000) // 1_000_000) * 1_000_000
            captures[frame_id] = capture_ns
            report = {column: "" for column in polling_columns}
            report.update({
                "polling_schema_version": "1",
                "run_id": RUN_ID,
                "frame_id": str(frame_id),
                "path_id": "1",
                "copy_id": "0",
                "sample_stage": "T2",
                "sample_offset_us": "2000",
                "report_available": "1",
                "capture_time_ns": str(capture_ns),
                "available_time_ns": str(capture_ns + 1_000_000),
                "staleness_us": str((sample_ns - capture_ns) // 1000),
                "latest_feature_event_time_ns": "",
                "latest_feature_event_sequence": "0",
                "feature_support_mask": "0x3ffffffffdffff",
            })
            for field in (
                "mpdu_tx_attempts_total", "mpdu_positive_acks_total",
                "mpdu_tx_attempt_failures_total", "mpdu_retries_total",
                "mpdu_terminal_drops_total", "mpdu_retry_limit_drops_total",
                "mpdu_lifetime_drops_total", "mpdu_queue_drops_total", "ppdu_tx_count_total",
            ):
                report[field] = "0"
            for window in (1000, 5000, 20000):
                label = _window_label(window)
                for prefix in (
                    "mpdu_attempts", "mpdu_positive_acks", "mpdu_attempt_failures",
                    "mpdu_retries", "acknowledged_mac_service_bytes",
                ):
                    report[_rolling_column(prefix, label)] = "0"
                report[_rolling_column("mpdu_retry_ratio", label)] = ""
                for prefix in (
                    "mpdu_queue_to_ack_mean", "mpdu_queue_to_ack_p95",
                    "mpdu_first_attempt_to_ack_mean", "mpdu_first_attempt_to_ack_p95",
                ):
                    report[f"{prefix}_{label}_us"] = ""
                for state in ("tx", "rx", "busy", "other"):
                    report[f"phy_{state}_fraction_{label}"] = "0"
                report[f"phy_idle_fraction_{label}"] = "1"
                report[f"history_coverage_{label}_us"] = str(window)
            reports.append(report)
        if self.action:
            primary = next(
                row for row in samples
                if row["frame_id"] == "8" and row["path_id"] == "1"
            )
            primary["mac_queue_packets"] = "12"
            current = next(row for row in reports if row["frame_id"] == "8")
            current.update({
                "phy_tx_fraction_5ms": "0.2695566",
                "phy_rx_fraction_5ms": "0.33984",
                "phy_busy_fraction_5ms": "0.1161316",
                "phy_idle_fraction_5ms": "0.2744718",
                "phy_other_fraction_5ms": "0",
            })
            ppdu_totals = {5: 5, 6: 5, 7: 15, 8: 40}
            for report in reports:
                frame_id = int(report["frame_id"])
                if frame_id in ppdu_totals:
                    report["ppdu_tx_count_total"] = str(ppdu_totals[frame_id])
        self.sample_columns = sample_columns
        self.polling_columns = polling_columns
        return samples, reports, captures

    def _decisions(self, captures: dict[int, int]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for frame in self.frames:
            frame_id = int(frame["frame_id"])
            generation_ns = int(frame["generation_time_us"]) * 1000
            sample_ns = generation_ns + 2_000_000
            balance = 12_000 + 0.006 * ((sample_ns - 1_000_000_000) / 1000)
            row = {column: "" for column in PAIRED_VALUE_T2_DECISION_COLUMNS}
            row.update(PAIRED_VALUE_T2_MODEL_METADATA)
            row.update({
                "schema_version": "1",
                "run_id": RUN_ID,
                "frame_id": str(frame_id),
                "decision_status": "history_warmup" if frame_id < 8 else (
                    "action" if self.action else "below_score_threshold"
                ),
                "primary_path_id": "1",
                "primary_copy_id": "0",
                "secondary_path_id": "0",
                "secondary_copy_id": "1",
                "generation_time_ns": str(generation_ns),
                "deadline_time_ns": str(generation_ns + 33_333_000),
                "primary_sample_time_ns": str(sample_ns),
                "secondary_sample_time_ns": str(sample_ns),
                "primary_feature_watermark_time_ns": "",
                "primary_feature_watermark_sequence": "0",
                "frame_type": frame["frame_type"],
                "frame_size_bytes": frame["frame_size_bytes"],
                "frame_packet_count": frame["packet_count"],
                "primary_actionable": "1",
                "decision_window_start_ns": "1000000000",
                "decision_window_stop_ns": "60466000000",
                "inside_decision_window": "1",
                "history_ready": "1" if frame_id >= 8 else "0",
                "current_poll_capture_time_ns": str(captures[frame_id]),
                "current_poll_available_time_ns": str(captures[frame_id] + 1_000_000),
                "feature_evaluated": "1" if frame_id == 8 else "0",
                "score_threshold_float32": repr(PAIRED_VALUE_T2_SCORE_THRESHOLD),
                "descriptor_checked": "1" if frame_id == 8 else "0",
                "descriptor_available": "1" if frame_id == 8 else "0",
                "canonical_cost_estimator_id": (
                    "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1"
                ),
                "cost_safety_factor": "1.25",
                "guard_fraction": "0.006",
                "guard_max_horizon_us": "10000000",
                "guard_initial_horizon_us": "2000000",
                "guard_capacity_us": "60000",
                "guard_initial_credit_us": "12000",
                "guard_balance_before_us": repr(balance),
                "meter_reserved_before_us": "0",
                "guard_available_before_us": repr(balance),
                "guard_debt_before_us": "0",
                "guard_admission_considered": "1" if self.action and frame_id == 8 else "0",
                "guard_admitted": "1" if self.action and frame_id == 8 else "0",
                "launch_attempted": "1" if self.action and frame_id == 8 else "0",
                "secondary_launched": "1" if self.action and frame_id == 8 else "0",
                "guard_balance_after_us": repr(balance),
                "meter_reserved_after_us": "0",
                "guard_available_after_us": repr(balance),
                "guard_debt_after_us": "0",
            })
            for lag in (1, 3, 8):
                if frame_id >= lag:
                    row[f"lag{lag}_frame_id"] = str(frame_id - lag)
                    row[f"lag{lag}_poll_capture_time_ns"] = str(captures[frame_id - lag])
            if frame_id == 8:
                packet_count = int(frame["packet_count"])
                nominal = _adaptive_nominal_airtime_us(
                    int(frame["frame_size_bytes"]), packet_count, 1.0
                )
                reserved = 1.25 * nominal
                if self.action:
                    primary_logit = 0.22906248243663435
                    primary_probability = 0.5570165353516654
                    treated_logit = -3.4484510705169495
                    treated_probability = 0.030815085331925698
                    predicted_log = 6.9506956056505125
                    predicted_cost = 1138.7851611363471
                    value = 0.5262014500197397
                    score = 0.0004620726394932717
                else:
                    primary_logit = -4.209074180083754
                    primary_probability = 0.01464253001654195
                    treated_logit = -6.367112060290477
                    treated_probability = 0.0017141675689690686
                    predicted_log = 7.280664746799867
                    predicted_cost = 1584.3559094205343
                    value = 0.01292836244757288
                    score = 8.160011930158362e-06
                row.update({
                    "descriptor_frame_packet_count": str(packet_count),
                    "descriptor_packet_count": str(packet_count),
                    "descriptor_packet_indices": ";".join(
                        str(index) for index in range(packet_count)
                    ),
                    "descriptor_expected_mac_service_bytes": str(
                        int(frame["frame_size_bytes"]) + packet_count * 86
                    ),
                    "descriptor_deadline_time_ns": str(generation_ns + 33_333_000),
                    "canonical_nominal_airtime_us": repr(nominal),
                    "canonical_reserved_airtime_us": repr(reserved),
                    "primary_bad12_logit": repr(primary_logit),
                    "primary_bad12_probability": repr(primary_probability),
                    "treated_bad12_logit": repr(treated_logit),
                    "treated_bad12_probability": repr(treated_probability),
                    "predicted_log_airtime": repr(predicted_log),
                    "predicted_secondary_airtime_us": repr(predicted_cost),
                    "nonnegative_bad12_value": repr(value),
                    "value_per_cost_score_float32": repr(score),
                    "passes_score_threshold": "1" if self.action else "0",
                })
                if self.action:
                    row["meter_reserved_after_us"] = repr(reserved)
                    row["guard_available_after_us"] = repr(balance - reserved)
            rows.append(row)
        return rows

    def _write_inputs(self) -> None:
        write_csv(self.root / "prediction_samples.csv", self.sample_columns, self.samples)
        write_csv(
            self.root / "prediction_polling_samples.csv", self.polling_columns, self.polling
        )
        write_csv(
            self.root / "paired_value_t2_decisions.csv",
            self.decision_columns,
            self.decisions,
        )

    def use_score_aware_profile(
        self,
        *,
        full_horizon: bool = False,
        remaining_refill: bool = False,
        cost_free: bool = False,
    ) -> None:
        if cost_free and (full_horizon or remaining_refill):
            raise ValueError("cost-free fixture inherits V2 admission only")
        if remaining_refill:
            full_horizon = True
        self.decision_columns = (
            PAIRED_VALUE_T2_DECISION_COLUMNS
            + PAIRED_VALUE_T2_SCORE_AWARE_DECISION_SUFFIX
            + (
                PAIRED_VALUE_T2_REMAINING_REFILL_DECISION_SUFFIX
                if remaining_refill
                else ()
            )
            + (PAIRED_VALUE_T2_COST_FREE_DECISION_SUFFIX if cost_free else ())
        )
        runtime_contract_id = (
            PAIRED_VALUE_T2_COST_FREE_CONTRACT_ID
            if cost_free
            else PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_ID
            if remaining_refill
            else PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_ID
            if full_horizon
            else PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_ID
        )
        runtime_contract_sha256 = (
            PAIRED_VALUE_T2_COST_FREE_CONTRACT_SHA256
            if cost_free
            else PAIRED_VALUE_T2_REMAINING_REFILL_CONTRACT_SHA256
            if remaining_refill
            else PAIRED_VALUE_T2_FULL_HORIZON_CONTRACT_SHA256
            if full_horizon
            else PAIRED_VALUE_T2_SCORE_AWARE_CONTRACT_SHA256
        )
        admission_profile_id = (
            "cost_free_score_aware_v5"
            if cost_free
            else "score_aware_remaining_refill_v4"
            if remaining_refill
            else "score_aware_full_horizon_v3"
            if full_horizon
            else "score_aware_emergency_v2"
        )
        guard_max_horizon_us = 60_000_000 if full_horizon else 10_000_000
        guard_capacity_us = (
            int(PAIRED_VALUE_T2_FULL_HORIZON_GUARD_CAPACITY_US)
            if full_horizon
            else 60_000
        )
        self.decision_profile = {
            "score_aware": True,
            "remaining_refill": remaining_refill,
            "cost_free": cost_free,
            "decision_schema_version": 4 if cost_free else 3 if remaining_refill else 2,
            "summary_schema_version": 4 if cost_free else 3 if remaining_refill else 2,
            "runtime_contract_id": runtime_contract_id,
            "runtime_contract_sha256": runtime_contract_sha256,
            "decision_columns": self.decision_columns,
            "admission_profile_id": admission_profile_id,
            "guard_max_horizon_us": guard_max_horizon_us,
            "guard_capacity_us": guard_capacity_us,
            "model_metadata": (
                PAIRED_VALUE_T2_COST_FREE_MODEL_METADATA
                if cost_free
                else PAIRED_VALUE_T2_MODEL_METADATA
            ),
            "score_threshold": (
                PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD
                if cost_free
                else PAIRED_VALUE_T2_SCORE_THRESHOLD
            ),
            "score_threshold_bits": (
                0x3E3F68CF if cost_free else 0x38BBC0E5
            ),
            "emergency_score_threshold": (
                PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD
                if cost_free
                else PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD
            ),
            "emergency_score_threshold_bits": (
                0x3E9D2AC5 if cost_free else 0x391D4952
            ),
        }
        for row in self.decisions:
            strict = row["guard_admitted"] == "1"
            row.update({
                "schema_version": "4" if cost_free else "3" if remaining_refill else "2",
                "admission_profile_id": admission_profile_id,
                "ranker": (
                    "legacy_bad12_value"
                    if cost_free
                    else "legacy_bad12_value_per_cost"
                ),
                "score_threshold_float32": repr(
                    PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD
                    if cost_free
                    else PAIRED_VALUE_T2_SCORE_THRESHOLD
                ),
                "guard_max_horizon_us": str(guard_max_horizon_us),
                "guard_capacity_us": repr(guard_capacity_us),
                "strict_guard_admitted": "1" if strict else "0",
                "emergency_score_threshold_float32": repr(
                    PAIRED_VALUE_T2_COST_FREE_EMERGENCY_SCORE_THRESHOLD
                    if cost_free
                    else PAIRED_VALUE_T2_EMERGENCY_SCORE_THRESHOLD
                ),
                "passes_emergency_score_threshold": "0",
                "emergency_admission_considered": "0",
                "emergency_maximum_debt_us": repr(
                    PAIRED_VALUE_T2_EMERGENCY_MAXIMUM_DEBT_US
                ),
                "emergency_admitted": "0",
                "admission_tier": "strict" if strict else "none",
            })
            if cost_free:
                row["policy_score_float32"] = (
                    repr(f32(float(row["nonnegative_bad12_value"])))
                    if row["feature_evaluated"] == "1"
                    else ""
                )
            if remaining_refill:
                sample_ns = int(row["primary_sample_time_ns"])
                remaining_credit_us = 0.006 * (
                    (61_000_000_000 - sample_ns) / 1000.0
                )
                row.update({
                    "remaining_refill_credit_us": repr(remaining_credit_us),
                    "remaining_refill_admission_considered": "0",
                    "remaining_refill_admitted": "0",
                })
        self._write_inputs()

    def validate_decisions(self) -> dict[str, object]:
        duplicated = {8} if self.action else set()
        return _validate_paired_value_t2_decisions(
            self.root,
            RUN_ID,
            self.frames,
            self.policy_decisions,
            duplicated,
            self.decision_profile,
        )

    def write_summary(self, evidence: dict[str, object]) -> dict[str, object]:
        statuses = evidence["status_counts"]
        action_count = 1 if self.action else 0
        summary = {
            "schema_version": 1,
            "run_id": RUN_ID,
            "policy": "paired_value_duplication_t2",
            "runtime_contract_id": "paired-value-duplication-t2-runtime-v1",
            "runtime_contract_sha256": (
                "b9b9caf6cf49e73cb0669107576a17790f59bda4875c43f676caa426393dbf41"
            ),
            "source_artifacts": {
                "frozen_selection": {
                    "path": (
                        "experiments/model-selection/"
                        "temporal-t2-primary-only-two-objective-v1.json"
                    ),
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
                        "temporal_t2_primary_only_two_objective_v1/"
                        "temporal_t2_value_models.pkl"
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
            },
            "model": {
                "model_spec_id": "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1",
                "artifact_sha256": PAIRED_VALUE_T2_MODEL_ARTIFACT_SHA256,
                "feature_family": "primary_compact_physics_temporal",
                "feature_count": 246,
                "feature_adapter_id": "finite_numeric_float32_then_float64_one_hot_v1",
                "ordered_feature_names_sha256": (
                    "a00ebbb9807f99972f2cd009d1b2a20bf0b001cee123ac60d5121b2b1c07209e"
                ),
                "ranker": "legacy_bad12_value_per_cost",
                "frame_gate": "p_frames_only",
                "score_adapter_id": "final_candidate_float32_threshold_ge_v1",
                "score_threshold_float32": PAIRED_VALUE_T2_SCORE_THRESHOLD,
                "score_threshold_float32_bits_hex": "0x38bbc0e5",
            },
            "telemetry": {
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
            },
            "decision_window": {
                "measurement_start_ns": 1_000_000_000,
                "measurement_stop_ns": 61_000_000_000,
                "decision_start_ns": 1_000_000_000,
                "decision_stop_ns": 60_466_000_000,
                "interval_semantics": "half_open",
                "decision_stop_guard_us": 534_000,
            },
            "budget_guard": {
                "canonical_estimator_id": (
                    "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1"
                ),
                "cost_safety_factor": 1.25,
                "fraction": 0.006,
                "max_horizon_us": 10_000_000,
                "initial_horizon_us": 2_000_000,
                "capacity_us": 60_000,
                "initial_credit_us": 12_000,
                "initialization_time_ns": 1_000_000_000,
                "accounting_absolute_tolerance_us": 1e-9,
            },
            "counts": {
                "generated_frames": 9,
                "paired_t2_frames": 9,
                "outside_decision_window": statuses["outside_decision_window"],
                "history_warmup": statuses["history_warmup"],
                "frame_type_restricted": statuses["frame_type_restricted"],
                "not_actionable": statuses["not_actionable"],
                "descriptor_unavailable": statuses["descriptor_unavailable"],
                "feature_evaluated": 1,
                "below_score_threshold": statuses["below_score_threshold"],
                "score_threshold_passed": action_count,
                "airtime_guard_rejected": statuses["airtime_guard_rejected"],
                "launch_attempted": action_count,
                "launch_rejected": statuses["launch_rejected"],
                "secondary_launched": action_count,
                "secondary_settled": action_count,
            },
            "airtime": {
                "learned_predicted_cost_sum_evaluated_us": evidence["learned_evaluated"],
                "learned_predicted_cost_sum_launched_us": evidence["learned_launched"],
                "canonical_nominal_launched_sum_us": evidence["nominal_launched"],
                "canonical_reserved_launched_sum_us": evidence["reserved_launched"],
                "measured_secondary_airtime_debited_us": 0,
            },
            "integrity": {
                "pending_pair_empty": True,
                "generated_equals_paired": True,
                "status_counts_reconcile": True,
                "launches_equal_settlements": True,
                "launched_frame_ids_equal_duplicated_frame_ids": True,
                "meter_reserved_final_within_tolerance": True,
                "meter_reserved_final_raw_us": 0,
                "meter_reserved_final_normalized_us": 0,
                "learned_cost_used_for_token_accounting": False,
            },
        }
        if self.decision_profile is not None:
            summary.update({
                "schema_version": self.decision_profile["summary_schema_version"],
                "runtime_contract_id": self.decision_profile["runtime_contract_id"],
                "runtime_contract_sha256":
                    self.decision_profile["runtime_contract_sha256"],
            })
            summary["budget_guard"].update({
                "admission_profile_id":
                    self.decision_profile["admission_profile_id"],
                "max_horizon_us": self.decision_profile["guard_max_horizon_us"],
                "capacity_us": self.decision_profile["guard_capacity_us"],
                "emergency_score_threshold_float32":
                    self.decision_profile["emergency_score_threshold"],
                "emergency_score_threshold_float32_bits_hex": (
                    "0x3e9d2ac5"
                    if self.decision_profile["cost_free"]
                    else "0x391d4952"
                ),
                "emergency_maximum_debt_us": 60_000,
            })
            if self.decision_profile["cost_free"]:
                summary["model"].update({
                    "ranker": "legacy_bad12_value",
                    "score_threshold_float32":
                        PAIRED_VALUE_T2_COST_FREE_SCORE_THRESHOLD,
                    "score_threshold_float32_bits_hex": "0x3e3f68cf",
                })
            summary["counts"].update({
                "strict_guard_admitted": evidence["strict_guard_admitted"],
                "emergency_score_threshold_passed": evidence["emergency_score_passed"],
                "emergency_admission_considered":
                    evidence["emergency_admission_considered"],
                "emergency_admitted": evidence["emergency_admitted"],
            })
            if self.decision_profile["remaining_refill"]:
                summary["budget_guard"].update({
                    "remaining_refill_borrowing_enabled": True,
                    "remaining_refill_repayment_stop_ns": 61_000_000_000,
                    "remaining_refill_credit_formula": (
                        "fraction * (repayment_stop_ns - "
                        "last_causal_guard_refill_ns) / 1000"
                    ),
                })
                summary["counts"].update({
                    "remaining_refill_admission_considered":
                        evidence["remaining_refill_admission_considered"],
                    "remaining_refill_admitted": evidence["remaining_refill_admitted"],
                })
                summary["integrity"][
                    "strict_plus_emergency_plus_remaining_refill_admitted_equals_"
                    "launch_attempted"
                ] = True
            else:
                summary["integrity"][
                    "strict_plus_emergency_admitted_equals_launch_attempted"
                ] = True
        (self.root / "paired_value_t2_summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        if self.action:
            nominal = evidence["nominal_launched"]
            reserved = evidence["reserved_launched"]
            self.settlements = [{
                "run_id": RUN_ID,
                "frame_id": "8",
                "settlement_time_ns": "1300000000",
                "released_airtime_us": format(reserved, ".12g"),
                "measured_airtime_us": "0",
                "nominal_airtime_us": format(nominal, ".12g"),
                "fallback": "1",
            }]
        meter_summary = {
            "estimated_action_airtime_us": float(format(
                evidence["reserved_launched"], ".12g"
            )),
            "tagged_secondary_tx_airtime_us": 0,
        }
        return meter_summary


class PairedValueT2ValidationTest(unittest.TestCase):
    def fixture(
        self, *, action: bool = False
    ) -> tuple[tempfile.TemporaryDirectory[str], PairedValueT2Fixture]:
        temporary = tempfile.TemporaryDirectory()
        return temporary, PairedValueT2Fixture(Path(temporary.name), action=action)

    def test_accepts_exact_decisions_and_summary_with_action_evidence(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            evidence = fixture.validate_decisions()
            self.assertEqual(evidence["action_byte_quanta"], {8: 1316})
            meter_summary = fixture.write_summary(evidence)
            _validate_paired_value_t2_summary(
                fixture.root,
                RUN_ID,
                9,
                evidence,
                fixture.events,
                fixture.settlements,
                meter_summary,
            )
            meter_summary.update({
                "tagged_ppdu_count": 0,
                "mixed_ppdu_count": 0,
                "measurement_start_ns": 1_000_000_000,
                "measurement_stop_ns": 61_000_000_000,
                "measurement_duration_us": 60_000_000,
                "tagged_secondary_tx_airtime_fraction": 0,
                "maximum_budget_debt_us": 0,
                "actual_to_estimated_airtime_ratio": 0,
                "forced_reservation_settlements": 1,
                "budget_fraction": 0.006,
                "initial_bucket_capacity_us": 12_000,
                "finite_run_budget_us": 372_000,
                "budget_excess_us": 0,
            })
            _validate_secondary_airtime(
                fixture.events,
                fixture.settlements,
                meter_summary,
                {
                    "enabled": True,
                    "path_id": 0,
                    "copy_id": 1,
                    "definition": "secondary_sender_phy_tx_airtime",
                    "measurement_start_ns": 1_000_000_000,
                    "measurement_stop_ns": 61_000_000_000,
                },
                [{"link_id": "0", "phy_tx_time_us": "0"}],
                "paired_value_duplication_t2",
                RUN_ID,
                None,
                evidence["action_estimates"],
                {8},
                0,
                fixture.frames,
                {
                    "payload_size_bytes": 1200,
                    "frame_size_bytes": 12000,
                },
                evidence["action_nominals"],
                evidence["profile"]["guard_capacity_us"],
            )

    def test_replays_between_decision_budget_debt_from_phy_events(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            evidence = fixture.validate_decisions()
            launch_time_ns = int(
                fixture.decisions[8]["primary_sample_time_ns"]
            )
            event_time_ns = launch_time_ns + 1_000
            measured_us = 14_000.0
            events = [{
                "run_id": RUN_ID,
                "time_ns": str(event_time_ns),
                "path_id": "0",
                "ppdu_duration_us": format(measured_us, ".12g"),
                "tagged_mpdu_bytes": "13160",
                "frame_ids": "8",
                "mixed_ppdu": "0",
                "cumulative_tagged_airtime_us": format(measured_us, ".12g"),
            }]
            estimate = evidence["action_estimates"][8]
            nominal = evidence["action_nominals"][8]
            settlements = [{
                "run_id": RUN_ID,
                "frame_id": "8",
                "settlement_time_ns": str(event_time_ns + 1_000),
                "released_airtime_us": "0",
                "measured_airtime_us": format(measured_us, ".12g"),
                "nominal_airtime_us": format(nominal, ".12g"),
                "fallback": "0",
            }]
            capacity_us = float(evidence["profile"]["guard_capacity_us"])
            peak_debt_us = _paired_value_t2_event_maximum_debt(
                events,
                1_000_000_000,
                capacity_us,
            )
            self.assertGreater(peak_debt_us, 0)
            meter_summary = {
                "tagged_ppdu_count": 1,
                "mixed_ppdu_count": 0,
                "tagged_secondary_tx_airtime_us": measured_us,
                "measurement_start_ns": 1_000_000_000,
                "measurement_stop_ns": 61_000_000_000,
                "measurement_duration_us": 60_000_000,
                "tagged_secondary_tx_airtime_fraction": float(format(
                    measured_us / 60_000_000,
                    ".12g",
                )),
                "maximum_budget_debt_us": float(format(peak_debt_us, ".12g")),
                "estimated_action_airtime_us": float(format(estimate, ".12g")),
                "actual_to_estimated_airtime_ratio": float(format(
                    measured_us / estimate,
                    ".12g",
                )),
                "forced_reservation_settlements": 0,
                "budget_fraction": 0.006,
                "initial_bucket_capacity_us": 12_000,
                "finite_run_budget_us": 372_000,
                "budget_excess_us": 0,
            }
            arguments = (
                events,
                settlements,
                meter_summary,
                {
                    "enabled": True,
                    "path_id": 0,
                    "copy_id": 1,
                    "definition": "secondary_sender_phy_tx_airtime",
                    "measurement_start_ns": 1_000_000_000,
                    "measurement_stop_ns": 61_000_000_000,
                },
                [{"link_id": "0", "phy_tx_time_us": "14000"}],
                "paired_value_duplication_t2",
                RUN_ID,
                None,
                evidence["action_estimates"],
                {8},
                0.0,
                fixture.frames,
                {
                    "payload_size_bytes": 1200,
                    "frame_size_bytes": 12000,
                },
                evidence["action_nominals"],
                capacity_us,
            )
            _validate_secondary_airtime(*arguments)
            meter_summary["maximum_budget_debt_us"] = 0
            with self.assertRaisesRegex(
                ValidationError,
                "maximum debt differs from exact replay",
            ):
                _validate_secondary_airtime(*arguments)

    def test_cost_reduction_crosscheck_uses_forward_error_scale(self) -> None:
        ordered = 1.0
        vectorized = ordered + 64 * math.ulp(ordered)
        self.assertTrue(
            _paired_value_t2_cost_reductions_close(
                ordered,
                vectorized,
                1_000.0,
                263,
            )
        )
        self.assertFalse(
            _paired_value_t2_cost_reductions_close(
                ordered,
                ordered + 1e-6,
                1_000.0,
                263,
            )
        )

    def test_accepts_score_aware_schema_and_reconstructs_strict_tier(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            fixture.use_score_aware_profile()
            evidence = fixture.validate_decisions()
            self.assertEqual(evidence["strict_guard_admitted"], 1)
            self.assertEqual(evidence["emergency_score_passed"], 0)
            self.assertEqual(evidence["emergency_admission_considered"], 0)
            self.assertEqual(evidence["emergency_admitted"], 0)
            meter_summary = fixture.write_summary(evidence)
            _validate_paired_value_t2_summary(
                fixture.root,
                RUN_ID,
                9,
                evidence,
                fixture.events,
                fixture.settlements,
                meter_summary,
            )
            fixture.decisions[8]["strict_guard_admitted"] = "0"
            fixture._write_inputs()
            with self.assertRaisesRegex(ValidationError, "emergency admission differs"):
                fixture.validate_decisions()

    def test_accepts_cost_free_schema_and_replays_active_score(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            fixture.use_score_aware_profile(cost_free=True)
            evidence = fixture.validate_decisions()
            self.assertTrue(evidence["profile"]["cost_free"])
            self.assertEqual(evidence["strict_guard_admitted"], 1)
            self.assertEqual(
                float(fixture.decisions[8]["policy_score_float32"]),
                f32(float(fixture.decisions[8]["nonnegative_bad12_value"])),
            )
            meter_summary = fixture.write_summary(evidence)
            _validate_paired_value_t2_summary(
                fixture.root,
                RUN_ID,
                9,
                evidence,
                fixture.events,
                fixture.settlements,
                meter_summary,
            )
            fixture.decisions[8]["policy_score_float32"] = repr(
                math.nextafter(
                    float(fixture.decisions[8]["policy_score_float32"]),
                    math.inf,
                )
            )
            fixture._write_inputs()
            with self.assertRaisesRegex(ValidationError, "cost-free policy score"):
                fixture.validate_decisions()

    def test_accepts_full_horizon_profile_without_startup_credit_inflation(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            fixture.use_score_aware_profile(full_horizon=True)
            evidence = fixture.validate_decisions()
            self.assertEqual(evidence["profile"]["guard_max_horizon_us"], 60_000_000)
            self.assertEqual(evidence["profile"]["guard_capacity_us"], 360_000.0)
            meter_summary = fixture.write_summary(evidence)
            _validate_paired_value_t2_summary(
                fixture.root,
                RUN_ID,
                9,
                evidence,
                fixture.events,
                fixture.settlements,
                meter_summary,
            )
            fixture.decisions[0]["guard_initial_credit_us"] = "360000"
            fixture._write_inputs()
            with self.assertRaisesRegex(ValidationError, "guard metadata differs"):
                fixture.validate_decisions()

    def test_accepts_remaining_refill_schema_and_summary(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            fixture.use_score_aware_profile(remaining_refill=True)
            evidence = fixture.validate_decisions()
            self.assertEqual(evidence["strict_guard_admitted"], 1)
            self.assertEqual(evidence["remaining_refill_admission_considered"], 0)
            self.assertEqual(evidence["remaining_refill_admitted"], 0)
            meter_summary = fixture.write_summary(evidence)
            _validate_paired_value_t2_summary(
                fixture.root,
                RUN_ID,
                9,
                evidence,
                fixture.events,
                fixture.settlements,
                meter_summary,
            )

    def test_reconstructs_remaining_refill_admission_after_inherited_tiers_fail(
        self,
    ) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            fixture.use_score_aware_profile(remaining_refill=True)
            row = fixture.decisions[8]
            primary_probability = 0.2
            treated_probability = 0.1
            predicted_cost = 1000.0
            nonnegative_value = primary_probability - treated_probability
            row.update({
                "primary_bad12_logit": repr(
                    math.log(primary_probability / (1.0 - primary_probability))
                ),
                "primary_bad12_probability": repr(primary_probability),
                "treated_bad12_logit": repr(
                    math.log(treated_probability / (1.0 - treated_probability))
                ),
                "treated_bad12_probability": repr(treated_probability),
                "predicted_log_airtime": repr(
                    math.log1p(predicted_cost) - PAIRED_VALUE_T2_LOG_SMEARING_FACTOR
                ),
                "predicted_secondary_airtime_us": repr(predicted_cost),
                "nonnegative_bad12_value": repr(nonnegative_value),
                "value_per_cost_score_float32": repr(
                    f32(nonnegative_value / predicted_cost)
                ),
                "guard_balance_before_us": "0",
                "guard_available_before_us": "0",
                "guard_balance_after_us": "0",
                "guard_available_after_us": repr(
                    -float(row["canonical_reserved_airtime_us"])
                ),
                "strict_guard_admitted": "0",
                "passes_emergency_score_threshold": "0",
                "emergency_admission_considered": "0",
                "emergency_admitted": "0",
                "remaining_refill_admission_considered": "1",
                "remaining_refill_admitted": "1",
                "admission_tier": "remaining_refill",
            })
            fixture._write_inputs()
            with mock.patch(
                "validate_outputs._validate_paired_value_t2_model_replays"
            ):
                evidence = fixture.validate_decisions()
                self.assertEqual(evidence["strict_guard_admitted"], 0)
                self.assertEqual(evidence["emergency_admitted"], 0)
                self.assertEqual(evidence["remaining_refill_admission_considered"], 1)
                self.assertEqual(evidence["remaining_refill_admitted"], 1)
                fixture.decisions[8]["remaining_refill_admitted"] = "0"
                fixture._write_inputs()
                with self.assertRaisesRegex(
                    ValidationError, "remaining-refill admission differs"
                ):
                    fixture.validate_decisions()

    def test_rejects_header_order_and_row_width_drift(self) -> None:
        for mutation in ("header", "extra width", "short width"):
            with self.subTest(mutation=mutation):
                temporary, fixture = self.fixture()
                with temporary:
                    columns = list(PAIRED_VALUE_T2_DECISION_COLUMNS)
                    rows = copy.deepcopy(fixture.decisions)
                    if mutation == "header":
                        columns[0], columns[1] = columns[1], columns[0]
                    write_csv(fixture.root / "paired_value_t2_decisions.csv", columns, rows)
                    if mutation != "header":
                        path = fixture.root / "paired_value_t2_decisions.csv"
                        lines = path.read_text(encoding="utf-8").splitlines()
                        if mutation == "extra width":
                            lines[1] += ",x"
                        else:
                            lines[1] = lines[1].rsplit(",", 1)[0]
                        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    expected = "ordered schema" if mutation == "header" else "row width"
                    with self.assertRaisesRegex(ValidationError, expected):
                        fixture.validate_decisions()

    def test_rejects_endpoint_lag_gate_and_float32_drift(self) -> None:
        mutations = {
            "secondary progress": lambda fixture: fixture.samples[0].update({
                "packets_submitted": "1"
            }),
            "exact lag": lambda fixture: fixture.decisions[8].update({
                "lag8_frame_id": "1"
            }),
            "gate status": lambda fixture: fixture.decisions[8].update({
                "decision_status": "action"
            }),
            "model nullability": lambda fixture: fixture.decisions[0].update({
                "primary_bad12_logit": "0"
            }),
            "frame identity": lambda fixture: fixture.decisions[8].update({
                "frame_id": "7"
            }),
            "float32 score": lambda fixture: fixture.decisions[8].update({
                "value_per_cost_score_float32": repr(f32(1e-8))
            }),
            "accounting": lambda fixture: fixture.decisions[8].update({
                "guard_available_before_us": "1"
            }),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                temporary, fixture = self.fixture()
                with temporary:
                    mutate(fixture)
                    fixture._write_inputs()
                    with self.assertRaises(ValidationError):
                        fixture.validate_decisions()

    def test_rejects_coherent_but_noncanonical_model_output(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            row = fixture.decisions[8]
            probability = 0.2
            logit = math.log(probability / (1.0 - probability))
            predicted_cost = 1000.0
            row.update({
                "primary_bad12_logit": repr(logit),
                "primary_bad12_probability": repr(probability),
                "treated_bad12_logit": repr(logit),
                "treated_bad12_probability": repr(probability),
                "predicted_log_airtime": repr(
                    math.log1p(predicted_cost) - PAIRED_VALUE_T2_LOG_SMEARING_FACTOR
                ),
                "predicted_secondary_airtime_us": repr(predicted_cost),
                "nonnegative_bad12_value": "0",
                "value_per_cost_score_float32": "0",
                "passes_score_threshold": "0",
            })
            fixture._write_inputs()
            with self.assertRaisesRegex(ValidationError, "canonical model replay differs"):
                fixture.validate_decisions()

    def test_replays_compiled_cost_accumulation_order(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            fixture.validate_decisions()
            # sklearn's vector reduction is the same canonical ridge model but
            # differs from the compiled evaluator's ordered scalar sum by five
            # binary64 ULPs for this feature vector.
            fixture.decisions[8].update({
                "predicted_log_airtime": "6.950695605650517",
                "predicted_secondary_airtime_us": "1138.7851611363524",
            })
            fixture._write_inputs()
            with self.assertRaisesRegex(ValidationError, "canonical model replay differs"):
                fixture.validate_decisions()

    def test_rejects_over_wide_paired_polling_row(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            path = fixture.root / "prediction_polling_samples.csv"
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[1] += ",unexpected"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "more values than the header"):
                _csv(path, set(fixture.polling_columns))

    def test_rejects_meter_causality_attribution_and_allocation_drift(self) -> None:
        temporary, fixture = self.fixture(action=True)
        with temporary:
            evidence = fixture.validate_decisions()
            meter_summary = fixture.write_summary(evidence)
            del meter_summary
            launch_time = int(fixture.decisions[8]["primary_sample_time_ns"])
            estimate = evidence["action_estimates"][8]
            nominal = evidence["action_nominals"][8]
            event = {
                "run_id": RUN_ID,
                "time_ns": str(launch_time + 1_000),
                "path_id": "0",
                "ppdu_duration_us": "100",
                "tagged_mpdu_bytes": "1200",
                "frame_ids": "8",
                "mixed_ppdu": "0",
                "cumulative_tagged_airtime_us": "100",
            }
            settlement = {
                "run_id": RUN_ID,
                "frame_id": "8",
                "settlement_time_ns": str(launch_time + 2_000),
                "released_airtime_us": format(estimate - 100.0, ".12g"),
                "measured_airtime_us": "100",
                "nominal_airtime_us": format(nominal, ".12g"),
                "fallback": "0",
            }
            _replay_paired_value_t2_meter(
                evidence["rows"],
                [event],
                [settlement],
                evidence["action_estimates"],
                evidence["action_nominals"],
            )

            mutations = {
                "event before launch": (
                    {"time_ns": str(launch_time - 1)},
                    {},
                ),
                "unlaunched attribution": ({"frame_ids": "7"}, {}),
                "settlement before launch": (
                    {},
                    {"settlement_time_ns": str(launch_time - 1)},
                ),
                "settlement before event": (
                    {},
                    {"settlement_time_ns": str(launch_time + 500)},
                ),
                "infeasible allocation": (
                    {},
                    {
                        "measured_airtime_us": "50",
                        "released_airtime_us": format(estimate - 50.0, ".12g"),
                    },
                ),
            }
            for name, (event_update, settlement_update) in mutations.items():
                with self.subTest(name=name):
                    changed_event = copy.deepcopy(event)
                    changed_settlement = copy.deepcopy(settlement)
                    changed_event.update(event_update)
                    changed_settlement.update(settlement_update)
                    with self.assertRaises(ValidationError):
                        _replay_paired_value_t2_meter(
                            evidence["rows"],
                            [changed_event],
                            [changed_settlement],
                            evidence["action_estimates"],
                            evidence["action_nominals"],
                        )

    def test_rejects_reassigned_event_between_two_active_actions(self) -> None:
        decisions = [
            {
                "frame_id": "0",
                "primary_sample_time_ns": "1100000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "0",
                "meter_reserved_after_us": "100",
            },
            {
                "frame_id": "1",
                "primary_sample_time_ns": "1200000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "100",
                "meter_reserved_after_us": "200",
            },
        ]
        event = {
            "time_ns": "1250000000",
            "ppdu_duration_us": "40",
            "tagged_mpdu_bytes": "1200",
            "frame_ids": "0",
        }
        settlements = [
            {
                "frame_id": "0",
                "settlement_time_ns": "1300000000",
                "released_airtime_us": "60",
                "measured_airtime_us": "40",
                "nominal_airtime_us": "80",
            },
            {
                "frame_id": "1",
                "settlement_time_ns": "1400000000",
                "released_airtime_us": "100",
                "measured_airtime_us": "0",
                "nominal_airtime_us": "80",
            },
        ]
        estimates = {0: 100.0, 1: 100.0}
        nominals = {0: 80.0, 1: 80.0}
        _replay_paired_value_t2_meter(
            decisions, [event], settlements, estimates, nominals
        )
        reassigned = copy.deepcopy(event)
        reassigned["frame_ids"] = "1"
        with self.assertRaisesRegex(ValidationError, "allocation|positive airtime"):
            _replay_paired_value_t2_meter(
                decisions, [reassigned], settlements, estimates, nominals
            )
        listed_without_allocation = copy.deepcopy(event)
        listed_without_allocation["frame_ids"] = "0;1"
        with self.assertRaisesRegex(
            ValidationError, "positive airtime|no feasible event allocation"
        ):
            _replay_paired_value_t2_meter(
                decisions,
                [listed_without_allocation],
                settlements,
                estimates,
                nominals,
            )

    def test_accepts_jointly_feasible_shared_ppdu_checkpoints(self) -> None:
        reservation = 1983.760667318285
        after_first = reservation - 1590.8
        after_shared = reservation - 1563.8
        decisions = [
            {
                "frame_id": "9",
                "primary_sample_time_ns": "1302000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "0",
                "meter_reserved_after_us": repr(reservation),
            },
            {
                "frame_id": "10",
                "primary_sample_time_ns": "1335333333",
                "secondary_launched": "1",
                "meter_reserved_before_us": repr(after_first),
                "meter_reserved_after_us": repr(after_first + reservation),
            },
            {
                "frame_id": "11",
                "primary_sample_time_ns": "1368666667",
                "secondary_launched": "0",
                "meter_reserved_before_us": repr(after_shared),
                "meter_reserved_after_us": repr(after_shared),
            },
            {
                "frame_id": "12",
                "primary_sample_time_ns": "1402000000",
                "secondary_launched": "0",
                "meter_reserved_before_us": repr(after_shared),
                "meter_reserved_after_us": repr(after_shared),
            },
            {
                "frame_id": "13",
                "primary_sample_time_ns": "1435333333",
                "secondary_launched": "0",
                "meter_reserved_before_us": "0",
                "meter_reserved_after_us": "0",
            },
        ]
        events = [
            {
                "time_ns": "1304611377",
                "ppdu_duration_us": "1590.8",
                "tagged_mpdu_bytes": "13160",
                "frame_ids": "9",
            },
            {
                "time_ns": "1341395435",
                "ppdu_duration_us": "3127.6",
                "tagged_mpdu_bytes": "26320",
                "frame_ids": "9;10",
            },
            {
                "time_ns": "1410599461",
                "ppdu_duration_us": "3127.6",
                "tagged_mpdu_bytes": "26320",
                "frame_ids": "9;10",
            },
        ]
        settlements = [
            {
                "frame_id": "9",
                "settlement_time_ns": "1413815127",
                "released_airtime_us": "0",
                "measured_airtime_us": "4718.4",
                "nominal_airtime_us": "1000",
            },
            {
                "frame_id": "10",
                "settlement_time_ns": "1413815127",
                "released_airtime_us": "0",
                "measured_airtime_us": "3127.6",
                "nominal_airtime_us": "1000",
            },
        ]
        estimates = {9: reservation, 10: reservation}
        nominals = {9: 1000.0, 10: 1000.0}
        byte_quanta = {9: 1316, 10: 1316}
        _replay_paired_value_t2_meter(
            decisions, events, settlements, estimates, nominals, byte_quanta
        )

        invalid_quantum = copy.deepcopy(events)
        invalid_quantum[1]["tagged_mpdu_bytes"] = "26321"
        with self.assertRaisesRegex(ValidationError, "MPDU quantum"):
            _replay_paired_value_t2_meter(
                decisions,
                invalid_quantum,
                settlements,
                estimates,
                nominals,
                byte_quanta,
            )

        inconsistent = copy.deepcopy(decisions)
        inconsistent[2]["meter_reserved_before_us"] = repr(after_shared + 1.0)
        inconsistent[2]["meter_reserved_after_us"] = repr(after_shared + 1.0)
        with self.assertRaisesRegex(
            ValidationError, "no feasible event allocation|witness violates"
        ):
            _replay_paired_value_t2_meter(
                inconsistent,
                events,
                settlements,
                estimates,
                nominals,
                byte_quanta,
            )

    def test_requires_integer_ppdu_byte_allocations(self) -> None:
        decisions = [
            {
                "frame_id": "0",
                "primary_sample_time_ns": "1100000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "0",
                "meter_reserved_after_us": "1",
            },
            {
                "frame_id": "1",
                "primary_sample_time_ns": "1200000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "1",
                "meter_reserved_after_us": "2",
            },
        ]
        event = {
            "time_ns": "1250000000",
            "ppdu_duration_us": "1",
            "tagged_mpdu_bytes": "3",
            "frame_ids": "0;1",
        }
        settlements = [
            {
                "frame_id": "0",
                "settlement_time_ns": "1300000000",
                "released_airtime_us": "0.666666666667",
                "measured_airtime_us": "0.333333333333",
                "nominal_airtime_us": "1",
            },
            {
                "frame_id": "1",
                "settlement_time_ns": "1300000000",
                "released_airtime_us": "0.333333333333",
                "measured_airtime_us": "0.666666666667",
                "nominal_airtime_us": "1",
            },
        ]
        estimates = {0: 1.0, 1: 1.0}
        nominals = {0: 1.0, 1: 1.0}
        _replay_paired_value_t2_meter(
            decisions, [event], settlements, estimates, nominals
        )

        impossible = copy.deepcopy(settlements)
        for settlement in impossible:
            settlement["released_airtime_us"] = "0.5"
            settlement["measured_airtime_us"] = "0.5"
        with self.assertRaisesRegex(ValidationError, "no feasible event allocation"):
            _replay_paired_value_t2_meter(
                decisions, [event], impossible, estimates, nominals
            )

    def test_replays_short_final_mpdu_profile_exactly(self) -> None:
        decisions = [
            {
                "frame_id": "0",
                "primary_sample_time_ns": "1100000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "0",
                "meter_reserved_after_us": "1",
            }
        ]
        event = {
            "time_ns": "1200000000",
            "ppdu_duration_us": "1",
            "tagged_mpdu_bytes": "16",
            "frame_ids": "0",
        }
        settlement = {
            "frame_id": "0",
            "settlement_time_ns": "1300000000",
            "released_airtime_us": "0",
            "measured_airtime_us": "1",
            "nominal_airtime_us": "1",
        }
        estimates = {0: 1.0}
        nominals = {0: 1.0}
        profiles = {0: (10, 6, 2)}
        _replay_paired_value_t2_meter(
            decisions,
            [event],
            [settlement],
            estimates,
            nominals,
            action_mpdu_profiles=profiles,
        )

        impossible = copy.deepcopy(event)
        impossible["tagged_mpdu_bytes"] = "15"
        with self.assertRaisesRegex(
            ValidationError, "no feasible|witness violates"
        ):
            _replay_paired_value_t2_meter(
                decisions,
                [impossible],
                [settlement],
                estimates,
                nominals,
                action_mpdu_profiles=profiles,
            )

    def test_checks_rounded_solver_integer_witness(self) -> None:
        decisions = [
            {
                "frame_id": "0",
                "primary_sample_time_ns": "1100000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "0",
                "meter_reserved_after_us": "1",
            },
            {
                "frame_id": "1",
                "primary_sample_time_ns": "1200000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "1",
                "meter_reserved_after_us": "2",
            },
        ]
        event = {
            "time_ns": "1250000000",
            "ppdu_duration_us": "1",
            "tagged_mpdu_bytes": "3",
            "frame_ids": "0;1",
        }
        settlements = [
            {
                "frame_id": "0",
                "settlement_time_ns": "1300000000",
                "released_airtime_us": "0.666666666667",
                "measured_airtime_us": "0.333333333333",
                "nominal_airtime_us": "1",
            },
            {
                "frame_id": "1",
                "settlement_time_ns": "1300000000",
                "released_airtime_us": "0.333333333333",
                "measured_airtime_us": "0.666666666667",
                "nominal_airtime_us": "1",
            },
        ]
        estimates = {0: 1.0, 1: 1.0}
        nominals = {0: 1.0, 1: 1.0}

        from scipy import optimize

        original_milp = optimize.milp

        def represented_with_residual(*args, **kwargs):
            result = original_milp(*args, **kwargs)
            result.x = result.x.copy()
            result.x[0] += 2.6e-7
            return result

        with mock.patch("scipy.optimize.milp", side_effect=represented_with_residual):
            _replay_paired_value_t2_meter(
                decisions, [event], settlements, estimates, nominals
            )

        def represented_with_wrong_rounding(*args, **kwargs):
            result = original_milp(*args, **kwargs)
            result.x = result.x.copy()
            result.x[0] += 0.51
            return result

        with mock.patch("scipy.optimize.milp", side_effect=represented_with_wrong_rounding):
            with self.assertRaisesRegex(ValidationError, "witness violates"):
                _replay_paired_value_t2_meter(
                    decisions, [event], settlements, estimates, nominals
                )

    def test_rejects_solver_tolerance_and_noncanonical_event_evidence(self) -> None:
        decisions = [
            {
                "frame_id": "0",
                "primary_sample_time_ns": "1100000000",
                "secondary_launched": "1",
                "meter_reserved_before_us": "0",
                "meter_reserved_after_us": "1",
            },
            {
                "frame_id": "1",
                "primary_sample_time_ns": "1200000000",
                "secondary_launched": "0",
                "meter_reserved_before_us": "0.75",
                "meter_reserved_after_us": "0.75",
            },
        ]
        event = {
            "time_ns": "1150000000",
            "ppdu_duration_us": "0.25",
            "tagged_mpdu_bytes": "1",
            "frame_ids": "0",
        }
        settlement = {
            "frame_id": "0",
            "settlement_time_ns": "1300000000",
            "released_airtime_us": "0.75",
            "measured_airtime_us": "0.25",
            "nominal_airtime_us": "1",
        }
        _replay_paired_value_t2_meter(
            decisions, [event], [settlement], {0: 1.0}, {0: 1.0}
        )

        drifted = copy.deepcopy(decisions)
        drifted[1]["meter_reserved_before_us"] = "0.75000005"
        drifted[1]["meter_reserved_after_us"] = "0.75000005"
        with self.assertRaisesRegex(
            ValidationError, "no feasible event allocation|witness violates"
        ):
            _replay_paired_value_t2_meter(
                drifted, [event], [settlement], {0: 1.0}, {0: 1.0}
            )

        noncanonical = copy.deepcopy(event)
        noncanonical["ppdu_duration_us"] = "0.2500000000001"
        with self.assertRaisesRegex(ValidationError, "not canonical"):
            _replay_paired_value_t2_meter(
                decisions, [noncanonical], [settlement], {0: 1.0}, {0: 1.0}
            )

        unsorted = copy.deepcopy(event)
        unsorted.update({"tagged_mpdu_bytes": "2", "frame_ids": "1;0"})
        with self.assertRaisesRegex(ValidationError, "not canonical"):
            _replay_paired_value_t2_meter(
                decisions, [unsorted], [settlement], {0: 1.0}, {0: 1.0}
            )

    def test_rejects_submillisecond_controller_summary_airtime_drift(self) -> None:
        for key in (
            "canonical_reserved_launched_sum_us",
            "measured_secondary_airtime_debited_us",
        ):
            with self.subTest(key=key):
                temporary, fixture = self.fixture(action=True)
                with temporary:
                    evidence = fixture.validate_decisions()
                    meter_summary = fixture.write_summary(evidence)
                    path = fixture.root / "paired_value_t2_summary.json"
                    summary = json.loads(path.read_text(encoding="utf-8"))
                    summary["airtime"][key] += 0.0001
                    path.write_text(json.dumps(summary), encoding="utf-8")
                    with self.assertRaisesRegex(ValidationError, "airtime.*differs"):
                        _validate_paired_value_t2_summary(
                            fixture.root,
                            RUN_ID,
                            9,
                            evidence,
                            fixture.events,
                            fixture.settlements,
                            meter_summary,
                        )

    def test_accepts_accumulated_meter_serialization_envelope(self) -> None:
        serialized = ["4"] * 224
        self.assertTrue(_paired_meter_sum_close(896.0 - 1.4e-9, serialized))
        self.assertFalse(_paired_meter_sum_close(896.0 - 0.0001, serialized))
        settlement_values = [
            "4064.8", "1590.8", "2623.6", "3506.8", "3915.2", "6484",
            "1586.33333333", "10931.2666667", "3616", "2107.2", "3452.8",
        ]
        self.assertTrue(_paired_meter_sum_close(43878.8, settlement_values))
        self.assertFalse(_paired_meter_sum_close(43878.8001, settlement_values))

    def test_rejects_action_settlement_and_summary_count_drift(self) -> None:
        action_temporary, action_fixture = self.fixture(action=True)
        with action_temporary:
            action_fixture.policy_decisions[8]["duplicated"] = "0"
            action_fixture.policy_decisions[8]["secondary_link"] = ""
            with self.assertRaisesRegex(ValidationError, "launch evidence differs"):
                action_fixture.validate_decisions()

        temporary, fixture = self.fixture(action=True)
        with temporary:
            evidence = fixture.validate_decisions()
            meter_summary = fixture.write_summary(evidence)
            fixture.settlements[0]["frame_id"] = "7"
            with self.assertRaisesRegex(ValidationError, "settlements do not match actions"):
                _validate_paired_value_t2_summary(
                    fixture.root,
                    RUN_ID,
                    9,
                    evidence,
                    fixture.events,
                    fixture.settlements,
                    meter_summary,
                )
            fixture.settlements[0]["frame_id"] = "8"
            summary = json.loads(
                (fixture.root / "paired_value_t2_summary.json").read_text(encoding="utf-8")
            )
            summary["counts"]["secondary_launched"] = 0
            (fixture.root / "paired_value_t2_summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValidationError, "counts differ"):
                _validate_paired_value_t2_summary(
                    fixture.root,
                    RUN_ID,
                    9,
                    evidence,
                    fixture.events,
                    fixture.settlements,
                    meter_summary,
                )

    def test_accepts_exact_resolved_config_and_rejects_nearby_guard(self) -> None:
        config = copy.deepcopy(DECLARED_NEUTRAL_ENVIRONMENT)
        wifi = config.pop("shared_target_wifi")
        wifi.update(copy.deepcopy(DECLARED_TOPOLOGY_WIFI["dual_interface"]))
        config.update({
            "topology": "dual_interface",
            "policy": "paired_value_duplication_t2",
            "environment": "unchanged_neutral_mixed4x4",
            "wifi": wifi,
            "pairedValueDuplicationT2": copy.deepcopy(PAIRED_VALUE_T2_CONFIG),
            "predictionTelemetry": copy.deepcopy(PAIRED_VALUE_T2_PREDICTION_CONFIG),
            "secondaryAirtimeMeter": {
                "enabled": True,
                "path_id": 0,
                "copy_id": 1,
                "definition": "secondary_sender_phy_tx_airtime",
                "measurement_start_ns": 1_000_000_000,
                "measurement_stop_ns": 61_000_000_000,
            },
        })
        config.pop("randomizedIntervention", None)
        _validate_paired_value_t2_config(config)
        score_aware_config = copy.deepcopy(config)
        score_aware_config["pairedValueDuplicationT2"] = copy.deepcopy(
            PAIRED_VALUE_T2_SCORE_AWARE_CONFIG
        )
        profile = _validate_paired_value_t2_config(score_aware_config)
        self.assertTrue(profile["score_aware"])
        full_horizon_config = copy.deepcopy(config)
        full_horizon_config["pairedValueDuplicationT2"] = copy.deepcopy(
            PAIRED_VALUE_T2_FULL_HORIZON_CONFIG
        )
        profile = _validate_paired_value_t2_config(full_horizon_config)
        self.assertTrue(profile["score_aware"])
        self.assertEqual(profile["guard_max_horizon_us"], 60_000_000)
        self.assertEqual(profile["guard_capacity_us"], 360_000)
        remaining_refill_config = copy.deepcopy(config)
        remaining_refill_config["pairedValueDuplicationT2"] = copy.deepcopy(
            PAIRED_VALUE_T2_REMAINING_REFILL_CONFIG
        )
        profile = _validate_paired_value_t2_config(remaining_refill_config)
        self.assertTrue(profile["score_aware"])
        self.assertTrue(profile["remaining_refill"])
        self.assertEqual(profile["decision_schema_version"], 3)
        cost_free_config = copy.deepcopy(config)
        cost_free_config["pairedValueDuplicationT2"] = copy.deepcopy(
            PAIRED_VALUE_T2_COST_FREE_CONFIG
        )
        profile = _validate_paired_value_t2_config(cost_free_config)
        self.assertTrue(profile["score_aware"])
        self.assertTrue(profile["cost_free"])
        self.assertFalse(profile["remaining_refill"])
        self.assertEqual(profile["decision_schema_version"], 4)
        changed_environment = copy.deepcopy(config)
        changed_environment["wifi"]["queue_max_packets"] = 501
        with self.assertRaisesRegex(ValidationError, "shared target Wi-Fi differs"):
            _validate_paired_value_t2_config(changed_environment)
        conflicting_controller = copy.deepcopy(config)
        conflicting_controller["selectiveDuplication"] = {}
        with self.assertRaisesRegex(ValidationError, "another controller"):
            _validate_paired_value_t2_config(conflicting_controller)

        generalization_config = copy.deepcopy(score_aware_config)
        generalization_config["pairedTemporalT2FrameProfile"] = (
            "environment_generalization_v1"
        )
        generalization_config["environment"] = (
            "held_out_environment_generalization_v1"
        )
        generalization_config["stream"].update({
            "fps": 24,
            "frame_size_bytes": 13700,
            "gop_length": 120,
            "keyframe_size_multiplier": 3.999,
            "deadline_us": 41667,
        })
        generalization_config["background"]["profile"] = "legacy_mixed8"
        generalization_config["propagation"]["path_loss_exponent"] = 3.1893
        _validate_paired_value_t2_config(generalization_config)

        invalid_generalization = copy.deepcopy(generalization_config)
        invalid_generalization["stream"]["frame_size_bytes"] = 14001
        with self.assertRaisesRegex(ValidationError, "outside the frozen domain"):
            _validate_paired_value_t2_config(invalid_generalization)

        config["pairedValueDuplicationT2"]["budget_fraction"] = 0.0060001
        with self.assertRaisesRegex(ValidationError, "differs from contract"):
            _validate_paired_value_t2_config(config)


if __name__ == "__main__":
    unittest.main()
