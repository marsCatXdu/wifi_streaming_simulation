#!/usr/bin/env python3
"""Adversarial checks for adaptive-airtime output validation."""

from __future__ import annotations

import copy
import csv
import json
import statistics
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from validate_outputs import (
    PRIMARY_T0_MODEL_ID,
    PRIMARY_TARGET_ID,
    PRIMARY_TARGET_PROVENANCE_SHA256,
    ValidationError,
    _prediction_model_offsets_valid,
    _validate_adaptive_config,
    _validate_adaptive_decisions,
    _validate_secondary_airtime,
)
from plot_adaptive_airtime_duplication import (
    _interval,
    plot_adaptive_airtime,
    summarize_adaptive_runs,
)
from run_experiments import CLI_KEYS


def adaptive_config() -> dict[str, object]:
    return {
        "model_id": "commodity_polling_1ms_genuine_v1",
        "source_model_sha256": "a" * 64,
        "feature_set": "F0+F1-degraded",
        "degradation_profile": "polling_1ms",
        "calibration": "platt",
        "stages": ["T0", "T1"],
        "primary_path": 1,
        "secondary_path": 0,
        "budget_definition": "secondary_sender_phy_tx_airtime",
        "budget_fraction": 0.05,
        "bucket_horizon_us": 200_000,
        "initial_bucket_capacity_us": 10_000.0,
        "initial_shadow_price": 0.2,
        "dual_step": 0.03,
        "cost_safety_factor": 1.4,
        "cost_ewma_alpha": 0.25,
        "decision_offsets_us": [0, 1000],
    }


def adaptive_rows() -> list[dict[str, str]]:
    common = {
        "run_id": "run",
        "frame_id": "7",
        "actionable": "1",
        "estimated_airtime_us": "100",
        "reference_airtime_us": "100",
        "shadow_price": "0.2",
        "normalized_cost": "1",
        "airtime_budget_fraction": "0.05",
        "bucket_capacity_us": "10000",
        "bucket_balance_us": "10000",
        "initial_bucket_capacity_us": "10000",
        "reserved_airtime_us": "0",
        "available_airtime_us": "10000",
        "measured_airtime_total_us": "0",
    }
    return [
        {
            **common,
            "sample_stage": "T0",
            "sample_offset_us": "0",
            "sample_time_ns": "1000000",
            "calibrated_probability": "0.1",
            "net_utility": "-0.1",
            "decision": "price_rejected",
            "secondary_launched": "0",
        },
        {
            **common,
            "sample_stage": "T1",
            "sample_offset_us": "1000",
            "sample_time_ns": "2000000",
            "calibrated_probability": "0.5",
            "net_utility": "0.3",
            "decision": "action",
            "secondary_launched": "1",
        },
    ]


def explicit_cost_config(uses_retry_inflation: bool) -> dict[str, object]:
    config = adaptive_config()
    config.update({
        "admission_uses_retry_inflation": uses_retry_inflation,
        "admission_cost_definition": (
            "retry_inflated_estimated_secondary_sender_phy_tx_airtime"
            if uses_retry_inflation
            else "nominal_estimated_secondary_sender_phy_tx_airtime"
        ),
        "reservation_cost_definition": (
            "retry_inflated_estimated_secondary_sender_phy_tx_airtime"
        ),
    })
    return config


def nominal_admission_rows() -> list[dict[str, str]]:
    rows = adaptive_rows()
    for row in rows:
        row["admission_airtime_us"] = "50"
        row["normalized_cost"] = "0.5"
    rows[0]["net_utility"] = "0"
    rows[1]["net_utility"] = "0.4"
    return rows


def prediction_samples(
    generation_time_ns: int = 1_000_000,
) -> dict[tuple[int, int], dict[str, str]]:
    return {
        (7, offset): {
            "run_id": "run",
            "frame_id": "7",
            "path_id": "1",
            "copy_id": "0",
            "sample_stage": f"T{offset // 1000}",
            "sample_offset_us": str(offset),
            "sample_time_ns": str(generation_time_ns + offset * 1000),
            "generation_time_ns": str(generation_time_ns),
            "deadline_time_ns": str(generation_time_ns + 33_333_000),
            "actionable": "1",
        }
        for offset in (0, 1000)
    }


