#!/usr/bin/env python3
"""Focused tests for fold-honest temporal-T2 shadow references."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import crossfit_temporal_t2_distributions as crossfit  # noqa: E402
import fit_temporal_t2_shadow_reference as reference  # noqa: E402


def _selection_result() -> dict[str, Any]:
    policies = []
    for ordinal, family in enumerate(crossfit.FEATURE_FAMILY_ORDER):
        for spec in crossfit.MODEL_SPECS:
            variant = crossfit._variant_id(family, spec)
            policies.append(
                {
                    "variant": variant,
                    "objective": "deadline_rescue",
                    "frame_gate": "p_frames_only",
                    "budget_us_per_run": 372_000,
                    "captured_primary_deadline_misses": 100 - ordinal,
                    "dr_policy_deadline_miss_probability": 0.01,
                    "dr_policy_minus_none_completed_late18_ratio": {
                        "estimate": 0.0,
                    },
                    "mean_canonical_reservation_us_per_run": 200_000.0,
                    "feature_family": family,
                    "model_spec_id": spec["id"],
                }
            )
    return {"policies": policies}


class ShadowReferenceTest(unittest.TestCase):
    def test_variant_definition_closes_over_frozen_grid(self) -> None:
        variants = []
        for ordinal, family in enumerate(crossfit.FEATURE_FAMILY_ORDER):
            for spec in crossfit.MODEL_SPECS:
                variant = crossfit._variant_id(family, spec)
                resolved_family, resolved_spec, resolved_ordinal = (
                    reference._variant_definition(variant)
                )
                variants.append(variant)
                self.assertEqual(resolved_family, family)
                self.assertEqual(resolved_spec, spec)
                expected = (
                    ordinal * len(crossfit.MODEL_SPECS)
                    + list(crossfit.MODEL_SPECS).index(spec)
                )
                self.assertEqual(resolved_ordinal, expected)
        self.assertEqual(len(set(variants)), 4)
        with self.assertRaisesRegex(
            reference.ShadowReferenceError, "outside the frozen grid"
        ):
            reference._variant_definition("unknown")

    def test_selection_prioritizes_direct_capture(self) -> None:
        result = _selection_result()
        preferred = result["policies"][1]
        preferred["captured_primary_deadline_misses"] = 101
        preferred["dr_policy_deadline_miss_probability"] = 0.99
        selected, record = reference.select_variant(result)
        self.assertEqual(selected, preferred["variant"])
        self.assertIs(record, preferred)

    def test_selection_tie_prefers_primary_then_smaller_model(self) -> None:
        result = _selection_result()
        for row in result["policies"]:
            row["captured_primary_deadline_misses"] = 100
        selected, _ = reference.select_variant(result)
        self.assertEqual(selected, "primary_hgb64")

    def test_selection_uses_tail_as_nonregression_gate_only(self) -> None:
        result = _selection_result()
        for index, row in enumerate(result["policies"]):
            row["captured_primary_deadline_misses"] = 100
            row["mean_canonical_reservation_us_per_run"] = 300_000.0 + index
        economical = result["policies"][0]
        economical["mean_canonical_reservation_us_per_run"] = 100_000.0
        economical["dr_policy_minus_none_completed_late18_ratio"][
            "estimate"
        ] = -0.001
        result["policies"][1][
            "dr_policy_minus_none_completed_late18_ratio"
        ]["estimate"] = -0.5
        selected, _ = reference.select_variant(result)
        self.assertEqual(selected, economical["variant"])

    def test_prediction_header_is_selected_variant_specific(self) -> None:
        header = reference._header("primary_hgb64")
        self.assertEqual(
            header[:5],
            [
                "reference_schema_version",
                "evaluation_fold",
                "seed",
                "run_number",
                "frame_id",
            ],
        )
        self.assertEqual(len(header), 15)
        self.assertEqual(len(set(header)), len(header))


if __name__ == "__main__":
    unittest.main()
