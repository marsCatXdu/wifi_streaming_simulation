#!/usr/bin/env python3
"""Focused tests for the temporal-T2 distributional cross-fit."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import crossfit_temporal_t2_distributions as distribution  # noqa: E402


class _Classifier:
    classes_ = np.asarray([0, 2], dtype=int)


class _Model:
    named_steps = {"classifier": _Classifier()}

    def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
        return np.tile(np.asarray([[0.75, 0.25]]), (len(matrix), 1))


class DistributionCrossfitTest(unittest.TestCase):
    def test_latency_bins_include_boundary_and_incomplete(self) -> None:
        self.assertEqual(distribution.latency_bin(0.0), 0)
        self.assertEqual(distribution.latency_bin(12_000.0), 0)
        self.assertEqual(distribution.latency_bin(12_000.1), 1)
        self.assertEqual(distribution.latency_bin(33_333.0), 4)
        self.assertEqual(distribution.latency_bin(33_333.1), 5)
        self.assertEqual(distribution.latency_bin(None), 5)
        with self.assertRaisesRegex(distribution.DistributionError, "latency"):
            distribution.latency_bin(-1.0)

    def test_group_folds_are_deterministic_balanced_and_disjoint(self) -> None:
        groups = [(seed, 1) for seed in range(1101, 1197)]
        assignment, folds = distribution.assign_group_folds(groups)
        repeated, repeated_folds = distribution.assign_group_folds(groups)
        self.assertEqual(assignment, repeated)
        self.assertEqual(folds, repeated_folds)
        self.assertEqual({len(fold) for fold in folds}, {12})
        flattened = [group for fold in folds for group in fold]
        self.assertEqual(set(flattened), set(groups))
        self.assertEqual(len(flattened), len(set(flattened)))

    def test_probability_alignment_guards_absent_classes(self) -> None:
        probabilities = distribution.aligned_smoothed_probabilities(
            _Model(), np.zeros((2, 1)), training_count=10
        )
        expected = np.asarray([8.0, 0.5, 3.0, 0.5, 0.5, 0.5]) / 13.0
        np.testing.assert_allclose(probabilities[0], expected)
        np.testing.assert_allclose(np.sum(probabilities, axis=1), 1.0)
        self.assertTrue(np.all(probabilities > 0))
        cdf = np.cumsum(probabilities, axis=1)[:, :-1]
        self.assertTrue(np.all(np.diff(cdf, axis=1) >= 0))

    def test_doubly_robust_components_use_known_propensity(self) -> None:
        bins = np.asarray([0, 5], dtype=np.int8)
        treatment = np.asarray([0, 1], dtype=np.int8)
        propensity = np.asarray([0.5, 0.5])
        cdf = np.full((2, 2, len(distribution.THRESHOLDS_US)), 0.5)
        phi0, phi1 = distribution.doubly_robust_cdf_components(
            bins, treatment, propensity, cdf
        )
        np.testing.assert_allclose(phi0[0], 1.5)
        np.testing.assert_allclose(phi0[1], 0.5)
        np.testing.assert_allclose(phi1[0], 0.5)
        np.testing.assert_allclose(phi1[1], -0.5)

    def test_class_probabilities_round_trip_a_valid_cdf(self) -> None:
        probabilities = np.asarray(
            [
                [0.2, 0.1, 0.3, 0.1, 0.2, 0.1],
                [0.5, 0.1, 0.1, 0.1, 0.1, 0.1],
            ]
        )
        cdf = np.cumsum(probabilities, axis=1)[:, :-1]
        np.testing.assert_allclose(
            distribution._class_probabilities(cdf), probabilities
        )

    def test_frozen_design_contract_matches_tool(self) -> None:
        contract = distribution._validate_design_contract()
        self.assertEqual(contract["analysis_id"], distribution.ANALYSIS_ID)


if __name__ == "__main__":
    unittest.main()
