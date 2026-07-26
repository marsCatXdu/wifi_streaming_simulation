from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from prediction.online_replay import (
    FrozenPredictor,
    ModelBundle,
    TokenBucket,
    aggregate_metrics,
    load_replay_config,
    replay_scores,
    score_individual_run,
)
from replay_online_prediction import _verify_run_is_5ghz


class StubPipeline:
    def decision_function(self, matrix: np.ndarray) -> np.ndarray:
        return matrix[:, 0] / 100


class StubCalibrator:
    def predict(self, score: np.ndarray) -> np.ndarray:
        return np.clip(score, 0, 1)


def replay_config() -> dict:
    return {
        "online_replay_schema_version": 1,
        "analysis_schema_version": 1,
        "primary_link": 1,
        "primary_band": "5GHz",
        "stages": ["T0", "T1", "T2", "T4"],
        "decision_policies": [
            {"policy_id": "fixed_t0", "stages": ["T0"]},
            {"policy_id": "fixed_t1", "stages": ["T1"]},
            {"policy_id": "sequential", "stages": ["T0", "T1", "T2", "T4"]},
        ],
        "pipelines": [
            {
                "pipeline_id": "test",
                "feature_set": "F0+F1-degraded",
                "degradation_profile": "polling",
                "evidence_role": "primary",
            }
        ],
        "probability_thresholds": [0.5],
        "budgets": [0.2],
        "budget_kinds": ["frames", "bytes"],
        "token_bucket": {
            "initial_fill": "full",
            "burst_horizon_frames": 5,
            "minimum_frame_capacity": 1.0,
            "byte_capacity_minimum_quantile": 0.99,
        },
        "replay_split_roles": [
            "in_distribution_test",
            "out_of_distribution_test",
        ],
        "minimum_useful_lead_time_us": 5000,
        "confidence_level": 0.95,
        "bootstrap_replicates": 20,
        "bootstrap_seed": 7,
        "audit_operating_points": [
            {
                "probability_threshold": 0.5,
                "budget_kind": "frames",
                "budget": 0.2,
            }
        ],
    }


def score_rows() -> list[dict]:
    rows = []
    stages = {"T0": 0, "T1": 1000, "T2": 2000, "T4": 4000}
    for frame_id in range(6):
        generation = frame_id * 33_333_000
        for stage, offset_us in stages.items():
            probability = 0.9
            if frame_id == 2 and stage == "T0":
                probability = 0.1
            rows.append(
                {
                    "run_id": "run",
                    "frame_id": frame_id,
                    "pipeline_id": "test",
                    "feature_set": "F0+F1-degraded",
                    "evidence_role": "primary",
                    "model": "test",
                    "stage": stage,
                    "sample_time_ns": generation + offset_us * 1000,
                    "generation_time_ns": generation,
                    "deadline_time_ns": generation + 33_333_000,
                    "frame_size_bytes": 100,
                    "actionable": True,
                    "ranking_score": probability,
                    "calibrated_probability": probability,
                }
            )
    return rows


class TokenBucketTests(unittest.TestCase):
    def test_fractional_refill_and_capacity(self) -> None:
        bucket = TokenBucket.full(1.0)
        self.assertTrue(bucket.consume(1.0))
        self.assertFalse(bucket.consume(1.0))
        for _ in range(4):
            bucket.refill(0.2)
        self.assertFalse(bucket.consume(1.0))
        bucket.refill(0.2)
        self.assertTrue(bucket.consume(1.0))
        bucket.refill(10)
        self.assertEqual(bucket.balance, 1.0)


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = {
            "run_id": "run",
            "run_group_id": "group",
            "split_role": "in_distribution_test",
            "scenario_name": "id",
            "selected_policy": "fixed_link_1",
            "primary_link": 1,
            "primary_band": "5GHz",
        }

    def test_budget_is_online_and_resets_per_operating_point(self) -> None:
        labels = {index: int(index in {0, 1, 5}) for index in range(6)}
        metrics, audits = replay_scores(
            score_rows(),
            labels,
            replay_config(),
            median_frame_size=100,
            p99_frame_size=100,
            run_metadata=self.metadata,
        )
        fixed = next(
            row
            for row in metrics
            if row["decision_policy"] == "fixed_t0"
            and row["budget_kind"] == "frames"
        )
        self.assertEqual(fixed["eligible_frames"], 6)
        self.assertEqual(fixed["actions"], 2)
        self.assertEqual(fixed["true_positive_actions"], 2)
        self.assertEqual(fixed["budget_suppressions"], 3)
        self.assertEqual(fixed["budget_suppressed_misses"], 1)
        self.assertEqual(fixed["threshold_negative_misses"], 0)
        self.assertAlmostEqual(fixed["recall"], 2 / 3)
        self.assertEqual(len(audits), 18)
        byte = next(
            row
            for row in metrics
            if row["decision_policy"] == "fixed_t0"
            and row["budget_kind"] == "bytes"
        )
        self.assertEqual(byte["actions"], 2)
        self.assertAlmostEqual(byte["realized_byte_overhead"], 1 / 3)
        self.assertEqual(labels, {index: int(index in {0, 1, 5}) for index in range(6)})

    def test_input_order_does_not_change_chronological_replay(self) -> None:
        labels = {index: int(index in {0, 1, 5}) for index in range(6)}
        forward, _ = replay_scores(
            score_rows(),
            labels,
            replay_config(),
            median_frame_size=100,
            p99_frame_size=100,
            run_metadata=self.metadata,
        )
        reverse, _ = replay_scores(
            list(reversed(score_rows())),
            labels,
            replay_config(),
            median_frame_size=100,
            p99_frame_size=100,
            run_metadata=self.metadata,
        )
        self.assertEqual(forward, reverse)

    def test_sequential_uses_first_threshold_crossing(self) -> None:
        labels = {index: 0 for index in range(6)}
        _, audits = replay_scores(
            score_rows(),
            labels,
            replay_config(),
            median_frame_size=100,
            p99_frame_size=100,
            run_metadata=self.metadata,
        )
        selected = next(
            row
            for row in audits
            if row["decision_policy"] == "sequential" and row["frame_id"] == 2
        )
        self.assertEqual(selected["crossing_stage"], "T1")

    def test_outcome_identity_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "identities"):
            replay_scores(
                score_rows(),
                {0: 0},
                replay_config(),
                median_frame_size=100,
                p99_frame_size=100,
                run_metadata=self.metadata,
            )

    def test_aggregation_can_use_any_run_subset(self) -> None:
        labels = {index: int(index == 0) for index in range(6)}
        first, _ = replay_scores(
            score_rows(),
            labels,
            replay_config(),
            median_frame_size=100,
            p99_frame_size=100,
            run_metadata=self.metadata,
        )
        second = [dict(row, run_id="run-2", run_group_id="group-2") for row in first]
        aggregate = aggregate_metrics(
            first + second,
            confidence=0.95,
            bootstrap_replicates=20,
            bootstrap_seed=7,
        )
        combined = next(
            row
            for row in aggregate
            if row["scenario_name"] == "__all_selected__"
            and row["decision_policy"] == "fixed_t0"
            and row["budget_kind"] == "frames"
        )
        self.assertEqual(combined["run_count"], 2)
        self.assertEqual(combined["run_group_count"], 2)
        self.assertEqual(combined["eligible_frames"], 12)
        self.assertIsNotNone(combined["recall_ci_lower"])
        self.assertIsNotNone(combined["recall_ci_upper"])


