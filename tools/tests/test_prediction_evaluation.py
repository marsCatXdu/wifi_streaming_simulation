from __future__ import annotations

import sys
import argparse
import csv
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from prediction.calibration import select_threshold
from prediction.features import build_feature_sets, degrade_f1
from prediction.metrics import (
    average_precision_tied,
    equal_frequency_calibration,
    topk_metrics,
)
from evaluate_prediction import evaluate, evaluate_atomic


class TieAwareMetricTests(unittest.TestCase):
    def test_topk_analytical_tie_expectation(self) -> None:
        labels = np.array([1, 0, 1, 0, 1])
        scores = np.array([2.0, 1.0, 1.0, 1.0, 0.0])
        result = topk_metrics(labels, scores, 0.4)
        self.assertEqual(result["k"], 2)
        self.assertEqual(result["topk_strict_count"], 1)
        self.assertEqual(result["topk_tie_count"], 3)
        self.assertAlmostEqual(result["expected_topk_true_positives"], 4 / 3)
        self.assertAlmostEqual(result["recall"], 4 / 9)

    def test_ties_are_invariant_to_all_row_identifying_orders(self) -> None:
        labels = np.array([1, 0, 1, 0, 1, 0, 0, 1])
        scores = np.array([0.8, 0.8, 0.8, 0.5, 0.5, 0.5, 0.1, 0.1])
        expected = topk_metrics(labels, scores, 0.5)
        expected_ap = average_precision_tied(labels, scores)
        permutations = [
            np.arange(len(labels))[::-1],
            np.array([3, 0, 7, 1, 6, 2, 5, 4]),
            np.array([1, 3, 5, 7, 0, 2, 4, 6]),
        ]
        for permutation in permutations:
            observed = topk_metrics(labels[permutation], scores[permutation], 0.5)
            self.assertEqual(observed["recall"], expected["recall"])
            self.assertEqual(observed["precision"], expected["precision"])
            self.assertEqual(
                average_precision_tied(labels[permutation], scores[permutation]),
                expected_ap,
            )

    def test_calibration_bins_never_split_probability_ties(self) -> None:
        labels = np.array([0, 1, 0, 1, 1, 0])
        probability = np.array([0.1, 0.1, 0.1, 0.8, 0.8, 0.8])
        _, bins, effective = equal_frequency_calibration(labels, probability, 10)
        self.assertEqual(effective, 2)
        self.assertEqual([item["count"] for item in bins], [3, 3])

    def test_fixed_threshold_acts_on_complete_ties(self) -> None:
        y = np.array([0, 1, 0, 1])
        probability = np.array([0.2, 0.8, 0.8, 0.8])
        selected = select_threshold(probability, y, 0.5)
        self.assertEqual(selected["calibration_score_threshold"], 0.8)
        self.assertEqual(selected["observed_calibration_action_rate"], 0.75)


