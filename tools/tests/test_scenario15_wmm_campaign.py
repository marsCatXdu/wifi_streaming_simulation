#!/usr/bin/env python3
"""Contract tests for the opened-seed scenario-15 WMM comparison."""

from __future__ import annotations

import copy
import pathlib
import sys
import unittest
from collections import Counter


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from run_experiments import (  # noqa: E402
    expand_config,
    load_yaml,
    validate_runtime_contract,
)


CONFIG = ROOT / "experiments/configs/scenario15_wmm_comparison_v1.yaml"
SHARDS = (
    ROOT / "experiments/configs/scenario15_wmm_comparison_v1_shard0.yaml",
    ROOT / "experiments/configs/scenario15_wmm_comparison_v1_shard1.yaml",
)
PREFLIGHT = (
    ROOT / "experiments/configs/scenario15_wmm_comparison_preflight_v1.yaml"
)


class Scenario15WmmCampaignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_yaml(CONFIG)
        cls.specs = expand_config(cls.document)

    def test_matrix_has_exact_paired_shape(self) -> None:
        self.assertEqual(len(self.specs), 288)
        counts = Counter(
            (
                spec["config"]["wifi"]["wmm_mode"],
                spec["config"]["topology"],
                spec["config"]["policy"],
            )
            for spec in self.specs
        )
        self.assertEqual(
            counts,
            {
                ("off", "mlo_str", "fixed_link_0"): 48,
                ("on", "mlo_str", "fixed_link_0"): 48,
                ("off", "dual_interface", "paired_value_duplication_t2"): 48,
                ("on", "dual_interface", "paired_value_duplication_t2"): 48,
                ("off", "dual_interface", "distributional_shadow_duplication_t2"): 48,
                ("on", "dual_interface", "distributional_shadow_duplication_t2"): 48,
            },
        )
        self.assertEqual({spec["seed"] for spec in self.specs}, set(range(1251, 1299)))
        self.assertEqual({spec["run"] for spec in self.specs}, {1})
        self.assertTrue(all(not 1301 <= spec["seed"] <= 1348 for spec in self.specs))

    def test_wmm_is_the_only_within_arm_treatment_difference(self) -> None:
        indexed = {}
        for spec in self.specs:
            config = copy.deepcopy(spec["config"])
            mode = config["wifi"].pop("wmm_mode")
            key = (spec["seed"], spec["run"], config["topology"], config["policy"])
            indexed.setdefault(key, {})[mode] = config
        self.assertEqual(len(indexed), 144)
        for treatments in indexed.values():
            self.assertEqual(set(treatments), {"off", "on"})
            self.assertEqual(treatments["off"], treatments["on"])

    def test_preserves_scenario15_environment_and_frozen_policies(self) -> None:
        for spec in self.specs:
            config = spec["config"]
            stream = config["stream"]
            wifi = config["wifi"]
            self.assertEqual(
                (stream["duration"], stream["fps"], stream["frame_size"]),
                (60, 30, 12000),
            )
            self.assertEqual(
                (stream["gop_length"], stream["keyframe_size_multiplier"]),
                (60, 4),
            )
            self.assertEqual(wifi["wifi_standard"], "eht")
            self.assertEqual(wifi["mlo_sta_max_inflights"], 1)
            self.assertFalse(wifi["ul_ofdma_enabled"])
            policy = config["policy"]
            prediction = config.get("prediction", {})
            if policy == "paired_value_duplication_t2":
                self.assertEqual(
                    prediction["paired_value_t2_admission_profile"],
                    "score_aware_emergency_v2",
                )
            if policy in {
                "paired_value_duplication_t2",
                "distributional_shadow_duplication_t2",
            }:
                self.assertTrue(prediction["prediction_telemetry_enabled"])
                self.assertTrue(prediction["secondary_airtime_meter_enabled"])

    def test_runtime_contract_and_sources_are_hash_closed(self) -> None:
        validated = validate_runtime_contract(self.document)
        self.assertIsNotNone(validated)
        self.assertEqual(
            validated["runtime_contract_id"],
            "scenario15-wmm-comparison-v1",
        )
        self.assertEqual(len(validated["source_artifacts"]), 10)

    def test_two_shards_are_disjoint_and_cover_the_matrix(self) -> None:
        shard_specs = [expand_config(load_yaml(path)) for path in SHARDS]
        self.assertEqual([len(specs) for specs in shard_specs], [144, 144])

        def identity(spec: dict) -> tuple:
            return (
                spec["seed"],
                spec["run"],
                spec["config"]["topology"],
                spec["config"]["policy"],
                spec["config"]["wifi"]["wmm_mode"],
            )

        full = {identity(spec) for spec in self.specs}
        left = {identity(spec) for spec in shard_specs[0]}
        right = {identity(spec) for spec in shard_specs[1]}
        self.assertFalse(left & right)
        self.assertEqual(left | right, full)

    def test_preflight_contains_one_complete_six_arm_unit(self) -> None:
        specs = expand_config(load_yaml(PREFLIGHT))
        self.assertEqual(len(specs), 6)
        self.assertEqual({spec["seed"] for spec in specs}, {1251})
        self.assertEqual(
            Counter(spec["config"]["wifi"]["wmm_mode"] for spec in specs),
            {"off": 3, "on": 3},
        )


if __name__ == "__main__":
    unittest.main()
