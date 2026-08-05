#!/usr/bin/env python3
"""Tests for the frozen environment-generalization scenario generator."""

from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from generate_environment_generalization_v1 import (  # noqa: E402
    CANONICAL_CONTRACT,
    GeneralizationContractError,
    build_artifacts,
    generate_phase_scenarios,
    latin_hypercube_strata,
    load_contract,
    sha256_file,
    validate_contract,
)
from run_experiments import cli_arguments, expand_config, load_yaml  # noqa: E402


class EnvironmentGeneralizationGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract()
        cls.base = load_yaml(
            ROOT / cls.contract["randomized_collection"]["base_config_path"]
        )
        cls.scenarios = {
            phase: generate_phase_scenarios(cls.contract, phase, cls.base)
            for phase in (
                "randomized_collection",
                "closed_loop_qualification",
                "preflight",
            )
        }

    def test_campaign_counts_fill_complete_64_worker_waves(self) -> None:
        phases = self.contract["sampling"]["phases"]
        self.assertEqual(len(self.scenarios["randomized_collection"]), 96)
        self.assertEqual(
            sum(len(item["seeds"]) for item in self.scenarios["randomized_collection"]),
            384,
        )
        self.assertEqual(phases["randomized_collection"]["expected_64_worker_waves"], 6)

        self.assertEqual(len(self.scenarios["closed_loop_qualification"]), 48)
        self.assertEqual(
            sum(
                len(item["seeds"])
                for item in self.scenarios["closed_loop_qualification"]
            ),
            192,
        )
        self.assertEqual(
            phases["closed_loop_qualification"]["expected_simulation_run_count"],
            576,
        )
        self.assertEqual(
            phases["closed_loop_qualification"]["expected_64_worker_waves"], 9
        )
        self.assertEqual(len(self.scenarios["preflight"]), 6)

    def test_phase_seeds_are_unique_disjoint_and_not_confirmation_seeds(self) -> None:
        by_phase: dict[str, set[int]] = {}
        for phase, scenarios in self.scenarios.items():
            seeds = [seed for item in scenarios for seed in item["seeds"]]
            self.assertEqual(len(seeds), len(set(seeds)))
            by_phase[phase] = set(seeds)
        phases = list(by_phase)
        for index, left in enumerate(phases):
            for right in phases[index + 1 :]:
                self.assertFalse(by_phase[left] & by_phase[right])
        reserved = self.contract["reserved_confirmation_seeds"]
        all_seeds = set().union(*by_phase.values())
        self.assertFalse(
            set(range(reserved["first"], reserved["last"] + 1)) & all_seeds
        )

    def test_every_sample_occupies_one_latin_hypercube_stratum(self) -> None:
        salt = self.contract["sampling"]["salt"]
        for phase_name, phase in self.contract["sampling"]["phases"].items():
            sample_count = phase["parameter_samples_per_family"]
            for family_id, family in self.contract["scenario_families"].items():
                for parameter_name, specification in family["parameters"].items():
                    if specification["kind"] == "derived_frame_period_us":
                        continue
                    strata = latin_hypercube_strata(
                        salt,
                        phase_name,
                        family_id,
                        parameter_name,
                        sample_count,
                    )
                    self.assertEqual(sorted(strata), list(range(sample_count)))

    def test_choice_sampling_is_balanced_within_each_family_phase(self) -> None:
        for phase_name, scenarios in self.scenarios.items():
            by_family: dict[str, list[dict[str, object]]] = {}
            for scenario in scenarios:
                by_family.setdefault(scenario["family_id"], []).append(scenario)
            for family_id, members in by_family.items():
                parameters = self.contract["scenario_families"][family_id]["parameters"]
                for parameter_name, specification in parameters.items():
                    if specification["kind"] != "choice":
                        continue
                    counts = Counter(
                        member["config"][parameter_name] for member in members
                    )
                    if len(members) >= len(specification["values"]):
                        self.assertEqual(set(counts), set(specification["values"]))
                    self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_derived_deadlines_and_keyframe_bounds_hold(self) -> None:
        base_stream = self.base["base"]["stream"]
        maximum_keyframe = self.contract["randomized_collection"][
            "maximum_keyframe_bytes"
        ]
        for scenarios in self.scenarios.values():
            for scenario in scenarios:
                overlay = scenario["config"]
                fps = overlay.get("stream.fps", base_stream["fps"])
                deadline = overlay.get("stream.deadline_us", base_stream["deadline_us"])
                if "stream.fps" in overlay:
                    self.assertEqual(deadline, math.floor(1000000 / fps + 0.5))
                frame_size = overlay.get("stream.frame_size", base_stream["frame_size"])
                multiplier = overlay.get(
                    "stream.keyframe_size_multiplier",
                    base_stream["keyframe_size_multiplier"],
                )
                self.assertLessEqual(frame_size * multiplier, maximum_keyframe)
                self.assertGreater(deadline, 4000)

    def test_each_family_overlay_contains_only_its_frozen_domain(self) -> None:
        for scenarios in self.scenarios.values():
            for scenario in scenarios:
                family = self.contract["scenario_families"][scenario["family_id"]]
                expected = set(family["fixed_overrides"]) | set(family["parameters"])
                self.assertEqual(set(scenario["config"]), expected)
                self.assertFalse(
                    {"topology", "policy", "scenario_id", "family_id"}
                    & set(scenario["config"])
                )
        compound_prefixes = {
            path.split(".", 1)[0]
            for path in self.scenarios["randomized_collection"][-1]["config"]
        }
        self.assertEqual(
            compound_prefixes, {"background", "obss", "propagation", "stream"}
        )

    def test_executable_matrices_expand_without_latent_identity_leakage(self) -> None:
        expected = {
            "environment_generalization_randomized_collection_v1.yaml": 384,
            "environment_generalization_randomized_preflight_v1.yaml": 6,
        }
        for name, count in expected.items():
            path = ROOT / "experiments/configs" / name
            document = load_yaml(path)
            specs = expand_config(document)
            self.assertEqual(len(specs), count)
            for spec in specs:
                self.assertIn("scenario", spec)
                self.assertNotIn("scenario_id", spec["config"])
                self.assertNotIn("family_id", spec["config"])
                arguments = cli_arguments(spec["config"], path.parent)
                self.assertFalse(
                    any(
                        argument.startswith(("--scenario", "--family", "--parameter"))
                        for argument in arguments
                    )
                )

    def test_generated_artifacts_match_exactly_and_close_provenance(self) -> None:
        artifacts = build_artifacts()
        for path, expected in artifacts.items():
            self.assertEqual(path.read_bytes(), expected)
        manifest_path = ROOT / self.contract["generated_artifacts"]["artifact_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["contract"]["sha256"], sha256_file(CANONICAL_CONTRACT))
        self.assertEqual(
            manifest["generator"]["sha256"],
            sha256_file(ROOT / "tools/generate_environment_generalization_v1.py"),
        )
        self.assertEqual(
            manifest["invariants"]["reserved_confirmation_seed_overlap_count"], 0
        )

    def test_catalog_is_the_exact_pre_result_scenario_freeze(self) -> None:
        catalog_path = ROOT / self.contract["generated_artifacts"]["scenario_catalog"]
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        for phase, scenarios in self.scenarios.items():
            self.assertEqual(catalog["phases"][phase]["scenarios"], scenarios)
        self.assertTrue(
            json.loads(
                (ROOT / self.contract["generated_artifacts"]["artifact_manifest"]).read_text(
                    encoding="utf-8"
                )
            )["invariants"]["qualification_catalog_generated_before_results"]
        )

    def test_contract_mutations_fail_closed(self) -> None:
        drifted_hash = copy.deepcopy(self.contract)
        drifted_hash["randomized_collection"]["base_config_file_sha256"] = "0" * 64
        with self.assertRaisesRegex(GeneralizationContractError, "hash drifted"):
            validate_contract(drifted_hash)

        wrong_count = copy.deepcopy(self.contract)
        wrong_count["sampling"]["phases"]["randomized_collection"][
            "expected_run_count"
        ] += 1
        with self.assertRaisesRegex(GeneralizationContractError, "count differs"):
            validate_contract(wrong_count)

        forbidden_arm = copy.deepcopy(self.contract)
        forbidden_arm["closed_loop_qualification"]["actual_simulation_arms"][0][
            "policy"
        ] = "full_duplication"
        with self.assertRaisesRegex(GeneralizationContractError, "must not add"):
            validate_contract(forbidden_arm)


if __name__ == "__main__":
    unittest.main()
