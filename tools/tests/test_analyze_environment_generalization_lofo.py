#!/usr/bin/env python3
"""Focused tests for environment LOFO completion-distribution analysis."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_environment_generalization_lofo as analyzer  # noqa: E402
import environment_generalization_lofo as lofo  # noqa: E402


class AnalyzeEnvironmentGeneralizationLofoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = lofo.load_contract()

    @staticmethod
    def _small_model_spec() -> dict[str, object]:
        return {
            "id": "synthetic_hgb",
            "class": "sklearn.ensemble.HistGradientBoostingClassifier",
            "loss": "log_loss",
            "learning_rate": 0.1,
            "max_iter": 2,
            "max_leaf_nodes": 3,
            "max_depth": 2,
            "min_samples_leaf": 2,
            "l2_regularization": 1.0,
            "max_bins": 31,
            "early_stopping": False,
            "random_seed_base": 1729,
        }

    def test_probability_alignment_smooths_unobserved_classes(self) -> None:
        matrix = np.asarray(
            [[-2.0], [-1.0], [-0.5], [0.5], [1.0], [2.0]], dtype=float
        )
        labels = np.asarray([0, 0, 0, 2, 2, 2], dtype=int)
        model = analyzer._model(self._small_model_spec(), seed=17)
        model.fit(matrix, labels)
        probabilities = analyzer.aligned_smoothed_probabilities(
            model,
            matrix,
            training_count=len(labels),
            class_count=4,
            alpha=0.5,
        )
        self.assertEqual(probabilities.shape, (len(matrix), 4))
        np.testing.assert_allclose(np.sum(probabilities, axis=1), 1.0)
        self.assertTrue(np.all(probabilities > 0))
        np.testing.assert_allclose(probabilities[:, 1], probabilities[:, 3])

    def test_doubly_robust_components_use_known_propensity(self) -> None:
        outcomes = np.asarray([0, 2], dtype=int)
        treatment = np.asarray([0, 1], dtype=int)
        propensity = np.asarray([0.25, 0.25], dtype=float)
        cdf = np.asarray(
            [
                [[0.4, 0.7], [0.6, 0.8]],
                [[0.2, 0.3], [0.5, 0.6]],
            ],
            dtype=float,
        )
        phi0, phi1 = analyzer.doubly_robust_cdf_components(
            outcomes, treatment, propensity, cdf
        )
        np.testing.assert_allclose(
            phi0,
            np.asarray(
                [
                    [0.4 + (1.0 - 0.4) / 0.75, 0.7 + (1.0 - 0.7) / 0.75],
                    [0.2, 0.3],
                ]
            ),
        )
        np.testing.assert_allclose(
            phi1,
            np.asarray(
                [
                    [0.6, 0.8],
                    [0.5 - 0.5 / 0.25, 0.6 - 0.6 / 0.25],
                ]
            ),
        )

    def _synthetic_dataset(self) -> lofo.LofoDataset:
        families = tuple(self.contract["cross_fitting"]["outer_family_order"])
        context_names = tuple(
            lofo._read_json(ROOT / self.contract["parent_contract"]["path"])[
                "model_evaluation"
            ]["observable_environment_features"]
        )
        rows: list[tuple[int, int, int, int]] = []
        scenario_ids: list[str] = []
        family_ids: list[str] = []
        for family_index, family in enumerate(families):
            for scenario_index in range(4):
                scenario = f"{family}-p{scenario_index:02d}"
                for replicate in range(4):
                    rows.append(
                        (family_index, scenario_index, replicate, len(rows))
                    )
                    scenario_ids.append(scenario)
                    family_ids.append(family)
        row_count = len(rows)
        treatment = np.asarray(
            [(scenario + replicate) % 2 for _, scenario, replicate, _ in rows],
            dtype=np.int8,
        )
        outcome_bins = np.asarray(
            [
                (family + 2 * scenario + replicate + int(treatment[index])) % 4
                for index, (family, scenario, replicate, _) in enumerate(rows)
            ],
            dtype=np.int8,
        )
        model_matrix = np.asarray(
            [
                [
                    float(scenario),
                    float(replicate),
                    float((scenario + replicate) % 3),
                ]
                for _, scenario, replicate, _ in rows
            ],
            dtype=np.float32,
        )
        ood_context = np.empty((row_count, len(context_names)), dtype=float)
        for index, (_, scenario, replicate, _) in enumerate(rows):
            for feature in range(len(context_names)):
                ood_context[index, feature] = (
                    (feature + 1) * 0.1 + scenario + replicate * 0.25
                )
        ood_context[:, 0] = np.asarray(
            [24, 30, 45, 60] * (row_count // 4), dtype=float
        )
        ood_context[:, 1] = 8_000 + 1_000 * np.asarray(
            [scenario for _, scenario, _, _ in rows]
        )
        ood_context[:, 2] = 30 + 30 * np.asarray(
            [replicate for _, _, replicate, _ in rows]
        )
        ood_context[:, 3] = 2 + 0.25 * np.asarray(
            [scenario for _, scenario, _, _ in rows]
        )
        ood_context[:, 4] = np.asarray(
            [16_667, 22_222, 33_333, 41_667] * (row_count // 4), dtype=float
        )
        optional = self.contract["ood_detector"][
            "allowed_missing_context_features"
        ]
        ood_context[::11, context_names.index(optional[0])] = np.nan
        ood_context[::13, context_names.index(optional[1])] = np.nan
        deadline_indices = np.full(row_count, 6, dtype=np.int8)
        deadline_miss = (outcome_bins > deadline_indices).astype(np.int8)
        primary_miss = ((outcome_bins + treatment) > deadline_indices).astype(np.int8)
        completed_late18 = (
            (outcome_bins > 2)
            & (
                outcome_bins
                < len(self.contract["completion_distribution"]["thresholds_us"])
            )
        ).astype(np.int8)
        return lofo.LofoDataset(
            path=ROOT,
            metadata={},
            artifact_manifest={},
            contract=self.contract,
            model_matrix=model_matrix,
            model_feature_names=("a", "b", "c"),
            ood_context=ood_context,
            ood_context_names=context_names,
            run_ids=tuple(f"run-{index:03d}" for index in range(row_count)),
            seeds=np.arange(row_count, dtype=np.int32),
            run_numbers=np.zeros(row_count, dtype=np.int16),
            frame_ids=np.arange(row_count, dtype=np.int32),
            scenario_ids=tuple(scenario_ids),
            family_ids=tuple(family_ids),
            parameter_samples=np.asarray(
                [scenario for _, scenario, _, _ in rows], dtype=np.int16
            ),
            treatment=treatment,
            propensity=np.full(row_count, 0.2, dtype=float),
            outcome_bins=outcome_bins,
            union_latencies_us=np.full(row_count, 10_000.0),
            primary_latencies_us=np.full(row_count, 11_000.0),
            deadline_us=np.full(row_count, 33_333, dtype=np.int32),
            deadline_threshold_indices=deadline_indices,
            deadline_miss=deadline_miss,
            primary_deadline_miss=primary_miss,
            completed_late18=completed_late18,
            canonical_reservation_us=np.full(row_count, 100.0),
            frame_types=tuple("P" for _ in range(row_count)),
        )

    def test_lofo_fit_predicts_each_family_once(self) -> None:
        data = self._synthetic_dataset()
        weights = analyzer._hierarchical_weights(
            data, np.ones(len(data.run_ids), dtype=bool)
        )
        families = np.asarray(data.family_ids, dtype=object)
        scenarios = np.asarray(data.scenario_ids, dtype=object)
        for family in set(data.family_ids):
            np.testing.assert_allclose(np.sum(weights[families == family]), 1 / 6)
        for scenario in set(data.scenario_ids):
            np.testing.assert_allclose(np.sum(weights[scenarios == scenario]), 1 / 24)
        cdf, ood, fits = analyzer.fit_lofo_predictions(
            data, model_spec=self._small_model_spec()
        )
        self.assertEqual(cdf.shape, (len(data.run_ids), 2, 8))
        self.assertTrue(np.all(np.isfinite(cdf)))
        self.assertTrue(np.all(np.diff(cdf, axis=2) >= -1e-7))
        self.assertEqual(len(fits), 18)
        self.assertEqual(
            sum(record.get("record_type") == "ood" for record in fits), 6
        )
        for values in ood.values():
            self.assertEqual(values.shape, (len(data.run_ids),))
        diagnostics = analyzer.summarize_predictions(data, cdf, ood, fits)
        self.assertEqual(len(diagnostics["family_metrics"]), 6)
        self.assertEqual(len(diagnostics["fit_records"]), 18)


if __name__ == "__main__":
    unittest.main()
