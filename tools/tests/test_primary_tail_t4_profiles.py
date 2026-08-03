from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from evaluate_primary_tail_t4_profiles import (
    apply_raw_joint_gate,
    estimate_primary_deficit_airtime_us,
    load_profile_config,
    replay_joint_t0_i_t4,
    select_joint_t4_threshold,
    stabilize_strict_threshold,
)


class CostTests(unittest.TestCase):
    def test_deficit_estimator_prices_only_unacknowledged_packets(self) -> None:
        estimator = {
            "payload_bytes_per_packet": 1200,
            "cost_safety_factor": 1.25,
            "retry_inflation": 1.0,
            "phy_preamble_us": 48.0,
            "phy_data_rate_bps": 68823530,
            "streaming_header_bytes": 50,
            "expected_mac_service_overhead_bytes": 36,
            "additional_airtime_bytes_per_packet": 38,
        }
        observed = estimate_primary_deficit_airtime_us(
            np.asarray([12000, 12000]),
            np.asarray([10, 10]),
            np.asarray([8, 10]),
            estimator,
        )
        self.assertAlmostEqual(observed[0], 444.752133463657)
        self.assertEqual(observed[1], 0.0)


class SelectionTests(unittest.TestCase):
    def test_joint_threshold_charges_t0_before_selecting_t4(self) -> None:
        threshold, audit = select_joint_t4_threshold(
            partition=np.ones(3, dtype=bool),
            t0_actionable=np.ones(3, dtype=bool),
            t0_frame_type=np.asarray(["I_FRAME", "P_FRAME", "P_FRAME"]),
            t0_probability=np.asarray([0.9, 0.0, 0.0]),
            t0_cost_us=np.asarray([8.0, 8.0, 8.0]),
            t4_actionable=np.ones(3, dtype=bool),
            t4_admission_score=np.asarray([0.9, 0.8, 0.7]),
            t4_admission_cost_us=np.asarray([4.0, 4.0, 4.0]),
            t0_threshold=0.1,
            reference_cost_us=1.0,
            absolute_budget_us=12.0,
        )
        self.assertEqual(audit["raw_t0_i_gate_crossings"], 1)
        self.assertEqual(audit["residual_t4_gate_crossings"], 1)
        self.assertEqual(audit["combined_planned_cost_us"], 12.0)
        self.assertEqual(audit["selection_boundary_risk_density"], 0.175)
        self.assertEqual(audit["maximum_rejected_risk_density"], 0.175)
        self.assertEqual(audit["minimum_selected_risk_density"], 0.2)
        self.assertEqual(threshold, 0.1875)
        self.assertTrue(audit["selection_action_mask_unchanged"])

    def test_stabilized_gate_rejects_a_last_bit_backend_flip(self) -> None:
        rejected = 0.07903474843584872
        selected = 0.07919705203207761
        threshold, audit = stabilize_strict_threshold(
            np.asarray([rejected, selected]), np.asarray([False, True])
        )
        deployment = np.asarray([np.nextafter(rejected, np.inf), selected])
        np.testing.assert_array_equal(deployment > threshold, [False, True])
        self.assertTrue(deployment[0] > rejected)
        self.assertGreater(audit["margin_above_maximum_rejected"], 0)
        self.assertGreater(audit["margin_below_minimum_selected"], 0)

    def test_stabilization_rejects_a_tied_selected_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "no open gap"):
            stabilize_strict_threshold(
                np.asarray([0.2, 0.2]), np.asarray([False, True])
            )

    def test_selector_and_replay_have_no_label_argument(self) -> None:
        self.assertNotIn("label", inspect.signature(select_joint_t4_threshold).parameters)
        self.assertNotIn("label", inspect.signature(apply_raw_joint_gate).parameters)
        self.assertNotIn("label", inspect.signature(replay_joint_t0_i_t4).parameters)

    def test_raw_gate_resolves_t0_before_t4(self) -> None:
        t0_action, t4_action, audit = apply_raw_joint_gate(
            partition=np.ones(2, dtype=bool),
            t0_actionable=np.ones(2, dtype=bool),
            t0_frame_type=np.asarray(["I_FRAME", "P_FRAME"]),
            t0_probability=np.asarray([0.9, 0.0]),
            t0_cost_us=np.asarray([2.0, 2.0]),
            t4_actionable=np.ones(2, dtype=bool),
            t4_admission_score=np.asarray([0.9, 0.9]),
            t4_admission_cost_us=np.asarray([2.0, 2.0]),
            t0_threshold=0.1,
            t4_threshold=0.1,
            reference_cost_us=1.0,
        )
        np.testing.assert_array_equal(t0_action, [True, False])
        np.testing.assert_array_equal(t4_action, [False, True])
        self.assertEqual(audit["combined"]["actions"], 2)

    def test_shared_bucket_can_defer_rejected_t0_frame_to_t4(self) -> None:
        t0_action, t4_action, audit = replay_joint_t0_i_t4(
            group=np.asarray(["a", "a"], dtype=object),
            frame_id=np.asarray([0, 1]),
            partition=np.ones(2, dtype=bool),
            t0_sample_time_ns=np.asarray([0, 1000]),
            t0_actionable=np.ones(2, dtype=bool),
            t0_frame_type=np.asarray(["I_FRAME", "P_FRAME"]),
            t0_probability=np.asarray([0.9, 0.0]),
            t0_cost_us=np.asarray([20.0, 2.0]),
            t4_sample_time_ns=np.asarray([100_000, 200_000]),
            t4_actionable=np.ones(2, dtype=bool),
            t4_admission_score=np.asarray([0.9, 0.9]),
            t4_admission_cost_us=np.asarray([4.0, 4.0]),
            t0_threshold=0.01,
            t4_threshold=0.01,
            reference_cost_us=1.0,
            budget_fraction=0.1,
            bucket_horizon_us=100,
            initial_bucket_horizon_us=100,
        )
        np.testing.assert_array_equal(t0_action, [False, False])
        np.testing.assert_array_equal(t4_action, [True, True])
        self.assertEqual(audit["t0_i"]["bucket_rejections"], 1)
        self.assertEqual(audit["combined"]["actions"], 2)


