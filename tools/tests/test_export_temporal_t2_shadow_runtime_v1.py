#!/usr/bin/env python3
"""Tests for the distributional temporal-T2 runtime exporter."""

from __future__ import annotations

import copy
import math
import sys
import unittest
from decimal import Decimal
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import crossfit_temporal_t2_distributions as crossfit
import export_temporal_t2_shadow_runtime_v1 as exporter

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = ROOT / "results/randomized_full_copy_exploration_collection_v1"
ARTIFACT = CAMPAIGN / "temporal_t2_shadow_borrow_runtime_v1"
DATA_SOURCE = ROOT / exporter.DEFAULT_DATA_SOURCE
DATA_HEADER = ROOT / exporter.DEFAULT_DATA_HEADER


class TemporalT2ShadowRuntimeExporterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        generator = np.random.default_rng(20260805)
        cls.matrix = generator.normal(size=(480, exporter.SELECTED_FEATURE_COUNT))
        cls.matrix[::11, 7] = np.nan
        cls.matrix[::17, 301] = np.nan
        cls.labels = np.tile(np.arange(crossfit.CLASS_COUNT), 80)
        cls.model = crossfit._model(dict(exporter.MODEL_SPEC), exporter.CONTROL_SEED)
        cls.model.fit(cls.matrix, cls.labels)
        cls.payload = exporter.multiclass_hgb_payload(
            cls.model,
            exporter.SELECTED_FEATURE_COUNT,
            len(cls.labels),
            "synthetic head",
        )

    def test_frozen_contract_and_sources_validate(self) -> None:
        contract = exporter.validate_contract_and_sources()
        self.assertEqual(contract["runtime_contract_id"], exporter.RUNTIME_CONTRACT_ID)
        self.assertEqual(
            contract["full_data_refit"]["selected_variant"],
            exporter.SELECTED_VARIANT,
        )
        self.assertEqual(
            contract["evidence_boundary"]["reserved_confirmation_seed_range"],
            [1301, 1348],
        )

    def test_extracts_all_six_trees_per_boosting_iteration(self) -> None:
        self.assertEqual(len(self.payload["trees"]), 64 * 6)
        self.assertEqual(len(self.payload["baseline"]), 6)
        self.assertEqual(self.payload["training_count"], 480)
        for iteration in range(64):
            rows = self.payload["trees"][iteration * 6 : (iteration + 1) * 6]
            self.assertEqual([row[2] for row in rows], list(range(6)))
            self.assertEqual([row[3] for row in rows], [iteration] * 6)

    def test_portable_head_matches_sklearn_logits_and_smoothed_probabilities(self) -> None:
        for index in (0, 1, 17, 111, 479):
            raw = self.matrix[index]
            portable = exporter.evaluate_portable_head(self.payload, raw)
            quantized = exporter._quantize_features(raw).reshape(1, -1)
            imputed = self.model.named_steps["impute"].transform(quantized)
            expected_logits = self.model.named_steps["classifier"].decision_function(
                imputed
            )[0]
            expected_probability = crossfit.aligned_smoothed_probabilities(
                self.model, quantized, 480
            )[0]
            np.testing.assert_allclose(
                portable["logits"], expected_logits, rtol=0.0, atol=1e-12
            )
            np.testing.assert_allclose(
                portable["smoothed_probabilities"],
                expected_probability,
                rtol=0.0,
                atol=1e-12,
            )

    def test_rejects_tree_feature_outside_transformed_input(self) -> None:
        model = copy.deepcopy(self.model)
        model.named_steps["classifier"]._predictors[0][0].nodes[0][
            "feature_idx"
        ] = 65535
        with self.assertRaisesRegex(exporter.RuntimeExportError, "tree feature escapes"):
            exporter.multiclass_hgb_payload(
                model,
                exporter.SELECTED_FEATURE_COUNT,
                480,
                "tampered head",
            )

    def test_shadow_curve_uses_exact_per_run_resource_boundary(self) -> None:
        curve = exporter.RuntimeCurve(np.asarray([10.0, 8.0, 6.0]), 2)
        cost = Decimal("2")
        self.assertTrue(math.isinf(curve.opportunity_cost(Decimal("0.99"), cost)))
        self.assertEqual(curve.opportunity_cost(Decimal("1"), cost), 10.0)
        self.assertEqual(curve.opportunity_cost(Decimal("2"), cost), 8.0)
        self.assertEqual(curve.opportunity_cost(Decimal("3"), cost), 0.0)

    def test_compiled_curve_keeps_every_reachable_boundary(self) -> None:
        row = {
            "training_run_count": 32,
            "density_descending": [float(7000 - index) for index in range(7000)],
        }
        values, complete = exporter._reachable_curve_prefix(
            row, Decimal("1983.760667318285")
        )
        expected_affordable = int(
            (
                exporter.MAXIMUM_REPAYABLE_CREDIT_US
                * row["training_run_count"]
            )
            // Decimal("1983.760667318285")
        )
        self.assertEqual(len(values), expected_affordable + 1)
        self.assertFalse(complete)
        self.assertEqual(values[0], 7000.0)

    def test_generated_data_interface_is_deterministic(self) -> None:
        first = exporter.emit_data_header()
        second = exporter.emit_data_header()
        self.assertEqual(first, second)
        self.assertIn("MulticlassClassifier", first)
        self.assertIn("std::array<ShadowCurve, 3>", first)
        self.assertNotIn("globalCurves", first)

    def test_canonical_artifact_and_compiled_data_are_current(self) -> None:
        model, reference, metrics, manifest = exporter.load_runtime_artifacts(
            ARTIFACT
        )
        self.assertEqual(metrics["provenance"]["project_git_status_porcelain"], "")
        self.assertEqual(
            DATA_HEADER.read_text(encoding="utf-8"), exporter.emit_data_header()
        )
        self.assertEqual(
            DATA_SOURCE.read_text(encoding="utf-8"),
            exporter.emit_data_source(
                ARTIFACT.resolve(), model, reference, metrics, manifest
            ),
        )

    def test_credit_goldens_include_action_debt_cap_and_rejection(self) -> None:
        rows = exporter._credit_golden_cases(Decimal("1983.760667318285"))
        self.assertEqual(
            [row["label"] for row in rows],
            ["initial_action", "debt_action", "positive_cap", "horizon_reject"],
        )
        self.assertTrue(rows[0]["expected_admitted"])
        self.assertLess(rows[1]["expected_balance_after_us"], 0.0)
        self.assertEqual(rows[2]["expected_refilled_balance_us"], 360000.0)
        self.assertFalse(rows[3]["expected_admitted"])

    def test_regime_boundaries_match_searchsorted_side_right(self) -> None:
        cutpoints = (0.25, 0.75)
        self.assertEqual(exporter._regime(0.249, cutpoints), 0)
        self.assertEqual(exporter._regime(0.25, cutpoints), 1)
        self.assertEqual(exporter._regime(0.749, cutpoints), 1)
        self.assertEqual(exporter._regime(0.75, cutpoints), 2)


if __name__ == "__main__":
    unittest.main()
