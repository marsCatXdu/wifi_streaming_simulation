#!/usr/bin/env python3
"""Focused tests for the protected factual phase-1 analysis."""

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
import analyze_t2_repair_mechanism_factual_phase1 as factual  # noqa: E402
import analyze_t2_repair_mechanism_prefix as prefix  # noqa: E402
from tests.test_analyze_t2_repair_mechanism import observation  # noqa: E402


class T2RepairMechanismFactualPhase1Test(unittest.TestCase):
    def test_factual_order_includes_fec_and_excludes_oracle(self) -> None:
        self.assertIn("ideal_systematic_fec_12p5_t2", factual.ARM_ORDER)
        self.assertNotIn("oracle_eventual_missing_repair_t2", factual.ARM_ORDER)
        self.assertEqual(len(factual.ARM_ORDER), 5)

    def test_generic_prefix_primitives_accept_five_arm_grid(self) -> None:
        observations = [
            observation(f"scenario-{scenario}", seed, arm)
            for scenario in range(5)
            for seed in range(4)
            for arm in factual.ARM_ORDER
        ]
        grid = prefix._paired_grid(observations, factual.ARM_ORDER)
        with mock.patch.object(prefix, "BOOTSTRAP_REPLICATIONS", 100):
            report = prefix._bootstrap(grid, factual.ARM_ORDER)
        self.assertEqual(
            set(report["versus_str"]), set(factual.ARM_ORDER[1:])
        )
        self.assertIn(
            "ideal_systematic_fec_12p5_t2", report["versus_str"]
        )


if __name__ == "__main__":
    unittest.main()