class FeatureContractTests(unittest.TestCase):
    def test_explicit_manifest_tiers_and_prohibited_check(self) -> None:
        dictionary = {
            "frame_size_bytes": {"tier": "F0", "model_eligible": True},
            "mpdu_retries_5ms": {"tier": "F1-ideal", "model_eligible": True},
            "mac_queue_packets": {"tier": "F2", "model_eligible": True},
            "current_cw": {"tier": "F3", "model_eligible": True},
            "run_id": {"tier": "metadata", "model_eligible": False},
        }
        sets = build_feature_sets(dictionary, ["mac_queue_packets"])
        self.assertEqual(sets.sets["F0"], ("frame_size_bytes",))
        self.assertNotIn("run_id", set().union(*map(set, sets.sets.values())))
        dictionary["run_id"] = {"tier": "F0", "model_eligible": True}
        with self.assertRaisesRegex(ValueError, "prohibited"):
            build_feature_sets(dictionary, ["mac_queue_packets"])

    def test_f1_degradation_is_causal_and_deterministic(self) -> None:
        matrix = np.arange(10, dtype=np.float32).reshape(5, 2)
        times = np.array([0, 1000, 2000, 3000, 4000])
        runs = np.zeros(5, dtype=int)
        profile = {
            "report_interval_us": 1000,
            "observation_delay_us": 1000,
            "counter_quantization": 1,
            "signal_quantization_db": 1,
            "rate_quantization": 1,
            "disabled_feature_families": [],
        }
        first, sources, staleness = degrade_f1(
            matrix, ("mpdu_attempts_5ms", "current_mcs"), times, runs, profile
        )
        second, _, _ = degrade_f1(
            matrix, ("mpdu_attempts_5ms", "current_mcs"), times, runs, profile
        )
        np.testing.assert_equal(first, second)
        self.assertTrue(np.isnan(first[0]).all())
        np.testing.assert_equal(first[1:], matrix[:-1])
        self.assertTrue(np.all(staleness[np.isfinite(staleness)] >= 1000))
        self.assertTrue(np.all(sources[np.isfinite(sources)] <= times[1:] - 1000))


class AtomicPublicationTests(unittest.TestCase):
    def test_concurrent_evaluation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evaluation"
            lock = root / ".evaluation.lock"
            lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
            args = argparse.Namespace(output_dir=output)

            with self.assertRaisesRegex(ValueError, "already running"):
                evaluate_atomic(args)

            self.assertEqual(lock.read_text(encoding="utf-8"), f"{os.getpid()}\n")

    def test_failure_preserves_previous_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evaluation"
            output.mkdir()
            (output / "report.txt").write_text("previous", encoding="utf-8")
            args = argparse.Namespace(output_dir=output)

            def fail(staged_args: argparse.Namespace) -> dict:
                staged_args.output_dir.mkdir()
                (staged_args.output_dir / "partial.txt").write_text(
                    "partial", encoding="utf-8"
                )
                raise ValueError("expected failure")

            with mock.patch("evaluate_prediction.evaluate", side_effect=fail):
                with self.assertRaisesRegex(ValueError, "expected failure"):
                    evaluate_atomic(args)

            self.assertEqual(
                (output / "report.txt").read_text(encoding="utf-8"), "previous"
            )
            self.assertFalse(list(root.glob(".evaluation.building-*")))

    def test_success_atomically_replaces_previous_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evaluation"
            output.mkdir()
            (output / "report.txt").write_text("previous", encoding="utf-8")
            args = argparse.Namespace(output_dir=output)

            def succeed(staged_args: argparse.Namespace) -> dict:
                staged_args.output_dir.mkdir()
                (staged_args.output_dir / "report.txt").write_text(
                    "replacement", encoding="utf-8"
                )
                return {"status": "ok"}

            with mock.patch("evaluate_prediction.evaluate", side_effect=succeed):
                self.assertEqual(evaluate_atomic(args), {"status": "ok"})

            self.assertEqual(
                (output / "report.txt").read_text(encoding="utf-8"), "replacement"
            )
            self.assertFalse((root / ".evaluation.previous").exists())


