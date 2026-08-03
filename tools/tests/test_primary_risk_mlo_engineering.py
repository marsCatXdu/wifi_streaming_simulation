#!/usr/bin/env python3
"""Checks for the fresh primary-risk MLO engineering matrices."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from run_experiments import cli_arguments, expand_config, load_yaml


class PrimaryRiskMloEngineeringConfigTest(unittest.TestCase):
    def test_005_matrix_has_fresh_paired_three_way_runs(self) -> None:
        document = load_yaml(
            ROOT / "experiments/configs/closed_loop_primary_risk_mlo_005.yaml"
        )
        specs = expand_config(document)

        self.assertEqual(document["name"], "closed-loop-primary-risk-mlo-005-v1")
        self.assertEqual(document["workers"], 36)
        self.assertEqual(len(specs), 36)
        self.assertEqual({spec["seed"] for spec in specs}, set(range(43, 55)))
        self.assertEqual(
            {
                (spec["config"]["topology"], spec["config"]["policy"])
                for spec in specs
            },
            {
                ("dual_interface", "adaptive_airtime_duplication"),
                ("mlo_str", "fixed_link_0"),
                ("mlo_emlsr", "fixed_link_0"),
            },
        )

        adaptive = [
            spec
            for spec in specs
            if spec["config"]["policy"] == "adaptive_airtime_duplication"
        ]
        self.assertEqual(len(adaptive), 12)
        for spec in adaptive:
            prediction = spec["config"]["prediction"]
            self.assertEqual(prediction["prediction_sample_offsets_us"], [0])
            self.assertEqual(prediction["adaptive_airtime_decision_offsets_us"], [0])
            self.assertFalse(
                prediction["adaptive_airtime_admission_uses_retry_inflation"]
            )
            self.assertEqual(prediction["adaptive_airtime_budget_fraction"], 0.02)
            self.assertEqual(
                prediction["adaptive_airtime_bucket_horizon_us"], 10_000_000
            )
            self.assertEqual(
                prediction["adaptive_airtime_initial_bucket_horizon_us"], 2_000_000
            )
            self.assertEqual(
                prediction["adaptive_airtime_initial_shadow_price"],
                0.055527989892436465,
            )
            self.assertEqual(prediction["adaptive_airtime_dual_step"], 0.0)
            arguments = cli_arguments(
                spec["config"], ROOT / "experiments/configs"
            )
            self.assertIn(
                "--adaptiveAirtimeAdmissionUsesRetryInflation=0", arguments
            )
            self.assertIn(
                "--adaptiveAirtimeInitialBucketHorizonUs=2000000", arguments
            )
            self.assertIn("--adaptiveAirtimeDualStep=0.0", arguments)

    def test_007_matrix_adds_only_the_second_adaptive_point(self) -> None:
        document = load_yaml(
            ROOT / "experiments/configs/closed_loop_primary_risk_mlo_007.yaml"
        )
        specs = expand_config(document)

        self.assertEqual(document["name"], "closed-loop-primary-risk-mlo-007-v1")
        self.assertEqual(document["workers"], 12)
        self.assertEqual(len(specs), 12)
        self.assertEqual({spec["seed"] for spec in specs}, set(range(43, 55)))
        self.assertEqual(
            {
                (spec["config"]["topology"], spec["config"]["policy"])
                for spec in specs
            },
            {("dual_interface", "adaptive_airtime_duplication")},
        )
        for spec in specs:
            prediction = spec["config"]["prediction"]
            self.assertFalse(
                prediction["adaptive_airtime_admission_uses_retry_inflation"]
            )
            self.assertEqual(prediction["adaptive_airtime_budget_fraction"], 0.02)
            self.assertEqual(
                prediction["adaptive_airtime_bucket_horizon_us"], 10_000_000
            )
            self.assertEqual(
                prediction["adaptive_airtime_initial_bucket_horizon_us"], 2_000_000
            )
            self.assertEqual(
                prediction["adaptive_airtime_initial_shadow_price"],
                0.028614642886399477,
            )
            self.assertEqual(prediction["adaptive_airtime_dual_step"], 0.0)


if __name__ == "__main__":
    unittest.main()
