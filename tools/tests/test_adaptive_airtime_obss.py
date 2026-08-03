#!/usr/bin/env python3
"""Unit checks for the adaptive-airtime OBSS matrix configuration."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parents[1]
import sys
from unittest import mock

sys.path.insert(0, str(TOOLS))
from run_experiments import expand_config
from run_adaptive_airtime_obss import main as run_matrix


class AdaptiveAirtimeObssConfigTest(unittest.TestCase):
    def test_matrix_has_paired_five_way_runs(self) -> None:
        path = ROOT / "experiments/configs/closed_loop_adaptive_airtime_obss.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(document["name"], "closed-loop-adaptive-airtime-obss-v2")
        self.assertEqual(document["output_root"], "results/adaptive_airtime_obss_v2/runs")
        specs = expand_config(document)
        expected = {
            ("dual_interface", "fixed_link_1"),
            ("dual_interface", "selective_duplication"),
            ("dual_interface", "adaptive_airtime_duplication"),
            ("dual_interface", "full_duplication"),
            ("mlo_str", "fixed_link_0"),
        }
        observed = {
            (item["config"]["topology"], item["config"]["policy"]) for item in specs
        }
        self.assertEqual(observed, expected)
        self.assertEqual(len(document["seeds"]), 30)
        by_seed = {}
        for item in specs:
            seed = item["seed"]
            by_seed.setdefault(seed, set()).add(
                (item["config"]["topology"], item["config"]["policy"])
            )
        for seed, approaches in by_seed.items():
            self.assertEqual(approaches, expected, seed)

        adaptive = [
            item for item in specs
            if item["config"]["policy"] == "adaptive_airtime_duplication"
        ]
        self.assertEqual(len(adaptive), 30)
        for item in adaptive:
            prediction = item["config"]["prediction"]
            self.assertTrue(prediction["prediction_telemetry_enabled"])
            self.assertTrue(prediction["secondary_airtime_meter_enabled"])
            self.assertEqual(prediction["adaptive_airtime_budget_fraction"], 0.02)
            self.assertEqual(prediction["adaptive_airtime_dual_step"], 0.01)
            self.assertEqual(prediction["adaptive_airtime_cost_safety_factor"], 1.25)

        selective = [
            item for item in specs
            if item["config"]["policy"] == "selective_duplication"
        ]
        for item in selective:
            prediction = item["config"]["prediction"]
            self.assertEqual(prediction["selective_duplication_threshold"], 0.2)
            self.assertEqual(prediction["selective_duplication_frame_budget"], 0.3)
            self.assertTrue(prediction["secondary_airtime_meter_enabled"])

        self.assertFalse(
            any("combined" in str(item["config"]).lower() for item in specs)
        )

    @mock.patch("run_adaptive_airtime_obss.subprocess.run")
    def test_runner_uses_matrix_worker_limit_and_single_analysis_pass(
        self,
        run: mock.Mock,
    ) -> None:
        self.assertEqual(run_matrix(), 0)
        self.assertEqual(run.call_count, 2)
        experiment_command = run.call_args_list[1].args[0]
        self.assertNotIn("--workers", experiment_command)
        self.assertTrue(
            any(
                str(argument).endswith("results/adaptive_airtime_obss_v2/runs")
                for argument in experiment_command
            )
        )


if __name__ == "__main__":
    unittest.main()
