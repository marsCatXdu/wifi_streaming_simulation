#!/usr/bin/env python3
"""Focused tests for the temporal-T2 shadow-priced borrow/repay screen."""

from __future__ import annotations

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

import analyze_temporal_t2_shadow_borrow as borrow  # noqa: E402


class ShadowBorrowTest(unittest.TestCase):
    def test_credit_decision_preserves_strict_and_repayable_boundaries(
        self,
    ) -> None:
        self.assertEqual(
            borrow.credit_decision(
                Decimal("5"),
                Decimal("10"),
                Decimal("4"),
                "strict_current_credit",
            ),
            "strict_action",
        )
        self.assertEqual(
            borrow.credit_decision(
                Decimal("3"),
                Decimal("10"),
                Decimal("4"),
                "strict_current_credit",
            ),
            "current_credit",
        )
        self.assertEqual(
            borrow.credit_decision(
                Decimal("3"),
                Decimal("1"),
                Decimal("4"),
                "shadow_borrow_repay",
            ),
            "borrowed_action",
        )
        self.assertEqual(
            borrow.credit_decision(
                Decimal("-2"),
                Decimal("5.9"),
                Decimal("4"),
                "shadow_borrow_repay",
            ),
            "horizon_credit",
        )

    def test_credit_decision_rejects_invalid_accounting(self) -> None:
        with self.assertRaisesRegex(borrow.ShadowBorrowError, "invalid"):
            borrow.credit_decision(
                Decimal("0"),
                Decimal("-1"),
                Decimal("1"),
                "shadow_borrow_repay",
            )

    def test_decision_summary_keeps_direct_miss_counts(self) -> None:
        data = SimpleNamespace(
            primary_deadline_miss=np.asarray([1, 0, 1, 0]),
            canonical_cost_us=np.asarray([2.0, 2.0, 5.0, 2.0]),
        )
        rewards = np.asarray([0.8, 0.2, 0.5, 0.1])
        reasons = np.asarray(
            ["strict_action", "opportunity_price", "gate", "strict_action"]
        )
        summary = borrow._decision_summary(data, rewards, reasons)
        self.assertEqual(summary["strict_action"]["frame_count"], 2)
        self.assertEqual(
            summary["strict_action"]["primary_deadline_misses"], 1
        )
        self.assertEqual(summary["gate"]["primary_deadline_misses"], 1)
        self.assertAlmostEqual(
            summary["strict_action"]["mean_predicted_reward"], 0.45
        )

    def test_debt_summary_reports_repayment_boundary(self) -> None:
        details = {
            (1, 1): {
                "maximum_debt_us": 10.0,
                "borrowed_actions": 2,
                "balance_at_measurement_stop_us": 0.0,
            },
            (2, 1): {
                "maximum_debt_us": 0.0,
                "borrowed_actions": 0,
                "balance_at_measurement_stop_us": 4.0,
            },
        }
        summary = borrow._debt_summary(details)
        self.assertEqual(summary["runs_with_debt"], 1)
        self.assertEqual(summary["maximum_debt_us"], 10.0)
        self.assertEqual(summary["minimum_final_balance_us"], 0.0)

    def test_transition_summary_exposes_high_value_substitution(self) -> None:
        data = SimpleNamespace(
            seeds=np.asarray([1, 1, 1, 1]),
            primary_deadline_miss=np.asarray([1, 0, 1, 0]),
            frame_ids=np.asarray([10, 20, 30, 40]),
            canonical_cost_us=np.full(4, 2.0),
        )
        rewards = np.asarray([0.9, 0.2, 0.8, 0.1])
        baseline = np.asarray([False, True, False, True])
        candidate = np.asarray([True, True, True, False])
        summary = borrow._transition_summary(
            data, rewards, baseline, candidate
        )
        self.assertEqual(summary["common"]["frame_count"], 1)
        self.assertEqual(summary["strict_only"]["primary_deadline_misses"], 0)
        self.assertEqual(summary["borrow_only"]["primary_deadline_misses"], 2)
        self.assertEqual(summary["borrow_only"]["median_frame_id"], 20.0)

    def test_report_and_figure_render_both_credit_modes(self) -> None:
        static_comparator = {
            "primary_miss_capture_fraction": 0.8,
            "dr_policy_deadline_miss_probability": 0.005,
            "dr_policy_completed_late18_ratio": 0.018,
            "mean_canonical_reservation_us_per_run": 370_000.0,
        }
        policies = []
        for regime_index, regime in enumerate(borrow.online.REGIME_MODES):
            for mode_index, mode in enumerate(borrow.CREDIT_MODES):
                outcomes = {
                    reason: {
                        "frame_count": 1 if reason.endswith("action") else 2,
                        "primary_deadline_misses": (
                            1 if reason.endswith("action") else 0
                        ),
                    }
                    for reason in borrow.REASONS
                }
                policies.append(
                    {
                        "regime_mode": regime,
                        "credit_mode": mode,
                        "action_count": 100 + regime_index + mode_index,
                        "captured_primary_deadline_misses": 70 + mode_index,
                        "primary_miss_capture_fraction": 0.7 + 0.05 * mode_index,
                        "dr_policy_deadline_miss_probability": (
                            0.01 - 0.002 * mode_index
                        ),
                        "dr_policy_completed_late18_ratio": (
                            0.02 - 0.001 * mode_index
                        ),
                        "mean_canonical_reservation_us_per_run": 300_000.0,
                        "decision_outcomes": outcomes,
                        "debt": {
                            "maximum_debt_us": 10_000.0 * mode_index
                        },
                        "static_372ms_comparator": static_comparator,
                    }
                )
        result = {
            "policies": policies,
            "engineering_screen": {
                "overall_pass": True,
                "checks": {
                    "fixture": {"actual": 1.0, "pass": True}
                },
            },
            "interpretation_limits": ["fixture only"],
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.md"
            figure = Path(directory) / "figure.png"
            borrow.write_report(report, result)
            borrow.write_figure(figure, result)
            text = report.read_text(encoding="utf-8")
            self.assertIn("shadow_borrow_repay", text)
            self.assertIn("PASS", text)
            self.assertGreater(figure.stat().st_size, 1_000)

    def test_frozen_contract_matches_tool(self) -> None:
        contract = borrow._validate_contract()
        self.assertEqual(contract["analysis_id"], borrow.ANALYSIS_ID)


if __name__ == "__main__":
    unittest.main()
