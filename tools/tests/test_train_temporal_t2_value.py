#!/usr/bin/env python3
"""Focused tests for the primary-only temporal T2 value trainer."""

from __future__ import annotations

import csv
import hashlib
import json
import pickle
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import build_randomized_intervention_dataset as base  # noqa: E402
import build_randomized_temporal_dataset as temporal  # noqa: E402
import train_randomized_value as audited  # noqa: E402
import train_temporal_t2_value as trainer  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(root: Path) -> None:
    value = {
        "manifest_schema_version": 1,
        "hash_algorithm": "sha256",
        "artifacts_sha256": {
            temporal.OUTPUT_CSV: sha256(root / temporal.OUTPUT_CSV),
            temporal.OUTPUT_METADATA: sha256(root / temporal.OUTPUT_METADATA),
        },
    }
    (root / temporal.OUTPUT_MANIFEST).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _latency_fields(primary_latency: int | None, union_latency: int | None) -> dict[str, str]:
    row = {
        "outcome_incomplete": str(int(union_latency is None)),
        "outcome_deadline_miss": str(
            int(union_latency is None or union_latency > 33333)
        ),
        "outcome_union_latency_us": "" if union_latency is None else str(union_latency),
        "outcome_primary_incomplete": str(int(primary_latency is None)),
        "outcome_primary_deadline_miss": str(
            int(primary_latency is None or primary_latency > 33333)
        ),
        "outcome_primary_latency_us": (
            "" if primary_latency is None else str(primary_latency)
        ),
    }
    for threshold in (10000, 11000, 12000, 12500):
        good = int(union_latency is not None and union_latency <= threshold)
        primary_good = int(
            primary_latency is not None and primary_latency <= threshold
        )
        row[f"outcome_complete_by_{threshold}us"] = str(good)
        row[f"outcome_primary_complete_by_{threshold}us"] = str(primary_good)
        row[f"outcome_tail_rescue_{threshold}us"] = str(
            int(primary_good == 0 and good == 1)
        )
    row["outcome_deadline_rescue"] = str(
        int(
            (primary_latency is None or primary_latency > 33333)
            and union_latency is not None
            and union_latency <= 33333
        )
    )
    row["outcome_deadline_capped_latency_saving_us"] = "0"
    return row


def _feature_value(name: str, seed: int, frame: int, risk: bool) -> str:
    if name == "x_f0_frame_type":
        return "I_FRAME" if frame % 60 == 0 else "P_FRAME"
    if name == "x_f0_frame_size_bytes" and seed == 1000 and frame == 8:
        return "16777217"
    if name == "x_f0_frame_age_us":
        return "2000"
    if name == "x_f0_deadline_slack_us":
        return "31333"
    if name == "x_primary_frame_mac_service_bytes_pending_primary":
        return "12000" if risk else "1200"
    if name == "x_primary_frame_packets_pending_primary":
        return "10" if risk else "1"
    if name == "x_primary_mac_service_bytes_ahead_of_frame":
        return "15000" if risk else "500"
    if name.startswith("x_primary_acknowledged_mac_service_bytes_"):
        return "100" if risk else "8000"
    if name.startswith("x_primary_phy_busy_fraction_"):
        return "0.9" if risk else "0.1"
    if name.startswith("x_primary_phy_idle_fraction_"):
        return "0.05" if risk else "0.8"
    if name.startswith("x_primary_phy_") and "fraction" in name:
        return "0.0125" if risk else "0.025"
    if name.endswith("_missing"):
        return "0"
    return format(((seed % 17) + frame % 13 + int(risk) * 20) / 50.0, ".8f")