class FrozenConfigurationTests(unittest.TestCase):
    def test_repository_profile_is_full_copy_primary_and_iso_deficit(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_tail_t4_operating_profiles_v1.yaml"
        config = load_profile_config(path)
        full = config["profiles"]["full_copy_primary"]
        deficit = config["profiles"]["primary_deficit_iso_ablation"]
        self.assertEqual(full["admission_cost_mode"], "whole_secondary_copy")
        self.assertEqual(
            full["absolute_nominal_budget_fractions"],
            [0.0055, 0.008, 0.01, 0.012],
        )
        self.assertEqual(
            full["candidate_designations"],
            {"balanced": 0.008, "strong_tail": 0.01},
        )
        self.assertEqual(deficit["admission_cost_mode"], "whole_secondary_copy")
        self.assertEqual(deficit["decision_source"], "full_copy_primary_same_budget")
        self.assertEqual(full["token_bucket_fraction"], 0.02)
        self.assertEqual(deficit["token_bucket_fraction"], 0.02)
        runtime = config["model_artifact"]["runtime_export"]
        self.assertEqual(
            runtime["export_sha256"],
            "164e87a3c4649f35d70fa1a3f3826211da0ae6590eeb84eefac8a45307ef885c",
        )
        self.assertEqual(
            runtime["feature_contract_sha256"],
            "8ccf33d6af8dffb8da758016acbd809a7cc054be4a1abc070d129c788b9c7cb0",
        )
        self.assertEqual(
            runtime["export_manifest_sha256"],
            "6159ace30648e3ea00116628f05155d120423201fda911e84b72f1122a826870",
        )
        self.assertEqual(
            runtime["combiner_sha256"],
            "3d47b994ef5fcf579c73fb74492e0293dfe3ba377911f72f7a6b5fe764e6d9e0",
        )
        population = config["expected_diagnostics"]["outcome_population"]
        self.assertEqual(population["source_seed_count"], 24)
        self.assertEqual(population["primary_miss_count"], 637)
        self.assertEqual(population["completed_tail_count"], 2006)
        expected_thresholds = {
            "0.0055": 0.07905991394024306,
            "0.008": 0.04585237128013217,
            "0.01": 0.030390547162877975,
            "0.012": 0.02073164341662734,
        }
        for budget, diagnostics in config["expected_diagnostics"][
            "full_copy_primary"
        ].items():
            self.assertEqual(
                diagnostics["risk_density_threshold"], expected_thresholds[budget]
            )
            self.assertTrue(diagnostics["selection_action_mask_unchanged"])
            self.assertTrue(diagnostics["stabilization_action_mask_unchanged"])
            self.assertFalse(diagnostics["stabilization_uses_labels"])
            self.assertEqual(
                diagnostics["calibration_runtime_gate_mask_difference_count"], 0
            )
            self.assertEqual(
                diagnostics["all_engineering_runtime_gate_mask_difference_count"], 0
            )
            self.assertGreater(diagnostics["margin_above_maximum_rejected"], 0)
            self.assertGreater(diagnostics["margin_below_minimum_selected"], 0)
            self.assertIn("raw_admission_priced_cost_fraction", diagnostics)
            self.assertIn("realized_admission_priced_cost_fraction", diagnostics)
            self.assertIn("realized_bucket_rejections", diagnostics)
            self.assertIn("raw_primary_miss_selected", diagnostics)
            self.assertIn("raw_completed_tail_selected", diagnostics)
            self.assertIn("raw_i_actions", diagnostics)
            self.assertIn("raw_p_actions", diagnostics)
            self.assertIn("realized_primary_miss_selected", diagnostics)
            self.assertIn("realized_completed_tail_selected", diagnostics)
            self.assertIn("realized_i_actions", diagnostics)
            self.assertIn("realized_p_actions", diagnostics)

    def test_rejects_unknown_top_level_section(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_tail_t4_operating_profiles_v1.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["hidden_policy"] = {}
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed.yaml"
            changed.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown keys"):
                load_profile_config(changed)

    def test_rejects_unknown_nested_runtime_export_key(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_tail_t4_operating_profiles_v1.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["model_artifact"]["runtime_export"]["hidden_adapter"] = "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed.yaml"
            changed.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "runtime export identity"):
                load_profile_config(changed)


if __name__ == "__main__":
    unittest.main()
