#!/usr/bin/env python3
"""Tests for the frozen adaptive-MCS generalization campaign."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from check_environment_generalization_adaptive_mcs_v1 import (  # noqa: E402
    check,
    unique_configuration_checks,
)


class EnvironmentGeneralizationAdaptiveMcsTest(unittest.TestCase):
    def test_campaign_is_exact_one_variable_ablation(self) -> None:
        result = check()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["simulation_run_count"], 576)
        self.assertEqual(result["paired_unit_count"], 192)
        self.assertEqual(result["shard_run_counts"], [288, 288])
        self.assertEqual(
            result["only_simulation_config_difference"],
            ["wifi.mcs_mode=fixed(default)->adaptive"],
        )

    def test_all_144_unique_configurations_select_adaptive_mcs(self) -> None:
        checks = unique_configuration_checks()
        self.assertEqual(len(checks), 144)
        self.assertTrue(all(
            spec["config"]["wifi"]["mcs_mode"] == "adaptive"
            for spec in checks
        ))


if __name__ == "__main__":
    unittest.main()
