#!/usr/bin/env python3
"""Focused tests for the repair-subset resource sensitivity."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_t2_repair_resource_sensitivity as sensitivity  # noqa: E402


class T2RepairResourceSensitivityTest(unittest.TestCase):
    def test_cheapest_unit_benefits_fit_exact_budget(self) -> None:
        result = sensitivity._select_cheapest([3.0, 1.0, 2.0], 3.0)
        self.assertEqual(result["candidate_rescues"], 3)
        self.assertEqual(result["selected_rescues"], 2)
        self.assertAlmostEqual(result["spent_measured_tagged_airtime_us"], 3.0)

    def test_negative_headroom_admits_only_zero_cost_upper_bound(self) -> None:
        result = sensitivity._select_cheapest([0.0, 1.0], -4.0)
        self.assertEqual(result["selected_rescues"], 1)
        self.assertEqual(result["budget_us"], 0.0)
        self.assertEqual(result["raw_headroom_us"], -4.0)

    def test_per_run_projection_does_not_transfer_budget(self) -> None:
        units = [
            {
                "primary_deadline_misses": 5,
                "str_deadline_misses": 3,
                "generated_frames": 10,
                "engineering_1p20": {
                    "selected_rescues": 1,
                    "spent_measured_tagged_airtime_us": 2.0,
                    "budget_us": 2.0,
                    "raw_headroom_us": 2.0,
                },
            },
            {
                "primary_deadline_misses": 4,
                "str_deadline_misses": 3,
                "generated_frames": 10,
                "engineering_1p20": {
                    "selected_rescues": 2,
                    "spent_measured_tagged_airtime_us": 3.0,
                    "budget_us": 4.0,
                    "raw_headroom_us": 4.0,
                },
            },
        ]
        result = sensitivity._projection(units, "engineering_1p20")
        self.assertEqual(result["selected_rescues"], 3)
        self.assertEqual(result["projected_deadline_misses"], 6)
        self.assertFalse(result["beats_str_on_misses"])
        self.assertTrue(result["all_runs_resource_feasible"])
        self.assertAlmostEqual(result["projected_deadline_miss_rate"], 0.3)

    def test_negative_headroom_marks_run_projection_infeasible(self) -> None:
        units = [
            {
                "primary_deadline_misses": 3,
                "str_deadline_misses": 3,
                "generated_frames": 10,
                "equal_airtime": {
                    "selected_rescues": 1,
                    "spent_measured_tagged_airtime_us": 0.0,
                    "budget_us": 0.0,
                    "raw_headroom_us": -1.0,
                },
            }
        ]
        result = sensitivity._projection(units, "equal_airtime")
        self.assertEqual(result["projected_deadline_misses"], 2)
        self.assertEqual(result["resource_infeasible_run_count"], 1)
        self.assertFalse(result["all_runs_resource_feasible"])
        self.assertFalse(result["beats_str_on_misses"])

    def test_minimum_pooled_ratio_uses_cheapest_required_rescues(self) -> None:
        units = [
            {
                "primary_deadline_misses": 5,
                "str_deadline_misses": 3,
                "rescue_costs_us": [5.0, 1.0, 2.0],
                "primary_sender_airtime_us": 100.0,
                "str_sender_airtime_us": 100.0,
            }
        ]
        result = sensitivity._minimum_pooled_ratio_to_beat_str(units)
        self.assertEqual(result["required_rescues_for_one_fewer_miss_than_str"], 3)
        self.assertAlmostEqual(result["selected_measured_tagged_airtime_us"], 8.0)
        self.assertAlmostEqual(result["optimistic_minimum_sender_airtime_ratio"], 1.08)


if __name__ == "__main__":
    unittest.main()
