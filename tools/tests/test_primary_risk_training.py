from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from train_primary_risk_t0 import (
    assign_group_roles,
    estimate_whole_copy_airtime_us,
    load_config,
    primary_miss_from_fixed_frame,
    select_risk_density_threshold,
)


def fixed_frame() -> dict[str, str]:
    return {
        "generation_time_us": "1000",
        "deadline_us": "100",
        "primary_link": "1",
        "duplicated": "0",
        "union_completion_us": "1090",
        "copy_0_completion_us": "1090",
        "copy_1_completion_us": "",
        "deadline_miss": "0",
    }


class PrimaryLabelTests(unittest.TestCase):
    def test_derives_inclusive_deadline_primary_label(self) -> None:
        row = fixed_frame()
        row["union_completion_us"] = "1100"
        row["copy_0_completion_us"] = "1100"
        self.assertEqual(primary_miss_from_fixed_frame(row, 1), 0)
        row["union_completion_us"] = "1101"
        row["copy_0_completion_us"] = "1101"
        row["deadline_miss"] = "1"
        self.assertEqual(primary_miss_from_fixed_frame(row, 1), 1)

    def test_rejects_union_or_treatment_contamination(self) -> None:
        row = fixed_frame()
        row["union_completion_us"] = "1080"
        with self.assertRaisesRegex(ValueError, "copy-0 and union"):
            primary_miss_from_fixed_frame(row, 1)
        row = fixed_frame()
        row["duplicated"] = "1"
        with self.assertRaisesRegex(ValueError, "duplication treatment"):
            primary_miss_from_fixed_frame(row, 1)


class SplitTests(unittest.TestCase):
    def test_group_assignment_is_order_independent_and_balanced(self) -> None:
        config = {
            "seed": "primary-risk-t0-v1",
            "fold_count": 4,
            "role_by_fold": ["test", "calibration", "training", "training"],
        }
        groups = [f"group-{index}" for index in range(24)]
        forward, entries = assign_group_roles(groups, config)
        reverse, _ = assign_group_roles(list(reversed(groups)), config)
        self.assertEqual(forward, reverse)
        self.assertEqual(len(entries), 24)
        self.assertEqual(list(forward.values()).count("training"), 12)
        self.assertEqual(list(forward.values()).count("calibration"), 6)
        self.assertEqual(list(forward.values()).count("test"), 6)


class AirtimeOperatingPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.estimator = {
            "cost_safety_factor": 1.25,
            "retry_inflation": 1.0,
            "phy_preamble_us": 48.0,
            "phy_data_rate_bps": 68823530,
            "streaming_header_bytes": 50,
            "expected_mac_service_overhead_bytes": 36,
            "additional_airtime_bytes_per_packet": 38,
        }

    def test_matches_controller_p_and_i_estimates(self) -> None:
        observed = estimate_whole_copy_airtime_us(
            np.asarray([12000, 48000]), np.asarray([10, 40]), self.estimator
        )
        np.testing.assert_allclose(
            observed,
            [1983.760667318285, 7755.04266927314],
            rtol=1e-12,
            atol=1e-9,
        )

    def test_density_selector_respects_budget_and_strict_ties(self) -> None:
        probability = np.asarray([0.9, 0.8, 0.8, 0.2])
        normalized = np.ones(4)
        costs = np.asarray([5.0, 5.0, 5.0, 5.0])
        threshold, action = select_risk_density_threshold(
            probability, normalized, costs, budget_us=10.0
        )
        # Strict gating cannot select one member of the tied 0.8 pair, so the
        # largest admissible prefix contains only the 0.9 candidate.
        self.assertEqual(threshold, 0.8)
        np.testing.assert_array_equal(action, [True, False, False, False])

    def test_density_selector_has_no_label_input(self) -> None:
        probability = np.asarray([0.9, 0.7, 0.3])
        normalized = np.asarray([1.0, 1.0, 1.0])
        costs = np.asarray([2.0, 2.0, 2.0])
        first = select_risk_density_threshold(probability, normalized, costs, 4.0)
        second = select_risk_density_threshold(
            probability.copy(), normalized.copy(), costs.copy(), 4.0
        )
        self.assertEqual(first[0], second[0])
        np.testing.assert_array_equal(first[1], second[1])


class FrozenConfigurationTests(unittest.TestCase):
    def test_repository_configuration_is_valid(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_risk_t0_obss_v1.yaml"
        config = load_config(path)
        self.assertEqual(config["target"]["copy_id"], 0)
        self.assertEqual(config["target"]["selected_policy"], "fixed_link_1")
        self.assertEqual(
            config["risk_density_operating_point"]["estimated_airtime_budget_fractions"],
            [0.005, 0.007, 0.0095],
        )

    def test_rejects_union_target(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_risk_t0_obss_v1.yaml"
        config = load_config(path)
        changed = copy.deepcopy(config)
        changed["target"]["target_id"] = "union_deadline_miss"
        with tempfile.TemporaryDirectory() as directory:
            changed_path = Path(directory) / "changed.yaml"
            changed_path.write_text(yaml.safe_dump(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "target contract"):
                load_config(changed_path)


if __name__ == "__main__":
    unittest.main()
