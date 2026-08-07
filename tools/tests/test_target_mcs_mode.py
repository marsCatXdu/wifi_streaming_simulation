#!/usr/bin/env python3
"""Focused tests for target MCS selection and campaign sharding."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_environment_generalization_adaptive_mcs_v1 import (  # noqa: E402
    adaptive_cli_arguments,
    select_paired_unit_shard,
)
from validate_outputs import (  # noqa: E402
    ValidationError,
    _validate_target_mcs_config,
)


class TargetMcsModeTest(unittest.TestCase):
    @staticmethod
    def config() -> dict[str, object]:
        return {
            "topology": "dual_interface",
            "wifi": {
                "standard": "802.11be",
                "mcs_mode": "fixed",
                "station_manager": "ConstantRateWifiManager",
                "data_mode": "EhtMcs5",
                "control_mode": "ErpOfdmRate24Mbps,OfdmRate24Mbps",
                "data_modes_per_link": ["EhtMcs5", "EhtMcs5"],
                "adaptive_mcs_update_interval_ms": 50,
                "adaptive_mcs_use_latest_amendment_only": True,
                "adaptive_mcs_random_stream_base": 900000,
                "adaptive_mcs_random_stream_count": 0,
            },
        }

    def test_accepts_exact_fixed_and_adaptive_provenance(self) -> None:
        fixed = self.config()
        self.assertEqual(_validate_target_mcs_config(fixed, "test"), "fixed")

        adaptive = copy.deepcopy(fixed)
        adaptive["wifi"].update({
            "mcs_mode": "adaptive",
            "station_manager": "MinstrelHtWifiManager",
            "data_mode": "manager_selected",
            "control_mode": "manager_selected,manager_selected",
            "data_modes_per_link": ["manager_selected", "manager_selected"],
            "adaptive_mcs_random_stream_count": 8,
        })
        self.assertEqual(_validate_target_mcs_config(adaptive, "test"), "adaptive")

        adaptive["wifi"]["adaptive_mcs_random_stream_count"] = 7
        with self.assertRaisesRegex(ValidationError, "MCS provenance differs"):
            _validate_target_mcs_config(adaptive, "test")

    def test_legacy_fixed_output_remains_valid(self) -> None:
        legacy = self.config()
        for key in tuple(legacy["wifi"]):
            if key == "mcs_mode" or key.startswith("adaptive_mcs_"):
                legacy["wifi"].pop(key)
        self.assertEqual(_validate_target_mcs_config(legacy, "legacy"), "fixed")

    def test_yaml_leaf_maps_to_simple_cli_attribute(self) -> None:
        self.assertEqual(
            adaptive_cli_arguments({"wifi": {"mcs_mode": "adaptive"}}, ROOT),
            ["--mcsMode=adaptive"],
        )

    def test_sharding_keeps_all_arms_of_each_paired_unit_together(self) -> None:
        specs = []
        for scenario_id in ("s0", "s1"):
            for seed in (11, 12):
                for arm in ("str", "v2", "distributional"):
                    specs.append({
                        "scenario": {"scenario_id": scenario_id},
                        "seed": seed,
                        "run": 1,
                        "config": {"arm": arm},
                    })
        left = select_paired_unit_shard(specs, 0, 2)
        right = select_paired_unit_shard(specs, 1, 2)
        self.assertEqual(len(left), 6)
        self.assertEqual(len(right), 6)
        left_units = {
            (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
            for spec in left
        }
        right_units = {
            (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
            for spec in right
        }
        self.assertTrue(left_units.isdisjoint(right_units))
        for selected in (left, right):
            counts: dict[tuple[str, int, int], int] = {}
            for spec in selected:
                unit = (
                    spec["scenario"]["scenario_id"],
                    spec["seed"],
                    spec["run"],
                )
                counts[unit] = counts.get(unit, 0) + 1
            self.assertEqual(set(counts.values()), {3})


if __name__ == "__main__":
    unittest.main()
