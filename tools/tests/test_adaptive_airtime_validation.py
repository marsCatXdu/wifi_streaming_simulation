#!/usr/bin/env python3
"""Adversarial checks for adaptive-airtime output validation."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from validate_outputs import (
    ValidationError,
    _validate_adaptive_config,
    _validate_adaptive_decisions,
    _validate_secondary_airtime,
)


def adaptive_config() -> dict[str, object]:
    return {
        "model_id": "commodity_polling_1ms_genuine_v1",
        "source_model_sha256": "a" * 64,
        "feature_set": "F0+F1-degraded",
        "degradation_profile": "polling_1ms",
        "calibration": "platt",
        "stages": ["T0", "T1"],
        "primary_path": 1,
        "secondary_path": 0,
        "budget_definition": "secondary_sender_phy_tx_airtime",
        "budget_fraction": 0.05,
        "bucket_horizon_us": 200_000,
        "initial_bucket_capacity_us": 10_000.0,
        "initial_shadow_price": 0.2,
        "dual_step": 0.03,
        "cost_safety_factor": 1.4,
        "cost_ewma_alpha": 0.25,
        "decision_offsets_us": [0, 1000],
    }


def adaptive_rows() -> list[dict[str, str]]:
    common = {
        "run_id": "run",
        "frame_id": "7",
        "actionable": "1",
        "estimated_airtime_us": "100",
        "reference_airtime_us": "100",
        "shadow_price": "0.2",
        "normalized_cost": "1",
        "airtime_budget_fraction": "0.05",
        "bucket_capacity_us": "10000",
        "bucket_balance_us": "10000",
        "initial_bucket_capacity_us": "10000",
        "reserved_airtime_us": "0",
        "available_airtime_us": "10000",
        "measured_airtime_total_us": "0",
    }
    return [
        {
            **common,
            "sample_stage": "T0",
            "sample_offset_us": "0",
            "sample_time_ns": "1000000",
            "calibrated_probability": "0.1",
            "net_utility": "-0.1",
            "decision": "price_rejected",
            "secondary_launched": "0",
        },
        {
            **common,
            "sample_stage": "T1",
            "sample_offset_us": "1000",
            "sample_time_ns": "2000000",
            "calibrated_probability": "0.5",
            "net_utility": "0.3",
            "decision": "action",
            "secondary_launched": "1",
        },
    ]


def meter_fixture() -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, object],
    dict[str, object],
    list[dict[str, str]],
]:
    events = [{
        "run_id": "run",
        "time_ns": "2100000",
        "path_id": "0",
        "ppdu_duration_us": "80",
        "tagged_mpdu_bytes": "1200",
        "frame_ids": "7",
        "mixed_ppdu": "0",
        "cumulative_tagged_airtime_us": "80",
    }]
    settlements = [{
        "run_id": "run",
        "frame_id": "7",
        "settlement_time_ns": "3000000",
        "released_airtime_us": "20",
        "measured_airtime_us": "80",
        "nominal_airtime_us": "75",
        "fallback": "0",
    }]
    summary = {
        "tagged_ppdu_count": 1,
        "mixed_ppdu_count": 0,
        "tagged_secondary_tx_airtime_us": 80.0,
        "measurement_start_ns": 0,
        "measurement_stop_ns": 10_000_000,
        "measurement_duration_us": 10_000.0,
        "tagged_secondary_tx_airtime_fraction": 0.008,
        "maximum_budget_debt_us": 0.0,
        "estimated_action_airtime_us": 100.0,
        "actual_to_estimated_airtime_ratio": 0.8,
        "forced_reservation_settlements": 0,
        "budget_fraction": 0.05,
        "initial_bucket_capacity_us": 10_000.0,
        "finite_run_budget_us": 10_500.0,
        "budget_excess_us": 0.0,
    }
    meter = {
        "enabled": True,
        "path_id": 0,
        "copy_id": 1,
        "definition": "secondary_sender_phy_tx_airtime",
        "measurement_start_ns": 0,
        "measurement_stop_ns": 10_000_000,
    }
    links = [{"link_id": "0", "phy_tx_time_us": "100"}]
    return events, settlements, summary, meter, links


class AdaptiveDecisionValidationTest(unittest.TestCase):
    def test_accepts_valid_nondefault_controller_parameters(self) -> None:
        config = adaptive_config()
        self.assertEqual(_validate_adaptive_config(config), [0, 1000])
        estimates = _validate_adaptive_decisions(
            adaptive_rows(), config, [{"frame_id": "7", "generation_time_us": "1000"}], "run"
        )
        self.assertEqual(estimates, {7: 100.0})

    def test_rejects_non_hex_model_hash(self) -> None:
        config = adaptive_config()
        config["source_model_sha256"] = "z" * 64
        with self.assertRaisesRegex(ValidationError, "provenance"):
            _validate_adaptive_config(config)

    def test_rejects_cost_arithmetic_mutation(self) -> None:
        rows = adaptive_rows()
        rows[1]["normalized_cost"] = "0.9"
        with self.assertRaisesRegex(ValidationError, "arithmetic"):
            _validate_adaptive_decisions(
                rows,
                adaptive_config(),
                [{"frame_id": "7", "generation_time_us": "1000"}],
                "run",
            )

    def test_rejects_action_with_insufficient_airtime(self) -> None:
        rows = adaptive_rows()
        rows[1]["bucket_balance_us"] = "50"
        rows[1]["available_airtime_us"] = "50"
        with self.assertRaisesRegex(ValidationError, "action predicate"):
            _validate_adaptive_decisions(
                rows,
                adaptive_config(),
                [{"frame_id": "7", "generation_time_us": "1000"}],
                "run",
            )


class SecondaryAirtimeValidationTest(unittest.TestCase):
    def validate_fixture(
        self,
        events: list[dict[str, str]],
        settlements: list[dict[str, str]],
        summary: dict[str, object],
        meter: dict[str, object],
        links: list[dict[str, str]],
    ) -> None:
        _validate_secondary_airtime(
            events,
            settlements,
            summary,
            meter,
            links,
            "adaptive_airtime_duplication",
            "run",
            adaptive_config(),
            {7: 100.0},
            {7},
            0.0,
        )

    def test_reconciles_valid_event_and_settlement_ledgers(self) -> None:
        self.validate_fixture(*meter_fixture())

    def test_rejects_event_at_exclusive_stop_boundary(self) -> None:
        fixture = list(meter_fixture())
        fixture[0] = copy.deepcopy(fixture[0])
        fixture[0][0]["time_ns"] = "10000000"
        with self.assertRaisesRegex(ValidationError, "half-open"):
            self.validate_fixture(*fixture)

    def test_rejects_released_reservation_mutation(self) -> None:
        fixture = list(meter_fixture())
        fixture[1] = copy.deepcopy(fixture[1])
        fixture[1][0]["released_airtime_us"] = "19"
        with self.assertRaisesRegex(ValidationError, "released reservation"):
            self.validate_fixture(*fixture)


if __name__ == "__main__":
    unittest.main()
