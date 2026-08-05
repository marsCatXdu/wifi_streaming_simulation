#!/usr/bin/env python3
"""Focused tests for environment policy replay and uncertainty analysis."""

from __future__ import annotations

import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_environment_generalization_policy as analysis  # noqa: E402
import environment_generalization_lofo as lofo  # noqa: E402
import environment_generalization_policy as policy  # noqa: E402


class AnalyzeEnvironmentGeneralizationPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = lofo.load_contract()

    def _campaign_data(self) -> SimpleNamespace:
        run_ids: list[str] = []
        scenario_ids: list[str] = []
        family_ids: list[str] = []
        seeds: list[int] = []
        run_numbers: list[int] = []
        frame_ids: list[int] = []
        for family_index, family in enumerate(
            self.contract["cross_fitting"]["outer_family_order"]
        ):
            for scenario_index in range(16):
                scenario_id = f"{family}-p{scenario_index:02d}"
                for replicate in range(4):
                    run_ids.append(
                        f"{family_index}-{scenario_index:02d}-{replicate}"
                    )
                    scenario_ids.append(scenario_id)
                    family_ids.append(family)
                    seeds.append(10_000 + len(seeds))
                    run_numbers.append(1)
                    frame_ids.append(0)
        row_count = len(run_ids)
        return SimpleNamespace(
            contract=self.contract,
            run_ids=tuple(run_ids),
            scenario_ids=tuple(scenario_ids),
            family_ids=tuple(family_ids),
            seeds=np.asarray(seeds),
            run_numbers=np.asarray(run_numbers),
            frame_ids=np.asarray(frame_ids),
            treatment=np.zeros(row_count, dtype=np.int8),
            canonical_reservation_us=np.full(row_count, 100.0),
        )

    def test_bootstrap_preserves_fixed_equal_family_strata(self) -> None:
        data = self._campaign_data()
        family_index = {
            family: index
            for index, family in enumerate(
                self.contract["cross_fitting"]["outer_family_order"]
            )
        }
        components = {
            "one": np.ones(len(data.run_ids)),
            "family_index": np.asarray(
                [family_index[family] for family in data.family_ids], dtype=float
            ),
        }
        first = analysis.make_bootstrap_plan(data, 128, 999)
        second = analysis.make_bootstrap_plan(data, 128, 999)
        for family in first.family_order:
            np.testing.assert_array_equal(
                first.scenario_draws[family], second.scenario_draws[family]
            )
            np.testing.assert_array_equal(
                first.run_draws[family], second.run_draws[family]
            )
        names, samples = analysis.bootstrap_policy_values(data, components, first)
        self.assertEqual(names, ("one", "family_index"))
        np.testing.assert_allclose(samples[:, 0], 1.0)
        np.testing.assert_allclose(samples[:, 1], 2.5)

    def test_resource_metrics_use_hierarchy_and_enforce_run_budget(self) -> None:
        data = self._campaign_data()
        action = np.asarray(
            [float(index % 2 == 0) for index in range(len(data.run_ids))]
        )
        trace = policy.PolicyTrace(action_probability=action, run_details={})
        metrics = analysis._resource_metrics(data, trace, budget_us=100)
        self.assertEqual(metrics["expected_action_count"], 192.0)
        self.assertEqual(metrics["hierarchical_action_fraction"], 0.5)
        self.assertEqual(metrics["maximum_canonical_reservation_us_per_run"], 100)
        with self.assertRaisesRegex(policy.PolicyError, "exceeds"):
            analysis._resource_metrics(data, trace, budget_us=99)

    def test_fraction_gain_interval_reports_partially_unidentified_draws(self) -> None:
        result = analysis._fraction_gain_interval(
            0.01,
            0.007,
            0.005,
            np.full(200, 0.01),
            np.full(200, 0.007),
            np.concatenate((np.full(150, 0.005), np.full(50, 0.011))),
            0.95,
        )
        self.assertAlmostEqual(result["estimate"], 0.6)
        self.assertEqual(result["bootstrap_valid_fraction"], 0.75)
        self.assertAlmostEqual(result["ci_lower"], 0.6)
        self.assertAlmostEqual(result["ci_upper"], 0.6)

    def test_full_replay_bundle_evaluates_every_frozen_policy(self) -> None:
        data = self._campaign_data()
        row_count = len(data.run_ids)
        treatment = np.asarray(
            [replicate >= 2 for _ in range(96) for replicate in range(4)],
            dtype=np.int8,
        )
        outcome_bins = np.asarray(
            [
                0 if treated or replicate == 0 else 8
                for treated, replicate in zip(
                    treatment, [value for _ in range(96) for value in range(4)]
                )
            ],
            dtype=np.int8,
        )
        data.treatment = treatment
        data.propensity = np.full(row_count, 0.5)
        data.outcome_bins = outcome_bins
        data.deadline_threshold_indices = np.full(row_count, 6, dtype=np.int8)
        data.completed_late18 = np.zeros(row_count, dtype=np.int8)
        data.frame_types = tuple("P_FRAME" for _ in range(row_count))
        data.canonical_reservation_texts = tuple("100" for _ in range(row_count))
        data.parameter_samples = np.asarray(
            [scenario for _ in range(6) for scenario in range(16) for _ in range(4)]
        )
        cdf = np.empty((row_count, 2, 8), dtype=float)
        cdf[:, 0, :] = 0.5
        cdf[:, 1, :] = 1.0
        predictions = policy.LofoPredictions(
            path=ROOT,
            metrics={},
            manifest={},
            cdf=cdf,
            ood_score=np.zeros(row_count),
            ood_threshold=np.ones(row_count),
            ood_hard_failure=np.zeros(row_count, dtype=np.int8),
            ood_soft=np.zeros(row_count, dtype=np.int8),
            ood_fallback=np.zeros(row_count, dtype=np.int8),
        )
        with mock.patch.object(analysis.lofo, "load_dataset", return_value=data):
            with mock.patch.object(
                analysis.policy, "load_lofo_predictions", return_value=predictions
            ):
                bundle = analysis._analyze_bundle("dataset", "predictions")
        self.assertEqual(tuple(bundle.result["policies"]), analysis.POLICY_ORDER)
        self.assertAlmostEqual(
            bundle.result["policies"]["no_secondary_copy"]["policy_value"][
                "deadline_miss"
            ]["estimate"],
            0.5,
        )
        self.assertAlmostEqual(
            bundle.result["policies"][
                "cross_fitted_scenario_resource_oracle_v1"
            ]["policy_value"]["deadline_miss"]["estimate"],
            0.0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            action_path = root / analysis.OUTPUT_ACTIONS
            analysis._write_actions(action_path, bundle)
            with gzip.open(
                action_path, mode="rt", encoding="utf-8", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), row_count)
            self.assertEqual(rows[0]["run_id"], data.run_ids[0])
            analysis._write_family_csv(root / analysis.OUTPUT_FAMILY_CSV, bundle.result)
            analysis._write_report(root / analysis.OUTPUT_REPORT, bundle.result)
            self.assertTrue((root / analysis.OUTPUT_FAMILY_CSV).stat().st_size)
            self.assertTrue((root / analysis.OUTPUT_REPORT).stat().st_size)


if __name__ == "__main__":
    unittest.main()
