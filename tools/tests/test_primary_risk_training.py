from __future__ import annotations

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from train_primary_risk_t0 import (
    _audit_source_frames,
    assign_group_roles,
    estimate_whole_copy_airtime_us,
    load_config,
    physical_feature_names,
    preserved_predictor_fingerprints,
    primary_miss_from_fixed_frame,
    select_risk_density_threshold,
    sha256_file,
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


class SourceAuditTests(unittest.TestCase):
    def _fixture(
        self,
        directory: Path,
        frame_run_id: str = "run",
        resolved_run_id: str = "run",
    ) -> tuple[dict, dict]:
        row = {**fixed_frame(), "run_id": frame_run_id, "frame_id": "0"}
        frame_path = directory / "frames.csv"
        with frame_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        resolved_path = directory / "resolved_config.json"
        resolved_path.write_text(
            json.dumps(
                {
                    "run_id": resolved_run_id,
                    "policy": "fixed_link_1",
                    "duration_s": 60,
                    "topology": "dual_interface",
                    "predictionTelemetry": {"telemetry_schema_version": 3},
                }
            ),
            encoding="utf-8",
        )
        build_path = directory / "build_info.json"
        build_path.write_text(
            json.dumps(
                {
                    "project_git_commit": "project",
                    "ns3_upstream_commit": "upstream",
                }
            ),
            encoding="utf-8",
        )
        manifest = {
            "included_runs": [
                {
                    "run_id": "run",
                    "run_group_id": "group",
                    "scenario_name": "obss_only",
                    "selected_policy": "fixed_link_1",
                    "selected_path": 1,
                    "source_directory": str(directory),
                    "frame_count": 1,
                    "miss_count": 0,
                }
            ],
            "source_checksums": {
                "run": {
                    "frames.csv": sha256_file(frame_path),
                    "resolved_config.json": sha256_file(resolved_path),
                    "build_info.json": sha256_file(build_path),
                }
            },
        }
        config = {
            "target": {
                "scenario_name": "obss_only",
                "selected_policy": "fixed_link_1",
                "path_id": 1,
            },
            "expected_population": {
                "run_count": 1,
                "run_group_count": 1,
                "frame_count": 1,
                "miss_count": 0,
                "frames_per_run": 1,
                "measurement_duration_s": 60,
            },
            "dataset": {
                "telemetry_schema_versions": [3],
                "project_git_commits": ["project"],
                "ns3_upstream_commits": ["upstream"],
            },
        }
        return manifest, config

    def test_rejects_frame_run_id_different_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, config = self._fixture(Path(temporary), frame_run_id="other")
            with self.assertRaisesRegex(ValueError, "frame run ID"):
                _audit_source_frames(manifest, config)

    def test_rejects_resolved_run_id_different_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, config = self._fixture(Path(temporary), resolved_run_id="other")
            with self.assertRaisesRegex(ValueError, "treatment or duration"):
                _audit_source_frames(manifest, config)


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

    def test_density_selector_admits_all_affordable_positive_risk(self) -> None:
        probability = np.asarray([0.9, 0.8, 0.8])
        normalized = np.ones(3)
        costs = np.asarray([5.0, 5.0, 5.0])
        threshold, action = select_risk_density_threshold(
            probability, normalized, costs, budget_us=15.0
        )
        self.assertEqual(threshold, 0.0)
        np.testing.assert_array_equal(action, [True, True, True])


class ArtifactIntegrityTests(unittest.TestCase):
    def test_polling_columns_replace_ideal_f1_inputs(self) -> None:
        predictor = SimpleNamespace(
            feature_names=("deadline_slack_us", "retry_count_total"),
            f1_feature_names=("retry_count_total",),
        )
        self.assertEqual(
            physical_feature_names(predictor),
            ("deadline_slack_us", "polling_1ms_retry_count_total"),
        )

    def test_preserved_predictor_fingerprints_detect_mutation(self) -> None:
        base = {
            ("pipeline", "T0"): {"value": 0},
            ("pipeline", "T1"): {"value": 1},
        }
        output = copy.deepcopy(base)
        output[("pipeline", "T0")] = {"value": 2}
        fingerprints = preserved_predictor_fingerprints(
            base, output, ("pipeline", "T0")
        )
        self.assertEqual(len(fingerprints), 1)
        output[("pipeline", "T1")]["value"] = 3
        with self.assertRaisesRegex(ValueError, "preserved predictor changed"):
            preserved_predictor_fingerprints(base, output, ("pipeline", "T0"))


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
        self.assertEqual(
            config["dataset"]["manifest_sha256"],
            "cf2d4b5081a407ad20a866d533fd2448102dffa623e52ea76fc95f58f072945d",
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