def meter_fixture() -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, object],
    dict[str, object],
    list[dict[str, str]],
]:
    events = [{
        "run_id": "run",
        "time_ns": "2100000",
        "path_id": "0",
        "ppdu_duration_us": "80",
        "tagged_mpdu_bytes": "1200",
        "frame_ids": "7",
        "mixed_ppdu": "0",
        "cumulative_tagged_airtime_us": "80",
    }]
    settlements = [{
        "run_id": "run",
        "frame_id": "7",
        "settlement_time_ns": "3000000",
        "released_airtime_us": "20",
        "measured_airtime_us": "80",
        "nominal_airtime_us": "75",
        "fallback": "0",
    }]
    summary = {
        "tagged_ppdu_count": 1,
        "mixed_ppdu_count": 0,
        "tagged_secondary_tx_airtime_us": 80.0,
        "measurement_start_ns": 0,
        "measurement_stop_ns": 10_000_000,
        "measurement_duration_us": 10_000.0,
        "tagged_secondary_tx_airtime_fraction": 0.008,
        "maximum_budget_debt_us": 0.0,
        "estimated_action_airtime_us": 100.0,
        "actual_to_estimated_airtime_ratio": 0.8,
        "forced_reservation_settlements": 0,
        "budget_fraction": 0.05,
        "initial_bucket_capacity_us": 10_000.0,
        "finite_run_budget_us": 10_500.0,
        "budget_excess_us": 0.0,
    }
    meter = {
        "enabled": True,
        "path_id": 0,
        "copy_id": 1,
        "definition": "secondary_sender_phy_tx_airtime",
        "measurement_start_ns": 0,
        "measurement_stop_ns": 10_000_000,
    }
    links = [{"link_id": "0", "phy_tx_time_us": "100"}]
    return events, settlements, summary, meter, links


