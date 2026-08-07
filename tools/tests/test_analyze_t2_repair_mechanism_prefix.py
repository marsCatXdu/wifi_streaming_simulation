#!/usr/bin/env python3
"""Focused tests for valid-prefix mechanism analysis."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_t2_repair_mechanism as mechanism  # noqa: E402
import analyze_t2_repair_mechanism_prefix as prefix  # noqa: E402
from tests.test_analyze_t2_repair_mechanism import observation  # noqa: E402


class T2RepairMechanismPrefixAnalysisTest(unittest.TestCase):
    def test_prefix_order_excludes_oracle_and_fec(self) -> None:
        self.assertEqual(prefix.ARM_ORDER, mechanism.ARM_ORDER[:4])

    def test_paired_bootstrap_uses_all_four_prefix_arms(self) -> None:
        scenarios = [f"scenario-{index}" for index in range(5)]
        observations = [
            observation(scenario, seed, arm)
            for scenario in scenarios
            for seed in range(4)
            for arm in prefix.ARM_ORDER
        ]
        grid = prefix._paired_grid(observations)
        with mock.patch.object(prefix, "BOOTSTRAP_REPLICATIONS", 100):
            result = prefix._bootstrap(grid)
        self.assertEqual(set(result["versus_str"]), set(prefix.ARM_ORDER[1:]))
        for contrast in result["versus_str"].values():
            self.assertAlmostEqual(contrast["miss_rate_delta"]["estimate"], 0.0)
            self.assertAlmostEqual(contrast["sender_airtime_ratio"]["estimate"], 1.0)


if __name__ == "__main__":
    unittest.main()
