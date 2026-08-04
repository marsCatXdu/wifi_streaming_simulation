#!/usr/bin/env python3
"""Focused tests for the randomized-intervention dataset builder."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from build_randomized_intervention_dataset import (  # noqa: E402
    DATASET_COLUMNS,
    FEATURE_COLUMNS,
    F0_FIELDS,
    PRIMARY_CURRENT_FIELDS,
    PRIMARY_ROLLING_FIELDS,
    SECONDARY_PHY_FIELDS,
    SECONDARY_QUEUE_FIELDS,
    DatasetError,
    build_dataset,
    deterministic_run_splits,
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


class RunFixture:
    """Minimal ledgers accepted after the authoritative validator is mocked."""

    arms = {
        0: "CONTROL",
        1: "FULL_COPY_T2",
        2: "FULL_COPY_T4",
        3: "FULL_COPY_T4",
    }

    def __init__(self, path: Path, seed: int, placement_x: float) -> None:
        self.path = path
        self.seed = seed
        self.run_number = 1
        self.run_id = f"run-{seed}"
        path.mkdir(parents=True)
        self.config = {
            "run_id": self.run_id,
            "seed": seed,
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
                    "bsses": [{"bss_id": 0, "ap": [placement_x, 1.0]}],
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
        self._write_ledgers()

    def _write_ledgers(self) -> None:
        frames: list[dict[str, object]] = []
        assignments: list[dict[str, object]] = []
        executions: list[dict[str, object]] = []
        settlements: list[dict[str, object]] = []
        samples: list[dict[str, object]] = []
        polling: list[dict[str, object]] = []
        propensities = {
            "CONTROL": 0.80,
            "FULL_COPY_T2": 0.08,
            "FULL_COPY_T4": 0.12,
        }
        union_latencies = {0: 9000, 1: 10500, 2: 12000, 3: 40000}
        primary_latencies = {0: 13000, 1: 35000, 2: None, 3: 45000}
        for frame_id, arm in self.arms.items():
            launched = arm != "CONTROL" and frame_id != 3
            generation_us = 1_000_000 + frame_id * 33333
            primary_latency = primary_latencies[frame_id]
            frames.append(
                {
                    "run_id": self.run_id,
                    "frame_id": frame_id,
                    "frame_type": "I_FRAME" if frame_id == 0 else "P_FRAME",
                    "generation_time_us": generation_us,
                    "deadline_us": 33333,
                    "copy_0_completion_us": (
                        ""
                        if primary_latency is None
                        else generation_us + primary_latency
                    ),
                    "incomplete": 0,
                    "deadline_miss": int(frame_id == 3),
                    "union_latency_us": union_latencies[frame_id],
                }
            )
            assignments.append(
                {
                    "run_id": self.run_id,
                    "frame_id": frame_id,
                    "eligible_t2": 1,
                    "assigned_arm": arm,
                    "propensity": propensities[arm],
                    "nominal_airtime_us": 100 + frame_id,
                    "estimated_airtime_us": 125 + frame_id,
                }
            )
            if arm == "CONTROL":
                stage = "T2"
                status = "control_no_launch"
            elif arm == "FULL_COPY_T2":
                stage = "T2"
                status = "launched_t2"
            elif frame_id == 3:
                stage = "T4"
                status = "primary_not_actionable_t4"
            else:
                stage = "T4"
                status = "launched_t4"
            executions.append(
                {
                    "run_id": self.run_id,
                    "frame_id": frame_id,
                    "assigned_arm": arm,
                    "attempted": int(launched),
                    "launched": int(launched),
                    "noncompliance": 0,
                    "execution_stage": stage,
                    "status": status,
                }
            )
            if launched:
                settlements.append(
                    {
                        "run_id": self.run_id,
                        "frame_id": frame_id,
                        "measured_airtime_us": 90 + frame_id,
                        "released_airtime_us": 10 + frame_id,
                        "fallback": 0,
                    }
                )
            for stage, offset in (("T2", 2000), ("T4", 4000)):
                for path_id, copy_id in ((1, 0), (0, 1)):
                    row: dict[str, object] = {
                        "run_id": self.run_id,
                        "frame_id": frame_id,
                        "path_id": path_id,
                        "copy_id": copy_id,
                        "sample_stage": stage,
                        "sample_offset_us": offset,
                        "actionable": int(not (frame_id == 3 and stage == "T4")),
                        "frame_age_us": offset,
                        "deadline_slack_us": 33333 - offset,
                        "frame_size_bytes": 12000,
                        "frame_packet_count": 10,
                        "frame_type": "I_FRAME" if frame_id == 0 else "P_FRAME",
                        "packets_submitted": 10 if path_id == 1 else 0,
                        "application_socket_packet_bytes_submitted": (
                            13000 if path_id == 1 else 0
                        ),
                        "packets_remaining_to_submit": 0 if path_id == 1 else 10,
                    }
                    for index, field in enumerate(PRIMARY_CURRENT_FIELDS):
                        row[field] = (
                            1000 * frame_id
                            + 100 * (stage == "T4")
                            + 10 * path_id
                            + index
                        )
                    samples.append(row)

                    poll: dict[str, object] = {
                        "run_id": self.run_id,
                        "frame_id": frame_id,
                        "path_id": path_id,
                        "copy_id": copy_id,
                        "sample_stage": stage,
                        "sample_offset_us": offset,
                        "report_available": 1,
                    }
                    for index, field in enumerate(PRIMARY_ROLLING_FIELDS):
                        poll[field] = (
                            100_000 * frame_id
                            + 10_000 * (stage == "T4")
                            + 1000 * path_id
                            + index
                        )
                    polling.append(poll)

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
            ],
            executions,
        )
        _write_csv(
            self.path / "secondary_airtime_settlements.csv",
            [
                "run_id",
                "frame_id",
                "measured_airtime_us",
                "released_airtime_us",
                "fallback",
            ],
            settlements,
        )
        sample_fields = list(
            dict.fromkeys(
                [
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
                ]
            )
        )
        _write_csv(self.path / "prediction_samples.csv", sample_fields, samples)
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
                    *PRIMARY_ROLLING_FIELDS,
                    *SECONDARY_PHY_FIELDS,
                ]
            )
        )
        _write_csv(
            self.path / "prediction_polling_samples.csv", polling_fields, polling
        )


class RandomizedDatasetBuilderTest(unittest.TestCase):
    def test_builds_leakage_safe_t2_and_untreated_t4_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = RunFixture(root / "run-a", 1101, -2.0)
            second = RunFixture(root / "run-b", 1102, 8.0)
            output = root / "dataset"
            with mock.patch(
                "build_randomized_intervention_dataset.validate_run"
            ) as validator:
                metadata = build_dataset([first.path, second.path], output)

            self.assertEqual(validator.call_count, 2)
            self.assertEqual(
                {Path(call.args[0]) for call in validator.call_args_list},
                {first.path, second.path},
            )
            t2 = _read_csv(output / "randomized_t2.csv")
            t4 = _read_csv(output / "randomized_t4_wait.csv")
            self.assertEqual(len(t2), 4)
            self.assertEqual(len(t4), 4)
            self.assertEqual({row["assigned_arm"] for row in t2}, {
                "CONTROL", "FULL_COPY_T2"
            })
            self.assertEqual({row["assigned_arm"] for row in t4}, {
                "CONTROL", "FULL_COPY_T4"
            })
            self.assertNotIn("1", {row["frame_id"] for row in t4})
            self.assertNotIn("3", {row["frame_id"] for row in t4})
            self.assertTrue(all(row["decision_primary_actionable"] == "1" for row in t4))
            self.assertEqual(
                float(t2[0]["treatment_probability"]), 0.08 / (0.08 + 0.80)
            )
            self.assertEqual(
                float(t4[0]["treatment_probability"]), 0.12 / (0.12 + 0.80)
            )

            first_t2_control = next(
                row
                for row in t2
                if row["seed"] == "1101" and row["frame_id"] == "0"
            )
            self.assertEqual(first_t2_control["x_primary_mpdu_attempts_1ms"], "1000")
            self.assertEqual(first_t2_control["x_primary_mac_queue_packets"], "17")
            self.assertEqual(first_t2_control["x_secondary_mac_queue_packets"], "7")
            self.assertEqual(first_t2_control["outcome_secondary_airtime_us"], "0")
            self.assertEqual(first_t2_control["outcome_primary_incomplete"], "0")
            self.assertEqual(first_t2_control["outcome_primary_deadline_miss"], "0")
            self.assertEqual(first_t2_control["outcome_primary_latency_us"], "13000")
            self.assertEqual(first_t2_control["outcome_deadline_rescue"], "0")
            self.assertEqual(first_t2_control["outcome_tail_rescue_10000us"], "1")
            self.assertEqual(
                first_t2_control["outcome_deadline_capped_latency_saving_us"],
                "4000",
            )
            first_t2_treated = next(
                row
                for row in t2
                if row["seed"] == "1101" and row["frame_id"] == "1"
            )
            self.assertEqual(first_t2_treated["outcome_secondary_airtime_us"], "91")
            self.assertEqual(first_t2_treated["outcome_primary_deadline_miss"], "1")
            self.assertEqual(first_t2_treated["outcome_deadline_rescue"], "1")
            self.assertEqual(first_t2_treated["outcome_tail_rescue_10000us"], "0")
            self.assertEqual(first_t2_treated["outcome_tail_rescue_11000us"], "1")
            self.assertEqual(
                first_t2_treated["outcome_deadline_capped_latency_saving_us"],
                "22833",
            )
            first_t4_treated = next(
                row
                for row in t4
                if row["seed"] == "1101" and row["frame_id"] == "2"
            )
            self.assertEqual(first_t4_treated["outcome_primary_incomplete"], "1")
            self.assertEqual(first_t4_treated["outcome_primary_latency_us"], "")
            self.assertEqual(first_t4_treated["outcome_deadline_rescue"], "1")
            self.assertEqual(first_t4_treated["outcome_tail_rescue_11000us"], "0")
            self.assertEqual(first_t4_treated["outcome_tail_rescue_12000us"], "1")
            self.assertEqual(
                first_t4_treated["outcome_deadline_capped_latency_saving_us"],
                "21333",
            )

            self.assertEqual(tuple(t2[0]), DATASET_COLUMNS)
            self.assertEqual(tuple(t4[0]), DATASET_COLUMNS)
            self.assertEqual(
                metadata["feature_contract"]["feature_columns"], list(FEATURE_COLUMNS)
            )
            self.assertEqual(metadata["split"]["counts"]["train"], 1)
            # Realized OBSS placements are allowed to vary by seed.
            self.assertEqual(len(metadata["source_runs"]), 2)
            manifest = json.loads((output / "artifact_manifest.json").read_text())
            self.assertEqual(
                set(manifest["artifacts_sha256"]),
                {"randomized_t2.csv", "randomized_t4_wait.csv", "dataset_metadata.json"},
            )

    def test_feature_allowlist_has_no_known_leakage_families(self) -> None:
        self.assertEqual(len(FEATURE_COLUMNS), len(set(FEATURE_COLUMNS)))
        self.assertTrue(all(name.startswith("x_") for name in FEATURE_COLUMNS))
        forbidden = (
            "run_id",
            "frame_id",
            "sample_time",
            "generation_time",
            "deadline_time",
            "watermark",
            "raw_draw",
            "assigned_arm",
            "execution_status",
            "outcome_",
            "current_cw",
            "_total",
        )
        for name in FEATURE_COLUMNS:
            self.assertFalse(any(token in name for token in forbidden), name)
        secondary = [name for name in FEATURE_COLUMNS if name.startswith("x_secondary_")]
        self.assertTrue(secondary)
        self.assertTrue(
            all(
                "phy_" in name
                or name
                in {
                    "x_secondary_mac_queue_packets",
                    "x_secondary_mac_queue_service_bytes",
                }
                for name in secondary
            )
        )

    def test_exact_deterministic_64_16_16_run_split(self) -> None:
        identities = [(seed, 1) for seed in range(1101, 1197)]
        first = deterministic_run_splits(identities)
        second = deterministic_run_splits(list(reversed(identities)))
        self.assertEqual(first, second)
        self.assertEqual(
            Counter(first.values()),
            {"train": 64, "calibration": 16, "test": 16},
        )
        self.assertEqual(len({identity for identity, role in first.items() if role == "test"}), 16)

    def test_authoritative_validator_runs_before_source_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            run.mkdir()
            (run / "resolved_config.json").write_text("not json", encoding="utf-8")
            sentinel = RuntimeError("authoritative validation failed")
            with mock.patch(
                "build_randomized_intervention_dataset.validate_run",
                side_effect=sentinel,
            ) as validator:
                with self.assertRaisesRegex(RuntimeError, "authoritative validation failed"):
                    build_dataset([run], Path(temporary) / "output")
            validator.assert_called_once_with(run.resolve())

    def test_rejects_incomplete_paired_polling_join(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = RunFixture(root / "run", 1101, 0.0)
            path = fixture.path / "prediction_polling_samples.csv"
            rows = _read_csv(path)
            header = list(rows[0])
            rows = [
                row
                for row in rows
                if not (
                    row["frame_id"] == "2"
                    and row["sample_stage"] == "T4"
                    and row["path_id"] == "0"
                )
            ]
            _write_csv(path, header, rows)
            with mock.patch("build_randomized_intervention_dataset.validate_run"):
                with self.assertRaisesRegex(DatasetError, "paired T2/T4 telemetry"):
                    build_dataset([fixture.path], root / "output")

    def test_rejects_invariant_design_change_but_not_realized_placement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = RunFixture(root / "run-a", 1101, -4.0)
            second = RunFixture(root / "run-b", 1102, 9.0)
            second.config["stream"]["fps"] = 60
            (second.path / "resolved_config.json").write_text(
                json.dumps(second.config), encoding="utf-8"
            )
            with mock.patch("build_randomized_intervention_dataset.validate_run"):
                with self.assertRaisesRegex(DatasetError, "experiment design differs"):
                    build_dataset([first.path, second.path], root / "output")

    def test_rejects_union_completion_later_than_primary_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = RunFixture(root / "run", 1101, 0.0)
            path = fixture.path / "frames.csv"
            rows = _read_csv(path)
            header = list(rows[0])
            rows[0]["copy_0_completion_us"] = str(
                int(rows[0]["generation_time_us"]) + 5000
            )
            _write_csv(path, header, rows)
            with mock.patch("build_randomized_intervention_dataset.validate_run"):
                with self.assertRaisesRegex(
                    DatasetError, "union completion is later than primary completion"
                ):
                    build_dataset([fixture.path], root / "output")


if __name__ == "__main__":
    unittest.main()
