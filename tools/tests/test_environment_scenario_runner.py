#!/usr/bin/env python3
"""Tests for explicit environment-scenario expansion and identity."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_experiments import (  # noqa: E402
    build_experiment_manifest,
    derive_run_id,
    expand_config,
    run_identity_document,
)


def document() -> dict[str, object]:
    return {
        "name": "scenario-fixture",
        "base": {"stream": {"duration": 1}},
        "seeds": [99],
        "runs": [1],
        "scenario_instances": [
            {
                "scenario_id": "load-p00",
                "family_id": "load",
                "parameter_sample": 0,
                "seeds": [20001, 20002],
                "config": {
                    "obss.obss_ul_min_rate_mbps": 1.25,
                    "obss.obss_off_mean_ms": 100,
                },
            },
            {
                "scenario_id": "geometry-p00",
                "family_id": "geometry",
                "parameter_sample": 0,
                "seeds": [20003, 20004],
                "config": {"propagation.station_distance_m": 14.5},
            },
        ],
        "topologies": [
            {"name": "dual_interface", "policies": ["fixed_link_1"]},
            {"name": "mlo_str", "policies": ["fixed_link_0"]},
        ],
        "policies": ["fixed_link_0", "fixed_link_1"],
    }


class EnvironmentScenarioRunnerTest(unittest.TestCase):
    def test_expansion_replicates_named_scenarios_across_arms_and_seeds(self) -> None:
        specs = expand_config(document())
        self.assertEqual(len(specs), 8)
        by_scenario: dict[str, list[dict[str, object]]] = {}
        for spec in specs:
            scenario = spec["scenario"]
            by_scenario.setdefault(scenario["scenario_id"], []).append(spec)
            self.assertNotIn("scenario_id", spec["config"])
        self.assertEqual(set(by_scenario), {"load-p00", "geometry-p00"})
        for members in by_scenario.values():
            self.assertEqual(len(members), 4)
            self.assertEqual(
                {
                    (member["config"]["topology"], member["config"]["policy"])
                    for member in members
                },
                {("dual_interface", "fixed_link_1"), ("mlo_str", "fixed_link_0")},
            )
        load = by_scenario["load-p00"][0]
        self.assertEqual(load["config"]["obss"]["obss_ul_min_rate_mbps"], 1.25)

    def test_scenario_identity_is_bound_to_run_id_and_manifest(self) -> None:
        specs = expand_config(document())
        first = specs[0]
        identity = run_identity_document(
            first["config"], 20001, 1, "ns3", "project", scenario=first["scenario"]
        )
        self.assertEqual(identity["scenario"], first["scenario"])
        run_id = derive_run_id(
            first["config"], 20001, 1, "ns3", "project", scenario=first["scenario"]
        )
        changed = copy.deepcopy(first["scenario"])
        changed["family_id"] = "different-family"
        self.assertNotEqual(
            run_id,
            derive_run_id(
                first["config"], 20001, 1, "ns3", "project", scenario=changed
            ),
        )
        completed = copy.deepcopy(specs[:2])
        for index, spec in enumerate(completed):
            spec["run_id"] = f"run-{index}"
            spec["completed"] = True
        manifest = build_experiment_manifest(
            "fixture", "matrix", Path("fixture.yaml"), "project", completed
        )
        self.assertEqual(manifest["scenario_schema_version"], 1)
        self.assertEqual(len(manifest["scenario_instances"]), 1)
        self.assertTrue(all("scenario" in item for item in manifest["runs"]))

    def test_ambiguous_or_malformed_scenario_declarations_fail(self) -> None:
        with_sweep = document()
        with_sweep["sweep"] = {"stream.fps": [30, 60]}
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            expand_config(with_sweep)

        policy_seeds = document()
        policy_seeds["policies"] = [
            {"name": "fixed_link_1", "topologies": ["dual_interface"], "seeds": [1]}
        ]
        with self.assertRaisesRegex(ValueError, "own replication"):
            expand_config(policy_seeds)

        duplicate = document()
        duplicate["scenario_instances"].append(
            copy.deepcopy(duplicate["scenario_instances"][0])
        )
        with self.assertRaisesRegex(ValueError, "duplicate scenario_id"):
            expand_config(duplicate)

        unknown = document()
        unknown["scenario_instances"][0]["ground_truth_label"] = "leak"
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            expand_config(unknown)


if __name__ == "__main__":
    unittest.main()
