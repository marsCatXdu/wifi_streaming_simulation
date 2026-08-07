#!/usr/bin/env python3
"""Focused tests for the frozen two-stage T2 mechanism campaign."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_experiments import cli_arguments, load_yaml  # noqa: E402
from run_t2_repair_mechanism import (  # noqa: E402
    ARM_IDS,
    PHASE1_ARMS,
    build_campaign_specs,
    validate_mechanism_contract,
)


CONFIG = ROOT / "experiments/configs/t2_repair_mechanism_v1.yaml"


class T2RepairMechanismRunnerTest(unittest.TestCase):
    def test_contract_closes_exact_representative_scenarios(self) -> None:
        document = load_yaml(CONFIG)
        contract = validate_mechanism_contract(document)
        self.assertEqual(contract["id"], "t2_repair_mechanism_v1")
        self.assertEqual(
            contract["sha256"],
            "bb5f0c805497a741a3f5155573712a615c151da8f6181f6e3e89f6e8d4a94511",
        )
        self.assertEqual(len(document["scenario_instances"]), 5)
        self.assertEqual(
            {row["family_id"] for row in document["scenario_instances"]},
            {"radio_propagation", "obss_intensity", "legacy_coexistence", "compound_shift"},
        )
        self.assertTrue(all(len(row["seeds"]) == 4 for row in document["scenario_instances"]))

    def test_two_shards_preserve_complete_paired_units_and_derive_oracles(self) -> None:
        document = load_yaml(CONFIG)
        shards = [
            build_campaign_specs(document, "project-commit", index, 2)
            for index in range(2)
        ]
        all_units: set[tuple[str, int, int]] = set()
        all_run_ids: set[str] = set()
        for phase1, oracle, pairings in shards:
            self.assertEqual(len(phase1), 50)
            self.assertEqual(len(oracle), 10)
            self.assertEqual(len(pairings), 10)
            units = {
                (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
                for spec in phase1
            }
            self.assertEqual(len(units), 10)
            self.assertTrue(all_units.isdisjoint(units))
            all_units.update(units)
            for unit in units:
                members = [
                    spec for spec in phase1
                    if (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
                    == unit
                ]
                self.assertEqual(
                    {
                        (spec["config"]["topology"], spec["config"]["policy"])
                        for spec in members
                    },
                    PHASE1_ARMS,
                )
            for spec in [*phase1, *oracle]:
                self.assertNotIn(spec["run_id"], all_run_ids)
                all_run_ids.add(spec["run_id"])
                self.assertEqual(
                    spec["arm_id"],
                    ARM_IDS[(spec["config"]["topology"], spec["config"]["policy"])],
                )
                self.assertTrue(cli_arguments(
                    spec["config"],
                    Path("/campaign-root") if spec in oracle else CONFIG.parent,
                ))
            for spec in oracle:
                source = spec["config"]["prediction"][
                    "mechanism_oracle_packet_outcome_file"
                ]
                self.assertEqual(
                    source,
                    f"{spec['paired_baseline_run_id']}/frame_packet_outcomes.csv",
                )
        self.assertEqual(len(all_units), 20)
        self.assertEqual(len(all_run_ids), 120)

    def test_invalid_shard_is_rejected(self) -> None:
        document = load_yaml(CONFIG)
        with self.assertRaisesRegex(ValueError, "invalid shard"):
            build_campaign_specs(document, "project-commit", 2, 2)


if __name__ == "__main__":
    unittest.main()
