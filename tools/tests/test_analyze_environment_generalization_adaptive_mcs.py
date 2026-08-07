#!/usr/bin/env python3
"""Tests for fixed/adaptive MCS qualification analysis."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from analyze_environment_generalization_adaptive_mcs_v1 import (  # noqa: E402
    ARMS,
    METRICS,
    MODES,
    _history_job,
    bootstrap,
    build_grid,
)


class AdaptiveMcsAnalysisTest(unittest.TestCase):
    def test_history_reduction_includes_incomplete_and_late_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fields = (
                "run_id",
                "deadline_us",
                "union_latency_us",
                "deadline_miss",
                "incomplete",
            )
            with (run_dir / "frames.csv").open(
                "w", newline="", encoding="ascii"
            ) as target:
                writer = csv.DictWriter(target, fieldnames=fields)
                writer.writeheader()
                writer.writerows([
                    {
                        "run_id": "run-a",
                        "deadline_us": 100,
                        "union_latency_us": 50,
                        "deadline_miss": 0,
                        "incomplete": 0,
                    },
                    {
                        "run_id": "run-a",
                        "deadline_us": 100,
                        "union_latency_us": 125,
                        "deadline_miss": 1,
                        "incomplete": 0,
                    },
                    {
                        "run_id": "run-a",
                        "deadline_us": 100,
                        "union_latency_us": "",
                        "deadline_miss": 1,
                        "incomplete": 1,
                    },
                ])
            reduced = _history_job({
                "mode": "adaptive",
                "arm_id": ARMS[0],
                "run_id": "run-a",
                "run_dir": str(run_dir),
                "generated_frame_count": 3,
                "completed_frame_count": 2,
                "incomplete_frame_count": 1,
                "deadline_miss_count": 2,
            })
            self.assertEqual(reduced["completed"], [50.0, 125.0])
            self.assertEqual(reduced["censored"], [50.0, 100.0, 100.0])
            self.assertEqual(reduced["bursts"], [2])
            self.assertAlmostEqual(reduced["censored_mean_us"], 250 / 3)

    def test_hierarchical_bootstrap_keeps_mode_and_arm_pairing(self) -> None:
        families = tuple(f"family-{index}" for index in range(6))
        scenarios = {
            family: tuple(f"{family}-scenario-{index}" for index in range(8))
            for family in families
        }
        rows = []
        for family_index, family in enumerate(families):
            for scenario_index, scenario in enumerate(scenarios[family]):
                for replicate in range(4):
                    for mode_index, mode in enumerate(MODES):
                        for arm_index, arm in enumerate(ARMS):
                            base = 1 + family_index + scenario_index + replicate
                            values = {
                                "all_generated_deadline_miss_rate": (
                                    0.20 + base / 10000 - 0.01 * mode_index
                                    - 0.02 * arm_index
                                ),
                                "sender_airtime_us": (
                                    100 + base + 5 * mode_index + 10 * arm_index
                                ),
                                "background_throughput_mbps": (
                                    50 - base / 100 - mode_index - arm_index
                                ),
                                "all_generated_censored_mean_us": (
                                    10000 + base - 100 * mode_index
                                    - 200 * arm_index
                                ),
                            }
                            self.assertEqual(set(values), set(METRICS))
                            rows.append({
                                "mode": mode,
                                "family_id": family,
                                "scenario_id": scenario,
                                "seed": 1000 + scenario_index * 10 + replicate,
                                "run": 1,
                                "arm_id": arm,
                                **values,
                            })
        grid = build_grid(rows, families, scenarios)
        first = bootstrap(grid, families, scenarios, replications=30)
        second = bootstrap(grid, families, scenarios, replications=30)
        self.assertEqual(first, second)
        delta = first["comparisons"][
            "adaptive_minus_fixed__str_mlo_nmaxinflights_1"
        ]["deadline_miss_delta"]
        self.assertAlmostEqual(delta["estimate"], -0.01)
        self.assertLess(delta["ci95_high"], 0)


if __name__ == "__main__":
    unittest.main()
