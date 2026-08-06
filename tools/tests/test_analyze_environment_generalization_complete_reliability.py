#!/usr/bin/env python3
"""Tests for complete environment-generalization reliability analysis."""

from __future__ import annotations

import copy
import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from analyze_environment_generalization_complete_reliability import (  # noqa: E402
    _frame_metrics_allow_unsupported_p99,
    hierarchical_bootstrap,
)
from analyze_environment_generalization_qualification import (  # noqa: E402
    ARM_IDS,
    load_analysis_contract,
)


class CompleteEnvironmentReliabilityTest(unittest.TestCase):
    def test_frame_metrics_keep_reliability_when_p99_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fields = (
                "run_id",
                "frame_id",
                "generation_time_us",
                "deadline_us",
                "union_latency_us",
                "deadline_miss",
                "incomplete",
            )
            with (run_dir / "frames.csv").open("w", newline="", encoding="ascii") as target:
                writer = csv.DictWriter(target, fieldnames=fields)
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "run_id": "run-a",
                            "frame_id": 0,
                            "generation_time_us": 1,
                            "deadline_us": 100,
                            "union_latency_us": 50,
                            "deadline_miss": 0,
                            "incomplete": 0,
                        },
                        {
                            "run_id": "run-a",
                            "frame_id": 1,
                            "generation_time_us": 2,
                            "deadline_us": 100,
                            "union_latency_us": "",
                            "deadline_miss": 1,
                            "incomplete": 1,
                        },
                    ]
                )
            metrics = _frame_metrics_allow_unsupported_p99(
                run_dir,
                {
                    "run_id": "run-a",
                    "measurement_start_s": 0,
                    "measurement_stop_s": 1,
                },
            )
            self.assertEqual(metrics["generated_frame_count"], 2)
            self.assertEqual(metrics["completed_frame_count"], 1)
            self.assertEqual(metrics["deadline_miss_count"], 1)
            self.assertEqual(metrics["all_generated_deadline_miss_rate"], 0.5)
            self.assertFalse(metrics["completed_frame_hf7_p99_supported"])
            self.assertIsNone(metrics["completed_frame_hf7_p99_us"])

    def test_reliability_bootstrap_is_paired_and_deterministic(self) -> None:
        families = ("family-a", "family-b")
        scenarios = {
            family: (f"{family}-0", f"{family}-1") for family in families
        }
        grid = {}
        values = {
            "str_mlo_nmaxinflights_1": (0.20, 100.0, 50.0),
            "score_aware_t2_v2": (0.10, 115.0, 49.5),
            "distributional_shadow_t2": (0.15, 110.0, 49.8),
        }
        for family in families:
            grid[family] = {}
            for scenario in scenarios[family]:
                grid[family][scenario] = [
                    {
                        arm: {
                            "all_generated_deadline_miss_rate": metrics[0],
                            "sender_airtime_us": metrics[1],
                            "background_throughput_mbps": metrics[2],
                        }
                        for arm, metrics in values.items()
                    }
                    for _ in range(2)
                ]
        contract = copy.deepcopy(load_analysis_contract())
        contract["bootstrap"]["replications"] = 30
        first = hierarchical_bootstrap(grid, families, scenarios, contract)
        second = hierarchical_bootstrap(grid, families, scenarios, contract)
        self.assertEqual(first, second)
        comparison = first["comparisons"][
            "score_aware_t2_v2_minus_str_mlo_nmaxinflights_1"
        ]
        self.assertAlmostEqual(comparison["deadline_miss_delta"]["estimate"], -0.1)
        self.assertAlmostEqual(
            comparison["relative_deadline_miss_reduction"]["estimate"], 0.5
        )
        self.assertAlmostEqual(comparison["sender_airtime_ratio"]["estimate"], 1.15)
        self.assertEqual(set(first["treatments"]), set(ARM_IDS))


if __name__ == "__main__":
    unittest.main()
