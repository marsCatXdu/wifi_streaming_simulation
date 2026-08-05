#!/usr/bin/env python3
"""Focused tests for environment LOFO data, split, and OOD primitives."""

from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import environment_generalization_lofo as lofo  # noqa: E402


class EnvironmentGeneralizationLofoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = lofo.load_contract()
        parent_path = ROOT / cls.contract["parent_contract"]["path"]
        cls.parent = json.loads(parent_path.read_text(encoding="utf-8"))
        cls.context_names = tuple(
            cls.parent["model_evaluation"]["observable_environment_features"]
        )
        cls.optional = tuple(
            cls.contract["ood_detector"]["allowed_missing_context_features"]
        )

    def test_completion_bins_include_exact_boundaries_and_incomplete(self) -> None:
        thresholds = self.contract["completion_distribution"]["thresholds_us"]
        self.assertEqual(lofo.completion_bin(0, thresholds), 0)
        for index, threshold in enumerate(thresholds):
            self.assertEqual(lofo.completion_bin(threshold, thresholds), index)
            if index:
                self.assertEqual(lofo.completion_bin(threshold - 1, thresholds), index)
        self.assertEqual(lofo.completion_bin(None, thresholds), len(thresholds))
        with self.assertRaises(lofo.LofoError):
            lofo.completion_bin(-1, thresholds)

    def test_model_layout_preserves_prior_308_and_appends_five_contexts(self) -> None:
        _, _, _, names, _ = lofo._feature_layout()
        self.assertEqual(len(names), lofo.MODEL_FEATURE_COUNT)
        self.assertEqual(len(names[:-5]), lofo.PRIOR_FEATURE_COUNT)
        self.assertEqual(
            names[-5:],
            tuple(self.contract["predictor"]["sender_context_features"]),
        )
        self.assertEqual(len(set(names)), len(names))

    def test_inner_scenario_folds_are_deterministic_and_family_balanced(self) -> None:
        families = ("family_a", "family_b", "family_c")
        mapping = {
            f"{family}-p{sample:02d}": family
            for family in families
            for sample in range(8)
        }
        first = lofo.assign_inner_scenario_folds(mapping, families, 4, "salt")
        second = lofo.assign_inner_scenario_folds(mapping, families, 4, "salt")
        self.assertEqual(first, second)
        for family in families:
            counts = Counter(
                first[scenario]
                for scenario, found_family in mapping.items()
                if found_family == family
            )
            self.assertEqual(counts, Counter({0: 2, 1: 2, 2: 2, 3: 2}))
        with self.assertRaisesRegex(lofo.LofoError, "cannot balance"):
            lofo.assign_inner_scenario_folds(
                {"a": "family_a", "b": "family_a"}, ("family_a",), 4, "salt"
            )

    def _context(self, rows: int = 240) -> np.ndarray:
        generator = np.random.default_rng(20260805)
        matrix = generator.normal(size=(rows, len(self.context_names)))
        matrix[:, 0] = generator.choice([24, 30, 45, 60], size=rows)
        matrix[:, 1] = generator.uniform(6_000, 14_000, size=rows)
        matrix[:, 2] = generator.choice([30, 60, 90, 120], size=rows)
        matrix[:, 3] = generator.uniform(2, 4, size=rows)
        matrix[:, 4] = generator.choice([16_667, 22_222, 33_333, 41_667], size=rows)
        optional_indices = [self.context_names.index(name) for name in self.optional]
        matrix[::7, optional_indices[0]] = np.nan
        matrix[::11, optional_indices[1]] = np.nan
        return matrix

    def test_ood_model_imputes_optional_values_and_fails_closed(self) -> None:
        context = self._context()
        detector = self.contract["ood_detector"]
        model = lofo.fit_ood_model(
            context,
            self.context_names,
            self.optional,
            detector["covariance_shrinkage_to_identity"],
            1e-9,
        )
        scores = model.scores(context)
        self.assertTrue(np.all(np.isfinite(scores)))
        self.assertTrue(np.all(scores >= 0))
        self.assertFalse(np.any(model.hard_failures(context)))

        optional_missing = context[[1]].copy()
        optional_missing[0, self.context_names.index(self.optional[0])] = np.nan
        self.assertFalse(model.hard_failures(optional_missing)[0])

        required_missing = context[[1]].copy()
        required_missing[0, 0] = np.nan
        self.assertTrue(model.hard_failures(required_missing)[0])

        unsupported = context[[1]].copy()
        unsupported[0, 0] = 120
        self.assertTrue(model.hard_failures(unsupported)[0])

        far_dynamic = context[[1]].copy()
        far_dynamic[0, -1] += 100
        self.assertGreater(model.scores(far_dynamic)[0], np.median(scores))

    def test_group_oof_ood_threshold_covers_each_training_row_once(self) -> None:
        families = ("family_a", "family_b")
        scenario_families = {
            f"{family}-p{sample:02d}": family
            for family in families
            for sample in range(8)
        }
        assignment = lofo.assign_inner_scenario_folds(
            scenario_families, families, 4, "ood-salt"
        )
        scenarios = tuple(
            scenario
            for scenario in sorted(scenario_families)
            for _ in range(15)
        )
        context = self._context(len(scenarios))
        threshold, scores = lofo.calibrate_ood_threshold(
            context,
            scenarios,
            scenario_families,
            families,
            assignment,
            self.context_names,
            self.optional,
            0.1,
            1e-9,
            0.995,
        )
        self.assertTrue(np.all(np.isfinite(scores)))
        self.assertEqual(threshold, np.quantile(scores, 0.995, method="higher"))
        self.assertGreaterEqual(threshold, 0)


if __name__ == "__main__":
    unittest.main()
