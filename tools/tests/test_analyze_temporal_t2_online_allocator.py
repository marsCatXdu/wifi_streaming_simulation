#!/usr/bin/env python3
"""Focused tests for the temporal-T2 online shadow-price allocator."""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_temporal_t2_online_allocator as online  # noqa: E402


class OnlineAllocatorTest(unittest.TestCase):
    def test_decision_time_matches_integer_nanosecond_frame_clock(self) -> None:
        self.assertEqual(online.decision_time_us(0), Decimal("2000"))
        self.assertEqual(online.decision_time_us(1), Decimal("35333.333"))
        self.assertEqual(online.decision_time_us(2), Decimal("68666.667"))
        self.assertEqual(online.decision_time_us(3), Decimal("102000"))
        with self.assertRaisesRegex(online.OnlineAllocatorError, "frame ID"):
            online.decision_time_us(-1)

    def test_shadow_curve_queries_exact_two_cost_prefix(self) -> None:
        curve = online.ShadowCurve(
            density_descending=np.asarray([10.0, 8.0, 5.0]),
            cumulative_p_frames=np.asarray([1, 2, 2]),
            cumulative_i_frames=np.asarray([0, 0, 1]),
            p_cost_us=Decimal("2"),
            i_cost_us=Decimal("5"),
            training_run_count=1,
        )
        self.assertTrue(math.isinf(curve.opportunity_cost(Decimal("1"))))
        self.assertEqual(curve.opportunity_cost(Decimal("2")), 10.0)
        self.assertEqual(curve.opportunity_cost(Decimal("4")), 8.0)
        self.assertEqual(curve.opportunity_cost(Decimal("9")), 0.0)

    def test_shadow_curve_scales_budget_per_training_run(self) -> None:
        curve = online.ShadowCurve(
            density_descending=np.asarray([4.0, 3.0, 2.0, 1.0]),
            cumulative_p_frames=np.asarray([1, 2, 3, 4]),
            cumulative_i_frames=np.zeros(4, dtype=int),
            p_cost_us=Decimal("2"),
            i_cost_us=Decimal("5"),
            training_run_count=2,
        )
        self.assertEqual(curve.opportunity_cost(Decimal("2")), 3.0)

    def test_regime_boundaries_are_stable(self) -> None:
        cutpoints = (0.2, 0.6)
        self.assertEqual(online._regime(0.1, cutpoints), 0)
        self.assertEqual(online._regime(0.2, cutpoints), 1)
        self.assertEqual(online._regime(0.59, cutpoints), 1)
        self.assertEqual(online._regime(0.6, cutpoints), 2)

    def test_reference_cdf_parser_enforces_probability_contract(self) -> None:
        variant = "primary_hgb64"
        row = {}
        for arm in (0, 1):
            for index, threshold in enumerate(online.crossfit.THRESHOLDS_US):
                row[f"{variant}__arm{arm}_cdf_{threshold}us"] = str(
                    0.1 * (index + 1)
                )
        parsed = online._parse_reference_cdf(row, variant, "fixture")
        self.assertEqual(parsed.shape, (2, 5))
        row[f"{variant}__arm0_cdf_18000us"] = "0.01"
        with self.assertRaisesRegex(
            online.OnlineAllocatorError, "nonmonotone"
        ):
            online._parse_reference_cdf(row, variant, "fixture")

    def test_reference_training_boundary_excludes_evaluation_fold(self) -> None:
        data = SimpleNamespace(
            seeds=np.asarray([1, 2]),
            folds=np.asarray([0, 1]),
        )
        with self.assertRaisesRegex(
            online.OnlineAllocatorError, "training-score boundary"
        ):
            online.build_shadow_curves(
                data,
                None,
                np.asarray([0.5, 0.5]),
                evaluation_fold=0,
                frame_gate="p_frames_only",
                regime_mode="global",
            )

    def test_shadow_curve_tie_break_prioritizes_frame_id(self) -> None:
        units = ((20, 0), (10, 0), (1, 0))
        data = SimpleNamespace(
            seeds=np.asarray([20, 10, 1]),
            run_numbers=np.zeros(3, dtype=int),
            frame_ids=np.asarray([0, 1, 2]),
            folds=np.ones(3, dtype=int),
            frame_types=("I_FRAME", "P_FRAME", "P_FRAME"),
            canonical_cost_us=np.ones(3),
        )
        context = online.AllocatorContext(
            decision_times=np.zeros(3),
            time_bins=np.zeros(3, dtype=int),
            indices_by_unit={unit: np.asarray([index]) for index, unit in enumerate(units)},
            training_units_by_fold={0: units},
            regime_units_by_fold_bin={},
            cutpoints_by_fold_bin={
                (0, time_bin): (0.2, 0.6)
                for time_bin in range(online.TIME_BIN_COUNT)
            },
            p_cost_us=Decimal("1"),
            i_cost_us=Decimal("1"),
        )
        rewards = np.ones(3)
        curves, _ = online.build_shadow_curves(
            data,
            context,
            rewards,
            evaluation_fold=0,
            frame_gate="all_frames_cost_aware",
            regime_mode="global",
        )
        self.assertEqual(curves[(0, 0)].cumulative_i_frames.tolist(), [1, 1, 1])

    def test_primary_report_and_figure_render_selected_predictor(self) -> None:
        static_comparator = {
            "primary_miss_capture_fraction": 0.8,
            "dr_policy_deadline_miss_probability": 0.004,
            "dr_policy_completed_late18_ratio": 0.01,
            "mean_canonical_reservation_us_per_run": 300_000.0,
        }
        policies = []
        for index, regime in enumerate(online.REGIME_MODES):
            policies.append(
                {
                    "variant": "primary_hgb64",
                    "regime_mode": regime,
                    "objective": "deadline_rescue",
                    "frame_gate": "p_frames_only",
                    "action_count": 100 + index,
                    "captured_primary_deadline_misses": 80 + index,
                    "primary_miss_capture_fraction": 0.7 + 0.01 * index,
                    "dr_policy_deadline_miss_probability": 0.005 - 0.001 * index,
                    "dr_policy_completed_late18_ratio": 0.012 - 0.001 * index,
                    "mean_canonical_reservation_us_per_run": 280_000.0,
                    "gap_from_static_372ms": {
                        "captured_primary_deadline_misses": -10 + index
                    },
                    "static_372ms_comparator": static_comparator,
                }
            )
        result = {
            "selected_predictor": {"variant": "primary_hgb64"},
            "policies": policies,
            "interpretation_limits": ["fixture only"],
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.md"
            figure = Path(directory) / "figure.png"
            online.write_report(report, result)
            online.write_figure(figure, result)
            self.assertIn("primary_hgb64", report.read_text(encoding="utf-8"))
            self.assertGreater(figure.stat().st_size, 1_000)

    def test_frozen_online_contract_matches_tool(self) -> None:
        contract = online._validate_contract()
        self.assertEqual(contract["analysis_id"], online.ANALYSIS_ID)


if __name__ == "__main__":
    unittest.main()
