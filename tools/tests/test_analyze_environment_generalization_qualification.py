#!/usr/bin/env python3
"""Tests for held-out environment qualification analysis."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import run_experiments  # noqa: E402
from analyze_environment_generalization_qualification import (  # noqa: E402
    ARM_IDS,
    CONFIG_PATH,
    QualificationAnalysisError,
    build_observation_grid,
    build_report,
    evaluate_str_gates,
    hierarchical_bootstrap,
    load_analysis_contract,
    validate_campaign_manifest,
)


class EnvironmentGeneralizationQualificationAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_analysis_contract()
        cls.families = tuple(f"family-{index}" for index in range(6))
        cls.scenarios = {
            family: tuple(f"{family}-scenario-{index}" for index in range(8))
            for family in cls.families
        }

    def _grid(self) -> dict[str, dict[str, list[dict[str, dict[str, object]]]]]:
        grid: dict[str, dict[str, list[dict[str, dict[str, object]]]]] = {}
        for family_index, family in enumerate(self.families):
            grid[family] = {}
            for scenario_index, scenario in enumerate(self.scenarios[family]):
                units = []
                for replicate in range(4):
                    baseline_miss = (
                        0.10
                        + 0.01 * family_index
                        + 0.001 * scenario_index
                        + 0.0001 * replicate
                    )
                    common = {
                        "family_id": family,
                        "scenario_id": scenario,
                        "parameter_sample": scenario_index,
                        "seed": 10000 + 100 * family_index + 4 * scenario_index + replicate,
                        "run": 1,
                    }
                    units.append(
                        {
                            "str_mlo_nmaxinflights_1": {
                                **common,
                                "all_generated_deadline_miss_rate": baseline_miss,
                                "completed_frame_hf7_p99_us": 20_000.0,
                                "sender_airtime_us": 300_000.0,
                                "background_throughput_mbps": 50.0,
                            },
                            "score_aware_t2_v2": {
                                **common,
                                "all_generated_deadline_miss_rate": baseline_miss / 2,
                                "completed_frame_hf7_p99_us": 16_000.0,
                                "sender_airtime_us": 345_000.0,
                                "background_throughput_mbps": 49.75,
                            },
                            "distributional_shadow_t2": {
                                **common,
                                "all_generated_deadline_miss_rate": baseline_miss * 0.75,
                                "completed_frame_hf7_p99_us": 18_000.0,
                                "sender_airtime_us": 330_000.0,
                                "background_throughput_mbps": 49.9,
                            },
                        }
                    )
                grid[family][scenario] = units
        return grid

    def test_contract_freezes_population_estimand_and_oracle_boundary(self) -> None:
        self.assertEqual(self.contract["population"]["simulation_run_count"], 576)
        self.assertEqual(
            self.contract["estimand"]["point_weighting"],
            "equal_family_then_equal_scenario_then_equal_replicate",
        )
        self.assertEqual(self.contract["bootstrap"]["replications"], 10_000)
        self.assertEqual(
            self.contract["oracle_estimand_boundary"]
            ["fraction_of_oracle_deadline_gain_realized"]["status"],
            "not_assessable",
        )
        self.assertIn("completion_latency_cdf", self.contract["plots"])
        self.assertIn("completion_latency_pdf", self.contract["plots"])

    def test_shared_hierarchical_bootstrap_is_deterministic(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["bootstrap"]["replications"] = 120
        result_a = hierarchical_bootstrap(
            self._grid(), self.families, self.scenarios, contract
        )
        result_b = hierarchical_bootstrap(
            self._grid(), self.families, self.scenarios, contract
        )
        self.assertEqual(
            result_a["shared_draw_sha256"], result_b["shared_draw_sha256"]
        )
        comparison = result_a["comparison_intervals"][
            "score_aware_t2_v2_minus_str_mlo_nmaxinflights_1"
        ]
        self.assertAlmostEqual(
            comparison["relative_deadline_miss_reduction"]["estimate"], 0.5
        )
        self.assertAlmostEqual(
            comparison["sender_airtime_ratio"]["estimate"], 1.15
        )
        self.assertAlmostEqual(
            comparison["completed_p99_delta_us"]["estimate"], -4000.0
        )

    def test_str_gate_requires_aggregate_and_every_family_safety_gate(self) -> None:
        comparison = {
            "deadline_miss_delta": {"estimate": -0.05, "ci95_high": -0.01},
            "completed_p99_delta_us": {"estimate": -4000.0, "ci95_high": -1000.0},
            "sender_airtime_ratio": {"estimate": 1.15, "ci95_high": 1.19},
            "background_throughput_loss": {"estimate": 0.005, "ci95_high": 0.009},
            "relative_deadline_miss_reduction": {"estimate": 0.5, "ci95_high": 0.6},
        }
        family = {
            family_id: {
                "deadline_miss_delta": {"estimate": -0.01},
                "completed_p99_delta_us": {"estimate": -1000.0},
                "sender_airtime_ratio": {"estimate": 1.2},
                "background_throughput_loss": {"estimate": 0.01},
            }
            for family_id in self.families
        }
        passed = evaluate_str_gates(comparison, family, self.contract)
        self.assertEqual(passed["direct_str_victory"]["status"], "pass")
        family[self.families[-1]]["deadline_miss_delta"]["estimate"] = 0.003
        failed = evaluate_str_gates(comparison, family, self.contract)
        self.assertEqual(failed["direct_str_victory"]["status"], "fail")
        self.assertEqual(
            failed["per_family"]["families"][self.families[-1]]["status"],
            "fail",
        )

    def test_report_keeps_direct_str_and_parent_oracle_status_separate(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["bootstrap"]["replications"] = 40
        grid = self._grid()
        observations = []
        build = {
            "ns3_version": "ns-3.test",
            "ns3_upstream_commit": run_experiments.NS3_UPSTREAM_COMMIT,
            "project_git_commit": "1" * 40,
            "compiler": "test",
            "build_profile": "optimized",
        }
        for family in self.families:
            for scenario in self.scenarios[family]:
                for unit in grid[family][scenario]:
                    for arm, row in unit.items():
                        row.update(
                            {
                                "arm_id": arm,
                                "run_id": f"{row['seed']:020d}-{arm}",
                                "run_dir": "/tmp/test-run",
                                "generated_frame_count": 1800,
                                "completed_frame_count": 1700,
                                "deadline_miss_count": round(
                                    1800 * row["all_generated_deadline_miss_rate"]
                                ),
                                "background_bytes_received": 1_000_000,
                                "build_identity": build,
                            }
                        )
                        observations.append(row)
        report, paired, family_rows, scenario_rows = build_report(
            observations,
            grid,
            self.families,
            self.scenarios,
            contract,
            {"project_commit": "1" * 40},
            {"project_commit": "1" * 40, "worktree_clean": True},
        )
        self.assertEqual(
            report["direct_str_victory"]["score_aware_t2_v2"]["status"],
            "pass",
        )
        self.assertEqual(report["parent_promotion_readiness"]["status"],
                         "not_assessable")
        self.assertEqual(len(paired), 192)
        self.assertEqual(len(family_rows), 18)
        self.assertEqual(len(scenario_rows), 144)

    def test_grid_rejects_a_missing_paired_arm(self) -> None:
        grid = self._grid()
        observations = [
            {**row, "arm_id": arm}
            for family in self.families
            for scenario in self.scenarios[family]
            for unit in grid[family][scenario]
            for arm, row in unit.items()
        ]
        observations.pop()
        with self.assertRaisesRegex(QualificationAnalysisError, "lacks"):
            build_observation_grid(
                observations,
                self.families,
                self.scenarios,
                self.contract,
            )

    def test_manifest_rejects_a_partial_matrix_before_reading_runs(self) -> None:
        document = run_experiments.load_yaml(CONFIG_PATH)
        runtime = run_experiments.validate_runtime_contract(document)
        self.assertIsNotNone(runtime)
        project_commit = "1" * 40
        specs = run_experiments.expand_config(document)
        for spec in specs:
            spec["run_id"] = run_experiments.derive_run_id(
                spec["config"],
                spec["seed"],
                spec["run"],
                run_experiments.NS3_UPSTREAM_COMMIT,
                project_commit,
                runtime,
                spec["scenario"],
            )
            spec["completed"] = True
        manifest = run_experiments.build_experiment_manifest(
            document["name"],
            run_experiments.matrix_sha256(document),
            CONFIG_PATH,
            project_commit,
            specs,
            runtime,
        )
        manifest["runs"].pop()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "experiment_manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                QualificationAnalysisError, "exact 576-run matrix"
            ):
                validate_campaign_manifest(
                    path,
                    self.contract,
                    {"project_commit": project_commit, "worktree_clean": True},
                )


if __name__ == "__main__":
    unittest.main()
