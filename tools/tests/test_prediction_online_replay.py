from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from prediction.online_replay import (
    TokenBucket,
    aggregate_metrics,
    load_replay_config,
    replay_scores,
)


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


if __name__ == "__main__":
    unittest.main()
