#!/usr/bin/env python3
"""Tests for the frozen environment-generalization policy replay contract."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / (
    "experiments/model-selection/environment-generalization-policy-replay-v1.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EnvironmentGeneralizationPolicyContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        lofo_path = ROOT / cls.contract["sources"]["lofo_contract"]["path"]
        cls.lofo = json.loads(lofo_path.read_text(encoding="utf-8"))

    def test_contract_and_pre_result_sources_are_hash_bound(self) -> None:
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(
            self.contract["analysis_id"],
            "environment-generalization-policy-replay-v1",
        )
        self.assertEqual(
            self.contract["status"],
            "frozen_during_collection_before_randomized_outcomes_read",
        )
        for source in self.contract["sources"].values():
            self.assertEqual(_sha256(ROOT / source["path"]), source["sha256"])

    def test_campaign_and_monte_carlo_fill_64_way_units(self) -> None:
        population = self.contract["population"]
        self.assertEqual(population["expected_run_count"], 384)
        self.assertEqual(population["expected_run_count"] % 64, 0)
        policies = {row["id"]: row for row in self.contract["policies"]}
        self.assertEqual(
            policies["uniform_random_t2_same_canonical_budget"]["replications"],
            64,
        )
        self.assertTrue(population["qualification_results_must_remain_unread"])
        self.assertTrue(
            population["reserved_neutral_seeds_1301_through_1348_must_remain_unopened"]
        )

    def test_oracle_is_cross_fitted_but_explicitly_not_deployable(self) -> None:
        policies = {row["id"]: row for row in self.contract["policies"]}
        oracle = policies["cross_fitted_scenario_resource_oracle_v1"]
        self.assertTrue(oracle["future_score_visibility"])
        self.assertFalse(oracle["perfect_information"])
        self.assertTrue(oracle["may_not_be_described_as_perfect_information"])
        self.assertTrue(oracle["may_not_be_deployed_online"])
        self.assertEqual(
            self.contract["resource"]["budget_us_per_60s_run"], 372_000
        )
        self.assertEqual(
            self.contract["resource"]["cost_arithmetic"],
            "exact decimal text from the source dataset",
        )

    def test_value_and_exploration_preserve_parent_contract(self) -> None:
        value = self.contract["policy_value"]
        self.assertIn("doubly robust", value["deadline_miss"]["estimator"])
        self.assertIn(
            "Horvitz-Thompson", value["completed_late18"]["estimator"]
        )
        self.assertEqual(
            value["weighting"],
            (
                "equal_family_then_equal_scenario_then_equal_replicate_then_"
                "equal_eligible_frame"
            ),
        )
        expected = self.lofo["deployment_exploration"]
        actual = self.contract["deployment_exploration"]
        self.assertEqual(actual["assignment_salt"], expected["assignment_salt"])
        self.assertEqual(
            actual["in_support"]["forced_t2_probability"],
            expected["in_support"]["forced_t2_action_probability"],
        )
        self.assertEqual(
            actual["in_support"]["forced_control_probability"],
            expected["in_support"]["forced_control_probability"],
        )
        self.assertEqual(
            actual["soft_ood"]["forced_t2_probability"],
            expected["soft_ood"]["maximum_forced_t2_action_probability"],
        )


if __name__ == "__main__":
    unittest.main()
