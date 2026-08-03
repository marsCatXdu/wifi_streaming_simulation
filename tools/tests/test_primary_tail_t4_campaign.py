#!/usr/bin/env python3
"""Checks for the neutral staged T0/T4 development campaign."""

from __future__ import annotations

import collections
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from run_experiments import (
    canonical_json,
    cli_arguments,
    derive_run_id,
    expand_config,
    load_yaml,
)


CONFIG = ROOT / "experiments/configs/closed_loop_primary_tail_t4_campaign_v1.yaml"
PREFLIGHT_CONFIG = (
    ROOT / "experiments/configs/closed_loop_primary_tail_t4_preflight_v1.yaml"
)
PARENT_CONFIG = ROOT / "experiments/configs/closed_loop_primary_risk_mlo_005.yaml"
T4_GATES = {
    "0.07905991394024306",
    "0.04585237128013217",
    "0.030390547162877975",
    "0.02073164341662734",
}
BALANCED_GATE = "0.04585237128013217"


def offset_prices(value: str) -> dict[int, str]:
    """Parse a campaign offset-price string without losing decimal identity."""
    return {
        int(offset): price
        for item in value.split(",")
        for offset, price in [item.split(":", 1)]
    }


class PrimaryTailT4CampaignConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_yaml(CONFIG)
        cls.parent = load_yaml(PARENT_CONFIG)
        cls.specs = expand_config(cls.document)

    def test_matrix_has_84_unique_paired_runs(self) -> None:
        self.assertEqual(
            self.document["name"], "closed-loop-primary-tail-t4-campaign-v1"
        )
        self.assertEqual(self.document["workers"], 64)
        self.assertEqual(self.document["runs"], [1])
        self.assertEqual({spec["seed"] for spec in self.specs}, set(range(43, 55)))
        self.assertEqual(len(self.specs), 84)

        counts = collections.Counter(
            (spec["config"]["topology"], spec["config"]["policy"])
            for spec in self.specs
        )
        self.assertEqual(
            counts,
            {
                ("dual_interface", "adaptive_airtime_duplication"): 48,
                ("dual_interface", "adaptive_deficit_duplication"): 12,
                ("mlo_str", "fixed_link_0"): 12,
                ("mlo_emlsr", "fixed_link_0"): 12,
            },
        )
        run_ids = {
            derive_run_id(
                spec["config"], spec["seed"], spec["run"], "ns3", "project"
            )
            for spec in self.specs
        }
        self.assertEqual(len(run_ids), len(self.specs))

    def test_campaign_preserves_neutral_environment_and_only_mlo_baselines(self) -> None:
        self.assertEqual(self.document["base"], self.parent["base"])
        self.assertEqual(self.document["base"]["obss"]["obss_profile"], "mixed4x4")
        self.assertEqual(self.document["base"]["wifi"]["mlo_sta_max_inflights"], 1)

        policies = self.document["policies"]
        self.assertEqual(
            {policy["name"] for policy in policies},
            {
                "adaptive_airtime_duplication",
                "adaptive_deficit_duplication",
                "fixed_link_0",
            },
        )
        baseline = [policy for policy in policies if policy["name"] == "fixed_link_0"]
        self.assertEqual(len(baseline), 1)
        self.assertEqual(baseline[0]["topologies"], ["mlo_str", "mlo_emlsr"])

    def test_preflight_is_the_same_seven_arms_for_seed_43_only(self) -> None:
        document = load_yaml(PREFLIGHT_CONFIG)
        specs = expand_config(document)
        expected = [spec for spec in self.specs if spec["seed"] == 43]

        self.assertEqual(
            document["name"], "closed-loop-primary-tail-t4-preflight-v1"
        )
        self.assertEqual(document["workers"], 7)
        self.assertEqual(document["seeds"], [43])
        self.assertEqual(len(specs), 7)
        self.assertEqual({(spec["seed"], spec["run"]) for spec in specs}, {(43, 1)})
        self.assertEqual(
            sorted(canonical_json(spec["config"]) for spec in specs),
            sorted(canonical_json(spec["config"]) for spec in expected),
        )
        run_ids = {
            derive_run_id(
                spec["config"], spec["seed"], spec["run"], "ns3", "project"
            )
            for spec in specs
        }
        self.assertEqual(len(run_ids), 7)

    def test_four_full_copy_gates_and_balanced_iso_arm_are_exact(self) -> None:
        adaptive = [
            spec
            for spec in self.specs
            if spec["config"]["topology"] == "dual_interface"
        ]
        gates = collections.Counter()
        balanced_full = []
        balanced_deficit = []
        for spec in adaptive:
            config = spec["config"]
            prediction = config["prediction"]
            prices = offset_prices(
                prediction["adaptive_airtime_decision_offset_shadow_prices"]
            )
            self.assertEqual(prices[0], "0.034")
            self.assertIn(prices[4000], T4_GATES)
            gates[(config["policy"], prices[4000])] += 1
            if prices[4000] == BALANCED_GATE:
                if config["policy"] == "adaptive_airtime_duplication":
                    balanced_full.append(config)
                else:
                    balanced_deficit.append(config)

        self.assertEqual(
            gates,
            {
                ("adaptive_airtime_duplication", gate): 12 for gate in T4_GATES
            }
            | {("adaptive_deficit_duplication", BALANCED_GATE): 12},
        )
        self.assertEqual(len(balanced_full), 12)
        self.assertEqual(len(balanced_deficit), 12)
        for full, deficit in zip(balanced_full, balanced_deficit):
            self.assertEqual(full["prediction"] | {
                "adaptive_airtime_admission_packet_cost": "whole_copy"
            }, deficit["prediction"])

    def test_every_staged_arm_has_the_frozen_common_settings(self) -> None:
        for spec in self.specs:
            config = spec["config"]
            if not config["policy"].startswith("adaptive_"):
                continue
            prediction = config["prediction"]
            self.assertTrue(prediction["prediction_telemetry_enabled"])
            self.assertEqual(prediction["prediction_sample_offsets_us"], [0, 4000])
            self.assertEqual(
                prediction["prediction_history_windows_us"], [1000, 5000, 20000]
            )
            self.assertEqual(prediction["prediction_polling_interval_us"], 1000)
            self.assertEqual(prediction["prediction_polling_report_delay_us"], 1000)
            self.assertFalse(prediction["prediction_event_log_enabled"])
            self.assertFalse(prediction["prediction_oracle_features_enabled"])
            self.assertTrue(prediction["secondary_airtime_meter_enabled"])
            self.assertFalse(
                prediction["adaptive_airtime_admission_uses_retry_inflation"]
            )
            self.assertEqual(prediction["adaptive_airtime_budget_fraction"], 0.02)
            self.assertEqual(
                prediction["adaptive_airtime_bucket_horizon_us"], 10_000_000
            )
            self.assertEqual(
                prediction["adaptive_airtime_initial_bucket_horizon_us"], 2_000_000
            )
            self.assertEqual(prediction["adaptive_airtime_initial_shadow_price"], 0.034)
            self.assertEqual(prediction["adaptive_airtime_dual_step"], 0.0)
            self.assertEqual(prediction["adaptive_airtime_cost_safety_factor"], 1.25)
            self.assertEqual(prediction["adaptive_airtime_cost_ewma_alpha"], 0.1)
            self.assertEqual(
                prediction["adaptive_airtime_decision_offsets_us"], [0, 4000]
            )
            self.assertEqual(
                prediction["adaptive_airtime_i_frame_only_decision_offsets_us"], [0]
            )

            arguments = cli_arguments(config, CONFIG.parent)
            self.assertIn("--predictionSampleOffsetsUs=0,4000", arguments)
            self.assertIn("--adaptiveAirtimeDecisionOffsetsUs=0,4000", arguments)
            self.assertIn(
                "--adaptiveAirtimeIFrameOnlyDecisionOffsetsUs=0", arguments
            )
            self.assertIn(
                "--adaptiveAirtimeDecisionOffsetShadowPrices="
                + prediction["adaptive_airtime_decision_offset_shadow_prices"],
                arguments,
            )


if __name__ == "__main__":
    unittest.main()