class EndToEndSmokeTest(unittest.TestCase):
    def test_synthetic_csv_workflow_and_insufficient_ood(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            output = root / "output"
            config_path = root / "analysis.yaml"
            config = {
                "analysis_schema_version": 1,
                "specification_revision": 8,
                "analysis_seed": 7,
                "split_seed": 7,
                "bootstrap_seed": 8,
                "minimum_run_groups_validation_selection": 2,
                "minimum_run_groups_validation_calibration": 2,
                "minimum_run_groups_id_test": 2,
                "minimum_run_groups_per_required_ood_scenario": 2,
                "stages": ["T0"],
                "links": [0],
                "combine_links_for_formal_decision": False,
                "ranking_budgets": [0.1],
                "screening_budget": 0.1,
                "pr_auc_metric": "average_precision",
                "calibration_bin_count": 3,
                "calibration_method": "platt",
                "confidence_level": 0.95,
                "bootstrap_replicates": 20,
                "minimum_rescue_time_us": 5000,
                "model_name_order": [
                    "logistic_regression",
                    "histogram_gradient_boosting",
                ],
                "required_ood_scenarios": ["ood_a", "ood_b"],
                "ood_scenarios": [
                    {"scenario_id": "ood_a", "status": "required", "run_filter": {"scenario_id": "ood_a"}},
                    {"scenario_id": "ood_b", "status": "required", "run_filter": {"scenario_id": "ood_b"}},
                ],
                "ood_formal_aggregation": "per_scenario_worst_case",
                "pooled_ood_decision_use": False,
                "minimum_f2_recall": 0.4,
                "minimum_random_multiple": 1.0,
                "minimum_heuristic_gain": 0.0,
                "minimum_f2_incremental_gain": 0.0,
                "minimum_ood_retention": 0.7,
                "minimum_later_stage_gain": 0.1,
                "maximum_fixed_threshold_action_rate_overshoot": 0.5,
                "f2_exportable_allowlist": ["mac_queue_packets"],
                "f1_degradation_profiles": [
                    {
                        "profile_id": "polling",
                        "source": "recorded_periodic_observation",
                        "report_interval_us": 1000,
                        "observation_delay_us": 1000,
                        "counter_quantization": 1,
                        "signal_quantization_db": 1,
                        "rate_quantization": 1,
                        "disabled_feature_families": [],
                    }
                ],
            }
            config_path.write_text(yaml.safe_dump(config, sort_keys=False))
            features = {
                "deadline_slack_us": ("F0", True),
                "frame_size_bytes": ("F0", True),
                "frame_type": ("F0", True),
                "mpdu_attempts_5ms": ("F1-ideal", True),
                "mpdu_retries_5ms": ("F1-ideal", True),
                "last_positive_ack_age_us": ("F1-ideal", True),
                "acknowledged_mac_service_bytes_20ms": ("F1-ideal", True),
                "mpdu_queue_to_ack_mean_20ms_us": ("F1-ideal", True),
                "history_coverage_20ms_us": ("provenance", False),
                "mac_queue_packets": ("F2", True),
                "mac_service_bytes_ahead_of_frame": ("F2", True),
                "frame_mac_service_bytes_pending_primary": ("F2", True),
                "frame_packets_terminally_dropped": ("F2", True),
                "packets_ahead_of_frame": ("F2", True),
                "frame_packets_pending_primary": ("F2", True),
                "current_cw": ("F3", True),
            }
            features.update({
                f"polling_1ms_{name}": ("F1-polling-1ms", True)
                for name, (tier, _) in tuple(features.items())
                if tier == "F1-ideal"
            })
            metadata = [
                "dataset_schema_version", "deadline_miss", "actionable", "path_id",
                "split_role", "scenario_name", "miss_regime", "correlation_mode",
                "run_group_id", "run_id", "frame_id",
                "sample_stage", "sample_time_ns", "sample_offset_us",
                "generation_time_ns", "deadline_time_ns",
                "polling_1ms_capture_time_ns", "polling_1ms_staleness_us",
            ]
            header = metadata + list(features)
            groups = [
                *(("training", "id", f"train-{i}") for i in range(6)),
                *(("validation_selection", "id", f"select-{i}") for i in range(2)),
                *(("validation_calibration", "id", f"calibrate-{i}") for i in range(2)),
                *(("in_distribution_test", "id", f"test-{i}") for i in range(2)),
                *(("out_of_distribution_test", "ood_a", f"ooda-{i}") for i in range(2)),
                ("out_of_distribution_test", "ood_b", "oodb-0"),
            ]
            rows = []
            frame_id = 0
            split_entries = []
            for role, scenario, group in groups:
                split_entries.append({"run_group_id": group, "split_role": role})
                for local in range(6):
                    miss = int(local >= 3)
                    generation = (frame_id + 1) * 20_000_000
                    values = {
                        "dataset_schema_version": 2,
                        "deadline_miss": miss,
                        "actionable": 1,
                        "path_id": 0,
                        "split_role": role,
                        "scenario_name": scenario,
                        "miss_regime": "medium",
                        "correlation_mode": "independent",
                        "run_group_id": group,
                        "run_id": group,
                        "frame_id": frame_id,
                        "sample_stage": "T0",
                        "sample_time_ns": generation,
                        "sample_offset_us": 0,
                        "generation_time_ns": generation,
                        "deadline_time_ns": generation + 33_333_000,
                        "polling_1ms_capture_time_ns": generation - 1_000_000,
                        "polling_1ms_staleness_us": 1000,
                        "deadline_slack_us": 33333,
                        "frame_size_bytes": 40000 + miss * 10000,
                        "frame_type": "I_FRAME" if local == 0 else "P_FRAME",
                        "mpdu_attempts_5ms": 10,
                        "mpdu_retries_5ms": miss * 5,
                        "last_positive_ack_age_us": miss * 1000,
                        "acknowledged_mac_service_bytes_20ms": 1000,
                        "mpdu_queue_to_ack_mean_20ms_us": 100 + miss * 100,
                        "history_coverage_20ms_us": 20000,
                        "mac_queue_packets": miss * 20,
                        "mac_service_bytes_ahead_of_frame": miss * 10000,
                        "frame_mac_service_bytes_pending_primary": 50000,
                        "frame_packets_terminally_dropped": 0,
                        "packets_ahead_of_frame": miss * 5,
                        "frame_packets_pending_primary": 40,
                        "current_cw": 15 + miss * 16,
                    }
                    for name, (tier, _) in features.items():
                        if tier == "F1-polling-1ms":
                            values[name] = values[name.removeprefix("polling_1ms_")]
                    rows.append([values[name] for name in header])
                    frame_id += 1
            csv_path = dataset / "labelled_samples.csv"
            with csv_path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)
            digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            feature_dictionary = {
                name: {"tier": tier, "model_eligible": eligible}
                for name, (tier, eligible) in features.items()
            }
            for name in metadata:
                feature_dictionary[name] = {
                    "tier": "outcome" if name == "deadline_miss" else "metadata",
                    "model_eligible": False,
                }
            manifest = {
                "dataset_schema_version": 2,
                "analysis_schema_version": 1,
                "analysis_config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                "dataset_file": csv_path.name,
                "dataset_sha256": digest,
                "counts": {
                    "frame_count": len(rows),
                    "run_group_count": len(groups),
                },
                "feature_dictionary": feature_dictionary,
            }
            (dataset / "dataset_manifest.json").write_text(json.dumps(manifest))
            (dataset / "splits.json").write_text(json.dumps({"groups": split_entries}))
            summary = evaluate(
                argparse.Namespace(
                    dataset_dir=dataset,
                    output_dir=output,
                    analysis_config=config_path,
                    seed=7,
                    skip_dataset_checksum=False,
                )
            )
            self.assertGreater(summary["rows_scanned"], 0)
            decision = json.loads((output / "go_no_go.json").read_text())
            ood_b = [
                item
                for item in decision["criteria"]
                if item["name"] == "required_ood_scenario_evidence"
                and item["scenario_id"] == "ood_b"
            ]
            self.assertEqual(ood_b[0]["status"], "insufficient_data")
            for filename in (
                "prediction_report.md",
                "model_metrics.csv",
                "budget_metrics.csv",
                "calibration.csv",
                "feature_ablation.csv",
                "feature_importance.csv",
                "f1_degradation.csv",
                "stage_rescue_eligibility.csv",
                "predictions.csv",
                "go_no_go.json",
            ):
                self.assertTrue((output / filename).is_file(), filename)


if __name__ == "__main__":
    unittest.main()