class ConfigurationTests(unittest.TestCase):
    def test_repository_configuration_is_valid(self) -> None:
        path = TOOLS.parent / "experiments" / "configs" / "prediction_online_replay.yaml"
        value = load_replay_config(path)
        self.assertEqual(value["primary_link"], 1)
        self.assertEqual(value["primary_band"], "5GHz")

    def test_wrong_primary_link_is_rejected(self) -> None:
        text = (
            TOOLS.parent
            / "experiments"
            / "configs"
            / "prediction_online_replay.yaml"
        ).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.yaml"
            path.write_text(text.replace("primary_link: 1", "primary_link: 0"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "path 1"):
                load_replay_config(path)

    def test_resolved_run_must_map_path_1_to_5ghz(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = {
                "run_id": "run",
                "source_directory": str(root),
            }
            config = {
                "policy": "fixed_link_1",
                "wifi": {
                    "frequency_ranges": [
                        "WIFI_SPECTRUM_2_4_GHZ",
                        "WIFI_SPECTRUM_5_GHZ",
                    ]
                },
            }
            (root / "resolved_config.json").write_text(json.dumps(config), encoding="utf-8")
            _verify_run_is_5ghz(run)
            config["wifi"]["frequency_ranges"][1] = "WIFI_SPECTRUM_6_GHZ"
            (root / "resolved_config.json").write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not 5 GHz"):
                _verify_run_is_5ghz(run)


class RawRunScoringTests(unittest.TestCase):
    def test_scores_are_generated_without_reading_frame_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fields = [
                "run_id",
                "frame_id",
                "path_id",
                "sample_stage",
                "sample_time_ns",
                "generation_time_ns",
                "deadline_time_ns",
                "frame_size_bytes",
                "actionable",
                "frame_packet_count",
                "frame_packets_tx_succeeded",
                "last_positive_ack_time_ns",
                "last_tx_attempt_time_ns",
                "mac_queue_oldest_enqueue_time_ns",
            ]
            with (root / "prediction_samples.csv").open(
                "w", newline="", encoding="utf-8"
            ) as output:
                writer = csv.DictWriter(output, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "run_id": "run",
                        "frame_id": 7,
                        "path_id": 1,
                        "sample_stage": "T0",
                        "sample_time_ns": 1_000_000,
                        "generation_time_ns": 1_000_000,
                        "deadline_time_ns": 34_333_000,
                        "frame_size_bytes": 80,
                        "actionable": 1,
                        "frame_packet_count": 1,
                        "frame_packets_tx_succeeded": 0,
                        "last_positive_ack_time_ns": "",
                        "last_tx_attempt_time_ns": "",
                        "mac_queue_oldest_enqueue_time_ns": "",
                    }
                )
            predictor = FrozenPredictor(
                pipeline_id="stub",
                feature_set="F0",
                evidence_role="primary",
                stage="T0",
                feature_names=("frame_size_bytes",),
                f1_feature_names=(),
                degradation_profile=None,
                model_name="stub",
                selection_recall=0,
                pipeline=StubPipeline(),
                calibrator=StubCalibrator(),
            )
            bundle = ModelBundle(
                schema_version=1,
                replay_config_sha256="",
                analysis_config_sha256="",
                dataset_sha256="",
                primary_link=1,
                feature_dictionary={},
                predictors={("stub", "T0"): predictor},
                median_frame_size_bytes=80,
                p99_frame_size_bytes=80,
            )
            scores = score_individual_run(root, bundle, 1)
            self.assertEqual(len(scores), 1)
            self.assertEqual(scores[0]["frame_id"], 7)
            self.assertAlmostEqual(scores[0]["calibrated_probability"], 0.8)
            self.assertFalse((root / "frames.csv").exists())


if __name__ == "__main__":
    unittest.main()
