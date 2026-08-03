#!/usr/bin/env python3
"""Adversarial validation checks for randomized intervention output."""

from __future__ import annotations

import csv
import copy
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from randomized_frame_assignment import ExplorationArm, assign_frame
from validate_outputs import (
    ALGORITHM_ID,
    RANDOMIZED_ASSIGNMENT_COLUMNS,
    RANDOMIZED_COMMON_ELIGIBILITY_RULE,
    RANDOMIZED_CONFIG_KEYS,
    RANDOMIZED_COST_ESTIMATOR,
    RANDOMIZED_COST_SAFETY_FACTOR,
    RANDOMIZED_CSV_SCHEMA_VERSION,
    RANDOMIZED_EXECUTION_COLUMNS,
    RANDOMIZED_PREDICTION_COLUMNS,
    SECONDARY_AIRTIME_EVENT_COLUMNS,
    SECONDARY_AIRTIME_SETTLEMENT_COLUMNS,
    ValidationError,
    _adaptive_nominal_airtime_us,
    _validate_randomized_intervention,
    _validate_secondary_airtime,
)


def _write_csv(path: Path, columns: set[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=sorted(columns))
        writer.writeheader()
        writer.writerows(rows)


class RandomizedInterventionFixture:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.salt = 0x123456789ABCDEF0
        self.seed = 7
        self.run = 3
        self.t2_probability = 0.3
        self.t4_probability = 0.3
        self.run_id = "random-validation"
        self.frame_size = 12_000
        self.packet_count = 10
        self.nominal = _adaptive_nominal_airtime_us(
            self.frame_size, self.packet_count, 1.0
        )
        self.estimated = RANDOMIZED_COST_SAFETY_FACTOR * self.nominal
        ids_by_arm: dict[ExplorationArm, list[int]] = {
            arm: [] for arm in ExplorationArm
        }
        frame_id = 0
        while (len(ids_by_arm[ExplorationArm.CONTROL]) < 1 or
               len(ids_by_arm[ExplorationArm.FULL_COPY_T2]) < 2 or
               len(ids_by_arm[ExplorationArm.FULL_COPY_T4]) < 3):
            assignment = assign_frame(
                self.salt,
                self.seed,
                self.run,
                frame_id,
                self.t2_probability,
                self.t4_probability,
            )
            ids_by_arm[assignment.arm].append(frame_id)
            frame_id += 1
        self.roles = {
            ids_by_arm[ExplorationArm.CONTROL][0]: "control",
            ids_by_arm[ExplorationArm.FULL_COPY_T2][0]: "t2_launch",
            ids_by_arm[ExplorationArm.FULL_COPY_T2][1]: "t2_reject",
            ids_by_arm[ExplorationArm.FULL_COPY_T4][0]: "t4_launch",
            ids_by_arm[ExplorationArm.FULL_COPY_T4][1]: "t4_nonactionable",
            ids_by_arm[ExplorationArm.FULL_COPY_T4][2]: "t4_missing_descriptor",
        }
        self.duplicated = {
            frame_id for frame_id, role in self.roles.items()
            if role in {"t2_launch", "t4_launch"}
        }
        meter = {
            "enabled": True,
            "path_id": 0,
            "copy_id": 1,
            "definition": "secondary_sender_phy_tx_airtime",
            "measurement_start_ns": 1_000_000_000,
            "measurement_stop_ns": 11_000_000_000,
        }
        randomized = {
            "csv_schema_version": RANDOMIZED_CSV_SCHEMA_VERSION,
            "assignment_algorithm": ALGORITHM_ID,
            "assignment_salt": self.salt,
            "randomization_consumes_ns3_rng": False,
            "arm_probabilities": {
                "FULL_COPY_T2": self.t2_probability,
                "FULL_COPY_T4": self.t4_probability,
                "CONTROL": 0.4,
            },
            "stages": ["T2", "T4"],
            "stage_offsets_us": [2000, 4000],
            "primary_path": 1,
            "primary_copy_id": 0,
            "secondary_path": 0,
            "secondary_copy_id": 1,
            "assignment_window_start_ns": 1_000_000_000,
            "assignment_window_stop_ns": 10_000_000_000,
            "assignment_stop_guard_us": 1_000_000,
            "common_eligibility_rule": RANDOMIZED_COMMON_ELIGIBILITY_RULE,
            "intervention": "canonical_full_secondary_copy",
            "token_gate_enabled": False,
            "cost_estimator_id": RANDOMIZED_COST_ESTIMATOR,
        }
        assert set(randomized) == RANDOMIZED_CONFIG_KEYS
        self.config = {
            "run_id": self.run_id,
            "seed": self.seed,
            "run": self.run,
            "warmup_s": 1,
            "duration_s": 10,
            "policy": "randomized_full_copy_exploration",
            "topology": "dual_interface",
            "wifi": {
                "guard_interval": "800ns",
                "queue_max_delay_ms": 500,
                "max_ampdu_size_bytes": 65535,
                "txop_limit_us": 0,
                "rts_cts_threshold_bytes": 4692480,
            },
            "stream": {
                "source": "synthetic",
                "frame_size_bytes": self.frame_size,
                "keyframe_size_multiplier": 4,
                "payload_size_bytes": 1200,
                "deadline_us": 33333,
                "emission_mode": "burst",
            },
            "secondaryAirtimeMeter": meter,
            "predictionTelemetry": {
                "enabled": True,
                "sample_offsets_us": [0, 2000, 4000],
                "polling_interval_us": 1000,
                "polling_report_delay_us": 1000,
                "event_log_enabled": False,
                "oracle_features_enabled": False,
            },
            "randomizedIntervention": randomized,
        }
        self.frames: list[dict[str, str]] = []
        prediction_rows: list[dict[str, object]] = []
        assignment_rows: list[dict[str, object]] = []
        execution_rows: list[dict[str, object]] = []
        for ordinal, (selected_id, role) in enumerate(sorted(self.roles.items())):
            generation_ns = 2_000_000_000 + ordinal * 10_000_000
            deadline_ns = generation_ns + 33_333_000
            frame = {
                "frame_id": str(selected_id),
                "generation_time_us": str(generation_ns // 1000),
                "frame_size_bytes": str(self.frame_size),
                "packet_count": str(self.packet_count),
                "frame_type": "P_FRAME",
                "policy": "randomized_full_copy_exploration",
                "primary_link": "1",
            }
            self.frames.append(frame)
            samples: dict[tuple[int, int], dict[str, object]] = {}
            for offset in (2000, 4000):
                for path_id, copy_id in ((1, 0), (0, 1)):
                    actionable = not (
                        role == "t4_nonactionable" and offset == 4000 and path_id == 1
                    )
                    sample_time_ns = generation_ns + offset * 1000
                    prefix = 100_000 * ordinal + 10_000 * offset + 100 * path_id
                    sample = {
                        "run_id": self.run_id,
                        "frame_id": selected_id,
                        "path_id": path_id,
                        "copy_id": copy_id,
                        "sample_stage": f"T{offset // 1000}",
                        "sample_offset_us": offset,
                        "sample_time_ns": sample_time_ns,
                        "latest_feature_event_time_ns": sample_time_ns - 1000,
                        "latest_feature_event_sequence": prefix + 1,
                        "generation_time_ns": generation_ns,
                        "deadline_time_ns": deadline_ns,
                        "sender_mac_complete": int(not actionable),
                        "actionable": int(actionable),
                        "frame_size_bytes": self.frame_size,
                        "frame_packet_count": self.packet_count,
                        "frame_type": "P_FRAME",
                        "packets_submitted": 0,
                        "application_socket_packet_bytes_submitted": 0,
                        "packets_remaining_to_submit": self.packet_count,
                        "frame_packets_mac_enqueued": 0,
                        "frame_packets_mac_dequeued": 0,
                        "frame_packets_tx_succeeded": 0,
                        "frame_mpdu_attempt_failures": 0,
                        "frame_packets_terminally_dropped": 0,
                        "frame_packets_currently_queued": 0,
                        "frame_mac_service_bytes_currently_queued": 0,
                    }
                    prediction_rows.append(sample)
                    samples[(offset, path_id)] = sample

            assignment = assign_frame(
                self.salt,
                self.seed,
                self.run,
                selected_id,
                self.t2_probability,
                self.t4_probability,
            )
            common = {
                "schema_version": RANDOMIZED_CSV_SCHEMA_VERSION,
                "run_id": self.run_id,
                "frame_id": selected_id,
                "eligible_t2": 1,
                "eligibility_reason": "eligible",
                "assigned_arm": assignment.arm.value,
                "assignment_seed": self.seed,
                "assignment_run": self.run,
                "assignment_salt": self.salt,
                "assignment_algorithm": ALGORITHM_ID,
                "raw_draw": assignment.raw_draw,
                "unit_draw": repr(assignment.unit_draw),
                "t2_probability": self.t2_probability,
                "t4_probability": self.t4_probability,
                "control_probability": 0.4,
                "propensity": assignment.arm_probability,
            }
            t2_primary = samples[(2000, 1)]
            t2_secondary = samples[(2000, 0)]
            assignment_row = {
                **common,
                "primary_sample_time_ns": t2_primary["sample_time_ns"],
                "secondary_sample_time_ns": t2_secondary["sample_time_ns"],
                "primary_feature_watermark_time_ns":
                    t2_primary["latest_feature_event_time_ns"],
                "primary_feature_watermark_sequence":
                    t2_primary["latest_feature_event_sequence"],
                "secondary_feature_watermark_time_ns":
                    t2_secondary["latest_feature_event_time_ns"],
                "secondary_feature_watermark_sequence":
                    t2_secondary["latest_feature_event_sequence"],
                "generation_time_ns": generation_ns,
                "deadline_time_ns": deadline_ns,
                "prospective_t4_time_ns": generation_ns + 4_000_000,
                "frame_size_bytes": self.frame_size,
                "frame_packet_count": self.packet_count,
                "frame_type": "P_FRAME",
                "descriptor_available": 1,
                "secondary_packet_count": self.packet_count,
                "secondary_packet_indices": ";".join(
                    str(index) for index in range(self.packet_count)
                ),
                "secondary_expected_mac_service_bytes":
                    self.frame_size + 86 * self.packet_count,
                "cost_estimator": RANDOMIZED_COST_ESTIMATOR,
                "cost_safety_factor": RANDOMIZED_COST_SAFETY_FACTOR,
                "nominal_airtime_us": repr(self.nominal),
                "estimated_airtime_us": repr(self.estimated),
            }
            assignment_rows.append(assignment_row)

            stage = 4000 if assignment.arm == ExplorationArm.FULL_COPY_T4 else 2000
            execution_primary = samples[(stage, 1)]
            execution_secondary = samples[(stage, 0)]
            if role == "control":
                descriptor_at_execution, attempted, launched = 1, 0, 0
                noncompliance, status = 0, "control_no_launch"
            elif role == "t2_launch":
                descriptor_at_execution, attempted, launched = 1, 1, 1
                noncompliance, status = 0, "launched_t2"
            elif role == "t2_reject":
                descriptor_at_execution, attempted, launched = 1, 1, 0
                noncompliance, status = 1, "launch_rejected_t2"
            elif role == "t4_launch":
                descriptor_at_execution, attempted, launched = 1, 1, 1
                noncompliance, status = 0, "launched_t4"
            elif role == "t4_nonactionable":
                # Production can still observe the delayed descriptor after
                # the primary becomes nonactionable at T4.
                descriptor_at_execution, attempted, launched = 1, 0, 0
                noncompliance, status = 0, "primary_not_actionable_t4"
            else:
                descriptor_at_execution, attempted, launched = 0, 0, 0
                noncompliance, status = 1, "assigned_t4_not_launched"
            execution_row = {
                **common,
                "execution_stage": f"T{stage // 1000}",
                "primary_sample_time_ns": execution_primary["sample_time_ns"],
                "secondary_sample_time_ns": execution_secondary["sample_time_ns"],
                "primary_feature_watermark_time_ns":
                    execution_primary["latest_feature_event_time_ns"],
                "primary_feature_watermark_sequence":
                    execution_primary["latest_feature_event_sequence"],
                "secondary_feature_watermark_time_ns":
                    execution_secondary["latest_feature_event_time_ns"],
                "secondary_feature_watermark_sequence":
                    execution_secondary["latest_feature_event_sequence"],
                "generation_time_ns": generation_ns,
                "deadline_time_ns": deadline_ns,
                "descriptor_available_at_assignment": 1,
                "descriptor_available_at_execution": descriptor_at_execution,
                "secondary_packet_count": self.packet_count,
                "secondary_packet_indices": assignment_row["secondary_packet_indices"],
                "secondary_expected_mac_service_bytes":
                    assignment_row["secondary_expected_mac_service_bytes"],
                "cost_estimator": RANDOMIZED_COST_ESTIMATOR,
                "cost_safety_factor": RANDOMIZED_COST_SAFETY_FACTOR,
                "nominal_airtime_us": repr(self.nominal),
                "estimated_airtime_us": repr(self.estimated),
                "primary_actionable": execution_primary["actionable"],
                "attempted": attempted,
                "launched": launched,
                "noncompliance": noncompliance,
                "status": status,
            }
            execution_rows.append(execution_row)

        _write_csv(
            directory / "prediction_samples.csv",
            RANDOMIZED_PREDICTION_COLUMNS,
            prediction_rows,
        )
        _write_csv(
            directory / "randomized_intervention_assignments.csv",
            RANDOMIZED_ASSIGNMENT_COLUMNS,
            assignment_rows,
        )
        _write_csv(
            directory / "randomized_intervention_executions.csv",
            RANDOMIZED_EXECUTION_COLUMNS,
            execution_rows,
        )

    def validate(self) -> tuple[dict[int, float], dict[int, float]]:
        return _validate_randomized_intervention(
            self.directory,
            self.config,
            self.run_id,
            self.frames,
            self.duplicated,
        )

    def mutate(self, file_name: str, frame_id: int, key: str, value: object) -> None:
        path = self.directory / file_name
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            assert reader.fieldnames is not None
            fieldnames = reader.fieldnames
            rows = list(reader)
        row = next(row for row in rows if int(row["frame_id"]) == frame_id)
        row[key] = str(value)
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


class RandomizedInterventionValidationTest(unittest.TestCase):
    def fixture(self, directory: Path) -> RandomizedInterventionFixture:
        return RandomizedInterventionFixture(directory)

    def test_accepts_exact_ledgers_and_nonexposed_t4(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(Path(temporary))
            estimates, nominals = fixture.validate()
            self.assertEqual(set(estimates), fixture.duplicated)
            self.assertEqual(set(nominals), fixture.duplicated)

    def test_accepts_nonactionable_t4_after_descriptor_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(Path(temporary))
            frame_id = next(
                frame_id for frame_id, role in fixture.roles.items()
                if role == "t4_nonactionable"
            )
            fixture.mutate(
                "randomized_intervention_executions.csv",
                frame_id,
                "descriptor_available_at_execution",
                0,
            )
            fixture.validate()

    def test_rejects_random_draw_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(Path(temporary))
            frame_id = next(iter(fixture.roles))
            fixture.mutate(
                "randomized_intervention_assignments.csv", frame_id, "raw_draw", 0
            )
            with self.assertRaisesRegex(ValidationError, "deterministic assignment draw"):
                fixture.validate()

    def test_rejects_watermark_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(Path(temporary))
            frame_id = next(iter(fixture.roles))
            fixture.mutate(
                "randomized_intervention_executions.csv",
                frame_id,
                "primary_feature_watermark_sequence",
                999999,
            )
            with self.assertRaisesRegex(ValidationError, "paired telemetry evidence"):
                fixture.validate()

    def test_rejects_descriptor_cost_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(Path(temporary))
            frame_id = next(iter(fixture.roles))
            fixture.mutate(
                "randomized_intervention_assignments.csv",
                frame_id,
                "secondary_expected_mac_service_bytes",
                fixture.frame_size,
            )
            with self.assertRaisesRegex(ValidationError, "descriptor/cost arithmetic"):
                fixture.validate()

    def test_rejects_noncompliance_on_t4_nonexposure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(Path(temporary))
            frame_id = next(
                frame_id for frame_id, role in fixture.roles.items()
                if role == "t4_nonactionable"
            )
            fixture.mutate(
                "randomized_intervention_executions.csv",
                frame_id,
                "noncompliance",
                1,
            )
            with self.assertRaisesRegex(ValidationError, "status/compliance semantics"):
                fixture.validate()

    def test_rejects_rng_or_token_gate_provenance_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(Path(temporary))
            mutated = copy.deepcopy(fixture.config)
            mutated["randomizedIntervention"]["token_gate_enabled"] = True
            with self.assertRaisesRegex(ValidationError, "provenance"):
                _validate_randomized_intervention(
                    fixture.directory,
                    mutated,
                    fixture.run_id,
                    fixture.frames,
                    fixture.duplicated,
                )


def _random_meter_fixture() -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, object],
    dict[str, object],
    list[dict[str, str]],
]:
    events = [{
        "run_id": "run",
        "time_ns": "1000",
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
        "settlement_time_ns": "2000",
        "released_airtime_us": "20",
        "measured_airtime_us": "80",
        "nominal_airtime_us": "75",
        "fallback": "0",
    }]
    summary = {
        "tagged_ppdu_count": 1,
        "mixed_ppdu_count": 0,
        "tagged_secondary_tx_airtime_us": 80,
        "measurement_start_ns": 0,
        "measurement_stop_ns": 10_000_000,
        "measurement_duration_us": 10_000,
        "tagged_secondary_tx_airtime_fraction": 0.008,
        "maximum_budget_debt_us": 0,
        "estimated_action_airtime_us": 100,
        "actual_to_estimated_airtime_ratio": 0.8,
        "forced_reservation_settlements": 0,
        "budget_fraction": None,
        "initial_bucket_capacity_us": None,
        "finite_run_budget_us": None,
        "budget_excess_us": None,
    }
    meter = {
        "definition": "secondary_sender_phy_tx_airtime",
        "path_id": 0,
        "copy_id": 1,
        "measurement_start_ns": 0,
        "measurement_stop_ns": 10_000_000,
    }
    links = [{"link_id": "0", "phy_tx_time_us": "100"}]
    return events, settlements, summary, meter, links


class RandomizedSecondaryAirtimeValidationTest(unittest.TestCase):
    def validate_fixture(
        self,
        events: list[dict[str, str]],
        settlements: list[dict[str, str]],
        summary: dict[str, object],
        meter: dict[str, object],
        links: list[dict[str, str]],
    ) -> None:
        self.assertEqual(set(events[0]), SECONDARY_AIRTIME_EVENT_COLUMNS)
        if settlements:
            self.assertEqual(set(settlements[0]), SECONDARY_AIRTIME_SETTLEMENT_COLUMNS)
        _validate_secondary_airtime(
            events,
            settlements,
            summary,
            meter,
            links,
            "randomized_full_copy_exploration",
            "run",
            None,
            {7: 100.0},
            {7},
            0.0,
            action_nominal_airtimes={7: 75.0},
        )

    def test_accepts_reservations_without_budget_metadata(self) -> None:
        self.validate_fixture(*_random_meter_fixture())

    def test_rejects_randomized_budget_metadata(self) -> None:
        fixture = list(_random_meter_fixture())
        fixture[2] = copy.deepcopy(fixture[2])
        fixture[2]["budget_fraction"] = 0.1
        with self.assertRaisesRegex(ValidationError, "must be null"):
            self.validate_fixture(*fixture)

    def test_rejects_missing_randomized_settlement(self) -> None:
        fixture = list(_random_meter_fixture())
        fixture[1] = []
        with self.assertRaisesRegex(ValidationError, "reserved actions"):
            self.validate_fixture(*fixture)

    def test_rejects_randomized_settlement_nominal_mutation(self) -> None:
        fixture = list(_random_meter_fixture())
        fixture[1] = copy.deepcopy(fixture[1])
        fixture[1][0]["nominal_airtime_us"] = "76"
        with self.assertRaisesRegex(ValidationError, "nominal estimator"):
            self.validate_fixture(*fixture)


if __name__ == "__main__":
    unittest.main()
