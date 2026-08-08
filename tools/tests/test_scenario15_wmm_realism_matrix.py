#!/usr/bin/env python3
"""Focused closure tests for the scenario-15 WMM realism screen."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_experiments import (  # noqa: E402
    cli_arguments,
    expand_config,
    load_yaml,
    validate_runtime_contract,
)
import analyze_scenario15_wmm_realism_matrix as analysis  # noqa: E402


CONFIG = ROOT / "experiments/configs/scenario15_wmm_realism_matrix_v1.yaml"
PREFLIGHT = ROOT / "experiments/configs/scenario15_wmm_realism_matrix_preflight_v1.yaml"
SHARDS = [
    ROOT / f"experiments/configs/scenario15_wmm_realism_matrix_v1_shard{index}.yaml"
    for index in range(2)
]
PROFILES = {
    "be_be": ("off", "be"),
    "af41_vi_be": ("af41", "be"),
    "af41_vi_one_vi_per_channel": ("af41", "one_vi_per_channel"),
    "af41_vi_all_vi": ("af41", "all_vi"),
}
ARMS = {
    ("dual_interface", "paired_value_duplication_t2"),
    ("dual_interface", "distributional_shadow_duplication_t2"),
    ("mlo_str", "fixed_link_0"),
}


class Scenario15WmmRealismMatrixTest(unittest.TestCase):
    def test_analyzer_is_bound_to_the_frozen_contract(self) -> None:
        contract = analysis._verify_contract()
        self.assertEqual(contract["campaign"]["simulation_run_count"], 120)
        self.assertEqual(
            analysis.EXPECTED_PROJECT_COMMIT,
            "d9867b13b7fac8df9b936e717855017a22e0b5fa",
        )
        indexes = analysis._bootstrap_indexes()
        self.assertEqual(len(indexes), 10_000)
        self.assertTrue(all(len(row) == 10 for row in indexes))

    def test_contract_and_full_matrix_are_closed(self) -> None:
        document = load_yaml(CONFIG)
        runtime_contract = validate_runtime_contract(document)
        self.assertEqual(
            runtime_contract,
            {
                "runtime_contract_id": "scenario15-wmm-realism-matrix-v1",
                "runtime_contract_sha256": (
                    "252fae821e78892f46addddf29f6ab919afce880a48b4f6d9815bdf97fa51d6e"
                ),
                "source_artifacts": runtime_contract["source_artifacts"],
            },
        )
        specs = expand_config(document)
        self.assertEqual(len(specs), 120)
        self._assert_matrix(specs, set(range(1251, 1261)))

    def test_preflight_and_shards_preserve_complete_paired_units(self) -> None:
        preflight = expand_config(load_yaml(PREFLIGHT))
        self.assertEqual(len(preflight), 12)
        self._assert_matrix(preflight, {1251})

        shard_specs = [expand_config(load_yaml(path)) for path in SHARDS]
        self.assertEqual([len(specs) for specs in shard_specs], [60, 60])
        self._assert_matrix(shard_specs[0], set(range(1251, 1256)))
        self._assert_matrix(shard_specs[1], set(range(1256, 1261)))
        first_units = {
            (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
            for spec in shard_specs[0]
        }
        second_units = {
            (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
            for spec in shard_specs[1]
        }
        self.assertTrue(first_units.isdisjoint(second_units))

    def _assert_matrix(self, specs: list[dict], expected_seeds: set[int]) -> None:
        by_profile: dict[str, list[dict]] = {}
        for spec in specs:
            profile = spec["scenario"]["scenario_id"]
            by_profile.setdefault(profile, []).append(spec)
            target_mode, competitor_profile = PROFILES[profile]
            self.assertEqual(spec["config"]["wifi"]["wmm_mode"], target_mode)
            self.assertEqual(
                spec["config"]["obss"]["obss_wmm_profile"],
                competitor_profile,
            )
            arguments = cli_arguments(spec["config"], CONFIG.parent)
            self.assertIn(f"--wmmMode={target_mode}", arguments)
            self.assertIn(f"--obssWmmProfile={competitor_profile}", arguments)
            self.assertNotIn(spec["seed"], range(1301, 1349))
        self.assertEqual(set(by_profile), set(PROFILES))
        for members in by_profile.values():
            self.assertEqual({member["seed"] for member in members}, expected_seeds)
            self.assertEqual(
                {
                    (member["config"]["topology"], member["config"]["policy"])
                    for member in members
                },
                ARMS,
            )
            self.assertEqual(len(members), len(expected_seeds) * len(ARMS))


if __name__ == "__main__":
    unittest.main()
