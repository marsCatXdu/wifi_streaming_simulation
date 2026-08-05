#!/usr/bin/env python3
"""Focused tests for the temporal-T2 static distributional frontier."""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

import numpy as np


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_temporal_t2_distributional_frontier as frontier  # noqa: E402
import crossfit_temporal_t2_distributions as crossfit  # noqa: E402


class StaticDistributionalFrontierTest(unittest.TestCase):
    def test_objectives_remain_separate_and_nonnegative(self) -> None:
        cdf = np.zeros((2, 2, len(crossfit.THRESHOLDS_US)))
        cdf[0, 0] = np.asarray([0.2, 0.4, 0.5, 0.6, 0.7])
        cdf[0, 1] = np.asarray([0.5, 0.7, 0.8, 0.9, 1.0])
        cdf[1, 0] = np.asarray([0.5, 0.6, 0.7, 0.8, 0.9])
        cdf[1, 1] = np.asarray([0.4, 0.5, 0.6, 0.7, 0.8])
        rewards = frontier.objective_rewards(cdf)
        self.assertEqual(tuple(rewards), frontier.OBJECTIVES)
        self.assertAlmostEqual(rewards["deadline_rescue"][0], 0.3)
        self.assertAlmostEqual(rewards["completion_by_18ms"][0], 0.3)
        self.assertGreater(rewards["deadline_capped_acceleration"][0], 0)
        self.assertEqual(rewards["deadline_rescue"][1], 0)
        self.assertEqual(rewards["completion_by_18ms"][1], 0)
        self.assertEqual(rewards["deadline_capped_acceleration"][1], 0)

    def test_two_cost_knapsack_is_exact_and_run_local(self) -> None:
        indices = np.arange(5)
        rewards = np.asarray([5.0, 4.0, 3.0, 11.0, 1.0])
        frame_types = ("P_FRAME", "P_FRAME", "P_FRAME", "I_FRAME", "I_FRAME")
        frame_ids = np.asarray([10, 11, 12, 13, 14])
        costs = ("2", "2", "2", "5", "5")
        chosen, spend, value = frontier.exact_two_cost_knapsack(
            indices,
            rewards,
            frame_types,
            frame_ids,
            costs,
            budget_us=7,
            frame_gate="all_frames_cost_aware",
        )
        self.assertEqual(set(chosen), {0, 3})
        self.assertEqual(spend, Decimal("7"))
        self.assertEqual(value, 16.0)

        p_chosen, p_spend, p_value = frontier.exact_two_cost_knapsack(
            indices,
            rewards,
            frame_types,
            frame_ids,
            costs,
            budget_us=7,
            frame_gate="p_frames_only",
        )
        self.assertEqual(set(p_chosen), {0, 1, 2})
        self.assertEqual(p_spend, Decimal("6"))
        self.assertEqual(p_value, 12.0)

    def test_zero_value_actions_are_not_used_to_fill_budget(self) -> None:
        chosen, spend, value = frontier.exact_two_cost_knapsack(
            np.arange(3),
            np.zeros(3),
            ("P_FRAME", "P_FRAME", "P_FRAME"),
            np.asarray([1, 2, 3]),
            ("2", "2", "2"),
            budget_us=100,
            frame_gate="p_frames_only",
        )
        self.assertEqual(chosen, ())
        self.assertEqual(spend, Decimal(0))
        self.assertEqual(value, 0.0)

    def test_bootstrap_matrix_is_shared_and_deterministic(self) -> None:
        first = frontier._bootstrap_indices(96)
        second = frontier._bootstrap_indices(96)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(
            first.shape, (frontier.BOOTSTRAP_REPLICATIONS, 96)
        )


if __name__ == "__main__":
    unittest.main()