def make_temporal_dataset(root: Path, run_count: int = 24) -> None:
    root.mkdir(parents=True)
    groups = [(1000 + index, 1) for index in range(run_count)]
    splits = audited.expected_run_splits(groups)
    assignments = [
        {
            "seed": seed,
            "run_number": run_number,
            "split_role": splits[(seed, run_number)],
        }
        for seed, run_number in sorted(groups)
    ]
    rows: list[dict[str, str]] = []
    for seed, run_number in groups:
        for frame in range(8, 68):
            treatment = (seed + frame) % 11 == 0
            risk = (seed * 3 + frame) % 5 == 0
            primary_incomplete = False
            primary_latency = None if primary_incomplete else (40000 if risk else 9000)
            rescue = treatment and (seed + frame) % 4 != 0
            union_latency = 8500 if rescue else primary_latency
            row = {field: "" for field in temporal.DATASET_COLUMNS}
            row.update(
                {
                    "dataset_schema_version": "1",
                    "run_id": f"run-{seed}",
                    "seed": str(seed),
                    "run_number": str(run_number),
                    "split_role": splits[(seed, run_number)],
                    "frame_id": str(frame),
                    "analysis_stage": "T2",
                    "assigned_arm": "FULL_COPY_T2" if treatment else "CONTROL",
                    "treatment": str(int(treatment)),
                    "treatment_probability": format(0.08 / 0.88, ".17g"),
                    "assigned_arm_probability": "0.08" if treatment else "0.8",
                    "eligible_t2": "1",
                    "decision_primary_actionable": "1",
                    "attempted": str(int(treatment)),
                    "launched": str(int(treatment)),
                    "noncompliance": "0",
                    "execution_stage": "T2" if treatment else "NONE",
                    "execution_status": "launched_t2" if treatment else "control_no_launch",
                    "outcome_secondary_airtime_us": (
                        str(8500 if frame % 60 == 0 else 2300) if treatment else "0"
                    ),
                    "outcome_secondary_released_airtime_us": "0",
                    "outcome_secondary_fallback": "0",
                    "action_nominal_airtime_us": "2000",
                    "action_estimated_airtime_us": "2500",
                }
            )
            row.update(_latency_fields(primary_latency, union_latency))
            row.update(
                {
                    name: _feature_value(name, seed, frame, risk)
                    for name in temporal.FEATURE_COLUMNS
                }
            )
            rows.append(row)
    with (root / temporal.OUTPUT_CSV).open(
        "w", newline="", encoding="utf-8"
    ) as destination:
        writer = csv.DictWriter(
            destination, fieldnames=temporal.DATASET_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)

    raw_files = set(base.SOURCE_FILES) | set(temporal.EXTRA_RAW_FILES)
    metadata = {
        "dataset_schema_version": temporal.DATASET_SCHEMA_VERSION,
        "feature_contract_id": temporal.FEATURE_CONTRACT_ID,
        "comparison": {
            **trainer.EXPECTED_COMPARISON,
            "row_count": len(rows),
        },
        "validation": {
            "authoritative_validator": "validate_outputs.validate_run",
            "every_raw_run_validated_before_augmenter_source_reads": True,
            "v1_rows_regenerated_and_joined_exactly_by_run_id_frame_id": True,
            "v1_artifact_manifest_verified_before_row_reads": True,
            "raw_v1_source_hashes_verified": True,
        },
        "input_v1": {
            "path": "/synthetic/v1",
            "feature_contract_id": base.FEATURE_CONTRACT_ID,
            "artifacts_sha256": {
                name: "1" * 64
                for name in set(temporal.V1_ARTIFACTS)
                | {"artifact_manifest.json"}
            },
        },
        "feature_contract": trainer._expected_feature_contract(),
        "non_feature_columns": list(temporal.NON_FEATURE_COLUMNS),
        "split": {
            "algorithm": audited.SPLIT_ALGORITHM,
            "unit": "(seed, run_number)",
            "target_ratio": {"train": 64, "calibration": 16, "test": 16},
            "counts": {
                role: list(splits.values()).count(role)
                for role in ("train", "calibration", "test")
            },
            "assignments": assignments,
        },
        "filter_counts": {
            "candidate_v1_t2_rows": len(rows),
            "excluded_lag8_warmup": 0,
            "excluded_secondary_direct_tx_dirty": 0,
            "excluded_secondary_active_reservation": 0,
            "excluded_any_secondary_action_dirty": 0,
            "included_rows": len(rows),
            "dirty_endpoint_counts": {
                name: {"direct_tx": 0, "active_reservation": 0}
                for name in ("current", "lag1", "lag3", "lag8")
            },
        },
        "build_identity": {
            "ns3_version": "ns-3.48",
            "ns3_upstream_commit": "upstream",
            "project_git_commit": "project",
            "compiler": "13.3.0",
            "build_profile": "optimized",
        },
        "environment_compatibility": {
            "ignored_seed_realization_fields": ["background.obss.bsses"],
            "invariant_projection": {
                "randomizedIntervention": audited.RANDOMIZED_INTERVENTION_CONTRACT,
                "predictionTelemetry": {
                    "history_windows_us": [1000, 5000, 20000],
                    "polling_interval_us": 1000,
                    "polling_report_delay_us": 1000,
                    "sample_offsets_us": [0, 2000, 4000],
                    "oracle_features_enabled": False,
                },
                "stream": {"deadline_us": 33333, "fps": 30},
            },
        },
        "design_contract_sha256": "2" * 64,
        "source_runs": [
            {
                "run_id": f"run-{seed}",
                "seed": seed,
                "run_number": run_number,
                "path": f"/synthetic/run-{seed}",
                "files_sha256": {name: "0" * 64 for name in raw_files},
            }
            for seed, run_number in groups
        ],
    }
    (root / temporal.OUTPUT_METADATA).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_manifest(root)


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "temporal"
        make_temporal_dataset(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_exact_primary_only_families_at_float32_boundary(self) -> None:
        dataset = trainer.load_temporal_dataset(self.root)
        self.assertEqual(
            {name: len(values) for name, values in dataset.family_feature_names.items()},
            {
                "primary_base": 68,
                "primary_compact_physics": 75,
                "primary_compact_physics_temporal": 246,
            },
        )
        self.assertTrue(
            all(
                "secondary" not in feature
                for names in dataset.family_feature_names.values()
                for feature in names
            )
        )
        index = dataset.family_feature_names["primary_base"].index(
            "x_f0_frame_size_bytes"
        )
        self.assertEqual(dataset.family_matrices["primary_base"][0, index], 16777216.0)
        self.assertNotEqual(dataset.family_matrices["primary_base"][0, index], 16777217.0)

    def test_rejects_rehashed_temporal_contract_mutation(self) -> None:
        path = self.root / temporal.OUTPUT_METADATA
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["feature_contract"]["exact_frame_lags"] = [1, 2, 8]
        path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _write_manifest(self.root)
        with self.assertRaisesRegex(trainer.TemporalTrainingError, "feature/schema"):
            trainer.load_temporal_dataset(self.root)

    def test_rejects_source_mutation_before_csv_parse(self) -> None:
        path = self.root / temporal.OUTPUT_CSV
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(trainer.TemporalTrainingError, "artifact hash"):
            trainer.load_temporal_dataset(self.root)

    def test_secondary_feature_values_cannot_change_primary_only_matrices(self) -> None:
        before = trainer.load_temporal_dataset(self.root)
        csv_path = self.root / temporal.OUTPUT_CSV
        with csv_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
            fieldnames = reader.fieldnames
        self.assertIsNotNone(fieldnames)
        selected_raw = {
            name for name in base.FEATURE_COLUMNS if not name.startswith("x_secondary_")
        } | set(trainer.PRIMARY_TEMPORAL_COLUMNS)
        excluded = [
            name for name in temporal.FEATURE_COLUMNS if name not in selected_raw
        ]
        self.assertTrue(any("secondary" in name for name in excluded))
        for row_index, row in enumerate(rows):
            for feature_index, name in enumerate(excluded):
                row[name] = format(1000.0 + row_index + feature_index / 1000.0, ".8f")
        with csv_path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination, fieldnames=fieldnames, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
        _write_manifest(self.root)
        after = trainer.load_temporal_dataset(self.root)
        for family in trainer.FEATURE_FAMILY_ORDER:
            np.testing.assert_array_equal(
                before.family_matrices[family], after.family_matrices[family]
            )


class SelectionTests(unittest.TestCase):
    def test_select_uses_frozen_maximin_tie_order_and_ignores_rejected(self) -> None:
        records = [
            {
                "candidate_ordinal": 1,
                "admissible": 0,
                "balanced_min_relative_improvement": 0.99,
                "mean_relative_improvement": 0.99,
                "dr_airtime_us_per_eligible_frame": 1.0,
                "realized_action_fraction": 0.01,
                "selected": 0,
            },
            {
                "candidate_ordinal": 5,
                "admissible": 1,
                "balanced_min_relative_improvement": 0.60,
                "mean_relative_improvement": 0.70,
                "dr_airtime_us_per_eligible_frame": 300.0,
                "realized_action_fraction": 0.15,
                "selected": 0,
            },
            {
                "candidate_ordinal": 4,
                "admissible": 1,
                "balanced_min_relative_improvement": 0.60,
                "mean_relative_improvement": 0.70,
                "dr_airtime_us_per_eligible_frame": 300.0,
                "realized_action_fraction": 0.15,
                "selected": 0,
            },
        ]
        selected = trainer._select(records)
        self.assertEqual(selected["candidate_ordinal"], 4)
        self.assertEqual(selected["selected"], 1)

    def test_global_fraction_respects_p_only_gate_and_frozen_threshold(self) -> None:
        scores = np.asarray([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2])
        gate = np.asarray([True, True, True, True, False, False, False, False])
        threshold = trainer._threshold_for_global_fraction(scores, gate, 0.25)
        self.assertEqual(threshold, float(np.float32(0.8)))
        np.testing.assert_array_equal(
            trainer._apply_threshold(scores, gate, threshold),
            [True, True, False, False, False, False, False, False],
        )
        test = np.asarray([0.79, 0.81, 0.95, 0.1])
        test_gate = np.asarray([True, True, False, True])
        np.testing.assert_array_equal(
            trainer._apply_threshold(test, test_gate, threshold),
            [False, True, False, False],
        )

    def test_conditional_tail_uses_completion_not_met_deadline(self) -> None:
        policy = np.asarray([False, True])
        components = {
            trainer.TARGET_DEADLINE: (
                np.asarray([0.2, 0.2]),
                np.asarray([0.1, 0.1]),
            ),
            trainer.TARGET_LATE18: (
                np.asarray([0.3, 0.3]),
                np.asarray([0.1, 0.1]),
            ),
            trainer.TARGET_COMPLETION: (
                np.asarray([0.8, 0.8]),
                np.asarray([0.9, 0.9]),
            ),
            trainer.TARGET_BAD12: (
                np.asarray([0.5, 0.5]),
                np.asarray([0.2, 0.2]),
            ),
        }
        result = trainer._policy_metrics(policy, components, np.ones(2))
        self.assertAlmostEqual(result["dr_policy_completion_probability"], 0.85)
        self.assertAlmostEqual(result["dr_policy_completed_late18_numerator"], 0.2)
        self.assertAlmostEqual(result["dr_policy_completed_late18_ratio"], 0.2 / 0.85)

    def test_candidate_rejects_infeasible_dr_estimates_and_nan_fails_closed(self) -> None:
        policy = np.asarray([True])
        components = {
            trainer.TARGET_DEADLINE: (np.asarray([0.1]), np.asarray([-0.1])),
            trainer.TARGET_LATE18: (np.asarray([0.1]), np.asarray([-0.1])),
            trainer.TARGET_COMPLETION: (np.asarray([0.9]), np.asarray([0.95])),
            trainer.TARGET_BAD12: (np.asarray([0.2]), np.asarray([0.1])),
        }
        record = trainer._candidate_record(
            ordinal=0,
            family="primary_base",
            ranker="deadline_value_per_cost",
            gate_name="all_frames",
            fraction=0.1,
            threshold=0.5,
            policy=policy,
            components=components,
            cost_phi1=np.asarray([-10.0]),
        )
        self.assertFalse(record["admissible"])
        self.assertIn("negative_assignment_airtime", record["rejection_reason"])
        self.assertIn("dr_probability_bounds", record["rejection_reason"])
        infeasible_metrics = trainer._policy_metrics(
            policy, components, np.asarray([-10.0])
        )
        with self.assertRaisesRegex(
            trainer.TemporalTrainingError, "infeasible policy metrics"
        ):
            trainer._require_feasible_policy_metrics(
                infeasible_metrics, "engineering test policy"
            )

        nested_components = {
            trainer.TARGET_DEADLINE: (np.asarray([0.1]), np.asarray([0.05])),
            trainer.TARGET_LATE18: (np.asarray([0.2]), np.asarray([0.1])),
            trainer.TARGET_COMPLETION: (np.asarray([0.8]), np.asarray([0.9])),
            trainer.TARGET_BAD12: (np.asarray([0.3]), np.asarray([0.15])),
        }
        nested_metrics = trainer._policy_metrics(
            policy, nested_components, np.asarray([1.0])
        )
        self.assertIn(
            "outcome_probability_nesting",
            trainer._metric_feasibility_reasons(nested_metrics),
        )

        invalid = dict(components)
        invalid[trainer.TARGET_DEADLINE] = (
            np.asarray([0.1]),
            np.asarray([np.nan]),
        )
        with self.assertRaisesRegex(trainer.TemporalTrainingError, "non-finite"):
            trainer._candidate_record(
                ordinal=1,
                family="primary_base",
                ranker="deadline_value_per_cost",
                gate_name="all_frames",
                fraction=0.1,
                threshold=0.5,
                policy=policy,
                components=invalid,
                cost_phi1=np.asarray([1.0]),
            )


class TrainingTests(unittest.TestCase):
    def test_emits_deterministic_atomic_honest_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_path = root / "temporal"
            make_temporal_dataset(dataset_path)
            dataset = trainer.load_temporal_dataset(dataset_path)
            first = root / "first"
            second = root / "second"
            with mock.patch.object(trainer, "BOOTSTRAP_REPLICATIONS", 100):
                metrics1 = trainer.train_temporal_t2_value(dataset, first)
                metrics2 = trainer.train_temporal_t2_value(dataset, second)
            for name in trainer.OUTPUT_FILES:
                self.assertEqual(sha256(first / name), sha256(second / name), name)
            self.assertFalse(metrics1["test_role_used_during_selection"])
            self.assertIn("previously_opened", metrics1["evidence_status"])
            self.assertEqual(metrics1, metrics2)
            selected = metrics1["selected_calibration_policy"]
            self.assertGreaterEqual(
                selected["deadline_miss_relative_improvement"], 0.50
            )
            self.assertGreaterEqual(
                selected["completed_late18_relative_improvement"], 0.50
            )
            self.assertIn(selected["frame_gate"], trainer.FRAME_GATES)
            test = metrics1["engineering_test_policy"]
            self.assertEqual(
                test["run_cluster_uncertainty"]["evidence_role"],
                "previously_opened_engineering_test",
            )
            with (first / trainer.OUTPUT_MODEL).open("rb") as source:
                bundle = pickle.load(source)
            self.assertEqual(bundle["selection_id"], trainer.SELECTION_ID)
            manifest = json.loads(
                (first / trainer.OUTPUT_MANIFEST).read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["selected_policy_contract"]["ordered_feature_names"],
                list(bundle["selected_policy"]["ordered_feature_names"]),
            )
            for family in trainer.FEATURE_FAMILY_ORDER:
                family_bundle = bundle["feature_families"][family]
                self.assertFalse(family_bundle["contains_secondary_feature"])
                self.assertTrue(
                    all(
                        "secondary" not in name
                        for name in family_bundle["ordered_feature_names"]
                    )
                )
                self.assertIn(
                    "training_risk_normalizers", family_bundle["heads"]
                )
            with self.assertRaisesRegex(
                trainer.TemporalTrainingError, "refusing to overwrite"
            ):
                trainer.train_temporal_t2_value(dataset, first)

            mutated_rows = []
            for row in dataset.data.rows:
                if row.split_role != "test":
                    mutated_rows.append(row)
                    continue
                outcomes = dict(row.outcomes)
                for target in (
                    trainer.TARGET_DEADLINE,
                    trainer.TARGET_LATE18,
                    trainer.TARGET_BAD12,
                ):
                    outcomes[target] = 1.0 - outcomes[target]
                outcomes[trainer.TARGET_COMPLETION] = 1.0
                mutated_rows.append(replace(row, outcomes=outcomes))
            mutated_dataset = replace(
                dataset,
                data=replace(dataset.data, rows=tuple(mutated_rows)),
            )
            mutated = root / "mutated-test"
            with mock.patch.object(
                trainer, "BOOTSTRAP_REPLICATIONS", 100
            ), mock.patch.object(trainer, "_require_feasible_policy_metrics"):
                mutated_metrics = trainer.train_temporal_t2_value(
                    mutated_dataset, mutated
                )
            self.assertEqual(
                metrics1["selected_calibration_policy"],
                mutated_metrics["selected_calibration_policy"],
            )
            self.assertEqual(
                sha256(first / trainer.OUTPUT_MODEL),
                sha256(mutated / trainer.OUTPUT_MODEL),
            )
            self.assertEqual(
                sha256(first / trainer.OUTPUT_CANDIDATES),
                sha256(mutated / trainer.OUTPUT_CANDIDATES),
            )

            failed = root / "failed"
            with mock.patch.object(
                trainer, "_write_candidates", side_effect=OSError("synthetic failure")
            ):
                with self.assertRaisesRegex(OSError, "synthetic failure"):
                    trainer.train_temporal_t2_value(dataset, failed)
            self.assertFalse(failed.exists())
            self.assertEqual(list(root.glob(f".{failed.name}.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
