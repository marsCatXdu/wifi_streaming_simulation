#!/usr/bin/env python3
"""Tests for the frozen environment-generalization LOFO analysis contract."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_environment_generalization_dataset as dataset  # noqa: E402
import environment_generalization_lofo as lofo  # noqa: E402


CONTRACT_PATH = (
    ROOT / "experiments/model-selection/environment-generalization-lofo-v1.json"
)
CONTRACT_SHA256 = "1566fba76e39f9e677d1c133199a47ff5275f4b3981149dd5871f599a278a9d4"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EnvironmentGeneralizationLofoContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.parent = json.loads(
            (
                ROOT / cls.contract["parent_contract"]["path"]
            ).read_text(encoding="utf-8")
        )

    def test_contract_and_all_pre_result_sources_are_hash_bound(self) -> None:
        self.assertEqual(_sha256(CONTRACT_PATH), CONTRACT_SHA256)
        sources = [
            self.contract["parent_contract"],
            self.contract["predictor"]["prior_distribution_contract"],
            self.contract["predictor"]["prior_runtime_contract"],
        ]
        for source in sources:
            with self.subTest(path=source["path"]):
                self.assertEqual(_sha256(ROOT / source["path"]), source["sha256"])

        amendment_path = ROOT / lofo.BUILDER_AMENDMENT_PATH
        self.assertEqual(_sha256(amendment_path), lofo.BUILDER_AMENDMENT_SHA256)
        amendment = lofo._load_builder_amendment(self.contract)
        archived = amendment["source_profiles"][lofo.ARCHIVED_BUILDER_PROFILE]
        current = amendment["source_profiles"][lofo.CURRENT_BUILDER_PROFILE]
        builder_path = self.contract["dataset_contract"]["builder_path"]
        self.assertEqual(
            archived["sources_sha256"][builder_path],
            self.contract["dataset_contract"]["builder_sha256"],
        )
        self.assertEqual(
            current["sources_sha256"],
            {
                str(path.relative_to(ROOT)): _sha256(path)
                for path in dataset.BUILDER_SOURCES
            },
        )

    def test_deadline_grid_covers_every_frozen_frame_cadence(self) -> None:
        distribution = self.contract["completion_distribution"]
        thresholds = distribution["thresholds_us"]
        self.assertEqual(thresholds, sorted(set(thresholds)))
        self.assertEqual(distribution["class_count"], len(thresholds) + 1)
        self.assertTrue(
            set(distribution["deadline_values_must_be_in_thresholds"])
            <= set(thresholds)
        )
        video_fps = self.parent["scenario_families"]["video_workload"][
            "parameters"
        ]["stream.fps"]["values"]
        derived = {int(1_000_000 / fps + 0.5) for fps in video_fps}
        self.assertEqual(
            derived, set(distribution["deadline_values_must_be_in_thresholds"])
        )
        self.assertIn(distribution["tail_threshold_us"], thresholds)

    def test_predictor_is_fixed_from_prior_evidence_and_adds_only_context(self) -> None:
        predictor = self.contract["predictor"]
        self.assertEqual(predictor["prior_selected_variant"], "primary_secondary_hgb64")
        self.assertEqual(
            predictor["sender_context_features"],
            list(dataset.ENVIRONMENT_FEATURE_COLUMNS),
        )
        self.assertEqual(
            predictor["encoded_feature_count"],
            predictor["encoded_prior_feature_count"]
            + predictor["sender_context_feature_count"],
        )
        self.assertEqual(predictor["encoded_feature_count"], 313)
        self.assertFalse(set(predictor["forbidden_inputs"]) & set(dataset.FEATURE_COLUMNS))
        self.assertTrue(
            predictor["diagnostic_ablation"][
                "may_not_replace_selected_predictor_from_held_out_results"
            ]
        )

    def test_outer_and_inner_groups_cannot_split_seed_replicates(self) -> None:
        crossfit = self.contract["cross_fitting"]
        self.assertEqual(
            crossfit["outer_family_order"], self.parent["sampling"]["family_order"]
        )
        self.assertEqual(crossfit["outer_fold_count"], len(crossfit["outer_family_order"]))
        self.assertEqual(crossfit["outer_unit"], "family_id")
        self.assertEqual(crossfit["inner_group_unit"], "scenario_id")
        self.assertTrue(crossfit["replicate_seeds_never_cross_inner_folds"])

    def test_ood_context_and_exploration_match_parent_limits(self) -> None:
        detector = self.contract["ood_detector"]
        observable = self.parent["model_evaluation"]["observable_environment_features"]
        self.assertEqual(detector["raw_context_feature_count"], len(observable))
        self.assertEqual(detector["encoded_context_feature_count"], 2 * len(observable))
        self.assertTrue(
            set(detector["allowed_missing_context_features"]) <= set(observable)
        )
        self.assertEqual(
            detector["covariance_shrinkage_to_identity"],
            self.parent["ood_policy"]["covariance_shrinkage_to_identity"],
        )
        self.assertEqual(
            detector["threshold_quantile"],
            self.parent["ood_policy"]["threshold_quantile"],
        )
        exploration = self.contract["deployment_exploration"]
        parent_exploration = self.parent["deployment_exploration"]
        in_support = exploration["in_support"]
        self.assertAlmostEqual(sum(in_support.values()), 1.0)
        self.assertEqual(
            in_support["forced_t2_action_probability"],
            parent_exploration["forced_t2_action_probability"],
        )
        self.assertLessEqual(
            exploration["soft_ood"]["maximum_forced_t2_action_probability"],
            self.parent["ood_policy"]["maximum_ood_exploration_action_probability"],
        )
        self.assertEqual(
            exploration["hard_failure"]["forced_t2_action_probability"],
            self.parent["ood_policy"]["hard_failure_exploration_action_probability"],
        )

    def test_contract_preserves_qualification_and_confirmation_boundaries(self) -> None:
        evaluation = self.contract["evaluation"]
        self.assertTrue(
            evaluation["qualification_results_must_remain_unread_during_this_analysis"]
        )
        self.assertTrue(
            evaluation["reserved_neutral_seeds_1301_through_1348_must_remain_unopened"]
        )
        self.assertTrue(
            evaluation["scenario_resource_oracle"][
                "must_not_be_described_as_perfect_information"
            ]
        )


if __name__ == "__main__":
    unittest.main()
