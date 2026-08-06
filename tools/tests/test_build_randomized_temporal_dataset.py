#!/usr/bin/env python3
"""Focused tests for the action-clean temporal T2 dataset augmenter."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import build_randomized_intervention_dataset as base  # noqa: E402
from build_randomized_temporal_dataset import (  # noqa: E402
    DATASET_COLUMNS,
    FEATURE_COLUMNS,
    FEATURE_CONTRACT_ID,
    OUTPUT_CSV,
    PRIMARY_PATH,
    SECONDARY_PATH,
    TemporalDatasetError,
    build_temporal_dataset,
)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for values in rows:
            row = {field: "" for field in fields}
            row.update(values)
            writer.writerow(row)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


class TemporalFixture:
    """Small but temporally complete raw randomized run."""

    def __init__(
        self,
        path: Path,
        *,
        frame_count: int = 16,
        reverse_polling_rows: bool = False,
        dirty_event: bool = False,
        active_reservation: bool = False,
        timing_failure: bool = False,
        cadence_failure: bool = False,
        counter_reset: bool = False,
        rolling_counter_failure: bool = False,
        missing_ahead_frame: int | None = None,
    ) -> None:
        self.path = path
        self.run_id = "temporal-run"
        self.seed = 1101
        self.run_number = 1
        self.frame_count = frame_count
        path.mkdir(parents=True)
        self.config = {
            "run_id": self.run_id,
            "seed": self.seed,
            "run": self.run_number,
            "policy": "randomized_full_copy_exploration",
            "topology": "dual_interface",
            "duration_s": 60,
            "warmup_s": 1,
            "measurement_start_s": 1,
            "measurement_stop_s": 61,
            "stream": {
                "fps": 30,
                "deadline_us": 33333,
                "source": "synthetic",
            },
            "wifi": {"data_mode": "EhtMcs5", "guard_interval": "800ns"},
            "background": {
                "obss": {
                    "profile": "mixed4x4",
                    "stations_per_bss": 4,
                    "bsses": [{"bss_id": 0, "ap": [0.0, 1.0]}],
                }
            },
            "propagation": {"model": "log_distance_nakagami"},
            "predictionTelemetry": {
                "sample_offsets_us": [0, 2000, 4000],
                "history_windows_us": [1000, 5000, 20000],
                "polling_interval_us": 1000,
                "polling_report_delay_us": 1000,
                "oracle_features_enabled": False,
            },
            "randomizedIntervention": {
                "assignment_algorithm": "splitmix64_v1",
                "assignment_salt": 17,
                "arm_probabilities": {
                    "FULL_COPY_T2": 0.08,
                    "FULL_COPY_T4": 0.12,
                    "CONTROL": 0.80,
                },
                "assignment_window_start_ns": 1_000_000_000,
                "assignment_window_stop_ns": 60_466_000_000,
                "stages": ["T2", "T4"],
            },
            "secondaryAirtimeMeter": {
                "enabled": True,
                "path_id": 0,
                "copy_id": 1,
                "measurement_start_ns": 1_000_000_000,
                "measurement_stop_ns": 61_000_000_000,
            },
        }
        (path / "resolved_config.json").write_text(
            json.dumps(self.config), encoding="utf-8"
        )
        (path / "build_info.json").write_text(
            json.dumps(
                {
                    "ns3_version": "ns-3.48",
                    "ns3_upstream_commit": "upstream",
                    "project_git_commit": "project",
                    "compiler": "13.3.0",
                    "build_profile": "optimized",
                }
            ),
            encoding="utf-8",
        )
        (path / "secondary_airtime_summary.json").write_text("{}", encoding="utf-8")
        self._write_ledgers(
            reverse_polling_rows=reverse_polling_rows,
            dirty_event=dirty_event,
            active_reservation=active_reservation,
            timing_failure=timing_failure,
            cadence_failure=cadence_failure,
            counter_reset=counter_reset,
            rolling_counter_failure=rolling_counter_failure,
            missing_ahead_frame=missing_ahead_frame,
        )

    @staticmethod
    def _generation_ns(frame_id: int) -> int:
        return 1_000_000_000 + (frame_id * 1_000_000_000 + 15) // 30

    def _write_ledgers(
        self,
        *,
        reverse_polling_rows: bool,
        dirty_event: bool,
        active_reservation: bool,
        timing_failure: bool,
        cadence_failure: bool,
        counter_reset: bool,
        rolling_counter_failure: bool,
        missing_ahead_frame: int | None,
    ) -> None:
        frames: list[dict[str, object]] = []
        assignments: list[dict[str, object]] = []
        executions: list[dict[str, object]] = []
        settlements: list[dict[str, object]] = []
        samples: list[dict[str, object]] = []
        polling: list[dict[str, object]] = []
        capture_by_frame: dict[int, int] = {}
        for frame_id in range(self.frame_count):
            generation_ns = self._generation_ns(frame_id)
            if cadence_failure and frame_id == 9:
                generation_ns += 1
            generation_us = generation_ns // 1_000
            frame_type = "I_FRAME" if frame_id == 0 else "P_FRAME"
            arm = "FULL_COPY_T2" if active_reservation and frame_id == 0 else "CONTROL"
            launched = arm == "FULL_COPY_T2"
            frames.append(
                {
                    "run_id": self.run_id,
                    "frame_id": frame_id,
                    "frame_type": frame_type,
                    "generation_time_us": generation_us,
                    "deadline_us": 33333,
                    "copy_0_completion_us": generation_us + 13_000,
                    "incomplete": 0,
                    "deadline_miss": 0,
                    "union_latency_us": 9_000 if launched else 13_000,
                }
            )
            assignments.append(
                {
                    "run_id": self.run_id,
                    "frame_id": frame_id,
                    "eligible_t2": 1,
                    "assigned_arm": arm,
                    "propensity": 0.08 if launched else 0.80,
                    "nominal_airtime_us": 100,
                    "estimated_airtime_us": 125,
                }
            )
            executions.append(
                {
                    "run_id": self.run_id,
                    "frame_id": frame_id,
                    "assigned_arm": arm,
                    "attempted": int(launched),
                    "launched": int(launched),
                    "noncompliance": 0,
                    "execution_stage": "T2",
                    "status": "launched_t2" if launched else "control_no_launch",
                    "secondary_sample_time_ns": generation_ns + 2_000_000,
                }
            )
            if launched:
                settlements.append(
                    {
                        "run_id": self.run_id,
                        "frame_id": frame_id,
                        "settlement_time_ns": 1_180_000_000,
                        "released_airtime_us": 10,
                        "measured_airtime_us": 90,
                        "nominal_airtime_us": 100,
                        "fallback": 0,
                    }
                )
            for stage, offset_us in (("T2", 2_000), ("T4", 4_000)):
                sample_ns = generation_ns + offset_us * 1_000
                capture_ns = ((sample_ns - 1_000_000) // 1_000_000) * 1_000_000
                capture_by_frame[frame_id] = capture_ns
                available_ns = capture_ns + 1_000_000
                for path_id, copy_id in (PRIMARY_PATH, SECONDARY_PATH):
                    sample: dict[str, object] = {
                        "run_id": self.run_id,
                        "frame_id": frame_id,
                        "path_id": path_id,
                        "copy_id": copy_id,
                        "sample_stage": stage,
                        "sample_offset_us": offset_us,
                        "sample_time_ns": sample_ns,
                        "latest_feature_event_time_ns": sample_ns - 100,
                        "latest_feature_event_sequence": frame_id * 10 + path_id,
                        "generation_time_ns": generation_ns,
                        "deadline_time_ns": generation_ns + 33_333_000,
                        "actionable": 1,
                        "frame_age_us": offset_us,
                        "deadline_slack_us": 33333 - offset_us,
                        "frame_size_bytes": 12_000,
                        "frame_packet_count": 10,
                        "frame_type": frame_type,
                        "packets_submitted": 10 if path_id == 1 else 0,
                        "application_socket_packet_bytes_submitted": 13_000 if path_id == 1 else 0,
                        "packets_remaining_to_submit": 0 if path_id == 1 else 10,
                    }
                    for index, field in enumerate(base.PRIMARY_CURRENT_FIELDS):
                        sample[field] = frame_id * 100 + path_id * 10 + index
                    # Keep physics ratios internally meaningful.
                    sample.update(
                        {
                            "frame_packets_mac_enqueued": 10 if path_id == 1 else 0,
                            "frame_packets_mac_dequeued": 5 if path_id == 1 else 0,
                            "frame_packets_tx_succeeded": 4 if path_id == 1 else 0,
                            "frame_packets_pending_primary": 6 if path_id == 1 else 10,
                            "frame_mac_service_bytes_pending_primary": 7_800,
                            "frame_mac_service_bytes_not_acknowledged": 7_800,
                            "frame_mac_service_bytes_currently_queued": 7_800,
                            "mac_queue_service_bytes": 9_000,
                            "mac_service_bytes_ahead_of_frame": 1_200,
                        }
                    )
                    if frame_id == missing_ahead_frame and path_id == PRIMARY_PATH[0]:
                        sample["packets_ahead_of_frame"] = ""
                        sample["mac_service_bytes_ahead_of_frame"] = ""
                    samples.append(sample)

                    poll: dict[str, object] = {
                        "run_id": self.run_id,
                        "frame_id": frame_id,
                        "path_id": path_id,
                        "copy_id": copy_id,
                        "sample_stage": stage,
                        "sample_offset_us": offset_us,
                        "report_available": 1,
                        "capture_time_ns": capture_ns,
                        "available_time_ns": available_ns,
                        "staleness_us": (sample_ns - capture_ns) // 1_000,
                        "latest_feature_event_time_ns": capture_ns - 100,
                        "latest_feature_event_sequence": frame_id * 10 + path_id,
                        "mpdu_tx_attempts_total": frame_id * 10,
                        "mpdu_positive_acks_total": frame_id * 8,
                        "mpdu_tx_attempt_failures_total": frame_id * 2,
                        "mpdu_retries_total": frame_id,
                        "mpdu_terminal_drops_total": frame_id // 8,
                        "mpdu_retry_limit_drops_total": frame_id // 9,
                        "mpdu_lifetime_drops_total": frame_id // 10,
                        "mpdu_queue_drops_total": frame_id // 11,
                        "ppdu_tx_count_total": frame_id * 3,
                        "last_tx_attempt_time_ns": capture_ns - 500_000,
                        "last_positive_ack_time_ns": "" if frame_id == 0 else capture_ns - 700_000,
                        "current_mcs": 5,
                        "current_nss": 1,
                        "current_channel_width_mhz": 20,
                        "current_guard_interval_ns": 800,
                    }
                    if counter_reset and frame_id == 9 and path_id == 1:
                        poll["mpdu_tx_attempts_total"] = 1
                    for window, coverage_us in (("1ms", 1_000), ("5ms", 5_000), ("20ms", 20_000)):
                        attempts = 0 if window == "1ms" and frame_id % 2 == 0 else frame_id + 1
                        retries = 0 if attempts == 0 else frame_id % min(attempts + 1, 3)
                        if (
                            rolling_counter_failure
                            and frame_id == 8
                            and path_id == 1
                            and window == "5ms"
                        ):
                            retries = attempts + 1
                        poll.update(
                            {
                                f"mpdu_attempts_{window}": attempts,
                                f"mpdu_positive_acks_{window}": max(0, attempts - retries),
                                f"mpdu_attempt_failures_{window}": retries,
                                f"mpdu_retries_{window}": retries,
                                f"mpdu_retry_ratio_{window}": (
                                    "" if attempts == 0 else retries / attempts
                                ),
                                f"acknowledged_mac_service_bytes_{window}": attempts * 1_300,
                                f"mpdu_queue_to_ack_mean_{window}_us": "",
                                f"mpdu_queue_to_ack_p95_{window}_us": "",
                                f"mpdu_first_attempt_to_ack_mean_{window}_us": "",
                                f"mpdu_first_attempt_to_ack_p95_{window}_us": "",
                                f"phy_tx_fraction_{window}": 0.1,
                                f"phy_rx_fraction_{window}": 0.1,
                                f"phy_busy_fraction_{window}": 0.2,
                                f"phy_idle_fraction_{window}": 0.6,
                                f"phy_other_fraction_{window}": 0.0,
                                f"history_coverage_{window}_us": coverage_us,
                            }
                        )
                    if timing_failure and frame_id == 8 and path_id == 1 and stage == "T2":
                        poll["available_time_ns"] = available_ns + 1
                    polling.append(poll)

        sample_fields = list(
            dict.fromkeys(
                [
                    "run_id",
                    "frame_id",
                    "path_id",
                    "copy_id",
                    "sample_stage",
                    "sample_offset_us",
                    "sample_time_ns",
                    "latest_feature_event_time_ns",
                    "latest_feature_event_sequence",
                    "generation_time_ns",
                    "deadline_time_ns",
                    "actionable",
                    *base.F0_FIELDS,
                    *base.PRIMARY_CURRENT_FIELDS,
                    *base.SECONDARY_QUEUE_FIELDS,
                ]
            )
        )
        polling_fields = list(
            dict.fromkeys(
                [
                    "run_id",
                    "frame_id",
                    "path_id",
                    "copy_id",
                    "sample_stage",
                    "sample_offset_us",
                    "report_available",
                    "capture_time_ns",
                    "available_time_ns",
                    "staleness_us",
                    "latest_feature_event_time_ns",
                    "latest_feature_event_sequence",
                    *[raw for _, raw in (
                        ("attempts", "mpdu_tx_attempts_total"),
                        ("acks", "mpdu_positive_acks_total"),
                        ("failures", "mpdu_tx_attempt_failures_total"),
                        ("retries", "mpdu_retries_total"),
                        ("ppdu", "ppdu_tx_count_total"),
                    )],
                    "mpdu_terminal_drops_total",
                    "mpdu_retry_limit_drops_total",
                    "mpdu_lifetime_drops_total",
                    "mpdu_queue_drops_total",
                    "last_tx_attempt_time_ns",
                    "last_positive_ack_time_ns",
                    "current_mcs",
                    "current_nss",
                    "current_channel_width_mhz",
                    "current_guard_interval_ns",
                    *base.PRIMARY_ROLLING_FIELDS,
                    *base.SECONDARY_PHY_FIELDS,
                    *[f"history_coverage_{window}_us" for window in ("1ms", "5ms", "20ms")],
                ]
            )
        )
        _write_csv(
            self.path / "frames.csv",
            [
                "run_id",
                "frame_id",
                "frame_type",
                "generation_time_us",
                "deadline_us",
                "copy_0_completion_us",
                "incomplete",
                "deadline_miss",
                "union_latency_us",
            ],
            frames,
        )
        _write_csv(
            self.path / "randomized_intervention_assignments.csv",
            [
                "run_id",
                "frame_id",
                "eligible_t2",
                "assigned_arm",
                "propensity",
                "nominal_airtime_us",
                "estimated_airtime_us",
            ],
            assignments,
        )
        _write_csv(
            self.path / "randomized_intervention_executions.csv",
            [
                "run_id",
                "frame_id",
                "assigned_arm",
                "attempted",
                "launched",
                "noncompliance",
                "execution_stage",
                "status",
                "secondary_sample_time_ns",
            ],
            executions,
        )
        _write_csv(
            self.path / "secondary_airtime_settlements.csv",
            [
                "run_id",
                "frame_id",
                "settlement_time_ns",
                "released_airtime_us",
                "measured_airtime_us",
                "nominal_airtime_us",
                "fallback",
            ],
            settlements,
        )
        _write_csv(self.path / "prediction_samples.csv", sample_fields, samples)
        if reverse_polling_rows:
            polling.reverse()
        _write_csv(
            self.path / "prediction_polling_samples.csv", polling_fields, polling
        )
        event_rows: list[dict[str, object]] = []
        if dirty_event:
            start_ns = capture_by_frame[5] - 5_000_000
            event_rows.append(
                {
                    "run_id": self.run_id,
                    "time_ns": start_ns,
                    "path_id": 0,
                    "ppdu_duration_us": 1000,
                    "tagged_mpdu_bytes": 1300,
                    "frame_ids": 0,
                    "mixed_ppdu": 0,
                    "cumulative_tagged_airtime_us": 1000,
                }
            )
        _write_csv(
            self.path / "secondary_airtime_events.csv",
            [
                "run_id",
                "time_ns",
                "path_id",
                "ppdu_duration_us",
                "tagged_mpdu_bytes",
                "frame_ids",
                "mixed_ppdu",
                "cumulative_tagged_airtime_us",
            ],
            event_rows,
        )


def _make_inputs(root: Path, **fixture_options: object) -> tuple[TemporalFixture, Path]:
    fixture = TemporalFixture(root / "run", **fixture_options)
    v1 = root / "v1"
    with mock.patch("build_randomized_intervention_dataset.validate_run"):
        base.build_dataset([fixture.path], v1)
    return fixture, v1


class TemporalDatasetBuilderTest(unittest.TestCase):
    def _build(
        self, root: Path, **options: object
    ) -> tuple[TemporalFixture, Path, dict[str, object]]:
        fixture, v1 = _make_inputs(root, **options)
        output = root / "temporal"
        with mock.patch("build_randomized_intervention_dataset.validate_run"):
            metadata = build_temporal_dataset(v1, [fixture.path], output)
        return fixture, output, metadata

    def test_exact_schema_order_and_preserved_v1_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, output, metadata = self._build(root)
            rows = _read_csv(output / OUTPUT_CSV)
            self.assertTrue(rows)
            self.assertEqual(tuple(rows[0]), DATASET_COLUMNS)
            self.assertEqual(metadata["feature_contract_id"], FEATURE_CONTRACT_ID)
            self.assertEqual(
                metadata["feature_contract"]["feature_columns"], list(FEATURE_COLUMNS)
            )
            self.assertEqual(rows[0]["analysis_stage"], "T2")
            self.assertEqual(rows[0]["split_role"], "train")
            self.assertEqual(rows[0]["outcome_primary_latency_us"], "13000")
            self.assertEqual(rows[0]["frame_id"], "8")
            self.assertEqual(rows[0]["x_primary_lag1_mpdu_attempts_5ms"], "8")
            self.assertEqual(rows[0]["x_primary_lag8_mpdu_attempts_5ms"], "1")
            self.assertEqual(rows[0]["x_primary_lag8_last_ack_age_us_missing"], "1")

    def test_allowlist_excludes_leakage_and_secondary_tagged_state(self) -> None:
        self.assertEqual(len(FEATURE_COLUMNS), len(set(FEATURE_COLUMNS)))
        self.assertTrue(all(column.startswith("x_") for column in FEATURE_COLUMNS))
        forbidden = (
            "run_id",
            "frame_id",
            "sample_time_ns",
            "capture_time_ns",
            "available_time_ns",
            "watermark",
            "_total",
            "assigned_arm",
            "execution",
            "outcome_",
            "settlement",
            "cleanliness",
        )
        for column in FEATURE_COLUMNS:
            self.assertFalse(any(token in column for token in forbidden), column)
        secondary_temporal = [
            column for column in FEATURE_COLUMNS if column.startswith("x_secondary_lag")
        ]
        self.assertTrue(secondary_temporal)
        self.assertTrue(all("phy_" in column for column in secondary_temporal))
        for column in secondary_temporal:
            self.assertFalse(
                any(token in column for token in ("mcs", "ack", "retry", "tagged", "signal")),
                column,
            )

    def test_lags_are_keyed_by_frame_not_csv_row_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, output, _ = self._build(root, reverse_polling_rows=True)
            row = _read_csv(output / OUTPUT_CSV)[0]
            self.assertEqual(row["frame_id"], "8")
            self.assertEqual(row["x_primary_lag1_mpdu_attempts_20ms"], "8")
            self.assertEqual(row["x_primary_lag3_mpdu_attempts_20ms"], "6")
            self.assertEqual(row["x_primary_lag8_mpdu_attempts_20ms"], "1")

    def test_historical_builder_rejects_missing_ahead_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                TemporalDatasetError,
                "invalid number x_primary_mac_service_bytes_ahead_of_frame",
            ):
                self._build(root, missing_ahead_frame=9)

    def test_timing_cadence_rolling_and_reset_fail_closed(self) -> None:
        cases = (
            ("timing", {"timing_failure": True}, "availability timing"),
            ("cadence", {"cadence_failure": True}, "cadence/position"),
            ("rolling", {"rolling_counter_failure": True}, "retry counters"),
            ("reset", {"counter_reset": True}, "cumulative counter reset"),
        )
        for name, options, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                fixture, v1 = _make_inputs(root, **options)
                with mock.patch("build_randomized_intervention_dataset.validate_run"):
                    with self.assertRaisesRegex(TemporalDatasetError, message):
                        build_temporal_dataset(v1, [fixture.path], root / "temporal")
                self.assertFalse((root / "temporal").exists())

    def test_warmup_and_secondary_action_dirty_rows_are_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, output, metadata = self._build(
                root, dirty_event=True, active_reservation=True
            )
            counts = metadata["filter_counts"]
            self.assertEqual(counts["excluded_lag8_warmup"], 8)
            self.assertGreater(counts["excluded_secondary_direct_tx_dirty"], 0)
            self.assertGreater(counts["excluded_secondary_active_reservation"], 0)
            self.assertGreater(counts["included_rows"], 0)
            self.assertEqual(
                counts["candidate_v1_t2_rows"],
                counts["excluded_lag8_warmup"]
                + counts["excluded_any_secondary_action_dirty"]
                + counts["included_rows"],
            )
            self.assertNotIn(
                "clean", " ".join(metadata["feature_contract"]["new_feature_columns"])
            )
            self.assertTrue((output / "artifact_manifest.json").is_file())

    def test_output_hashes_are_deterministic_and_manifest_closes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, v1 = _make_inputs(root)
            outputs = (root / "temporal-a", root / "temporal-b")
            with mock.patch("build_randomized_intervention_dataset.validate_run"):
                for output in outputs:
                    build_temporal_dataset(v1, [fixture.path], output)
            manifests = [
                json.loads((output / "artifact_manifest.json").read_text())
                for output in outputs
            ]
            self.assertEqual(manifests[0], manifests[1])
            self.assertEqual(
                set(manifests[0]["artifacts_sha256"]),
                {OUTPUT_CSV, "dataset_metadata.json"},
            )

    def test_tampered_v1_artifact_is_rejected_before_raw_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, v1 = _make_inputs(root)
            with (v1 / "randomized_t2.csv").open("a", encoding="utf-8") as destination:
                destination.write("tamper\n")
            with mock.patch("build_randomized_intervention_dataset.validate_run") as validator:
                with self.assertRaisesRegex(TemporalDatasetError, "artifact hash mismatch"):
                    build_temporal_dataset(v1, [fixture.path], root / "temporal")
            validator.assert_not_called()

    def test_authoritative_validator_precedes_augmenter_raw_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, v1 = _make_inputs(root)
            sentinel = RuntimeError("authoritative temporal gate")
            with mock.patch(
                "build_randomized_intervention_dataset.validate_run", side_effect=sentinel
            ) as validator:
                with self.assertRaisesRegex(RuntimeError, "authoritative temporal gate"):
                    build_temporal_dataset(v1, [fixture.path], root / "temporal")
            validator.assert_called_once_with(fixture.path.resolve())


if __name__ == "__main__":
    unittest.main()