class AdaptiveDecisionValidationTest(unittest.TestCase):
    def test_runner_maps_new_controller_options(self) -> None:
        self.assertEqual(
            CLI_KEYS["adaptive_airtime_initial_bucket_horizon_us"],
            "adaptiveAirtimeInitialBucketHorizonUs",
        )
        self.assertEqual(
            CLI_KEYS["adaptive_airtime_admission_uses_retry_inflation"],
            "adaptiveAirtimeAdmissionUsesRetryInflation",
        )

    def test_accepts_valid_nondefault_controller_parameters(self) -> None:
        config = adaptive_config()
        self.assertEqual(_validate_adaptive_config(config), [0, 1000])
        estimates = _validate_adaptive_decisions(
            adaptive_rows(),
            config,
            [{"frame_id": "7", "generation_time_us": "1000"}],
            prediction_samples(),
            "run",
        )
        self.assertEqual(estimates, {7: 100.0})

    def test_accepts_limited_initial_credit_horizon(self) -> None:
        config = adaptive_config()
        config["initial_bucket_horizon_us"] = 100_000
        config["initial_bucket_capacity_us"] = 5_000.0
        self.assertEqual(_validate_adaptive_config(config), [0, 1000])

        rows = adaptive_rows()
        for row in rows:
            row["initial_bucket_capacity_us"] = "5000"
            row["bucket_balance_us"] = "5000"
            row["available_airtime_us"] = "5000"
        self.assertEqual(
            _validate_adaptive_decisions(
                rows,
                config,
                [{"frame_id": "7", "generation_time_us": "1000"}],
                prediction_samples(),
                "run",
            ),
            {7: 100.0},
        )

    def test_rejects_invalid_initial_credit_horizon_or_capacity(self) -> None:
        config = adaptive_config()
        config["initial_bucket_horizon_us"] = 200_001
        with self.assertRaisesRegex(ValidationError, "initial bucket horizon"):
            _validate_adaptive_config(config)

        config["initial_bucket_horizon_us"] = 100_000
        with self.assertRaisesRegex(ValidationError, "initial capacity mismatch"):
            _validate_adaptive_config(config)

    def test_zero_dual_step_freezes_shadow_price_recurrence(self) -> None:
        config = adaptive_config()
        config.update({"stages": ["T0"], "decision_offsets_us": [0], "dual_step": 0.0})
        first = adaptive_rows()[0]
        second = copy.deepcopy(first)
        second.update({
            "frame_id": "8",
            "sample_time_ns": "2000000",
            "measured_airtime_total_us": "100",
        })
        samples = {
            (7, 0): prediction_samples()[(7, 0)],
            (8, 0): {
                **prediction_samples()[(7, 0)],
                "frame_id": "8",
                "sample_time_ns": "2000000",
                "generation_time_ns": "2000000",
                "deadline_time_ns": "35333000",
            },
        }
        self.assertEqual(
            _validate_adaptive_decisions(
                [first, second],
                config,
                [
                    {"frame_id": "7", "generation_time_us": "1000"},
                    {"frame_id": "8", "generation_time_us": "2000"},
                ],
                samples,
                "run",
            ),
            {},
        )

    def test_rejects_negative_dual_step(self) -> None:
        config = adaptive_config()
        config["dual_step"] = -0.01
        with self.assertRaisesRegex(ValidationError, "outside its domain"):
            _validate_adaptive_config(config)

    def test_accepts_nominal_admission_with_inflated_reservation(self) -> None:
        estimates = _validate_adaptive_decisions(
            nominal_admission_rows(),
            explicit_cost_config(False),
            [{"frame_id": "7", "generation_time_us": "1000"}],
            prediction_samples(),
            "run",
        )
        self.assertEqual(estimates, {7: 100.0})

    def test_rejects_nominal_admission_without_explicit_airtime(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires admission airtime"):
            _validate_adaptive_decisions(
                adaptive_rows(),
                explicit_cost_config(False),
                [{"frame_id": "7", "generation_time_us": "1000"}],
                prediction_samples(),
                "run",
            )

    def test_rejects_admission_airtime_above_reservation(self) -> None:
        rows = nominal_admission_rows()
        rows[1]["estimated_airtime_us"] = "40"
        with self.assertRaisesRegex(ValidationError, "arithmetic"):
            _validate_adaptive_decisions(
                rows,
                explicit_cost_config(False),
                [{"frame_id": "7", "generation_time_us": "1000"}],
                prediction_samples(),
                "run",
            )

    def test_accepts_submicrosecond_generation_precision_from_telemetry(self) -> None:
        rows = adaptive_rows()
        for row in rows:
            row["sample_time_ns"] = str(int(row["sample_time_ns"]) + 667)
        estimates = _validate_adaptive_decisions(
            rows,
            adaptive_config(),
            [{"frame_id": "7", "generation_time_us": "1000"}],
            prediction_samples(1_000_667),
            "run",
        )
        self.assertEqual(estimates, {7: 100.0})

    def test_accepts_exact_primary_deficit_packet_metadata(self) -> None:
        config = adaptive_config()
        config.update({
            "admission_feature_set": "F0+F1-degraded",
            "packet_selection_feature_set": "F2-primary-frame-ack-state",
            "packet_selection": "primary_unacknowledged_reverse",
        })
        rows = adaptive_rows()
        rows[0].update({
            "frame_packet_count": "4",
            "primary_acked_packets": "2",
            "primary_acked_packet_indices": "0;2",
            "secondary_packet_count": "2",
            "secondary_packet_indices": "3;1",
            "secondary_packet_order": "primary_unacknowledged_reverse",
        })
        rows[1].update({
            "frame_packet_count": "4",
            "primary_acked_packets": "3",
            "primary_acked_packet_indices": "0;2;3",
            "secondary_packet_count": "1",
            "secondary_packet_indices": "1",
            "secondary_packet_order": "primary_unacknowledged_reverse",
        })
        samples = prediction_samples()
        samples[(7, 0)].update({
            "frame_packet_count": "4", "frame_packets_tx_succeeded": "2",
        })
        samples[(7, 1000)].update({
            "frame_packet_count": "4", "frame_packets_tx_succeeded": "3",
        })
        self.assertEqual(
            _validate_adaptive_config(config, "primary_unacknowledged_reverse"),
            [0, 1000],
        )
        estimates = _validate_adaptive_decisions(
            rows,
            config,
            [{"frame_id": "7", "generation_time_us": "1000"}],
            samples,
            "run",
        )
        self.assertEqual(estimates, {7: 100.0})

    def test_rejects_primary_deficit_packet_count_mutation(self) -> None:
        config = adaptive_config()
        config.update({
            "admission_feature_set": "F0+F1-degraded",
            "packet_selection_feature_set": "F2-primary-frame-ack-state",
            "packet_selection": "primary_unacknowledged_reverse",
        })
        rows = adaptive_rows()
        for row in rows:
            row.update({
                "frame_packet_count": "4",
                "primary_acked_packets": "2",
                "primary_acked_packet_indices": "0;2",
                "secondary_packet_count": "1",
                "secondary_packet_indices": "3",
                "secondary_packet_order": "primary_unacknowledged_reverse",
            })
        samples = prediction_samples()
        for sample in samples.values():
            sample.update({
                "frame_packet_count": "4", "frame_packets_tx_succeeded": "2",
            })
        with self.assertRaisesRegex(ValidationError, "selected packet set"):
            _validate_adaptive_decisions(
                rows,
                config,
                [{"frame_id": "7", "generation_time_us": "1000"}],
                samples,
                "run",
            )

    def test_rejects_primary_deficit_packet_identity_mutation(self) -> None:
        config = adaptive_config()
        config.update({
            "admission_feature_set": "F0+F1-degraded",
            "packet_selection_feature_set": "F2-primary-frame-ack-state",
            "packet_selection": "primary_unacknowledged_reverse",
        })
        rows = adaptive_rows()
        for row in rows:
            row.update({
                "frame_packet_count": "4",
                "primary_acked_packets": "2",
                "primary_acked_packet_indices": "0;2",
                "secondary_packet_count": "2",
                "secondary_packet_indices": "3;2",
                "secondary_packet_order": "primary_unacknowledged_reverse",
            })
        samples = prediction_samples()
        for sample in samples.values():
            sample.update({
                "frame_packet_count": "4", "frame_packets_tx_succeeded": "2",
            })
        with self.assertRaisesRegex(ValidationError, "selected packet set"):
            _validate_adaptive_decisions(
                rows,
                config,
                [{"frame_id": "7", "generation_time_us": "1000"}],
                samples,
                "run",
            )

    def test_rejects_zero_deficit_before_all_packets_are_acked(self) -> None:
        config = adaptive_config()
        config.update({
            "admission_feature_set": "F0+F1-degraded",
            "packet_selection_feature_set": "F2-primary-frame-ack-state",
            "packet_selection": "primary_unacknowledged_reverse",
        })
        rows = adaptive_rows()
        for row in rows:
            row.update({
                "frame_packet_count": "4",
                "primary_acked_packets": "2",
                "primary_acked_packet_indices": "0;2",
                "secondary_packet_count": "2",
                "secondary_packet_indices": "3;1",
                "secondary_packet_order": "primary_unacknowledged_reverse",
            })
        rows[0].update({
            "estimated_airtime_us": "0",
            "normalized_cost": "0",
            "net_utility": "nan",
            "decision": "no_primary_deficit",
            "secondary_packet_count": "0",
            "secondary_packet_indices": "",
            "secondary_packet_order": "none",
        })
        samples = prediction_samples()
        for sample in samples.values():
            sample.update({
                "frame_packet_count": "4", "frame_packets_tx_succeeded": "2",
            })
        with self.assertRaisesRegex(ValidationError, "zero deficit"):
            _validate_adaptive_decisions(
                rows,
                config,
                [{"frame_id": "7", "generation_time_us": "1000"}],
                samples,
                "run",
            )

    def test_rejects_decision_timestamp_different_from_telemetry(self) -> None:
        rows = adaptive_rows()
        rows[1]["sample_time_ns"] = "2000001"
        with self.assertRaisesRegex(ValidationError, "time/telemetry"):
            _validate_adaptive_decisions(
                rows,
                adaptive_config(),
                [{"frame_id": "7", "generation_time_us": "1000"}],
                prediction_samples(),
                "run",
            )

    def test_rejects_non_hex_model_hash(self) -> None:
        config = adaptive_config()
        config["source_model_sha256"] = "z" * 64
        with self.assertRaisesRegex(ValidationError, "provenance"):
            _validate_adaptive_config(config)

    def test_accepts_primary_t0_target_provenance(self) -> None:
        config = adaptive_config()
        config.update({
            "model_id": PRIMARY_T0_MODEL_ID,
            "target_id": PRIMARY_TARGET_ID,
            "target_provenance_sha256": PRIMARY_TARGET_PROVENANCE_SHA256,
            "decision_offsets_us": [0],
            "stages": ["T0"],
        })
        self.assertEqual(_validate_adaptive_config(config), [0])

    def test_rejects_later_stage_with_primary_t0_model(self) -> None:
        config = adaptive_config()
        config.update({
            "model_id": PRIMARY_T0_MODEL_ID,
            "target_id": PRIMARY_TARGET_ID,
            "target_provenance_sha256": PRIMARY_TARGET_PROVENANCE_SHA256,
        })
        with self.assertRaisesRegex(ValidationError, "only supports offset 0"):
            _validate_adaptive_config(config)

    def test_selective_offsets_reject_later_primary_t0_stage(self) -> None:
        config = {"model_id": PRIMARY_T0_MODEL_ID}
        self.assertTrue(_prediction_model_offsets_valid(config, [0]))
        self.assertFalse(_prediction_model_offsets_valid(config, [0, 1000]))

    def test_rejects_primary_t0_without_target_provenance(self) -> None:
        config = adaptive_config()
        config["model_id"] = PRIMARY_T0_MODEL_ID
        with self.assertRaisesRegex(ValidationError, "provenance"):
            _validate_adaptive_config(config)

    def test_rejects_stage_unsupported_by_frozen_model(self) -> None:
        config = adaptive_config()
        config["decision_offsets_us"] = [0, 1500]
        config["stages"] = ["T0", "offset_1500us"]
        with self.assertRaisesRegex(ValidationError, "unsupported stage"):
            _validate_adaptive_config(config)

    def test_rejects_cost_arithmetic_mutation(self) -> None:
        rows = adaptive_rows()
        rows[1]["normalized_cost"] = "0.9"
        with self.assertRaisesRegex(ValidationError, "arithmetic"):
            _validate_adaptive_decisions(
                rows,
                adaptive_config(),
                [{"frame_id": "7", "generation_time_us": "1000"}],
                prediction_samples(),
                "run",
            )

    def test_rejects_action_with_insufficient_airtime(self) -> None:
        rows = adaptive_rows()
        rows[1]["bucket_balance_us"] = "50"
        rows[1]["available_airtime_us"] = "50"
        with self.assertRaisesRegex(ValidationError, "action predicate"):
            _validate_adaptive_decisions(
                rows,
                adaptive_config(),
                [{"frame_id": "7", "generation_time_us": "1000"}],
                prediction_samples(),
                "run",
            )


class SecondaryAirtimeValidationTest(unittest.TestCase):
    def validate_fixture(
        self,
        events: list[dict[str, str]],
        settlements: list[dict[str, str]],
        summary: dict[str, object],
        meter: dict[str, object],
        links: list[dict[str, str]],
        action_estimate: float = 100.0,
    ) -> None:
        _validate_secondary_airtime(
            events,
            settlements,
            summary,
            meter,
            links,
            "adaptive_airtime_duplication",
            "run",
            adaptive_config(),
            {7: action_estimate},
            {7},
            0.0,
        )

    def test_reconciles_valid_event_and_settlement_ledgers(self) -> None:
        self.validate_fixture(*meter_fixture())

    def test_rejects_event_at_exclusive_stop_boundary(self) -> None:
        fixture = list(meter_fixture())
        fixture[0] = copy.deepcopy(fixture[0])
        fixture[0][0]["time_ns"] = "10000000"
        with self.assertRaisesRegex(ValidationError, "half-open"):
            self.validate_fixture(*fixture)

    def test_rejects_released_reservation_mutation(self) -> None:
        fixture = list(meter_fixture())
        fixture[1] = copy.deepcopy(fixture[1])
        fixture[1][0]["released_airtime_us"] = "19"
        with self.assertRaisesRegex(ValidationError, "released reservation"):
            self.validate_fixture(*fixture)

    def test_accepts_precision_rounded_released_reservation(self) -> None:
        fixture = list(meter_fixture())
        estimate = 2025.22118035
        measured = 2025.2
        fixture[0] = copy.deepcopy(fixture[0])
        fixture[0][0]["ppdu_duration_us"] = str(measured)
        fixture[0][0]["cumulative_tagged_airtime_us"] = str(measured)
        fixture[1] = copy.deepcopy(fixture[1])
        fixture[1][0]["released_airtime_us"] = "0.0211803524214"
        fixture[1][0]["measured_airtime_us"] = str(measured)
        fixture[2] = copy.deepcopy(fixture[2])
        fixture[2]["tagged_secondary_tx_airtime_us"] = measured
        fixture[2]["tagged_secondary_tx_airtime_fraction"] = measured / 10_000.0
        fixture[2]["estimated_action_airtime_us"] = estimate
        fixture[2]["actual_to_estimated_airtime_ratio"] = measured / estimate
        fixture[4] = copy.deepcopy(fixture[4])
        fixture[4][0]["phy_tx_time_us"] = "2100"
        self.validate_fixture(*fixture, action_estimate=estimate)


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_analysis_run(
    root: Path,
    run_id: str,
    seed: int,
    run: int,
    topology: str,
    policy: str,
    link0_tx_us: int,
) -> dict[str, object]:
    directory = root / run_id
    directory.mkdir()
    adaptive = policy in {
        "adaptive_airtime_duplication", "adaptive_deficit_duplication",
    }
    write_rows(
        directory / "frames.csv",
        ([{
            "frame_id": "7",
            "generation_time_us": "0",
            "deadline_us": "100",
            "copy_0_completion_us": "",
            "deadline_miss": "0",
        }] if adaptive else [{"generation_time_us": "0", "deadline_miss": "0"}]),
    )
    write_rows(
        directory / "link_intervals.csv",
        [{"link_id": "0", "phy_tx_time_us": str(link0_tx_us)}],
    )
    if adaptive:
        meter = {
            "tagged_secondary_tx_airtime_us": 100.0,
            "tagged_secondary_tx_airtime_fraction": 0.1,
            "measurement_duration_us": 1000.0,
            "maximum_budget_debt_us": 0.0,
            "estimated_action_airtime_us": 100.0,
            "actual_to_estimated_airtime_ratio": 1.0,
            "forced_reservation_settlements": 0,
            "finite_run_budget_us": 150.0,
            "budget_excess_us": 0.0,
        }
        (directory / "secondary_airtime_summary.json").write_text(
            json.dumps(meter), encoding="utf-8"
        )
        write_rows(
            directory / "adaptive_airtime_decisions.csv",
            [{
                "frame_id": "7",
                "decision": "action",
                "estimated_airtime_us": "100",
                "sample_stage": "offset_1500us",
                "sample_time_ns": "1500000",
                "shadow_price": "0.2",
                "bucket_balance_us": "50",
            }],
        )
        write_rows(
            directory / "secondary_airtime_settlements.csv",
            [{"frame_id": "7", "measured_airtime_us": "100"}],
        )
    return {
        "run_id": run_id,
        "run_dir": str(directory),
        "seed": seed,
        "run": run,
        "topology": topology,
        "policy": policy,
        "config": {"duration_s": 0.001},
        "deadline_miss_ratio": 0.0,
        "latency_p99_us": {
            "fixed_link_1": 120.0,
            "fixed_link_0": 100.0,
            "adaptive_airtime_duplication": 90.0,
            "adaptive_deficit_duplication": 80.0,
        }.get(policy, 110.0),
        "redundant_byte_ratio": (
            0.1
            if policy in {"adaptive_airtime_duplication", "adaptive_deficit_duplication"}
            else 0.0
        ),
    }


class AdaptiveAirtimePlotTest(unittest.TestCase):
    def test_uses_student_t_interval(self) -> None:
        values = [float(value) for value in range(30)]
        _, lower, _ = _interval(values)
        normal_half = 1.96 * statistics.stdev(values) / (30 ** 0.5)
        self.assertGreater(lower, normal_half)

    def test_sorts_frames_by_generation_time_for_miss_bursts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = make_analysis_run(
                root, "fixed", 1, 1, "dual_interface", "fixed_link_1", 20
            )
            write_rows(
                root / "fixed" / "frames.csv",
                [
                    {"generation_time_us": "0", "deadline_miss": "1"},
                    {"generation_time_us": "2000", "deadline_miss": "0"},
                    {"generation_time_us": "1000", "deadline_miss": "1"},
                ],
            )

            [summary] = summarize_adaptive_runs({"runs": [run]}, root)

            self.assertEqual(summary["max_miss_burst"], 2)
            self.assertEqual(summary["p95_miss_burst"], 2.0)

    def test_falls_back_from_stale_serialized_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = make_analysis_run(
                root, "fixed", 1, 1, "dual_interface", "fixed_link_1", 20
            )
            run["run_dir"] = str(root / "remote-workstation" / "fixed")

            [summary] = summarize_adaptive_runs({"runs": [run]}, root)

            self.assertEqual(Path(summary["run_dir"]), root / "fixed")

    def test_classifies_primary_rescues_and_action_airtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = make_analysis_run(
                root,
                "adaptive",
                1,
                1,
                "dual_interface",
                "adaptive_airtime_duplication",
                100,
            )
            directory = root / "adaptive"
            write_rows(
                directory / "frames.csv",
                [
                    {
                        "frame_id": "7", "generation_time_us": "0",
                        "deadline_us": "50", "copy_0_completion_us": "",
                        "deadline_miss": "0",
                    },
                    {
                        "frame_id": "8", "generation_time_us": "100",
                        "deadline_us": "50", "copy_0_completion_us": "",
                        "deadline_miss": "1",
                    },
                    {
                        "frame_id": "9", "generation_time_us": "200",
                        "deadline_us": "50", "copy_0_completion_us": "240",
                        "deadline_miss": "0",
                    },
                    {
                        "frame_id": "10", "generation_time_us": "300",
                        "deadline_us": "50", "copy_0_completion_us": "",
                        "deadline_miss": "1",
                    },
                ],
            )
            write_rows(
                directory / "adaptive_airtime_decisions.csv",
                [
                    {
                        "frame_id": str(frame_id), "decision": decision,
                        "estimated_airtime_us": "20", "sample_stage": "T0",
                        "sample_time_ns": str(frame_id), "shadow_price": "0.2",
                        "bucket_balance_us": "50",
                    }
                    for frame_id, decision in (
                        (7, "action"), (8, "action"), (9, "action"),
                        (10, "price_rejected"),
                    )
                ],
            )
            write_rows(
                directory / "secondary_airtime_settlements.csv",
                [
                    {"frame_id": "7", "measured_airtime_us": "10"},
                    {"frame_id": "8", "measured_airtime_us": "20"},
                    {"frame_id": "9", "measured_airtime_us": "30"},
                ],
            )
            meter_path = directory / "secondary_airtime_summary.json"
            meter = json.loads(meter_path.read_text(encoding="utf-8"))
            meter["tagged_secondary_tx_airtime_us"] = 60.0
            meter["tagged_secondary_tx_airtime_fraction"] = 0.06
            meter_path.write_text(json.dumps(meter), encoding="utf-8")

            [summary] = summarize_adaptive_runs({"runs": [run]}, root)

            self.assertEqual(summary["primary_deadline_misses"], 3)
            self.assertEqual(summary["acted_primary_deadline_misses"], 2)
            self.assertEqual(summary["rescued_primary_deadline_misses"], 1)
            self.assertEqual(summary["failed_rescue_primary_deadline_misses"], 1)
            self.assertEqual(summary["unacted_primary_deadline_misses"], 1)
            self.assertEqual(summary["primary_hit_actions"], 1)
            self.assertAlmostEqual(summary["primary_miss_action_precision"], 2 / 3)
            self.assertAlmostEqual(summary["primary_miss_action_recall"], 2 / 3)
            self.assertAlmostEqual(summary["deadline_rescue_per_action"], 1 / 3)
            self.assertAlmostEqual(
                summary["deadline_rescue_given_acted_primary_miss"],
                1 / 2,
            )
            self.assertEqual(summary["rescued_primary_miss_airtime_us"], 10)
            self.assertEqual(summary["failed_rescue_airtime_us"], 20)
            self.assertEqual(summary["primary_hit_action_airtime_us"], 30)
            self.assertAlmostEqual(summary["primary_hit_action_airtime_share"], 0.5)

    def test_pairs_by_seed_and_run_and_plots_complete_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = {"runs": [
                make_analysis_run(
                    root, "fixed-run-2", 1, 2, "dual_interface", "fixed_link_1", 20
                ),
                make_analysis_run(
                    root, "fixed-run-1", 1, 1, "dual_interface", "fixed_link_1", 900
                ),
                make_analysis_run(
                    root,
                    "adaptive-run-2",
                    1,
                    2,
                    "dual_interface",
                    "adaptive_airtime_duplication",
                    120,
                ),
                make_analysis_run(
                    root, "mlo-run-2", 1, 2, "mlo_str", "fixed_link_0", 70
                ),
            ]}
            rows = summarize_adaptive_runs(aggregate, root)
            adaptive = next(
                row for row in rows if row["policy"] == "adaptive_airtime_duplication"
            )
            self.assertAlmostEqual(adaptive["incremental_link0_airtime_fraction"], 0.1)
            self.assertEqual(adaptive["actions_stage_offset_1500us"], 1)

            plot_adaptive_airtime(aggregate, root)
            output = root / "plots/adaptive_airtime"
            for name in (
                "p95_miss_burst.png",
                "tagged_vs_incremental_link0_airtime.png",
                "estimated_vs_measured_airtime.png",
                "action_stage_distribution.png",
            ):
                self.assertTrue((output / name).is_file(), name)
            with (root / "adaptive_airtime_summary.csv").open(
                newline="", encoding="utf-8"
            ) as source:
                header = next(csv.reader(source))
            self.assertNotIn("run_dir", header)

    def test_keeps_adaptive_treatments_in_separate_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = {"runs": [
                make_analysis_run(
                    root, "fixed", 1, 1, "dual_interface", "fixed_link_1", 80
                ),
                make_analysis_run(
                    root, "mlo", 1, 1, "mlo_str", "fixed_link_0", 100
                ),
                make_analysis_run(
                    root,
                    "adaptive",
                    1,
                    1,
                    "dual_interface",
                    "adaptive_airtime_duplication",
                    120,
                ),
                make_analysis_run(
                    root,
                    "deficit",
                    1,
                    1,
                    "dual_interface",
                    "adaptive_deficit_duplication",
                    110,
                ),
            ]}
            plot_adaptive_airtime(aggregate, root)
            output = root / "plots/adaptive_airtime"
            for policy in (
                "adaptive_airtime_duplication", "adaptive_deficit_duplication",
            ):
                self.assertTrue(
                    (output / f"action_stage_distribution_{policy}.png").is_file()
                )
                self.assertTrue(
                    (output / f"estimated_vs_measured_airtime_{policy}.png").is_file()
                )
            for name in (
                "latency_p99.png",
                "paired_miss_deltas.png",
                "paired_p99_deltas.png",
                "paired_total_airtime_increase_vs_mlo.png",
                "reliability_vs_total_airtime.png",
                "p99_vs_total_airtime.png",
            ):
                self.assertTrue((output / name).is_file(), name)

if __name__ == "__main__":
    unittest.main()
